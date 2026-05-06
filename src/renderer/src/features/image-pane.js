// Image sidebar view (Phase 6, txt2img slice). Pick an SD model, type a
// prompt, generate. The PNG result is fetched from the sidecar with
// bearer auth into a blob URL so it can satisfy the CSP without us
// having to widen ``img-src`` to allow loopback HTTP.

import { startJob } from "../lib/jobs.js";
import { listModels } from "../lib/models.js";
import { sidecarFetch } from "../lib/sidecar.js";

const state = {
  modelPath: "",
  prompt: "",
  negative: "",
  width: 512,
  height: 512,
  steps: 20,
  cfg: 7.0,
  seed: -1,
  job: null,
  // Track the last generated blob URL so we can revoke() it before
  // overwriting -- otherwise we leak an image-sized buffer per gen.
  lastBlobUrl: null,
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

function setStatus(s, kind = "info") {
  const node = document.getElementById("img-status");
  if (node) {
    node.textContent = s || "";
    node.dataset.kind = kind;
  }
}

function updateRunEnabled() {
  const btn = document.getElementById("img-run");
  if (!btn) return;
  btn.disabled = !!state.job || !state.modelPath || !state.prompt.trim();
}

async function modelSelect() {
  let models = [];
  try { const r = await listModels(); models = r.models || []; } catch {}
  const sel = el("select", { class: "dp-select" });
  sel.appendChild(el("option", { value: "" }, "Pick an SD model..."));
  for (const m of models) sel.appendChild(el("option", { value: m.path }, m.name));
  sel.appendChild(el("option", { value: "__browse__" }, "Browse..."));
  sel.value = state.modelPath || "";
  sel.addEventListener("change", async () => {
    if (sel.value === "__browse__") {
      const p = await window.cyllama.pickModel();
      sel.value = p || state.modelPath || "";
      state.modelPath = sel.value;
    } else {
      state.modelPath = sel.value;
    }
    updateRunEnabled();
  });
  return sel;
}

// Fetch the artifact via the authenticated sidecar wrapper, return a
// blob: URL the renderer can drop straight into <img src=>. Caller is
// responsible for URL.revokeObjectURL when the image is replaced.
async function fetchArtifactBlob(path) {
  const res = await sidecarFetch(path);
  if (!res.ok) throw new Error(`HTTP ${res.status} loading artifact`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}

async function run() {
  if (state.job) return;
  setStatus("starting...");

  let job;
  try {
    job = await startJob("image.txt2img", {
      model_path: state.modelPath,
      prompt: state.prompt,
      negative_prompt: state.negative,
      width: state.width,
      height: state.height,
      sample_steps: state.steps,
      cfg_scale: state.cfg,
      seed: state.seed,
    });
  } catch (e) {
    setStatus(`failed: ${e.message}`);
    return;
  }
  state.job = job;
  toggleStop(true);
  updateRunEnabled();

  const off = job.onEvent((ev) => {
    if (ev.type === "log" && ev.message) setStatus(ev.message);
    else if (ev.type === "progress" && typeof ev.value === "number") {
      setStatus(`generating ${(ev.value * 100).toFixed(0)}%`);
    } else if (ev.type === "error") {
      setStatus(`error: ${ev.message}`);
    } else if (ev.type === "cancelled") {
      setStatus("cancelled");
    }
  });

  try {
    const result = await job.done;
    setStatus("loading result...");
    const url = await fetchArtifactBlob(result.artifact_url);
    showImage(url, result);
    setStatus(`done · ${result.width}x${result.height} · seed ${result.seed}`);
  } catch (e) {
    setStatus(`failed: ${e.message}`);
  } finally {
    off();
    state.job = null;
    toggleStop(false);
    updateRunEnabled();
  }
}

function toggleStop(running) {
  const r = document.getElementById("img-run");
  const s = document.getElementById("img-stop");
  if (r) r.hidden = running;
  if (s) s.hidden = !running;
}

function showImage(url, meta) {
  const host = document.getElementById("img-result");
  if (!host) return;
  if (state.lastBlobUrl) {
    URL.revokeObjectURL(state.lastBlobUrl);
  }
  state.lastBlobUrl = url;
  host.replaceChildren(
    el("img", { class: "img-result-img", src: url, alt: state.prompt }),
    el("div", { class: "img-result-meta" },
      `${meta.width}x${meta.height} · ${meta.sample_steps} steps · cfg ${meta.cfg_scale} · seed ${meta.seed}`),
  );
}

function bindNumeric(input, key, isInt) {
  input.addEventListener("input", () => {
    const n = isInt ? parseInt(input.value, 10) : parseFloat(input.value);
    if (Number.isFinite(n)) state[key] = n;
  });
}

async function build() {
  const host = document.getElementById("imageBody");
  if (!host) return;
  host.replaceChildren();

  const promptInput = el("textarea", {
    class: "dp-textarea", rows: 3, placeholder: "Describe the image...",
  });
  promptInput.addEventListener("input", () => {
    state.prompt = promptInput.value; updateRunEnabled();
  });

  const negInput = el("textarea", {
    class: "dp-textarea", rows: 2, placeholder: "What to avoid...",
  });
  negInput.addEventListener("input", () => { state.negative = negInput.value; });

  // Param grid: each cell is itself a .dp-row so the label sits above
  // the input the same way the rest of the section does. The grid
  // override drops .dp-row's bottom margin so the cells sit flush.
  function numCell(labelText, key, isInt, opts) {
    const input = el("input", {
      type: "number", class: "dp-input",
      ...opts, value: state[key],
    });
    bindNumeric(input, key, isInt);
    return el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, labelText),
      input,
    );
  }

  // Width/Height + Steps/CFG sit naturally as paired rows; Seed is a
  // singleton so it gets its own full-width row beneath the grid
  // (otherwise it leaves an empty grid cell on the right).
  const params = el("div", { class: "img-grid" },
    numCell("Width",  "width",  true,  { min: 64, max: 2048, step: 64 }),
    numCell("Height", "height", true,  { min: 64, max: 2048, step: 64 }),
    numCell("Steps",  "steps",  true,  { min: 1, max: 200, step: 1 }),
    numCell("CFG",    "cfg",    false, { min: 0, max: 30, step: 0.1 }),
  );
  const seedRow = numCell("Seed", "seed", true, { step: 1 });

  const inputs = el("div", { class: "dp-section" },
    el("h3", {}, "Generate"),

    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Model"),
      await modelSelect(),
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Prompt"),
      promptInput,
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Negative prompt"),
      negInput,
    ),
    params,
    seedRow,
    el("div", { class: "dp-row dp-actions" },
      el("button", { type: "button", id: "img-run", class: "btn primary",
        onclick: run, disabled: true }, "Generate"),
      el("button", { type: "button", id: "img-stop", class: "btn", hidden: true,
        onclick: () => { if (state.job) state.job.cancel(); } }, "Stop"),
    ),
    el("div", { id: "img-status", class: "dp-status" }),
  );

  const result = el("div", { class: "dp-section" },
    el("h3", {}, "Result"),
    el("div", { id: "img-result", class: "img-result" }),
  );

  host.appendChild(inputs);
  host.appendChild(result);

  updateRunEnabled();
}

let mounted = false;
export async function show() {
  if (!mounted) { await build(); mounted = true; }
}
export function hide() {}

export function applyVisibility(features) {
  const btn = document.getElementById("navImage");
  if (!btn) return;
  btn.hidden = !(features && features.image);
}
