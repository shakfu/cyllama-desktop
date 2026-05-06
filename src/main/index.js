const { app, BrowserWindow, Menu, ipcMain, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");
const net = require("net");

// Override the menu-bar app name. In packaged builds electron-builder
// already sets this via Info.plist (productName), but in dev mode
// Electron derives the name from the executable, which is "Electron".
// setName() must run before app.whenReady() to take effect on macOS.
app.setName("Cyllama Desktop");

// E2E hook: redirect userData so the Playwright harness gets an
// isolated workspace per test rather than blowing away the user's
// real chats / artifacts / models cache. Must run *before* any
// app.getPath("userData") read.
if (process.env.ELECTRON_USER_DATA_DIR) {
  app.setPath("userData", process.env.ELECTRON_USER_DATA_DIR);
}

let mainWindow = null;
let sidecarProc = null;
let sidecarInfo = null; // { port, token }

// ---------------------------------------------------------------------------
// Storage layout
//
// Per-project state (chats, artifacts, RAG, presets, sandbox, settings) lives
// under <userData>/workspaces/<id>/. Only a single implicit `default`
// workspace exists today; multi-workspace lands later (see PLAN.md S.9). The
// model cache stays global at <userData>/models/ -- GGUFs are too large to
// duplicate per project; a workspace just pins a default by path.
//
// On launch, migrateLayoutIfNeeded() promotes any pre-existing top-level
// `chats/` and `artifacts/` dirs into workspaces/default/. The migration is
// guarded by a version stamp so it runs at most once per install.
// ---------------------------------------------------------------------------
const LAYOUT_VERSION = 1;
const DEFAULT_WORKSPACE_ID = "default";

function userDataDir() {
  return app.getPath("userData");
}

function workspaceDir(id = DEFAULT_WORKSPACE_ID) {
  return path.join(userDataDir(), "workspaces", id);
}

function migrateLayoutIfNeeded() {
  const stamp = path.join(userDataDir(), ".layout_version");
  let v = 0;
  try { v = parseInt(fs.readFileSync(stamp, "utf8").trim(), 10) || 0; } catch (_) {}
  if (v >= LAYOUT_VERSION) return;

  fs.mkdirSync(workspaceDir(), { recursive: true });

  // Idempotent: each rename only fires if the source still exists at the old
  // location and the destination hasn't been populated. A crash mid-migration
  // leaves the stamp unwritten so the remaining moves run on the next launch.
  for (const sub of ["chats", "artifacts"]) {
    const src = path.join(userDataDir(), sub);
    const dst = path.join(workspaceDir(), sub);
    if (!fs.existsSync(src)) continue;
    if (fs.existsSync(dst)) continue;
    try {
      fs.renameSync(src, dst);
    } catch (err) {
      console.error(`[layout] failed to migrate ${sub}: ${err.message}`);
      // Don't write the stamp -- retry next launch.
      return;
    }
  }

  fs.writeFileSync(stamp, String(LAYOUT_VERSION), "utf8");
}

// Sidecar stdio ring buffer. Each entry: { t: timestamp, s: "out" | "err", line: string }.
// Bounded so a noisy sidecar can't blow up main-process memory.
const LOG_BUFFER_MAX = 2000;
const logBuffer = [];

function pushLog(stream, raw) {
  // Buffer incoming chunks per-stream and split on newlines so each
  // log entry is exactly one line. Tail (no trailing newline) is held
  // until the next chunk completes it.
  const carry = stream === "out" ? pushLog._carryOut : pushLog._carryErr;
  const combined = carry + String(raw);
  const lines = combined.split("\n");
  const tail = lines.pop();
  if (stream === "out") pushLog._carryOut = tail; else pushLog._carryErr = tail;
  for (const line of lines) {
    if (!line) continue;
    const entry = { t: Date.now(), s: stream, line };
    logBuffer.push(entry);
    if (logBuffer.length > LOG_BUFFER_MAX) logBuffer.shift();
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send("sidecar:log", entry);
    }
  }
}
pushLog._carryOut = "";
pushLog._carryErr = "";

function resolvePythonBin() {
  // E2E hook: tests can point at a system python (no need for the
  // bundled python-build-standalone env) so the suite runs without
  // ``make python``. The same env var is consumed below to swap the
  // sidecar script for a stub-loading launcher.
  if (process.env.CYLLAMA_E2E_PYTHON) return process.env.CYLLAMA_E2E_PYTHON;

  // In packaged app: extraResources copied to <Resources>/python
  // In dev: build/python-<arch>-<platform>/ next to package.json
  const isPackaged = app.isPackaged;
  if (isPackaged) {
    const base = path.join(process.resourcesPath, "python");
    if (process.platform === "win32") return path.join(base, "python.exe");
    return path.join(base, "bin", "python3");
  }
  // Dev-mode dir naming matches electron-builder's ${os}-${arch} convention.
  const arch = process.arch === "arm64" ? "arm64" : "x64";
  const plat = process.platform === "darwin" ? "mac"
             : process.platform === "win32" ? "win"
             : "linux";
  const dir = path.join(__dirname, "..", "..", "build", `python-${plat}-${arch}`);
  if (process.platform === "win32") return path.join(dir, "python.exe");
  return path.join(dir, "bin", "python3");
}

function resolveSidecarScript() {
  // E2E hook: when set, Electron launches this script instead of the
  // real sidecar entry. The launcher installs the conftest cyllama
  // stub before importing sidecar.py, so the renderer talks to a
  // deterministic FastAPI app without any real cyllama backend.
  if (process.env.CYLLAMA_E2E_LAUNCHER) return process.env.CYLLAMA_E2E_LAUNCHER;

  // sidecar.py ships under extraResources too, via electron-builder files config?
  // Simpler: bundle it inside src/ so it ends up in app.asar — but Python can't
  // import from asar, so we keep it in python-sidecar/ and copy to resources.
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "python-sidecar", "sidecar.py");
  }
  return path.join(__dirname, "..", "..", "python-sidecar", "sidecar.py");
}

async function getFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const { port } = srv.address();
      srv.close(() => resolve(port));
    });
  });
}

async function startSidecar() {
  const pythonBin = resolvePythonBin();
  const script = resolveSidecarScript();

  if (!fs.existsSync(pythonBin)) {
    throw new Error(`Bundled Python not found at ${pythonBin}. Run: npm run build:python`);
  }
  if (!fs.existsSync(script)) {
    throw new Error(`Sidecar script not found at ${script}`);
  }

  const port = await getFreePort();
  const token = crypto.randomBytes(32).toString("hex");

  // Artifact root for long-running jobs (HF downloads, image gen, batch
  // outputs). Lives under the active workspace so artifacts follow the
  // project they belong to. Kept under userData so a clean uninstall
  // takes it with it.
  const artifactsDir = path.join(workspaceDir(), "artifacts");
  fs.mkdirSync(artifactsDir, { recursive: true });
  // Desktop-managed models dir. Global (shared across workspaces) -- GGUFs
  // are too large to duplicate per project. The Models tab is the primary
  // surface; HF cache is enumerated read-only as a secondary listing.
  const modelsDir = path.join(userDataDir(), "models");
  fs.mkdirSync(modelsDir, { recursive: true });
  // RAG state for the active workspace: collections.json manifest +
  // <collection_id>.sqlite per collection.
  const ragDir = path.join(workspaceDir(), "rag");
  fs.mkdirSync(ragDir, { recursive: true });

  sidecarProc = spawn(pythonBin, [script], {
    env: {
      ...process.env,
      CYLLAMA_SIDECAR_PORT: String(port),
      CYLLAMA_SIDECAR_TOKEN: token,
      CYLLAMA_SIDECAR_PARENT_PID: String(process.pid),
      CYLLAMA_SIDECAR_ARTIFACTS: artifactsDir,
      CYLLAMA_SIDECAR_MODELS: modelsDir,
      CYLLAMA_SIDECAR_RAG: ragDir,
      PYTHONUNBUFFERED: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  sidecarProc.stdout.on("data", (b) => {
    process.stdout.write(`[sidecar] ${b}`);
    pushLog("out", b);
  });
  sidecarProc.stderr.on("data", (b) => {
    process.stderr.write(`[sidecar:err] ${b}`);
    pushLog("err", b);
  });
  sidecarProc.on("exit", (code, sig) => {
    console.log(`[sidecar] exited code=${code} sig=${sig}`);
    sidecarProc = null;
  });

  // Wait for the sidecar to become reachable.
  await waitForSidecar(port, token, 30_000);
  sidecarInfo = { port, token };
  return sidecarInfo;
}

async function waitForSidecar(port, token, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/health`, {
        headers: { authorization: `Bearer ${token}` },
      });
      if (res.ok) return;
    } catch (_) { /* not ready yet */ }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("Sidecar failed to start within timeout");
}

function stopSidecar() {
  if (!sidecarProc) return;
  try {
    sidecarProc.kill("SIGTERM");
  } catch (_) {}
  // Hard-kill fallback after 3s
  setTimeout(() => {
    if (sidecarProc) {
      try { sidecarProc.kill("SIGKILL"); } catch (_) {}
    }
  }, 3000);
}

function buildApplicationMenu() {
  // Defining the menu explicitly (using ``app.name`` for the leading
  // submenu label) forces macOS to display "Cyllama Desktop" instead
  // of the Electron binary's bundle name in dev. The submenu roles
  // give us the standard Cmd-Q / Cmd-H / Cmd-W bindings for free.
  const isMac = process.platform === "darwin";
  const template = [
    ...(isMac ? [{
      label: app.name,
      submenu: [
        { role: "about" },
        { type: "separator" },
        { role: "services" },
        { type: "separator" },
        { role: "hide" },
        { role: "hideOthers" },
        { role: "unhide" },
        { type: "separator" },
        { role: "quit" },
      ],
    }] : []),
    {
      label: "Edit",
      submenu: [
        { role: "undo" }, { role: "redo" },
        { type: "separator" },
        { role: "cut" }, { role: "copy" }, { role: "paste" },
        { role: "selectAll" },
      ],
    },
    {
      label: "View",
      submenu: [
        { role: "reload" }, { role: "forceReload" },
        { type: "separator" },
        { role: "togglefullscreen" },
        { role: "toggleDevTools" },
      ],
    },
    {
      role: "window",
      submenu: [
        { role: "minimize" }, { role: "close" },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1000,
    minHeight: 600,
    webPreferences: {
      preload: path.join(__dirname, "..", "preload", "index.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.loadFile(path.join(__dirname, "..", "renderer", "index.html"));
}

ipcMain.handle("sidecar:info", () => sidecarInfo);

ipcMain.handle("log:recent", () => logBuffer.slice());

// ---------------------------------------------------------------------------
// Chats: persisted under <userData>/workspaces/<id>/chats/<chatId>.json.
// Atomic writes via tmp+rename so a crash mid-write doesn't leave a torn
// file. Chat IDs are validated against an allow-pattern to prevent path
// traversal. Currently only the implicit `default` workspace is used.
// ---------------------------------------------------------------------------
const CHAT_ID_RE = /^[A-Za-z0-9_-]{6,128}$/;

function chatsDir() {
  return path.join(workspaceDir(), "chats");
}
function chatPath(id) {
  if (typeof id !== "string" || !CHAT_ID_RE.test(id)) {
    throw new Error("invalid chat id");
  }
  return path.join(chatsDir(), `${id}.json`);
}
function ensureChatsDir() {
  fs.mkdirSync(chatsDir(), { recursive: true });
}

ipcMain.handle("chats:list", async () => {
  ensureChatsDir();
  let files = [];
  try { files = await fs.promises.readdir(chatsDir()); }
  catch { return []; }
  const out = [];
  for (const f of files) {
    if (!f.endsWith(".json") || f.endsWith(".tmp.json")) continue;
    try {
      const txt = await fs.promises.readFile(path.join(chatsDir(), f), "utf8");
      const c = JSON.parse(txt);
      if (!c || typeof c.id !== "string") continue;
      out.push({
        id: c.id,
        title: typeof c.title === "string" ? c.title : "Untitled",
        updatedAt: Number(c.updatedAt) || 0,
        createdAt: Number(c.createdAt) || 0,
        messageCount: Array.isArray(c.messages) ? c.messages.length : 0,
        tokens: Number(c.tokens) || 0,
        modelPath: typeof c.modelPath === "string" ? c.modelPath : "",
      });
    } catch { /* skip unreadable */ }
  }
  out.sort((a, b) => (b.updatedAt - a.updatedAt));
  return out;
});

ipcMain.handle("chats:load", async (_e, id) => {
  const p = chatPath(id);
  const txt = await fs.promises.readFile(p, "utf8");
  return JSON.parse(txt);
});

ipcMain.handle("chats:save", async (_e, chat) => {
  ensureChatsDir();
  if (!chat || typeof chat.id !== "string" || !CHAT_ID_RE.test(chat.id)) {
    throw new Error("chat.id required (alphanumeric, 6-128 chars)");
  }
  const safe = {
    id: chat.id,
    title: typeof chat.title === "string" ? chat.title.slice(0, 200) : "Untitled",
    createdAt: Number(chat.createdAt) || Date.now(),
    updatedAt: Date.now(),
    messages: Array.isArray(chat.messages) ? chat.messages : [],
    tokens: Number(chat.tokens) || 0,
    modelPath: typeof chat.modelPath === "string" ? chat.modelPath : "",
  };
  const p = chatPath(chat.id);
  const tmp = `${p}.tmp`;
  await fs.promises.writeFile(tmp, JSON.stringify(safe, null, 2), "utf8");
  await fs.promises.rename(tmp, p);
  return safe;
});

ipcMain.handle("chats:delete", async (_e, id) => {
  const p = chatPath(id);
  try { await fs.promises.unlink(p); } catch { /* already gone */ }
  return { ok: true };
});

ipcMain.handle("fs:exists", async (_e, p) => {
  // Cheap existence check used by the renderer to silently drop a
  // stale persisted model path on launch. Path is treated as opaque
  // -- this is read-only stat, no traversal risk.
  if (typeof p !== "string" || !p) return false;
  try {
    const st = await fs.promises.stat(p);
    return st.isFile();
  } catch {
    return false;
  }
});

ipcMain.handle("shell:revealItem", async (_e, p) => {
  // Reveal-in-Finder/Explorer for a model file in the Models tab.
  // Path is treated as opaque and only passed to shell.showItemInFolder,
  // which doesn't follow symlinks or read the target.
  if (typeof p !== "string" || !p) return false;
  try {
    shell.showItemInFolder(p);
    return true;
  } catch {
    return false;
  }
});

ipcMain.handle("dialog:pickModel", async () => {
  const r = await dialog.showOpenDialog(mainWindow, {
    title: "Select GGUF model",
    filters: [{ name: "GGUF", extensions: ["gguf"] }],
    properties: ["openFile"],
  });
  if (r.canceled || r.filePaths.length === 0) return null;
  return r.filePaths[0];
});

ipcMain.handle("dialog:pickAudio", async () => {
  const r = await dialog.showOpenDialog(mainWindow, {
    title: "Select audio file",
    // WAV-only for now (matches the sidecar's load_wav_file capability).
    // Other extensions surface a typed error from the job rather than
    // appearing as silent failures, so we still allow them through the
    // file dialog -- the user gets a clearer error than "file rejected".
    filters: [
      { name: "Audio", extensions: ["wav", "mp3", "m4a", "flac", "ogg"] },
      { name: "All Files", extensions: ["*"] },
    ],
    properties: ["openFile"],
  });
  if (r.canceled || r.filePaths.length === 0) return null;
  return r.filePaths[0];
});

ipcMain.handle("dialog:pickFolder", async () => {
  const r = await dialog.showOpenDialog(mainWindow, {
    title: "Select sandbox folder",
    properties: ["openDirectory"],
  });
  if (r.canceled || r.filePaths.length === 0) return null;
  return r.filePaths[0];
});

app.whenReady().then(async () => {
  buildApplicationMenu();
  try {
    migrateLayoutIfNeeded();
    await startSidecar();
  } catch (err) {
    dialog.showErrorBox("Sidecar startup failed", String(err.message || err));
    app.quit();
    return;
  }
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  // Quit on every platform when the last window closes -- including macOS.
  // The macOS convention is to keep the app process alive when its
  // window closes, but for a single-window inference app that holds
  // open a Python sidecar (and via it, GPU resources), keeping it
  // around in the background is wasteful and surprising.
  stopSidecar();
  app.quit();
});

app.on("before-quit", stopSidecar);
process.on("exit", stopSidecar);
