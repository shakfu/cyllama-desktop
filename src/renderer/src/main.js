/* global marked, katex, renderMathInElement */

// Phase 0 split: the bulk of the renderer still lives here, but the
// loopback HTTP client and the /jobs SSE client are extracted into
// reusable modules so the panes added in later phases (Documents,
// Transcribe, Image, Agents, ...) can consume them without re-implementing
// the bearer-auth handshake or SSE framing. The chat hot path keeps its
// existing direct-fetch code for now.
import * as cyllamaSidecar from "./lib/sidecar.js";
import * as cyllamaJobs from "./lib/jobs.js";
import * as cyllamaModels from "./lib/models.js";
import * as cyllamaRag from "./lib/rag.js";
import * as rightTabs from "./features/right-tabs.js";
import * as modelsTab from "./features/models-tab.js";
import * as agentsTab from "./features/agents-tab.js";
import * as generalTab from "./features/general-tab.js";
import * as modelPicker from "./features/model-picker.js";
import * as presets from "./features/presets.js";
import * as documentsDialog from "./features/documents-pane.js";
import * as transcribePane from "./features/transcribe-pane.js";
import * as imagePane from "./features/image-pane.js";
import * as serverPane from "./features/server-pane.js";
import * as batchPane from "./features/batch-pane.js";

// Expose the libs on a single namespace so feature modules added later --
// or ad-hoc devtools sessions -- can reach them without re-importing.
window.cyllamaLib = {
  sidecar: cyllamaSidecar,
  jobs: cyllamaJobs,
  models: cyllamaModels,
  rag: cyllamaRag,
  rightTabs,
};

// Sidebar-view switcher: nav-rail buttons tagged ``data-sidebar-view``
// pick which ``.sidebar-view`` is shown. Default = chats. Per-view
// lifecycle hooks are dispatched as the view becomes active or inactive
// so feature modules (e.g. Documents) can refresh / cleanup.
const SIDEBAR_VIEW_HOOKS = {
  documents: { onShow: () => documentsDialog.show(), onHide: () => documentsDialog.hide() },
  transcribe: { onShow: () => transcribePane.show(), onHide: () => transcribePane.hide() },
  image: { onShow: () => imagePane.show(), onHide: () => imagePane.hide() },
  server: { onShow: () => serverPane.show(), onHide: () => serverPane.hide() },
  batch: { onShow: () => batchPane.show(), onHide: () => batchPane.hide() },
};
let activeSidebarView = "chats";
function setSidebarView(name) {
  if (name === activeSidebarView) return;
  const prevHooks = SIDEBAR_VIEW_HOOKS[activeSidebarView];
  if (prevHooks?.onHide) try { prevHooks.onHide(); } catch (e) { console.error(e); }
  activeSidebarView = name;
  for (const v of document.querySelectorAll(".sidebar-view")) {
    const match = v.dataset.view === name;
    v.classList.toggle("active", match);
    v.hidden = !match;
  }
  for (const b of document.querySelectorAll(".nav-btn[data-sidebar-view]")) {
    b.classList.toggle("active", b.dataset.sidebarView === name);
  }
  const nextHooks = SIDEBAR_VIEW_HOOKS[name];
  if (nextHooks?.onShow) try { nextHooks.onShow(); } catch (e) { console.error(e); }
}
for (const b of document.querySelectorAll(".nav-btn[data-sidebar-view]")) {
  b.addEventListener("click", () => setSidebarView(b.dataset.sidebarView));
}

const appEl       = document.getElementById("app");
const logEl       = document.getElementById("log");
const promptEl    = document.getElementById("prompt");
const sendBtn     = document.getElementById("send");
const pickBtn     = document.getElementById("pick");
const ejectBtn    = document.getElementById("ejectBtn");
const modelNameEl = document.getElementById("modelName");
const emptyEl     = document.getElementById("emptyState");
const statusDot   = document.querySelector(".status-dot");
const statusText  = document.querySelector(".status-text");
const toggleLeft  = document.getElementById("toggleLeft");
const toggleRight = document.getElementById("toggleRight");

let sidecar = null;
let modelPath = "";
let inFlight = false;
let stickyScroll = true;
let abortCtl = null;
let approxTokens = 0;

// Conversation history for the active chat. Each entry is
// { role: "user" | "assistant", content: string }. The system prompt is
// NOT stored here -- it's read from the right-panel textarea each send,
// so editing it mid-chat updates immediately.
let messages = [];

// Persisted chat metadata for the current working chat. ``id == null``
// means an unsaved new chat that gets persisted on the first completed
// turn. ``createdAt`` is set at save time. Tokens / messageCount are
// derived from ``messages``; we cache the last-seen sidebar list to
// avoid disk hits on every render.
let activeChat = { id: null, title: "New Chat", createdAt: 0, modelPath: "", systemPrompt: "" };
let chatList = [];

const ACTIVE_CHAT_KEY = "active_chat_id";

// Last loaded model path. Persisted on every ``setModel()`` call so
// eject also clears it -- otherwise we'd "remember" a model the user
// just unloaded. Cleared by setModel("").
const LAST_MODEL_KEY = "last_model_path";

function newChatId() {
  // crypto.randomUUID is available in Electron's renderer (Chromium >= 92).
  return crypto.randomUUID();
}

function deriveTitle(msgs) {
  const first = (msgs || []).find((m) => m.role === "user");
  if (!first) return "New Chat";
  const t = first.content.replace(/\s+/g, " ").trim().slice(0, 60);
  return t || "New Chat";
}

// Fast client-side estimate (chars/4). Used only when no real count is
// available yet -- e.g. during the live stream before the turn completes,
// or as a fallback when no model is loaded.
function tokenCountEstimate(msgs) {
  let total = 0;
  for (const m of msgs || []) total += Math.max(1, Math.round((m.content || "").length / 4));
  return total;
}

// True token count via the sidecar's tokenizer. Concatenates message
// contents with a delimiter so we count something representative of the
// joined history without paying for full-template formatting (which
// would also include role tokens we don't expose to the user).
async function tokenizeMessages(msgs) {
  if (!sidecar || !modelPath || !msgs || msgs.length === 0) return 0;
  const text = msgs.map((m) => m.content).join("\n\n");
  try {
    const res = await fetch(`http://127.0.0.1:${sidecar.port}/tokenize`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "authorization": `Bearer ${sidecar.token}`,
      },
      body: JSON.stringify({ model_path: modelPath, text }),
    });
    if (!res.ok) return tokenCountEstimate(msgs);
    const j = await res.json();
    return Number(j.count) || 0;
  } catch {
    return tokenCountEstimate(msgs);
  }
}

/* ----------------------------------------------------------------
   Markdown + KaTeX
   ---------------------------------------------------------------- */
if (window.marked) {
  marked.setOptions({ gfm: true, breaks: false });
}

const KATEX_OPTS = {
  delimiters: [
    { left: "$$", right: "$$", display: true },
    { left: "\\[", right: "\\]", display: true },
    { left: "\\(", right: "\\)", display: false },
    { left: "$",  right: "$",   display: false },
  ],
  throwOnError: false,
  ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code"],
  ignoredClasses: ["cursor"],
};

function mdToHtml(md) {
  return window.marked ? marked.parse(md) : escapeHtml(md);
}
function applyKatex(node) {
  if (!window.renderMathInElement) return;
  try { renderMathInElement(node, KATEX_OPTS); } catch (_) { /* mid-stream */ }
}
function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

/* Render a complete (non-streaming) markdown blob into a target node, in place. */
function renderStatic(target, md) {
  target.innerHTML = mdToHtml(md);
  applyKatex(target);
}

/* ----------------------------------------------------------------
   Incremental renderer
   ----------------------------------------------------------------
   Splits the buffer into a "committed" prefix and a "tail":
     committed: parsed once, frozen, never touched again
     tail:      re-parsed on every animation frame
   Boundary moves forward when we find a "\n\n" that lies outside any
   open fenced-code block (```) or display-math block ($$). This keeps
   incomplete code/math intact in the tail until they're fully closed.
   ---------------------------------------------------------------- */
function findStableSplit(raw) {
  let inFence = false;
  let inMath = false;
  let lastSafe = 0;
  const n = raw.length;
  let atLineStart = true;

  for (let i = 0; i < n; i++) {
    const ch = raw[i];

    if (atLineStart && !inMath && raw.startsWith("```", i)) {
      inFence = !inFence;
      // skip rest of line
      while (i < n && raw[i] !== "\n") i++;
      atLineStart = true;
      continue;
    }

    if (!inFence && raw[i] === "$" && raw[i + 1] === "$") {
      inMath = !inMath;
      i += 1;            // consume the second $
      atLineStart = false;
      continue;
    }

    if (!inFence && !inMath && ch === "\n" && raw[i + 1] === "\n") {
      lastSafe = i + 2;  // commit through the blank line
      i += 1;
      atLineStart = true;
      continue;
    }

    atLineStart = (ch === "\n");
  }
  return lastSafe;
}

function createIncrementalRenderer(asstText) {
  const committedEl = document.createElement("div");
  committedEl.className = "committed";
  const tailEl = document.createElement("div");
  tailEl.className = "tail";
  asstText.appendChild(committedEl);
  asstText.appendChild(tailEl);

  let committedEnd = 0;

  function render(raw, streaming) {
    const split = findStableSplit(raw);

    if (split > committedEnd) {
      const slice = raw.slice(committedEnd, split);
      const buf = document.createElement("div");
      buf.innerHTML = mdToHtml(slice);
      applyKatex(buf);
      while (buf.firstChild) committedEl.appendChild(buf.firstChild);
      committedEnd = split;
    }

    const tailRaw = raw.slice(committedEnd);
    tailEl.innerHTML = mdToHtml(tailRaw);
    applyKatex(tailEl);
    if (streaming) {
      const cursor = document.createElement("span");
      cursor.className = "cursor";
      tailEl.appendChild(cursor);
    }
  }

  return render;
}

/* ----------------------------------------------------------------
   UI helpers
   ---------------------------------------------------------------- */
function setStatus(state, text) {
  if (statusDot) statusDot.dataset.state = state;
  if (statusText) statusText.textContent = text;
}
function dismissEmpty() {
  const e = logEl.querySelector(".empty-state");
  if (e) e.remove();
}
function showEmptyState() {
  if (logEl.querySelector(".empty-state")) return;
  const wrap = document.createElement("div");
  wrap.className = "empty-state";
  const t = document.createElement("div");
  t.className = "empty-title";
  t.textContent = "Local inference, in a window.";
  const s = document.createElement("div");
  s.className = "empty-sub";
  s.textContent = "Pick a model above and start a conversation.";
  wrap.appendChild(t); wrap.appendChild(s);
  logEl.appendChild(wrap);
}

function clearLog() {
  while (logEl.firstChild) logEl.removeChild(logEl.firstChild);
}

function newChat() {
  if (inFlight) return;            // ignore mid-stream
  messages = [];
  approxTokens = 0;
  // Seed a fresh working chat with the user's saved default system
  // prompt. Editing the textarea on this unsaved chat updates the
  // default; the value gets snapshotted into the chat's JSON on first
  // save so future edits to the default don't retroactively change
  // older chats.
  activeChat = {
    id: null, title: "New Chat", createdAt: 0,
    modelPath: "", systemPrompt: getDefaultSystemPrompt(),
  };
  setSystemPromptUI(activeChat.systemPrompt);
  clearLog();
  showEmptyState();
  saveActiveChatId();
  renderChatList();
  promptEl.focus();
}
function basename(p) { return p ? p.split(/[\\/]/).pop() : ""; }
function setModel(path) {
  modelPath = path || "";
  modelNameEl.textContent = path ? basename(path) : "Select a model";
  modelNameEl.title = path || "";
  try {
    if (modelPath) localStorage.setItem(LAST_MODEL_KEY, modelPath);
    else localStorage.removeItem(LAST_MODEL_KEY);
  } catch {}
  updateSendEnabled();
}
function bumpTokens(text) {
  // Maintained for streaming-time updates; the persisted total is
  // re-derived from messages at save time so we don't have to be exact.
  approxTokens += Math.max(1, Math.round(text.length / 4));
}

function updateSendEnabled() {
  if (inFlight) {
    sendBtn.disabled = false;
    sendBtn.title = "Stop";
    return;
  }
  const ok = !!modelPath && !!sidecar;
  sendBtn.disabled = !ok;
  sendBtn.title = !sidecar ? "Sidecar not connected"
                : !modelPath ? "Pick a model first"
                : "Send";
}

/* ----------------------------------------------------------------
   Multimodal attachments (LLAVA / MTMD)
   ---------------------------------------------------------------- */
const MMPROJ_PATH_KEY = "mmproj_path";
// Pending attachments for the *next* send. Each entry is the
// /chat/upload response: ``{id, name, size, path, url}``. Cleared
// after the message is committed to history.
let pendingAttachments = [];
// Cached blob URLs for display: keyed on the upload's ``url`` so a
// chat-log replay (or the regenerate path) reuses the same blob.
const attachmentBlobCache = new Map();

function getMmprojPath() {
  try { return localStorage.getItem(MMPROJ_PATH_KEY) || ""; } catch { return ""; }
}
function setMmprojPath(v) {
  try {
    if (v) localStorage.setItem(MMPROJ_PATH_KEY, v);
    else localStorage.removeItem(MMPROJ_PATH_KEY);
  } catch {}
}

async function attachmentBlobUrl(upload) {
  // upload.url is the sidecar-relative path like ``/chat/upload/<file>``.
  // Authenticated fetch -> blob URL works under the existing
  // ``img-src 'self' data:`` CSP.
  if (!upload || !upload.url) return "";
  if (attachmentBlobCache.has(upload.url)) return attachmentBlobCache.get(upload.url);
  try {
    const res = await cyllamaSidecar.sidecarFetch(upload.url);
    if (!res.ok) return "";
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    attachmentBlobCache.set(upload.url, url);
    return url;
  } catch {
    return "";
  }
}

async function uploadAttachmentFile(file) {
  // Multipart POST to /chat/upload. We use sidecarUrl to pick up the
  // bearer token so the sidecar's auth middleware accepts it.
  const url = await cyllamaSidecar.sidecarUrl("/chat/upload");
  const info = await cyllamaSidecar.getSidecarInfo();
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(url, {
    method: "POST",
    headers: { "authorization": `Bearer ${info.token}` },
    body: fd,
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`upload failed: ${res.status} ${detail}`);
  }
  return res.json();
}

function renderPendingAttachments() {
  const host = document.getElementById("composerAttachments");
  if (!host) return;
  if (!pendingAttachments.length) {
    host.replaceChildren();
    host.hidden = true;
    return;
  }
  host.hidden = false;
  host.replaceChildren();
  pendingAttachments.forEach((a, i) => {
    const card = document.createElement("div");
    card.className = "attachment-chip";
    const img = document.createElement("img");
    img.className = "attachment-thumb";
    img.alt = a.name || "";
    img.title = a.name || "";
    attachmentBlobUrl(a).then((u) => { if (u) img.src = u; });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "attachment-remove";
    remove.title = "Remove";
    remove.innerHTML = '<svg><use href="#i-x"/></svg>';
    remove.addEventListener("click", () => {
      pendingAttachments.splice(i, 1);
      renderPendingAttachments();
    });
    card.appendChild(img);
    card.appendChild(remove);
    host.appendChild(card);
  });
}

function renderMessageAttachments(images, host) {
  // ``images`` is the persisted ``[{id, name, url, path}, ...]`` list.
  if (!Array.isArray(images) || !images.length) return;
  const strip = document.createElement("div");
  strip.className = "msg-attachments";
  for (const a of images) {
    const img = document.createElement("img");
    img.className = "msg-attachment-thumb";
    img.alt = a.name || "";
    img.title = a.name || "";
    attachmentBlobUrl(a).then((u) => { if (u) img.src = u; });
    strip.appendChild(img);
  }
  host.appendChild(strip);
}

async function onAttachFiles(files) {
  if (!files || !files.length) return;
  const errors = [];
  for (const f of files) {
    try {
      const upload = await uploadAttachmentFile(f);
      pendingAttachments.push(upload);
    } catch (e) {
      errors.push(`${f.name}: ${e.message}`);
    }
  }
  if (errors.length) errorLine(`attach: ${errors.join("; ")}`);
  renderPendingAttachments();
}

function makeExchange(prompt, images) {
  dismissEmpty();
  const wrap = document.createElement("article");
  wrap.className = "exchange";
  // Stash the raw user prompt on the DOM so regenerate can re-send it
  // without re-parsing the rendered markdown.
  wrap.dataset.userPrompt = prompt;

  const userBlock = document.createElement("div");
  userBlock.className = "user-block";
  const userRole = document.createElement("div");
  userRole.className = "role-label";
  userRole.textContent = "You";
  const userText = document.createElement("div");
  userText.className = "user-text";
  // Render the user prompt through markdown + KaTeX (one-shot).
  renderStatic(userText, prompt);
  userBlock.appendChild(userRole);
  // Multimodal attachments sit between the role label and the text so
  // the visual order matches what the model "sees" -- image first,
  // then question. Persisted on the chat row's dataset for replay.
  if (Array.isArray(images) && images.length) {
    renderMessageAttachments(images, userBlock);
    try { wrap.dataset.userImages = JSON.stringify(images); } catch {}
  }
  userBlock.appendChild(userText);

  const asstBlock = document.createElement("div");
  asstBlock.className = "asst-block";
  const asstRole = document.createElement("div");
  asstRole.className = "role-label";
  asstRole.textContent = "Assistant";
  const asstText = document.createElement("div");
  asstText.className = "asst-text";
  asstBlock.appendChild(asstRole);
  asstBlock.appendChild(asstText);

  // Hover actions on the assistant block (copy / regenerate). The
  // raw streamed content is stashed on ``wrap.dataset.asstRaw`` by
  // the send loop so copy yields the original markdown rather than
  // the rendered HTML's textContent (which loses code fences).
  attachMessageActions(asstBlock, () => wrap.dataset.asstRaw || "", wrap);

  wrap.appendChild(userBlock);
  wrap.appendChild(asstBlock);
  logEl.appendChild(wrap);
  if (stickyScroll) logEl.scrollTop = logEl.scrollHeight;

  return { wrap, asstText };
}

function attachMessageActions(host, getText, exchangeEl) {
  const actions = document.createElement("div");
  actions.className = "msg-actions";

  const copyBtn = document.createElement("button");
  copyBtn.type = "button";
  copyBtn.className = "msg-action";
  copyBtn.title = "Copy";
  copyBtn.innerHTML = '<svg><use href="#i-copy"/></svg>';
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(getText());
      copyBtn.classList.add("ok");
      setTimeout(() => copyBtn.classList.remove("ok"), 900);
    } catch (e) {
      errorLine(`copy: ${e.message}`);
    }
  });
  actions.appendChild(copyBtn);

  const regenBtn = document.createElement("button");
  regenBtn.type = "button";
  regenBtn.className = "msg-action";
  regenBtn.title = "Regenerate";
  regenBtn.innerHTML = '<svg><use href="#i-refresh"/></svg>';
  regenBtn.addEventListener("click", () => regenerateFromExchange(exchangeEl));
  actions.appendChild(regenBtn);

  host.appendChild(actions);
}

function regenerateFromExchange(exchangeEl) {
  if (inFlight) return;
  if (!exchangeEl || !exchangeEl.parentNode) return;
  const userPrompt = exchangeEl.dataset.userPrompt || "";
  if (!userPrompt) return;

  // Trim history back to before this exchange's user turn. Each
  // exchange owns one user + (optionally) one assistant message.
  const exchanges = Array.from(logEl.querySelectorAll(".exchange"));
  const idx = exchanges.indexOf(exchangeEl);
  if (idx < 0) return;

  // Find the message-list index of this exchange's user message. Walk
  // messages, counting "user" entries, and stop at the idx-th user.
  let userMsgIdx = -1;
  let userCount = 0;
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].role !== "user") continue;
    if (userCount === idx) { userMsgIdx = i; break; }
    userCount++;
  }
  if (userMsgIdx < 0) return;

  // Drop everything from this user turn onward and remove all
  // exchange DOM nodes from this one to the end.
  messages = messages.slice(0, userMsgIdx);
  for (let i = exchanges.length - 1; i >= idx; i--) exchanges[i].remove();
  approxTokens = tokenCountEstimate(messages);

  // Replay this exchange's user prompt through the normal send path.
  promptEl.value = userPrompt;
  send();
}

function transientLine(text, cls) {
  dismissEmpty();
  const el = document.createElement("div");
  el.className = cls;
  el.textContent = text;
  logEl.appendChild(el);
  if (stickyScroll) logEl.scrollTop = logEl.scrollHeight;
  // Auto-dismiss after 4s.
  setTimeout(() => {
    el.classList.add("fade-out");
    setTimeout(() => { if (el.parentNode) el.remove(); }, 300);
  }, 4000);
}
function systemLine(text) { transientLine(text, "system-line"); }
function errorLine(text)  { transientLine(text, "error-line"); }

logEl.addEventListener("scroll", () => {
  const nearBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
  stickyScroll = nearBottom;
}, { passive: true });

/* ----------------------------------------------------------------
   Console (sidecar log) panel
   ---------------------------------------------------------------- */
const consoleEl    = document.getElementById("console");
const consoleBody  = document.getElementById("consoleBody");
const consoleClear = document.getElementById("consoleClear");
const consoleClose = document.getElementById("consoleClose");
const navConsole   = document.getElementById("navConsole");

function consoleVisible() {
  return consoleEl && !consoleEl.hidden;
}

function fmtTime(t) {
  const d = new Date(t);
  return d.toTimeString().slice(0, 8);
}

function appendLogLine(entry) {
  if (!consoleBody) return;
  const empty = consoleBody.querySelector(".log-empty");
  if (empty) empty.remove();
  const div = document.createElement("div");
  div.className = "log-line" + (entry.s === "err" ? " err" : "");
  const ts = document.createElement("span");
  ts.className = "ts";
  ts.textContent = fmtTime(entry.t);
  div.appendChild(ts);
  div.appendChild(document.createTextNode(entry.line));
  consoleBody.appendChild(div);
  // Cap visible nodes -- the in-process ring buffer is 2000 lines, so
  // mirror that ceiling here to avoid DOM bloat on long sessions.
  while (consoleBody.childElementCount > 2000) consoleBody.firstChild.remove();
  // Auto-scroll only when the user is already near the bottom; lets
  // them scroll up to read without fighting them.
  const nearBottom = consoleBody.scrollHeight - consoleBody.scrollTop - consoleBody.clientHeight < 40;
  if (nearBottom) consoleBody.scrollTop = consoleBody.scrollHeight;
}

async function openConsole() {
  if (!consoleEl) return;
  consoleEl.hidden = false;
  consoleEl.setAttribute("aria-hidden", "false");
  if (navConsole) navConsole.classList.add("active");
  // Lazily populate on first open.
  if (!consoleBody.dataset.loaded) {
    consoleBody.dataset.loaded = "1";
    try {
      const recent = await window.cyllama.log.recent();
      if (!recent || recent.length === 0) {
        const e = document.createElement("div");
        e.className = "log-empty";
        e.textContent = "(no output yet)";
        consoleBody.appendChild(e);
      } else {
        for (const entry of recent) appendLogLine(entry);
      }
    } catch (e) {
      errorLine(`load log: ${e.message}`);
    }
    consoleBody.scrollTop = consoleBody.scrollHeight;
  }
}

function closeConsole() {
  if (!consoleEl) return;
  consoleEl.hidden = true;
  consoleEl.setAttribute("aria-hidden", "true");
  if (navConsole) navConsole.classList.remove("active");
}

function toggleConsole() {
  if (consoleVisible()) closeConsole();
  else openConsole();
}

if (navConsole) navConsole.addEventListener("click", toggleConsole);
if (consoleClose) consoleClose.addEventListener("click", closeConsole);
if (consoleClear) consoleClear.addEventListener("click", () => {
  if (consoleBody) consoleBody.innerHTML = "";
});

// Subscribe once at module load so live lines accumulate even when the
// panel is closed (matches user expectation that the log "remembers"
// what happened while you weren't looking).
if (window.cyllama && window.cyllama.log && typeof window.cyllama.log.subscribe === "function") {
  window.cyllama.log.subscribe((entry) => {
    // Skip live updates until the user has opened the panel for the
    // first time. The main-process ring buffer holds the recent
    // history; first-open backfills from there to catch up.
    if (!consoleBody || !consoleBody.dataset.loaded) return;
    appendLogLine(entry);
  });
}

/* ----------------------------------------------------------------
   Sampling parameters (persisted)
   ---------------------------------------------------------------- */
const PARAM_DEFAULTS = {
  temperature:       0.8,
  top_p:             0.95,
  top_k:             40,
  min_p:             0.05,
  repeat_penalty:    1.0,
  presence_penalty:  0.0,
  frequency_penalty: 0.0,
  mirostat:          0,
  mirostat_tau:      5.0,
  mirostat_eta:      0.1,
  max_tokens:        512,
  seed:              "",   // empty string => omit from request
  stop_sequences:    "",   // comma-separated; parsed into list at send time
  // Phase 3 hardware fields. n_gpu_layers default -1 = "all on GPU".
  // n_ctx / n_batch left blank so cyllama uses the model's defaults.
  n_gpu_layers:      -1,
  n_ctx:             "",
  n_batch:           "",
  main_gpu:          0,
  split_mode:        1,
  tensor_split:      "",   // CSV; parsed to list[float] server-side
  // Phase 2 advanced. Grammar is raw GBNF text; speculative + ngram
  // are sent through ``params.speculative`` / ``params.ngram`` in
  // ``getCurrentParams`` rather than as flat keys, but the source-of-
  // truth values are kept here so the same persist / reset path covers
  // them. Speculative is "off" until a draft model is picked.
  grammar:           "",
  spec_draft_model:  "",
  spec_n_max:        16,
  spec_n_min:        0,
  spec_p_split:      0.1,
  spec_p_min:        0.75,
  ngram_enabled:     false,
};
const PARAM_KEYS = Object.keys(PARAM_DEFAULTS);
const PARAM_INT_KEYS = new Set([
  "top_k", "max_tokens", "seed", "mirostat",
  "n_gpu_layers", "n_ctx", "n_batch", "main_gpu", "split_mode",
  "spec_n_max", "spec_n_min",
]);
const PARAM_CSV_KEYS = new Set(["stop_sequences", "tensor_split"]);
// Free-form text fields that bypass numeric coercion entirely (grammar
// is multi-line GBNF; spec_draft_model is a path). Listed here so
// ``getCurrentParams`` doesn't drop them via ``Number(raw)`` checks.
const PARAM_TEXT_KEYS = new Set(["grammar", "spec_draft_model"]);
// Boolean checkbox-backed fields. Persisted as "0"/"1" strings via
// localStorage and round-tripped to bool at send time.
const PARAM_BOOL_KEYS = new Set(["ngram_enabled"]);

// Default system prompt used when a brand-new chat starts. Per-chat
// overrides live inside the chat's JSON file; this localStorage key
// is the *seed* for new chats and the global fallback when no chat is
// active. Editing the textarea on the unsaved working chat also
// updates this key so the next "New chat" inherits the change.
const SYSTEM_PROMPT_STORAGE_KEY = "system_prompt";

function paramEl(key) { return document.getElementById(`p-${key}`); }
function paramOut(key) { return document.getElementById(`p-${key}-out`); }

function formatParamValue(key, value) {
  if (PARAM_INT_KEYS.has(key)) return String(value);
  if (PARAM_CSV_KEYS.has(key)) return String(value);
  if (PARAM_TEXT_KEYS.has(key)) return String(value ?? "");
  if (PARAM_BOOL_KEYS.has(key)) return value ? "1" : "0";
  // Float: 2 decimals is enough for the UI display.
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : String(value);
}

function loadParams() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("params") || "{}"); } catch {}
  for (const key of PARAM_KEYS) {
    const el = paramEl(key);
    if (!el) continue;
    const v = saved[key] ?? PARAM_DEFAULTS[key];
    if (PARAM_BOOL_KEYS.has(key)) {
      el.checked = (v === true || v === "1" || v === 1);
    } else {
      el.value = String(v ?? "");
    }
    const out = paramOut(key);
    if (out) out.textContent = formatParamValue(key, PARAM_BOOL_KEYS.has(key) ? el.checked : v);
  }
}

function saveParams() {
  const out = {};
  for (const key of PARAM_KEYS) {
    const el = paramEl(key);
    if (!el) continue;
    out[key] = PARAM_BOOL_KEYS.has(key) ? (el.checked ? "1" : "0") : el.value;
  }
  try { localStorage.setItem("params", JSON.stringify(out)); } catch {}
}

function getCurrentParams() {
  const out = {};
  // Speculative is collected separately into a nested object since the
  // sidecar accepts ``params.speculative = {n_max, n_min, p_split, p_min,
  // draft_model_path}`` rather than flat keys.
  const spec = {};
  for (const key of PARAM_KEYS) {
    const el = paramEl(key);
    if (!el) continue;
    if (PARAM_BOOL_KEYS.has(key)) {
      // Only emit when checked; sidecar treats missing-key as "off".
      if (el.checked) {
        if (key === "ngram_enabled") out.ngram = true;
        else out[key] = true;
      }
      continue;
    }
    const raw = el.value;
    if (raw === "" || raw == null) continue;     // omit blank seed etc.
    if (PARAM_CSV_KEYS.has(key)) {
      const list = raw.split(",").map((s) => s.trim()).filter(Boolean);
      if (list.length) out[key] = list;
      continue;
    }
    if (PARAM_TEXT_KEYS.has(key)) {
      const trimmed = raw.trim();
      if (!trimmed) continue;
      if (key === "spec_draft_model") spec.draft_model_path = trimmed;
      else out[key] = trimmed;
      continue;
    }
    if (key.startsWith("spec_") && key !== "spec_draft_model") {
      const n = Number(raw);
      if (!Number.isFinite(n)) continue;
      spec[key.slice(5)] = PARAM_INT_KEYS.has(key) ? Math.round(n) : n;
      continue;
    }
    const n = Number(raw);
    if (!Number.isFinite(n)) continue;
    out[key] = PARAM_INT_KEYS.has(key) ? Math.round(n) : n;
  }
  // Only attach speculative if the user actually picked a draft model;
  // sliders alone are inert without one.
  if (spec.draft_model_path) out.speculative = spec;
  return out;
}

function getSystemPrompt() {
  const el = document.getElementById("p-system_prompt");
  if (!el) return "";
  return el.value.trim();
}

function setSystemPromptUI(value) {
  const el = document.getElementById("p-system_prompt");
  if (!el) return;
  el.value = value || "";
}

function getDefaultSystemPrompt() {
  try { return localStorage.getItem(SYSTEM_PROMPT_STORAGE_KEY) || ""; }
  catch { return ""; }
}

function setDefaultSystemPrompt(value) {
  try {
    if (value) localStorage.setItem(SYSTEM_PROMPT_STORAGE_KEY, value);
    else localStorage.removeItem(SYSTEM_PROMPT_STORAGE_KEY);
  } catch {}
}

// Editing the textarea updates the active chat's prompt in memory so
// the next request reflects the change immediately. If we're on the
// unsaved working chat, also update the default seed in localStorage
// so a future "New chat" inherits the edit. For a saved chat, the
// new value persists on the next auto-save (after the next completed
// turn).
function onSystemPromptInput() {
  const v = getSystemPrompt();
  activeChat.systemPrompt = v;
  if (!activeChat.id) setDefaultSystemPrompt(v);
}

function resetParams() {
  for (const key of PARAM_KEYS) {
    const el = paramEl(key);
    if (!el) continue;
    const def = PARAM_DEFAULTS[key];
    if (PARAM_BOOL_KEYS.has(key)) el.checked = !!def;
    else el.value = String(def ?? "");
    const out = paramOut(key);
    if (out) out.textContent = formatParamValue(key, PARAM_BOOL_KEYS.has(key) ? el.checked : def);
  }
  saveParams();
}

function applyMirostatVisibility() {
  const sel = paramEl("mirostat");
  // If the mirostat row itself is hidden because cyllama doesn't accept
  // it, pretend it's "off" so tau/eta also stay hidden -- they're
  // meaningless without the mode.
  const sectionEl = sel ? sel.closest(".param") : null;
  const sectionHidden = !sel || (sectionEl && sectionEl.hidden);
  const off = sectionHidden || sel.value === "0";
  for (const e of document.querySelectorAll("[data-mirostat-only]")) {
    e.hidden = off;
  }
}

// Estimate Layers: prompts for VRAM, hits /hardware/estimate-layers,
// drops the answer into the n_gpu_layers input. Surfaces 501 helpfully
// when the installed cyllama lacks the helper.
async function estimateGpuLayers() {
  if (!modelPath) {
    errorLine("Pick a model first to estimate layers.");
    return;
  }
  const raw = window.prompt("Available GPU memory (MB)?", "8000");
  if (raw == null) return;
  const vram = Number(raw);
  if (!Number.isFinite(vram) || vram <= 0) {
    errorLine("Enter a positive number of MB.");
    return;
  }
  const panel = document.getElementById("estimatePanel");
  const out = document.getElementById("estimateOutput");
  if (panel && out) {
    panel.hidden = false;
    out.textContent = "estimating...";
  }
  try {
    const j = await cyllamaSidecar.sidecarJson("/hardware/estimate-layers", {
      model_path: modelPath,
      gpu_memory_mb: vram,
    });
    const n = j.n_gpu_layers;
    if (typeof n === "number") {
      const el = paramEl("n_gpu_layers");
      if (el) {
        el.value = String(n);
        const o = paramOut("n_gpu_layers");
        if (o) o.textContent = formatParamValue("n_gpu_layers", el.value);
        saveParams();
      }
    }
    if (out) {
      const lines = [];
      if (typeof j.n_gpu_layers === "number") {
        lines.push(`n_gpu_layers: ${j.n_gpu_layers} / ${j.n_layers_total ?? "?"}`);
      }
      if (typeof j.model_size_mb === "number") lines.push(`model: ${j.model_size_mb} MB`);
      if (typeof j.kv_cache_mb === "number") lines.push(`kv cache: ${j.kv_cache_mb} MB`);
      if (typeof j.fits_fully === "boolean") lines.push(`fits fully: ${j.fits_fully ? "yes" : "no"}`);
      if (j.notes) lines.push(j.notes);
      out.textContent = lines.join(" · ") || JSON.stringify(j);
    }
  } catch (e) {
    if (out) out.textContent = `failed: ${e.message}`;
  }
}

// Hide rows for sampling fields the installed cyllama doesn't accept.
// Sourced from /info.supported_params + /info.features (see sidecar).
// Forward-looking presets that set values for unsupported fields are
// still safe -- the sidecar's _build_config drops them before
// constructing GenerationConfig.
async function applySupportedParams() {
  let info = null;
  try { info = await cyllamaSidecar.getInfo(); }
  catch { return; /* /info unreachable -> leave UI as-is */ }
  const supported = info && Array.isArray(info.supported_params)
    ? new Set(info.supported_params) : null;
  const features = (info && info.features) || {};
  if (supported) {
    for (const key of PARAM_KEYS) {
      // Advanced-section keys are gated by ``data-feature`` instead of
      // the supported_params list -- their wire shape is nested or
      // boolean and doesn't appear in supported_params.
      if (key.startsWith("spec_") || key === "ngram_enabled" || key === "grammar") continue;
      const el = paramEl(key);
      if (!el) continue;
      const row = el.closest(".param");
      if (!row) continue;
      row.hidden = !supported.has(key);
    }
  }

  // Advanced section: hide each ``data-feature`` row based on /info.features.
  // The ``grammar`` row stays visible if ``json_schema_to_grammar`` is
  // available even when chat-side grammar isn't wired -- the user can
  // still generate GBNF for copy/paste use.
  const featRowHidden = {
    grammar: !(features.grammar || features.json_schema_to_grammar),
    speculative: !features.speculative,
    ngram: !features.ngram,
  };
  for (const row of document.querySelectorAll("[data-feature]")) {
    const f = row.dataset.feature;
    row.hidden = !!featRowHidden[f];
  }
  // Hide the "From JSON Schema" button when the helper isn't present
  // (chat-side grammar might still be wired without it in future).
  const fromSchemaBtn = document.getElementById("grammarFromSchemaBtn");
  if (fromSchemaBtn) fromSchemaBtn.hidden = !features.json_schema_to_grammar;

  // If every advanced row is hidden, show the "no advanced features" note.
  const adv = document.getElementById("samplingAdvanced");
  if (adv) {
    const anyVisible = Array.from(adv.querySelectorAll("[data-feature]")).some(
      (r) => !r.hidden
    );
    const note = document.getElementById("advUnavailableNote");
    if (note) note.hidden = anyVisible;
  }

  applyMirostatVisibility();
  applySpeculativeVisibility();
  applyMultiGpuVisibility(info);
  transcribePane.applyVisibility(features);
  imagePane.applyVisibility(features);
  agentsTab.applyVisibility(features);
  serverPane.applyVisibility(features);
  batchPane.applyVisibility(features);
  // Composer paperclip is gated on the multimodal capability AND a
  // pinned mmproj path. The /info.features check alone isn't enough
  // -- attaching an image without an mmproj would 200 OK but the
  // sidecar would route through llm.chat() and silently drop the
  // attachment, so we hide the button until both pieces are in place.
  applyAttachButtonVisibility(features);
}

function applyAttachButtonVisibility(features) {
  const btn = document.getElementById("attach");
  if (!btn) return;
  const ok = !!(features && features.multimodal) && !!getMmprojPath();
  btn.hidden = !ok;
}

// Hide main_gpu / split_mode / tensor_split rows when the machine has
// at most one GPU-typed device. With a single GPU the only meaningful
// hardware knob is n_gpu_layers; the rest are noise. Empty / missing
// device list means the probe failed -- leave rows visible (safer
// than hiding on a real multi-GPU rig).
const MULTI_GPU_KEYS = ["main_gpu", "split_mode", "tensor_split"];
function applyMultiGpuVisibility(info) {
  const devices = (info && Array.isArray(info.devices)) ? info.devices : [];
  // GPU-typed entries: ggml reports CPU/ACCEL/GPU/iGPU. Treat both
  // 'GPU' and 'iGPU' as a GPU for this purpose.
  const gpuCount = devices.filter((d) => /^i?GPU$/i.test(String(d.type || ""))).length;
  // No probe data -> assume multi-GPU possible, leave visible.
  const hide = devices.length > 0 && gpuCount <= 1;
  for (const key of MULTI_GPU_KEYS) {
    const el = paramEl(key);
    if (!el) continue;
    const row = el.closest(".param");
    if (!row) continue;
    // Don't override a row already hidden by supported_params filtering.
    if (row.hidden && !row.dataset.multiGpuShown) continue;
    if (hide) {
      row.dataset.multiGpuShown = row.hidden ? "0" : "1";
      row.hidden = true;
    } else {
      delete row.dataset.multiGpuShown;
    }
  }
}

// Sub-rows that only matter when a draft model is selected (n_max,
// n_min, p_split, p_min) collapse otherwise. The toggle uses the same
// ``hidden`` mechanism as Mirostat tau/eta.
function applySpeculativeVisibility() {
  const sel = paramEl("spec_draft_model");
  const off = !sel || !sel.value;
  for (const e of document.querySelectorAll("[data-spec-only]")) {
    // Keep hidden if the speculative feature row itself is hidden --
    // the parent's data-feature row is the source of truth.
    const featHidden = e.hidden && !e.dataset.specOnly;
    e.hidden = off || featHidden;
  }
}

/* ----------------------------------------------------------------
   Retrieval (slice 4d): pick a RAG collection for chat-side context
   injection. The picker populates from /rag/collections; selection +
   top-K persist in localStorage. On send, the chat path retrieves the
   top-K chunks for the latest user message and prepends them to the
   system prompt.
   ---------------------------------------------------------------- */
const RAG_COLL_KEY = "chat_rag_collection_id";
const RAG_TOPK_KEY = "chat_rag_top_k";

async function refreshRagCollections() {
  const sel = document.getElementById("p-rag_collection");
  if (!sel) return;
  const saved = localStorage.getItem(RAG_COLL_KEY) || "";
  const want = sel.value || saved;
  let collections = [];
  try {
    const r = await cyllamaRag.listCollections();
    collections = r.collections || [];
  } catch (e) {
    console.warn("listCollections failed:", e);
  }
  sel.replaceChildren();
  const noneOpt = document.createElement("option");
  noneOpt.value = "";
  noneOpt.textContent = "— none —";
  sel.appendChild(noneOpt);
  for (const c of collections) {
    const opt = document.createElement("option");
    opt.value = c.id;
    opt.textContent = `${c.name}  ·  ${c.chunk_count} chunks`;
    sel.appendChild(opt);
  }
  if (want && collections.find((c) => c.id === want)) {
    sel.value = want;
  } else {
    sel.value = "";
    if (saved) localStorage.removeItem(RAG_COLL_KEY);
  }
}

function bindRetrieval() {
  const sel = document.getElementById("p-rag_collection");
  const topK = document.getElementById("p-rag_top_k");
  const topKOut = document.getElementById("p-rag_top_k-out");
  const refreshBtn = document.getElementById("ragRefresh");

  if (sel) {
    sel.addEventListener("change", () => {
      if (sel.value) localStorage.setItem(RAG_COLL_KEY, sel.value);
      else localStorage.removeItem(RAG_COLL_KEY);
    });
  }
  if (topK) {
    const saved = parseInt(localStorage.getItem(RAG_TOPK_KEY) || "", 10);
    if (saved >= 1 && saved <= 10) topK.value = String(saved);
    if (topKOut) topKOut.textContent = topK.value;
    topK.addEventListener("input", () => {
      if (topKOut) topKOut.textContent = topK.value;
      localStorage.setItem(RAG_TOPK_KEY, topK.value);
    });
  }
  if (refreshBtn) refreshBtn.addEventListener("click", refreshRagCollections);
  // The Documents view fires this after create / delete so the picker
  // doesn't go stale.
  window.addEventListener("rag:collections-changed", () => refreshRagCollections());

  refreshRagCollections();
}

function formatSourcesBlock(sources, collectionLabel) {
  const lines = sources.map((s, i) => `[${i + 1}] ${s.text}`);
  return [
    `Use the following retrieved context (from "${collectionLabel}") to answer the user's question.`,
    `If the context is irrelevant, say so and answer from your own knowledge.`,
    "",
    lines.join("\n\n"),
  ].join("\n");
}

async function buildOutgoingMessages() {
  const sys = getSystemPrompt();
  const sel = document.getElementById("p-rag_collection");
  const topKEl = document.getElementById("p-rag_top_k");
  const collId = sel?.value || "";
  const topK = parseInt(topKEl?.value || "3", 10) || 3;

  let augmentedSys = sys;
  if (collId) {
    // The latest user message is the retrieval query.
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (lastUser && lastUser.content) {
      try {
        const r = await cyllamaRag.retrieve({
          collection_id: collId,
          query: lastUser.content,
          top_k: topK,
        });
        const srcs = (r && r.sources) || [];
        if (srcs.length) {
          const opt = sel?.querySelector(`option[value="${collId}"]`);
          const label = (opt?.textContent?.split("·")[0] || collId).trim();
          const block = formatSourcesBlock(srcs, label);
          augmentedSys = sys ? `${sys}\n\n${block}` : block;
        }
      } catch (e) {
        // Soft-fail: log and send without context. RAG should never block
        // the chat hot path.
        console.warn("RAG retrieve failed:", e);
      }
    }
  }

  return augmentedSys
    ? [{ role: "system", content: augmentedSys }, ...messages]
    : [...messages];
}

function bindParams() {
  for (const key of PARAM_KEYS) {
    const el = paramEl(key);
    if (!el) continue;
    const evt = (PARAM_BOOL_KEYS.has(key) || el.tagName === "SELECT") ? "change" : "input";
    el.addEventListener(evt, () => {
      const v = PARAM_BOOL_KEYS.has(key) ? el.checked : el.value;
      const out = paramOut(key);
      if (out) out.textContent = formatParamValue(key, v);
      saveParams();
      if (key === "mirostat") applyMirostatVisibility();
      if (key === "spec_draft_model") applySpeculativeVisibility();
    });
  }
  const sysEl = document.getElementById("p-system_prompt");
  if (sysEl) sysEl.addEventListener("input", onSystemPromptInput);
  const resetBtn = document.getElementById("resetParams");
  if (resetBtn) resetBtn.addEventListener("click", () => {
    resetParams();
    applyMirostatVisibility();
    applySpeculativeVisibility();
  });
  applyMirostatVisibility();
  const estBtn = document.getElementById("estimateLayersBtn");
  if (estBtn) estBtn.addEventListener("click", estimateGpuLayers);

  const fromSchemaBtn = document.getElementById("grammarFromSchemaBtn");
  if (fromSchemaBtn) fromSchemaBtn.addEventListener("click", grammarFromSchema);

  // Populate the draft-model select once at mount, and again whenever
  // the Models tab signals a change (drag-drop import, HF download).
  refreshDraftModels();
  window.addEventListener("models:cache-changed", refreshDraftModels);
}

async function refreshDraftModels() {
  const sel = paramEl("spec_draft_model");
  if (!sel) return;
  const saved = sel.value;
  let models = [];
  try {
    // Speculative draft model: must be a chat model. Whisper / SD /
    // mmproj projectors won't satisfy the speculative-decoding contract.
    const r = await cyllamaModels.listModels({ kinds: ["chat"] });
    models = (r && r.models) || [];
  } catch { /* leave the select with just "off" */ }
  // Preserve the "off" option, replace the rest.
  while (sel.options.length > 1) sel.remove(1);
  for (const m of models) {
    const o = document.createElement("option");
    o.value = m.path;
    o.textContent = m.name;
    sel.appendChild(o);
  }
  // Restore prior selection if still valid.
  if (saved && Array.from(sel.options).some((o) => o.value === saved)) {
    sel.value = saved;
  }
  applySpeculativeVisibility();
}

// Generate a GBNF grammar from a JSON schema the user pastes in. Drops
// the result into the grammar textarea and fires its input event so
// the same persist / reset path covers it.
async function grammarFromSchema() {
  const raw = window.prompt(
    "Paste a JSON schema (object). Output GBNF goes into the Grammar field.",
    '{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}'
  );
  if (raw == null) return;
  let schema;
  try {
    schema = JSON.parse(raw);
  } catch (e) {
    errorLine(`Schema parse failed: ${e.message}`);
    return;
  }
  let grammar = "";
  try {
    const j = await cyllamaSidecar.sidecarJson("/grammar/from-schema", { schema });
    grammar = (j && j.grammar) || "";
  } catch (e) {
    errorLine(`Grammar generation failed: ${e.message}`);
    return;
  }
  const el = paramEl("grammar");
  if (!el) return;
  el.value = grammar;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  systemLine("Grammar generated from schema.");
}

/* ----------------------------------------------------------------
   Persistent chats: sidebar list, replay, save, switch
   ---------------------------------------------------------------- */
function saveActiveChatId() {
  try {
    if (activeChat.id) localStorage.setItem(ACTIVE_CHAT_KEY, activeChat.id);
    else localStorage.removeItem(ACTIVE_CHAT_KEY);
  } catch {}
}

async function refreshChatList() {
  try { chatList = await window.cyllama.chats.list(); }
  catch (e) { errorLine(`load chats: ${e.message}`); chatList = []; }
  renderChatList();
}

function renderChatList() {
  const listEl = document.getElementById("chatList");
  if (!listEl) return;
  while (listEl.firstChild) listEl.removeChild(listEl.firstChild);

  // Unsaved working chat shows up at the top until it gets persisted on
  // its first completed turn.
  if (!activeChat.id) {
    listEl.appendChild(makeChatRow({
      id: null,
      title: activeChat.title || "New Chat",
      messageCount: messages.length,
      tokens: tokenCountEstimate(messages),
    }, true));
  }

  for (const c of chatList) {
    listEl.appendChild(makeChatRow(c, c.id === activeChat.id));
  }
}

function makeChatRow(c, isActive) {
  const row = document.createElement("div");
  row.className = "chat-row" + (isActive ? " active" : "");
  row.setAttribute("role", "button");
  row.tabIndex = 0;
  row.dataset.id = c.id ?? "";

  const title = document.createElement("span");
  title.className = "row-title";
  title.textContent = c.title || "Untitled";
  const meta = document.createElement("span");
  meta.className = "row-meta";
  meta.textContent = `${c.tokens || 0} tokens`;
  row.appendChild(title);
  row.appendChild(meta);

  if (c.id) {
    const del = document.createElement("button");
    del.className = "row-delete";
    del.type = "button";
    del.title = "Delete chat";
    del.innerHTML = '<svg><use href="#i-trash"/></svg>';
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete "${c.title}"?`)) return;
      try { await window.cyllama.chats.delete(c.id); }
      catch (err) { errorLine(`delete: ${err.message}`); return; }
      if (c.id === activeChat.id) newChat();
      await refreshChatList();
    });
    row.appendChild(del);
  }

  const activate = () => {
    if (inFlight) return;
    if (c.id == null) return;
    if (c.id === activeChat.id) return;
    loadChat(c.id);
  };
  row.addEventListener("click", activate);
  row.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); activate(); }
  });

  return row;
}

async function loadChat(id) {
  if (inFlight) return;
  let c;
  try { c = await window.cyllama.chats.load(id); }
  catch (e) { errorLine(`load chat: ${e.message}`); return; }
  activeChat = {
    id: c.id,
    title: c.title || "Untitled",
    createdAt: c.createdAt || Date.now(),
    modelPath: c.modelPath || "",
    systemPrompt: typeof c.systemPrompt === "string" ? c.systemPrompt : "",
  };
  messages = Array.isArray(c.messages) ? c.messages.slice() : [];
  approxTokens = tokenCountEstimate(messages);
  // Mirror the chat's system prompt into the textarea so the right
  // panel stays in sync with the active chat.
  setSystemPromptUI(activeChat.systemPrompt);
  // Optional: if the chat remembers a model and the user hasn't picked
  // anything yet, auto-restore. Don't override an explicit pick.
  if (activeChat.modelPath && !modelPath) setModel(activeChat.modelPath);

  clearLog();
  if (messages.length === 0) showEmptyState();
  else replayMessages(messages);
  saveActiveChatId();
  renderChatList();
  stickyScroll = true;
  logEl.scrollTop = logEl.scrollHeight;
}

function replayMessages(msgs) {
  let i = 0;
  while (i < msgs.length) {
    if (msgs[i].role !== "user") { i++; continue; }
    const u = msgs[i];
    const a = msgs[i + 1] && msgs[i + 1].role === "assistant" ? msgs[i + 1] : null;

    const wrap = document.createElement("article");
    wrap.className = "exchange";
    wrap.dataset.userPrompt = u.content;

    // user
    const ub = document.createElement("div"); ub.className = "user-block";
    const ur = document.createElement("div"); ur.className = "role-label"; ur.textContent = "You";
    const ut = document.createElement("div"); ut.className = "user-text";
    renderStatic(ut, u.content);
    ub.appendChild(ur);
    if (Array.isArray(u.images) && u.images.length) {
      renderMessageAttachments(u.images, ub);
      try { wrap.dataset.userImages = JSON.stringify(u.images); } catch {}
    }
    ub.appendChild(ut);
    wrap.appendChild(ub);

    if (a) {
      const ab = document.createElement("div"); ab.className = "asst-block";
      const ar = document.createElement("div"); ar.className = "role-label"; ar.textContent = "Assistant";
      const at = document.createElement("div"); at.className = "asst-text";
      renderStatic(at, a.content);
      ab.appendChild(ar); ab.appendChild(at);
      wrap.dataset.asstRaw = a.content;
      attachMessageActions(ab, () => wrap.dataset.asstRaw || "", wrap);
      wrap.appendChild(ab);
      i += 2;
    } else {
      i += 1;
    }

    logEl.appendChild(wrap);
  }
}

async function persistActiveChat() {
  // First persist: assign an id and createdAt.
  if (!activeChat.id) {
    activeChat.id = newChatId();
    activeChat.createdAt = Date.now();
  }
  // Title rederives from the first user message each save -- handles the
  // case where the user edits the very first prompt before it's saved.
  activeChat.title = deriveTitle(messages);
  activeChat.modelPath = modelPath || activeChat.modelPath || "";

  // True token count via the sidecar's tokenizer. Falls back to the
  // chars/4 estimate if no model is loaded or the call fails.
  const tokens = await tokenizeMessages(messages);

  try {
    await window.cyllama.chats.save({
      id: activeChat.id,
      title: activeChat.title,
      createdAt: activeChat.createdAt,
      messages,
      tokens,
      modelPath: activeChat.modelPath,
      systemPrompt: activeChat.systemPrompt || "",
    });
    saveActiveChatId();
  } catch (e) {
    errorLine(`save chat: ${e.message}`);
  }
  await refreshChatList();
}

/* ----------------------------------------------------------------
   Sidebar collapse (persisted)
   ---------------------------------------------------------------- */
function applyCollapse(side, collapsed) {
  appEl.dataset[side] = collapsed ? "0" : "1";
  try { localStorage.setItem(`collapse-${side}`, collapsed ? "1" : "0"); } catch {}
}
function initCollapse() {
  for (const side of ["left", "right"]) {
    let v = "0";
    try { v = localStorage.getItem(`collapse-${side}`) || "0"; } catch {}
    appEl.dataset[side] = v === "1" ? "0" : "1";
  }
}
toggleLeft.addEventListener("click", () => {
  applyCollapse("left", appEl.dataset.left !== "0");
});
toggleRight.addEventListener("click", () => {
  applyCollapse("right", appEl.dataset.right !== "0");
});

/* ----------------------------------------------------------------
   Init / wiring
   ---------------------------------------------------------------- */
async function init() {
  initCollapse();
  loadParams();
  // Right-sidebar tabs: Models | Agents | General. Cog nav-rail button
  // jumps to General via the tab router's data-tab-jump wiring.
  rightTabs.bind();
  modelsTab.mount({
    onPick: (p) => { if (p) setModel(p); },
    reveal: (p) => window.cyllama.revealItem && window.cyllama.revealItem(p),
  });
  agentsTab.mount();
  generalTab.mount();
  // Presets bar lives at the top of the Sampling section. Bridge gives
  // it access to the existing #p-* inputs and helpers without coupling.
  presets.mount({
    paramKeys: PARAM_KEYS,
    paramEl, paramOut, formatParamValue,
    getSystemPrompt,
    setSystemPromptUI,
  });
  // Hide UI rows for sampler fields cyllama doesn't actually accept.
  // Fire-and-forget; if /info fails we leave the UI as-is and the
  // sidecar's whitelist filter still keeps chat from crashing.
  applySupportedParams();
  // Seed the unsaved working chat with the default system prompt so the
  // textarea is populated on first launch. loadChat() overrides this if
  // a chat is restored.
  activeChat.systemPrompt = getDefaultSystemPrompt();
  setSystemPromptUI(activeChat.systemPrompt);
  bindParams();
  bindRetrieval();
  setStatus("idle", "connecting");

  // Restore the last loaded model so the user doesn't have to re-pick
  // it every launch. Verify existence first -- a stale path (model
  // moved or deleted between sessions) gets cleared silently rather
  // than showing a phantom name in the pill that fails on send.
  try {
    const last = localStorage.getItem(LAST_MODEL_KEY);
    if (last) {
      const exists = await window.cyllama.fileExists(last);
      if (exists) setModel(last);
      else localStorage.removeItem(LAST_MODEL_KEY);
    }
  } catch {}

  updateSendEnabled();

  // Restore the last active chat if one exists; fall back to most-recent.
  await refreshChatList();
  let restoreId = null;
  try { restoreId = localStorage.getItem(ACTIVE_CHAT_KEY); } catch {}
  if (restoreId && chatList.find((c) => c.id === restoreId)) {
    await loadChat(restoreId);
  } else {
    renderChatList();
    showEmptyState();
  }

  try {
    sidecar = await window.cyllama.getSidecarInfo();
    setStatus("ready", `ready :${sidecar.port}`);
  } catch (err) {
    setStatus("error", "no sidecar");
    errorLine(`init error: ${err.message}`);
  }
  updateSendEnabled();

  // After a Preferences-driven sidecar restart the {port, token}
  // change. The cached lib-side info is reset by the General tab's
  // ``resetSidecarInfo()``, but the chat hot-path holds its own
  // reference (``sidecar`` above) so it has to be refreshed too.
  window.addEventListener("sidecar:restarted", async () => {
    try {
      sidecar = await window.cyllama.getSidecarInfo();
      setStatus("ready", `ready :${sidecar.port}`);
      updateSendEnabled();
    } catch (err) {
      setStatus("error", "no sidecar");
      errorLine(`reconnect error: ${err.message}`);
    }
  });
}

// ModelPicker dropdown takes over the pill click. The OS file dialog is
// still reachable as the "Browse..." item at the bottom of the dropdown.
modelPicker.bind({
  pillSelector: "#pick",
  onPickPath: (p) => { if (p) setModel(p); },
  onBrowsePath: () => window.cyllama.pickModel(),
});
ejectBtn.addEventListener("click", async () => {
  if (inFlight) return;
  // Tell the sidecar to drop its cached LLM so GPU memory is actually
  // released. We do this before clearing the path so an in-flight
  // request couldn't squeeze in against a half-cleared state.
  if (sidecar) {
    try {
      await fetch(`http://127.0.0.1:${sidecar.port}/unload`, {
        method: "POST",
        headers: { "authorization": `Bearer ${sidecar.token}` },
      });
    } catch (e) {
      errorLine(`unload: ${e.message}`);
    }
  }
  setModel("");
});

const newChatBtn = document.getElementById("newChatBtn");
if (newChatBtn) newChatBtn.addEventListener("click", newChat);

// Multimodal: paperclip button forwards clicks to a hidden file
// input. Multi-select; each picked file is uploaded via /chat/upload.
const attachBtn = document.getElementById("attach");
const attachInput = document.getElementById("attachInput");
if (attachBtn && attachInput) {
  attachBtn.addEventListener("click", () => attachInput.click());
  attachInput.addEventListener("change", async () => {
    const files = Array.from(attachInput.files || []);
    attachInput.value = "";  // allow re-selecting the same file
    await onAttachFiles(files);
  });
}
// Models tab fires this when the user pins / clears an mmproj. Re-run
// the visibility check so the paperclip appears immediately.
window.addEventListener("mmproj:changed", () => {
  cyllamaSidecar.getInfo().then((info) => {
    applyAttachButtonVisibility(info && info.features);
  }).catch(() => applyAttachButtonVisibility({ multimodal: true }));
});

sendBtn.addEventListener("click", () => {
  if (inFlight) abortCtl?.abort();
  else send();
});
promptEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!inFlight && !sendBtn.disabled) send();
  }
});
promptEl.addEventListener("input", autoGrow);
function autoGrow() {
  promptEl.style.height = "auto";
  promptEl.style.height = Math.min(promptEl.scrollHeight, 200) + "px";
}

function setBusy(busy) {
  inFlight = busy;
  promptEl.disabled = busy;
  sendBtn.classList.toggle("streaming", busy);
  if (busy) setStatus("busy", "generating");
  else setStatus("ready", sidecar ? `ready :${sidecar.port}` : "idle");
  updateSendEnabled();
}

/* ----------------------------------------------------------------
   Streaming
   ---------------------------------------------------------------- */
async function send() {
  const prompt = promptEl.value.trim();
  if (!prompt) return;
  if (!modelPath) { errorLine("Pick a model first"); return; }
  if (!sidecar)   { errorLine("Sidecar not connected"); return; }

  promptEl.value = "";
  autoGrow();
  stickyScroll = true;
  bumpTokens(prompt);

  // Snapshot the current attachments so we can append them to this
  // turn's user message and clear the composer strip atomically.
  const turnImages = pendingAttachments.slice();
  pendingAttachments = [];
  renderPendingAttachments();

  // Append the user turn to history before rendering so the outgoing
  // payload reflects the new turn even if the request fails.
  const userMsg = { role: "user", content: prompt };
  if (turnImages.length) userMsg.images = turnImages;
  messages.push(userMsg);

  const exchange = makeExchange(prompt, turnImages);
  const { asstText } = exchange;
  const render = createIncrementalRenderer(asstText);

  let raw = "";
  let aborted = false;
  let pendingRaf = 0;
  function scheduleRender() {
    if (pendingRaf) return;
    pendingRaf = requestAnimationFrame(() => {
      pendingRaf = 0;
      render(raw, true);
      if (stickyScroll) logEl.scrollTop = logEl.scrollHeight;
    });
  }

  // Build outgoing message list: optional system role + full history,
  // augmented with retrieved context if a RAG collection is selected.
  const outgoing = await buildOutgoingMessages();

  setBusy(true);
  abortCtl = new AbortController();

  try {
    const res = await fetch(`http://127.0.0.1:${sidecar.port}/chat`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "authorization": `Bearer ${sidecar.token}`,
      },
      body: JSON.stringify({
        model_path: modelPath,
        messages: outgoing,
        params: getCurrentParams(),
        // Multimodal: when an mmproj is pinned, the sidecar routes
        // image-bearing user messages through ImageAnalyzer instead
        // of llm.chat(). Empty string is the same as omitting -- the
        // sidecar guards on truthiness.
        mmproj_path: getMmprojPath(),
      }),
      signal: abortCtl.signal,
    });
    if (!res.ok || !res.body) {
      throw new Error(`HTTP ${res.status}: ${await res.text()}`);
    }

    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          const payload = line.slice(6);
          if (payload === "[DONE]") continue;
          let parsed;
          try { parsed = JSON.parse(payload); }
          catch { continue; }
          if (parsed.error) { errorLine(parsed.error); return; }
          if (typeof parsed.text === "string") {
            raw += parsed.text;
            bumpTokens(parsed.text);
            scheduleRender();
          }
        }
      }
    }
  } catch (err) {
    if (err.name === "AbortError") {
      aborted = true;
    } else {
      errorLine(err.message || String(err));
    }
  } finally {
    // Cancel any rAF queued mid-stream that hasn't fired yet -- if it
    // ran after the final render below, it would re-add the streaming
    // cursor to ``tailEl`` with nothing left to clean it up, leaving a
    // stray blue caret under each completed assistant turn.
    if (pendingRaf) {
      cancelAnimationFrame(pendingRaf);
      pendingRaf = 0;
    }
    // Final visual render: append "(stopped)" marker only if aborted,
    // but keep the marker out of the persisted history so the model
    // doesn't see its own UI marker on the next turn.
    render(raw + (aborted ? "\n\n_(stopped)_" : ""), false);
    if (raw) {
      messages.push({ role: "assistant", content: raw });
      // Stash raw markdown on the exchange so the copy action yields
      // the original content rather than the rendered HTML.
      exchange.wrap.dataset.asstRaw = raw;
      // Persist the chat now that we have at least one complete turn.
      // First save assigns an id; subsequent saves bump updatedAt.
      persistActiveChat();
    } else {
      // Empty assistant turn (immediate abort or error before any
      // tokens) -- drop the matching user turn to keep history balanced.
      const last = messages[messages.length - 1];
      if (last && last.role === "user") messages.pop();
    }
    setBusy(false);
    abortCtl = null;
    promptEl.focus();
  }
}

init();
