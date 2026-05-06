// Models tab content for the right sidebar.
//
// Sidebar-width compression of what was the Models workspace. Vertical
// stack: cached list, HF download, drop hint, selected metadata
// (collapsible). Sits inside #modelsTabHost; the system-prompt and
// sampling sections are static HTML in index.html so the existing chat
// code can keep using their fixed IDs.

import { listModels, inspectModel, importModel, hfPeek, hfDownload } from "../lib/models.js";
import { startJob } from "../lib/jobs.js";
import { sidecarFetch, getInfo } from "../lib/sidecar.js";

let onPickModel = (path) => {};
let revealInFolder = (path) => {};

const state = {
  models: [],
  modelsDir: "",
  selected: null,
  metaCache: new Map(),
  metaOpen: false,
  quantizeAvailable: false,
  quantizeFtypes: {},
  quantize: {
    src: "",
    ftype: "Q4_K_M",
    dst: "",
    job: null,
    status: "",
  },
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

function renderDropdown() {
  const select = el("select", { class: "mt-select" });
  if (state.models.length === 0) {
    select.appendChild(el("option", { value: "", disabled: true, selected: true }, "No models — drop a .gguf or paste an HF URL"));
    select.disabled = true;
  } else {
    if (!state.selected) {
      select.appendChild(el("option", { value: "", disabled: true, selected: true }, "Select a model..."));
    }
    for (const m of state.models) {
      // The catalog itself doesn't filter; surface the inferred kind
      // alongside size + source so users can see at a glance which
      // pickers a given file is eligible for.
      const kindLabel = m.kind && m.kind !== "unknown" ? `  ·  ${m.kind}` : "";
      const opt = el("option", { value: m.path, title: m.path },
        `${m.name}  ·  ${fmtBytes(m.size)}  ·  ${m.source}${kindLabel}`);
      if (state.selected && state.selected.path === m.path) opt.selected = true;
      select.appendChild(opt);
    }
  }
  select.addEventListener("change", () => {
    const m = state.models.find((x) => x.path === select.value);
    if (m) selectModel(m);
  });
  return select;
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
      redraw();
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

  const picker = el("div", { class: "mt-picker" },
    renderDropdown(),
    el("button", {
      type: "button",
      class: "icon-btn mt-refresh",
      title: "Refresh",
      onclick: refresh,
    }, el("span", {}, "↻")),
  );

  const addHead = el("div", { class: "rt-section-head" }, el("h3", {}, "Add a model"));
  const dropHint = el("div", { class: "mt-drop-hint" },
    "Drop ", el("span", { class: "mono" }, ".gguf"), " files anywhere on this tab",
    state.modelsDir ? el("div", { class: "mt-drop-sub mono" }, state.modelsDir) : null,
  );

  host.appendChild(el("div", { class: "rt-section" }, picker, renderSelected()));
  host.appendChild(el("div", { class: "rt-section" }, addHead, renderHfRow(), dropHint));

  if (state.quantizeAvailable) {
    host.appendChild(renderQuantizeSection());
  }

  host.appendChild(renderMultimodalSection());
}

// --- Multimodal projector pin ---------------------------------------------
//
// LLAVA / MTMD chat needs both the main GGUF model and a separate
// mmproj-only GGUF (the vision projector). The user picks an mmproj
// once; we persist the path in localStorage and the chat composer's
// paperclip button gates on it being set.
const MMPROJ_PATH_KEY = "mmproj_path";

function getMmprojPath() {
  try { return localStorage.getItem(MMPROJ_PATH_KEY) || ""; } catch { return ""; }
}
function setMmprojPath(v) {
  try {
    if (v) localStorage.setItem(MMPROJ_PATH_KEY, v);
    else localStorage.removeItem(MMPROJ_PATH_KEY);
  } catch {}
  // The chat composer's paperclip listens on this so it can reveal /
  // hide as soon as the user pins or clears a projector.
  try { window.dispatchEvent(new CustomEvent("mmproj:changed", { detail: v })); } catch {}
}

function renderMultimodalSection() {
  const head = el("div", { class: "rt-section-head" }, el("h3", {}, "Multimodal"));
  const subhead = el("div", { class: "mt-subhead" }, "Vision projector (mmproj)");
  const current = el("div", { class: "mt-mmproj-path mono" },
    getMmprojPath() ? basenameLike(getMmprojPath()) : "(not set)");

  // Catalog-driven dropdown of mmproj-classified models. ``unknown``
  // models pass through (heuristic safety) so a misclassified mmproj
  // is still pickable. Browse... remains the escape hatch for files
  // outside MODELS_DIR.
  const mmprojModels = state.models.filter(
    (m) => m.kind === "mmproj" || m.kind === "unknown",
  );
  const sel = el("select", { class: "mt-select" });
  sel.appendChild(el("option", { value: "" }, "(not set)"));
  for (const m of mmprojModels) {
    const opt = el("option", { value: m.path, title: m.path },
      `${m.name} · ${fmtBytes(m.size)}`);
    if (m.path === getMmprojPath()) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.appendChild(el("option", { value: "__browse__" }, "Browse..."));
  sel.addEventListener("change", async () => {
    if (sel.value === "__browse__") {
      const p = await window.cyllama.pickModel();
      if (p) {
        setMmprojPath(p);
        current.textContent = basenameLike(p);
      }
      sel.value = getMmprojPath() || "";
    } else {
      setMmprojPath(sel.value);
      current.textContent = sel.value ? basenameLike(sel.value) : "(not set)";
    }
  });

  return el("div", { class: "rt-section" },
    head, subhead,
    sel,
    current,
    el("div", { class: "mt-mmproj-hint" },
      "Pinned mmproj enables the paperclip in the chat composer. Pair it ",
      "with a vision-capable main model (LLaVA, Qwen-VL, ...) for image Q&A. ",
      "The dropdown shows files classified as mmproj; use Browse... for ",
      "files outside the models directory."),
  );
}

function basenameLike(p) { return p ? p.split(/[\\/]/).pop() : ""; }

// --- Quantize tool --------------------------------------------------------
//
// Lives as a section inside the Models tab so the source model picker can
// reuse the cached models list rather than duplicating its own. Output
// always lands in MODELS_DIR; the sidecar enforces that.
function renderQuantizeSection() {
  const head = el("div", { class: "rt-section-head" }, el("h3", {}, "Tools"));
  const subhead = el("div", { class: "mt-subhead" }, "Quantize");

  const srcSel = el("select", { class: "mt-select" });
  srcSel.appendChild(el("option", { value: "" }, "Pick source model..."));
  // Quantize is a GGUF tensor-conversion pass; only .gguf files
  // (chat / mmproj / embedding) are valid sources. Whisper .bin and
  // SD .safetensors aren't quantizable through this path.
  for (const m of state.models) {
    if (m.kind === "whisper" || m.kind === "sd") continue;
    if (!m.path.toLowerCase().endsWith(".gguf")) continue;
    srcSel.appendChild(el("option", { value: m.path }, `${m.name} · ${fmtBytes(m.size)}`));
  }
  srcSel.value = state.quantize.src || "";
  srcSel.addEventListener("change", () => {
    state.quantize.src = srcSel.value;
    // Auto-fill the dst name based on the source basename + ftype.
    if (state.quantize.src && !state.quantize.dst) {
      const base = state.quantize.src.split(/[\\/]/).pop() || "model.gguf";
      const stem = base.replace(/\.gguf$/i, "");
      state.quantize.dst = `${stem}.${state.quantize.ftype}.gguf`;
      const dstInput = document.getElementById("mt-quantize-dst");
      if (dstInput) dstInput.value = state.quantize.dst;
    }
    updateQuantizeRunEnabled();
  });

  const ftypeSel = el("select", { class: "mt-select" });
  for (const label of Object.keys(state.quantizeFtypes)) {
    ftypeSel.appendChild(el("option", { value: label }, label));
  }
  if (state.quantizeFtypes[state.quantize.ftype]) ftypeSel.value = state.quantize.ftype;
  ftypeSel.addEventListener("change", () => {
    state.quantize.ftype = ftypeSel.value;
    // Update auto-suggested dst extension.
    if (state.quantize.src) {
      const base = state.quantize.src.split(/[\\/]/).pop() || "model.gguf";
      const stem = base.replace(/\.gguf$/i, "");
      state.quantize.dst = `${stem}.${state.quantize.ftype}.gguf`;
      const dstInput = document.getElementById("mt-quantize-dst");
      if (dstInput) dstInput.value = state.quantize.dst;
    }
  });

  const dstInput = el("input", {
    id: "mt-quantize-dst", type: "text", class: "mt-input",
    placeholder: "output-name.gguf",
    value: state.quantize.dst,
  });
  dstInput.addEventListener("input", () => {
    state.quantize.dst = dstInput.value.trim();
    updateQuantizeRunEnabled();
  });

  const runBtn = el("button", {
    type: "button", id: "mt-quantize-run", class: "btn primary",
    disabled: true, onclick: runQuantize,
  }, "Quantize");
  const stopBtn = el("button", {
    type: "button", id: "mt-quantize-stop", class: "btn", hidden: true,
    onclick: () => { if (state.quantize.job) state.quantize.job.cancel(); },
  }, "Stop");

  return el("div", { class: "rt-section" },
    head, subhead,
    el("div", { class: "mt-quant-row" },
      el("label", { class: "mt-quant-label" }, "Source"), srcSel),
    el("div", { class: "mt-quant-row" },
      el("label", { class: "mt-quant-label" }, "Type"), ftypeSel),
    el("div", { class: "mt-quant-row" },
      el("label", { class: "mt-quant-label" }, "Output"), dstInput),
    el("div", { class: "mt-quant-actions" }, runBtn, stopBtn),
    el("div", { id: "mt-quantize-status", class: "mt-quant-status" }, state.quantize.status),
  );
}

function updateQuantizeRunEnabled() {
  const btn = document.getElementById("mt-quantize-run");
  if (!btn) return;
  btn.disabled = !!state.quantize.job
    || !state.quantize.src
    || !state.quantize.dst.trim();
}

function setQuantizeStatus(s, kind = "info") {
  state.quantize.status = s;
  const node = document.getElementById("mt-quantize-status");
  if (node) { node.textContent = s; node.dataset.kind = kind; }
}

async function runQuantize() {
  if (state.quantize.job) return;
  setQuantizeStatus("starting...");
  let job;
  try {
    job = await startJob("models.quantize", {
      src_path: state.quantize.src,
      dst_name: state.quantize.dst,
      ftype: state.quantize.ftype,
    });
  } catch (e) {
    setQuantizeStatus(`failed: ${e.message}`, "err");
    return;
  }
  state.quantize.job = job;
  const r = document.getElementById("mt-quantize-run");
  const s = document.getElementById("mt-quantize-stop");
  if (r) r.hidden = true;
  if (s) s.hidden = false;
  updateQuantizeRunEnabled();

  const off = job.onEvent((ev) => {
    if (ev.type === "log" && ev.message) setQuantizeStatus(ev.message);
    else if (ev.type === "error") setQuantizeStatus(`error: ${ev.message}`, "err");
  });
  try {
    const result = await job.done;
    setQuantizeStatus(`done · ${result?.name} (${fmtBytes(result?.size)})`, "ok");
    // Refresh the cached models list so the new file shows up in the
    // picker. Other panes that listen to this event get the same update.
    refresh();
    try { window.dispatchEvent(new CustomEvent("models:cache-changed")); } catch {}
  } catch (e) {
    setQuantizeStatus(`failed: ${e.message}`, "err");
  } finally {
    off();
    state.quantize.job = null;
    if (r) r.hidden = false;
    if (s) s.hidden = true;
    updateQuantizeRunEnabled();
  }
}

async function loadQuantizeMeta() {
  try {
    const info = await getInfo();
    state.quantizeAvailable = !!(info && info.features && info.features.quantize);
    if (state.quantizeAvailable) {
      try {
        const r = await sidecarFetch("/quantize/ftypes");
        if (r.ok) state.quantizeFtypes = (await r.json()).ftypes || {};
      } catch {}
    }
  } catch {
    state.quantizeAvailable = false;
  }
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
  // Quantize availability is async; redraw once it lands so the Tools
  // section can appear without a manual reload.
  loadQuantizeMeta().then(() => redraw());
}

export { refresh };
