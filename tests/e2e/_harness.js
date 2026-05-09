// Shared Electron-launch harness for the per-pane Playwright smoke
// suite. Each spec calls ``launchApp()`` to get a Page pointed at the
// app's renderer, with the sidecar already up and the
// /info.features capabilities reflecting the test stub.

const path = require("path");
const fs = require("fs");
const os = require("os");
const { _electron } = require("@playwright/test");

const ROOT = path.resolve(__dirname, "..", "..");
const SIDECAR_LAUNCHER = path.join(__dirname, "sidecar_launcher.py");

function pickPython() {
  // Honour an explicit override first; otherwise prefer ``python3``
  // and fall back to ``python``. The launcher only needs FastAPI +
  // uvicorn at the system level (already in
  // python-sidecar/pyproject.toml). A dev who's run ``pip install
  // -e python-sidecar`` already has them; CI installs them
  // explicitly.
  if (process.env.CYLLAMA_E2E_PYTHON) return process.env.CYLLAMA_E2E_PYTHON;
  // Prefer the bundled env if it exists -- it has FastAPI + uvicorn
  // wired up via build-python-env.sh, so no system pip is needed.
  const arch = process.arch === "arm64" ? "arm64" : "x64";
  const plat = process.platform === "darwin" ? "mac"
             : process.platform === "win32" ? "win" : "linux";
  const bundled = path.join(ROOT, "build", `python-${plat}-${arch}`,
    process.platform === "win32" ? "python.exe" : "bin/python3");
  if (fs.existsSync(bundled)) return bundled;
  return "python3";
}

async function launchApp() {
  // Per-test userData dir so workspaces / chats / artifacts don't
  // leak across specs. tmp_path equivalent for Electron.
  const userDataDir = fs.mkdtempSync(path.join(os.tmpdir(), "cyllama-e2e-"));

  // Ubuntu 24.04 (the GH Actions runner) ships an AppArmor profile
  // that blocks unprivileged user namespaces, which Chromium's setuid
  // sandbox needs. Without --no-sandbox the renderer process fails to
  // launch and the status pill never reaches "ready". Local dev on
  // macOS doesn't need it but the flag is harmless there.
  const electron = await _electron.launch({
    args: [path.join(ROOT, "src", "main", "index.js"), "--no-sandbox"],
    env: {
      ...process.env,
      ELECTRON_USER_DATA_DIR: userDataDir,
      // Routes the sidecar spawn to the stubbed launcher.
      CYLLAMA_E2E_PYTHON: pickPython(),
      CYLLAMA_E2E_LAUNCHER: SIDECAR_LAUNCHER,
      // Prevent the renderer from auto-restoring the last loaded
      // model (would 404 against the freshly-pointed userData).
      CYLLAMA_E2E: "1",
    },
  });

  const window = await electron.firstWindow();
  // Don't gate on networkidle -- the chat-list refresh + status
  // probes keep the renderer chatty against the sidecar. Wait for the
  // status pill to flip to ``ready`` instead, which signals the
  // sidecar handshake completed.
  await window.waitForLoadState("domcontentloaded");
  await window.waitForFunction(() => {
    const t = document.querySelector(".status-text");
    return t && /ready/.test(t.textContent || "");
  }, { timeout: 30_000 });

  // Cleanup hook the spec can register on its test scope.
  const cleanup = async () => {
    try { await electron.close(); } catch {}
    try { fs.rmSync(userDataDir, { recursive: true, force: true }); } catch {}
  };
  return { electron, window, userDataDir, cleanup };
}

// Click a sidebar tab by data-sidebar-view value. Used by every pane
// spec to switch into the surface under test.
async function openSidebarView(window, name) {
  await window.click(`.lt-tab[data-sidebar-view="${name}"]`);
  // Wait for the matching .sidebar-view to become active rather than
  // racing the click handler.
  await window.waitForSelector(`.sidebar-view[data-view="${name}"].active`,
    { timeout: 5_000 });
}

// Click a right-sidebar tab (Models / Agents / General) by data-tab.
async function openRightTab(window, name) {
  await window.click(`.rt-tab[data-tab="${name}"]`);
  await window.waitForSelector(`.rt-pane[data-tab="${name}"].active`,
    { timeout: 5_000 });
}

module.exports = { launchApp, openSidebarView, openRightTab };
