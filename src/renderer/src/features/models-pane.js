// Full-area Models pane (LMStudio-style). Three columns:
//
//   #modelsPaneSubnav  -- kind filter (View All / Chat / Multimodal /
//                         Whisper / SD / Embedding / Unknown). Each
//                         entry shows the matching count.
//   #modelsPaneMain    -- header (filter input + add controls), then
//                         a table of cached models, then a footer
//                         (count + disk usage + models dir).
//   #modelsPaneDetail  -- selected model: Use in Chat + Set as mmproj
//                         actions, then structured Info from
//                         /models/inspect, then Source File reveal.
//
// All the management surfaces that used to live in the right-tab
// "Models" pane (cached list, drag-drop import, HF download, GGUF
// metadata inspector, multimodal projector pin, quantize tool) live
// here now. The right tab is per-chat parameters only.

import {
  listModels, inspectModel, importModel, hfPeek, hfDownload,
} from "../lib/models.js";
import { startJob } from "../lib/jobs.js";
import { sidecarFetch, getInfo } from "../lib/sidecar.js";

let onPickModel = (path) => {};
let revealInFolder = (path) => {};

const KIND_DEFS = [
  { key: "all",       label: "View All",     match: (m) => true },
  { key: "chat",      label: "Chat",         match: (m) => m.kind === "chat" },
  { key: "mmproj",    label: "Multimodal",   match: (m) => m.kind === "mmproj" },
  { key: "whisper",   label: "Whisper",      match: (m) => m.kind === "whisper" },
  { key: "sd",        label: "Stable Diffusion", match: (m) => m.kind === "sd" },
  { key: "embedding", label: "Embedding",    match: (m) => m.kind === "embedding" },
  { key: "unknown",   label: "Unknown",      match: (m) => m.kind === "unknown" },
];

const MMPROJ_PATH_KEY = "mmproj_path";
const LAST_MODEL_KEY  = "last_model_path";

const state = {
  models: [],
  modelsDir: "",
  modelsExtra: [],
  filterKind: "all",
  filterText: "",
  selected: null,
  metaCache: new Map(),
  quantizeAvailable: false,
  quantizeFtypes: {},
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function fmtBytes(n) {
  if (!Number.isFinite(n)) return "-";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0; let v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 ? 0 : 1)} ${u[i]}`;
}

function basename(p) { return p ? p.split(/[\\/]/).pop() : ""; }

function filteredModels() {
  const def = KIND_DEFS.find((k) => k.key === state.filterKind) || KIND_DEFS[0];
  const q = (state.filterText || "").trim().toLowerCase();
  return state.models.filter((m) => {
    if (!def.match(m)) return false;
    if (!q) return true;
    return m.name.toLowerCase().includes(q) || (m.path || "").toLowerCase().includes(q);
  });
}

function totalSize() {
  return state.models.reduce((s, m) => s + (Number(m.size) || 0), 0);
}

// ---- Subnav -------------------------------------------------------------

function renderSubnav() {
  const host = document.getElementById("modelsPaneSubnav");
  if (!host) return;
  host.replaceChildren();
  host.appendChild(el("div", { class: "mp-subnav-head" }, "My Models"));
  for (const def of KIND_DEFS) {
    const count = state.models.filter(def.match).length;
    if (def.key !== "all" && count === 0) continue; // hide empty buckets
    const row = el("button", {
      type: "button",
      class: `mp-subnav-row${state.filterKind === def.key ? " active" : ""}`,
      onclick: () => { state.filterKind = def.key; render(); },
    },
      el("span", { class: "mp-subnav-label" }, def.label),
      el("span", { class: "mp-subnav-count" }, String(count)),
    );
    host.appendChild(row);
  }
}

// ---- Main: filter bar + table + footer -----------------------------------

function renderMain() {
  const host = document.getElementById("modelsPaneMain");
  if (!host) return;
  host.replaceChildren();

  // Header: filter input + Add Model dropdown + HF download row.
  const filterInput = el("input", {
    type: "search",
    class: "mp-filter",
    placeholder: "Filter models...",
    value: state.filterText,
  });
  filterInput.addEventListener("input", () => {
    state.filterText = filterInput.value;
    renderTable();
  });
  const headerRight = el("div", { class: "mp-header-actions" },
    addModelBlock(),
  );
  const header = el("header", { class: "mp-header" },
    el("h1", {}, "My Models"),
    filterInput,
    headerRight,
  );

  // Table.
  const table = el("div", { class: "mp-table" });
  table.id = "mpTable";

  // Footer.
  const footer = el("div", { class: "mp-footer" },
    el("span", { class: "mp-footer-count" },
      `${state.models.length} model${state.models.length === 1 ? "" : "s"} · ${fmtBytes(totalSize())}`),
    state.modelsDir
      ? el("button", {
          type: "button", class: "btn-mini mp-footer-path mono",
          title: "Reveal models dir",
          onclick: () => revealInFolder(state.modelsDir),
        }, state.modelsDir)
      : null,
  );

  host.appendChild(header);
  host.appendChild(table);
  host.appendChild(footer);

  renderTable();
}

function addModelBlock() {
  const input = el("input", {
    type: "text",
    class: "mp-hf-input",
    placeholder: "HF URL or user/repo:file.gguf",
  });
  const peekText = el("span", { class: "mp-hf-status" }, "");
  const dlBtn = el("button", { type: "button", class: "btn primary" }, "Download");
  const progress = el("div", { class: "mp-hf-progress", hidden: true },
    el("div", { class: "mp-hf-bar" }, el("div", { class: "mp-hf-bar-fill" })),
    el("span", { class: "mp-hf-bar-text" }, ""),
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
        else if (!r.exists) peekText.textContent = "(not found)";
        else if (r.already_local) peekText.textContent = `${fmtBytes(r.size)} · already downloaded`;
        else peekText.textContent = `${fmtBytes(r.size)}`;
      } catch (e) { peekText.textContent = `peek failed: ${e.message}`; }
    }, 500);
  });

  dlBtn.addEventListener("click", async () => {
    const body = bodyFromInput();
    if (!body) { peekText.textContent = "Enter a URL or user/repo:file"; return; }
    dlBtn.disabled = true;
    progress.hidden = false;
    const fill = progress.querySelector(".mp-hf-bar-fill");
    const text = progress.querySelector(".mp-hf-bar-text");
    text.textContent = "starting...";
    fill.style.width = "0%";

    let job;
    try { job = await hfDownload(body); }
    catch (e) { text.textContent = `failed: ${e.message}`; dlBtn.disabled = false; return; }

    job.onEvent((ev) => {
      if (ev.type === "progress") {
        const v = typeof ev.value === "number" ? ev.value : 0;
        fill.style.width = `${Math.min(100, Math.max(0, v * 100))}%`;
        text.textContent = ev.total
          ? `${fmtBytes(ev.downloaded)} / ${fmtBytes(ev.total)}`
          : `${fmtBytes(ev.downloaded)} / ?`;
      } else if (ev.type === "error") { text.textContent = `failed: ${ev.message}`; }
      else if (ev.type === "result") { text.textContent = `done · ${ev.result.name}`; fill.style.width = "100%"; }
      else if (ev.type === "done") {
        dlBtn.disabled = false;
        refresh();
        setTimeout(() => { progress.hidden = true; }, 1500);
      }
    });
  });

  return el("div", { class: "mp-add" },
    el("div", { class: "mp-hf-row" }, input, dlBtn),
    peekText,
    progress,
  );
}

function renderTable() {
  const host = document.getElementById("mpTable");
  if (!host) return;
  host.replaceChildren();
  const items = filteredModels();
  if (items.length === 0) {
    host.appendChild(el("div", { class: "mp-empty" },
      state.models.length === 0
        ? "No models. Drag a .gguf onto this pane or paste an HF URL above."
        : "No models match the current filter."));
    return;
  }

  // Header row.
  host.appendChild(el("div", { class: "mp-row mp-row-head" },
    el("div", { class: "mp-col-kind" },   "Kind"),
    el("div", { class: "mp-col-name" },   "Name"),
    el("div", { class: "mp-col-size" },   "Size"),
    el("div", { class: "mp-col-source" }, "Source"),
  ));

  for (const m of items) {
    const isSel = state.selected && state.selected.path === m.path;
    const row = el("button", {
      type: "button",
      class: `mp-row${isSel ? " active" : ""}`,
      onclick: () => selectModel(m),
    },
      el("div", { class: "mp-col-kind" },
        el("span", { class: `mp-kind-chip mp-kind-${m.kind || "unknown"}` }, m.kind || "?")),
      el("div", { class: "mp-col-name", title: m.path }, m.name),
      el("div", { class: "mp-col-size" }, fmtBytes(m.size)),
      el("div", { class: "mp-col-source" }, m.source),
    );
    host.appendChild(row);
  }
}

// ---- Detail rail ---------------------------------------------------------

async function selectModel(m) {
  state.selected = m;
  await ensureMetadata(m);
  render();
}

async function ensureMetadata(m) {
  if (state.metaCache.has(m.path)) return;
  state.metaCache.set(m.path, { loading: true });
  try {
    const r = await inspectModel(m.path);
    state.metaCache.set(m.path, r);
  } catch (e) {
    state.metaCache.set(m.path, { error: e.message });
  }
}

function metaKv(label, value) {
  return el("div", { class: "mp-meta-row" },
    el("div", { class: "mp-meta-k" }, label),
    el("div", { class: "mp-meta-v" }, value || "-"),
  );
}

function pickInfoFields(meta) {
  // Surface the most useful fields LMStudio-style. cyllama exposes
  // GGUF metadata as an opaque dict; we cherry-pick known keys and
  // best-guess the rest. Missing keys fall back to "-".
  const md = (meta && meta.metadata) || {};
  const arch = md["general.architecture"] || "-";
  const ftype = md["general.file_type"] != null
    ? `ftype ${md["general.file_type"]}` : "-";
  const params = md["general.parameter_count"] != null
    ? `${md["general.parameter_count"]}` : "-";
  const ctx = (md[`${arch}.context_length`] != null)
    ? `${md[`${arch}.context_length`]}` : "-";
  const name = md["general.name"] || "-";
  return { arch, ftype, params, ctx, name };
}

function renderDetail() {
  const host = document.getElementById("modelsPaneDetail");
  if (!host) return;
  host.replaceChildren();
  const m = state.selected;
  if (!m) {
    host.appendChild(el("div", { class: "mp-detail-empty" },
      "Select a model to see its information."));
    return;
  }

  // Header: name + small actions
  host.appendChild(el("header", { class: "mp-detail-head" },
    el("h2", { class: "mp-detail-name" }, m.name),
  ));

  // Action buttons
  const actions = el("div", { class: "mp-detail-actions" });
  const useBtn = el("button", {
    type: "button", class: "btn primary",
    onclick: () => { onPickModel(m.path); systemHint("loaded in chat"); },
  }, "Use in Chat");
  actions.appendChild(useBtn);
  if (m.kind === "mmproj" || m.kind === "unknown") {
    const mmprojBtn = el("button", {
      type: "button", class: "btn",
      title: "Pin as the multimodal projector for chat",
      onclick: () => {
        try { localStorage.setItem(MMPROJ_PATH_KEY, m.path); } catch {}
        try { window.dispatchEvent(new CustomEvent("mmproj:changed", { detail: m.path })); } catch {}
        systemHint("pinned as mmproj");
      },
    }, "Set as mmproj");
    actions.appendChild(mmprojBtn);
  }
  host.appendChild(actions);
  host.appendChild(el("div", { id: "mp-detail-hint", class: "mp-detail-hint" }));

  // Model Information KV table
  host.appendChild(el("h3", { class: "mp-detail-section" }, "Model Information"));
  const meta = state.metaCache.get(m.path);
  const fields = meta && !meta.loading && !meta.error ? pickInfoFields(meta) : null;
  const kv = el("div", { class: "mp-meta" },
    metaKv("Kind",   m.kind || "-"),
    metaKv("Source", m.source || "-"),
    metaKv("Size",   fmtBytes(m.size)),
    fields ? metaKv("Arch",     fields.arch)   : null,
    fields ? metaKv("Quantization", fields.ftype) : null,
    fields ? metaKv("Parameters",   fields.params): null,
    fields ? metaKv("Context",      fields.ctx)   : null,
    fields ? metaKv("Name",         fields.name)  : null,
  );
  if (meta?.loading) kv.appendChild(metaKv("...", "loading"));
  if (meta?.error)   kv.appendChild(metaKv("Error", meta.error));
  host.appendChild(kv);

  // Source File
  host.appendChild(el("h3", { class: "mp-detail-section" }, "Source File"));
  host.appendChild(el("div", { class: "mp-meta-row" },
    el("div", { class: "mp-meta-v mono mp-source-path" }, m.path),
  ));
  host.appendChild(el("div", { class: "mp-detail-actions" },
    el("button", {
      type: "button", class: "btn",
      onclick: () => revealInFolder(m.path),
    }, "Reveal"),
  ));
}

function systemHint(text) {
  const node = document.getElementById("mp-detail-hint");
  if (!node) return;
  node.textContent = text;
  node.dataset.kind = "ok";
  setTimeout(() => { if (node.textContent === text) { node.textContent = ""; node.dataset.kind = ""; } }, 1800);
}

// ---- Wiring --------------------------------------------------------------

function render() {
  renderSubnav();
  renderMain();
  renderDetail();
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
  render();
}

function bindDragDrop() {
  // Drop a .gguf anywhere on the pane to import it. Same flow as the
  // old right-tab dropzone; just bound to the pane root now.
  const pane = document.getElementById("modelsPane");
  if (!pane) return;
  pane.addEventListener("dragover", (e) => { e.preventDefault(); pane.classList.add("dragging"); });
  pane.addEventListener("dragleave", (e) => { if (e.target === pane) pane.classList.remove("dragging"); });
  pane.addEventListener("drop", async (ev) => {
    ev.preventDefault();
    pane.classList.remove("dragging");
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
  });
}

let mounted = false;
export function mount({ onPick, reveal } = {}) {
  if (typeof onPick === "function") onPickModel = onPick;
  if (typeof reveal === "function") revealInFolder = reveal;
  if (mounted) return;
  mounted = true;
  bindDragDrop();
  refresh();
}

export async function show() {
  // Re-fetch on view so a freshly-downloaded / quantized / dropped
  // file shows up without manual reload.
  await refresh();
}

export function hide() {}

export { refresh };
