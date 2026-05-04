// Models tab content for the right sidebar.
//
// Sidebar-width compression of what was the Models workspace. Vertical
// stack: cached list, HF download, drop hint, selected metadata
// (collapsible). Sits inside #modelsTabHost; the system-prompt and
// sampling sections are static HTML in index.html so the existing chat
// code can keep using their fixed IDs.

import { listModels, inspectModel, importModel, hfPeek, hfDownload } from "../lib/models.js";

let onPickModel = (path) => {};
let revealInFolder = (path) => {};

const state = {
  models: [],
  modelsDir: "",
  selected: null,
  metaCache: new Map(),
  metaOpen: false,
};

function fmtBytes(n) {
  if (!Number.isFinite(n)) return "-";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${u[i]}`;
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else {
      node.setAttribute(k, v === true ? "" : String(v));
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function renderRow(m) {
  const row = el("button", {
    type: "button",
    class: "mt-row" + (state.selected && state.selected.path === m.path ? " active" : ""),
    title: m.path,
    onclick: () => selectModel(m),
    ondblclick: () => onPickModel(m.path),
  },
    el("span", { class: "mt-row-name" }, m.name),
    el("span", { class: "mt-row-meta" }, `${fmtBytes(m.size)} · ${m.source}`),
  );
  return row;
}

async function selectModel(m) {
  state.selected = m;
  state.metaOpen = false;
  redraw();
}

async function ensureMetadata(m) {
  if (state.metaCache.has(m.path)) return;
  state.metaCache.set(m.path, { loading: true });
  redraw();
  try {
    const r = await inspectModel(m.path);
    state.metaCache.set(m.path, r);
  } catch (e) {
    state.metaCache.set(m.path, { error: e.message });
  }
  redraw();
}

async function refresh() {
  try {
    const r = await listModels();
    state.models = r.models || [];
    state.modelsDir = r.models_dir || "";
  } catch (e) {
    state.models = [];
    state.modelsDir = `(error: ${e.message})`;
  }
  if (state.selected && !state.models.find((m) => m.path === state.selected.path)) {
    state.selected = null;
  }
  redraw();
}

function renderHfRow() {
  const input = el("input", {
    type: "text",
    class: "mt-hf-input",
    placeholder: "HF URL or user/repo:file.gguf",
  });
  const peekText = el("div", { class: "mt-hf-status" }, "");
  const dlBtn = el("button", { type: "button", class: "btn primary mt-hf-btn" }, "Download");
  const progress = el("div", { class: "mt-hf-progress", hidden: true },
    el("div", { class: "mt-hf-bar" }, el("div", { class: "mt-hf-bar-fill" })),
    el("span", { class: "mt-hf-bar-text" }, ""),
  );

  function bodyFromInput() {
    const v = input.value.trim();
    if (!v) return null;
    if (/^https?:\/\//i.test(v)) return { url: v };
    if (v.includes(":")) return { target: v };
    return null;
  }

  let peekTimer = null;
  input.addEventListener("input", () => {
    peekText.textContent = "";
    clearTimeout(peekTimer);
    peekTimer = setTimeout(async () => {
      const body = bodyFromInput();
      if (!body) return;
      try {
        const r = await hfPeek(body);
        if (r.error) peekText.textContent = `peek failed: ${r.error}`;
        else if (!r.exists) peekText.textContent = "(not found on hub)";
        else if (r.already_local) peekText.textContent = `${fmtBytes(r.size)} · already downloaded`;
        else peekText.textContent = `${fmtBytes(r.size)}`;
      } catch (e) {
        peekText.textContent = `peek failed: ${e.message}`;
      }
    }, 500);
  });

  dlBtn.addEventListener("click", async () => {
    const body = bodyFromInput();
    if (!body) {
      peekText.textContent = "Enter a URL or user/repo:file";
      return;
    }
    dlBtn.disabled = true;
    progress.hidden = false;
    const fill = progress.querySelector(".mt-hf-bar-fill");
    const text = progress.querySelector(".mt-hf-bar-text");
    text.textContent = "starting...";
    fill.style.width = "0%";

    let job;
    try { job = await hfDownload(body); }
    catch (e) {
      text.textContent = `failed: ${e.message}`;
      dlBtn.disabled = false;
      return;
    }

    job.onEvent((ev) => {
      if (ev.type === "progress") {
        const v = typeof ev.value === "number" ? ev.value : 0;
        fill.style.width = `${Math.min(100, Math.max(0, v * 100))}%`;
        if (ev.total) text.textContent = `${fmtBytes(ev.downloaded)} / ${fmtBytes(ev.total)}`;
        else text.textContent = `${fmtBytes(ev.downloaded)} / ?`;
      } else if (ev.type === "error") {
        text.textContent = `failed: ${ev.message}`;
      } else if (ev.type === "result") {
        text.textContent = `done · ${ev.result.name}`;
        fill.style.width = "100%";
      } else if (ev.type === "done") {
        dlBtn.disabled = false;
        refresh();
        setTimeout(() => { progress.hidden = true; }, 1500);
      }
    });
  });

  return el("div", { class: "mt-hf" },
    el("div", { class: "mt-hf-row" }, input, dlBtn),
    peekText,
    progress,
  );
}

function renderDropZone() {
  return el("div", { class: "mt-drop", "data-dropzone": "1" },
    el("div", { class: "mt-drop-title" }, "Drag .gguf files here"),
    el("div", { class: "mt-drop-sub mono" }, state.modelsDir || ""),
  );
}

function renderSelected() {
  if (!state.selected) {
    return el("div", { class: "mt-meta-empty" }, "Select a model above to inspect");
  }
  const m = state.selected;
  const meta = state.metaCache.get(m.path);

  const useBtn = el("button", {
    type: "button",
    class: "btn primary",
    onclick: () => onPickModel(m.path),
  }, "Use in Chat");

  const revealBtn = el("button", {
    type: "button",
    class: "btn",
    onclick: () => revealInFolder(m.path),
  }, "Reveal");

  const toggleBtn = el("button", {
    type: "button",
    class: "btn mt-meta-toggle",
    onclick: async () => {
      state.metaOpen = !state.metaOpen;
      if (state.metaOpen) await ensureMetadata(m);
      else redraw();
    },
  }, state.metaOpen ? "Hide metadata" : "Show metadata");

  let metaBlock = null;
  if (state.metaOpen) {
    if (!meta || meta.loading) metaBlock = el("div", { class: "mt-meta-empty" }, "Loading...");
    else if (meta.error) metaBlock = el("div", { class: "mt-meta-error" }, `Inspect failed: ${meta.error}`);
    else if (!meta.metadata) metaBlock = el("div", { class: "mt-meta-empty" }, "No metadata.");
    else {
      const rows = [];
      const entries = Object.entries(meta.metadata);
      entries.sort(([a], [b]) => a.localeCompare(b));
      for (const [k, v] of entries) {
        let display;
        if (Array.isArray(v)) display = `[${v.length} items]`;
        else if (typeof v === "object" && v !== null) display = JSON.stringify(v);
        else display = String(v);
        if (display.length > 200) display = display.slice(0, 200) + "...";
        rows.push(el("div", { class: "mt-kv" },
          el("span", { class: "k" }, k),
          el("span", { class: "v mono" }, display),
        ));
      }
      metaBlock = el("div", { class: "mt-meta-list" }, ...rows);
    }
  }

  return el("div", { class: "mt-selected" },
    el("div", { class: "mt-selected-name" }, m.name),
    el("div", { class: "mt-selected-path mono" }, m.path),
    el("div", { class: "mt-selected-actions" }, useBtn, revealBtn, toggleBtn),
    metaBlock,
  );
}

function redraw() {
  const host = document.getElementById("modelsTabHost");
  if (!host) return;
  while (host.firstChild) host.removeChild(host.firstChild);

  const head = el("div", { class: "rt-section-head" },
    el("h3", {}, "Cached models"),
    el("button", {
      type: "button",
      class: "icon-btn",
      title: "Refresh",
      onclick: refresh,
    }, el("span", {}, "↻")),
  );

  const list = state.models.length === 0
    ? el("div", { class: "mt-empty" }, "No models yet. Drop a .gguf below or paste an HF URL.")
    : el("div", { class: "mt-rows" }, ...state.models.map(renderRow));

  const addHead = el("div", { class: "rt-section-head" }, el("h3", {}, "Add a model"));

  host.appendChild(el("div", { class: "rt-section" }, head, list, renderSelected()));
  host.appendChild(el("div", { class: "rt-section" }, addHead, renderHfRow(), renderDropZone()));
}

async function handleDrop(ev) {
  ev.preventDefault();
  const host = document.getElementById("modelsTabHost");
  if (host) host.classList.remove("dragging");
  const files = Array.from(ev.dataTransfer?.files || []);
  if (!files.length) return;
  const errors = [];
  for (const f of files) {
    if (!f.path) { errors.push(`${f.name}: no path`); continue; }
    if (!f.path.toLowerCase().endsWith(".gguf")) { errors.push(`${f.name}: not .gguf`); continue; }
    try { await importModel(f.path); }
    catch (e) { errors.push(`${f.name}: ${e.message}`); }
  }
  if (errors.length) console.warn("import errors:", errors);
  refresh();
}

export function mount({ onPick, reveal } = {}) {
  if (typeof onPick === "function") onPickModel = onPick;
  if (typeof reveal === "function") revealInFolder = reveal;
  const host = document.getElementById("modelsTabHost");
  if (!host) return;
  host.addEventListener("dragover", (e) => { e.preventDefault(); host.classList.add("dragging"); });
  host.addEventListener("dragleave", (e) => { if (e.target === host) host.classList.remove("dragging"); });
  host.addEventListener("drop", handleDrop);
  redraw();
  refresh();
}

export { refresh };
