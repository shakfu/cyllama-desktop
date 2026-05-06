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

function setStatus(s) {
  const el = document.getElementById("img-status");
  if (el) el.textContent = s || "";
}

function updateRunEnabled() {
  const btn = document.getElementById("img-run");
  if (!btn) return;
  btn.disabled = !!state.job || !state.modelPath || !state.prompt.trim();
}

async function modelSelect() {
  let models = [];
  try { const r = await listModels(); models = r.models || []; } catch {}
  const sel = el("select", { class: "img-select" });
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

  const modelRow = el("div", { class: "img-row" },
    el("label", { class: "img-label" }, "Model"),
    await modelSelect(),
  );

  const promptInput = el("textarea", { class: "img-textarea", rows: 3, placeholder: "Describe the image..." });
  promptInput.addEventListener("input", () => { state.prompt = promptInput.value; updateRunEnabled(); });

  const negInput = el("textarea", { class: "img-textarea", rows: 2, placeholder: "What to avoid..." });
  negInput.addEventListener("input", () => { state.negative = negInput.value; });

  const wInput = el("input", { type: "number", class: "img-num", min: 64, max: 2048, step: 64, value: state.width });
  const hInput = el("input", { type: "number", class: "img-num", min: 64, max: 2048, step: 64, value: state.height });
  const stepsInput = el("input", { type: "number", class: "img-num", min: 1, max: 200, step: 1, value: state.steps });
  const cfgInput = el("input", { type: "number", class: "img-num", min: 0, max: 30, step: 0.1, value: state.cfg });
  const seedInput = el("input", { type: "number", class: "img-num", step: 1, value: state.seed });
  bindNumeric(wInput, "width", true);
  bindNumeric(hInput, "height", true);
  bindNumeric(stepsInput, "steps", true);
  bindNumeric(cfgInput, "cfg", false);
  bindNumeric(seedInput, "seed", true);

  const runBtn = el("button", { type: "button", id: "img-run", class: "btn primary",
    onclick: run, disabled: true }, "Generate");
  const stopBtn = el("button", { type: "button", id: "img-stop", class: "btn", hidden: true,
    onclick: () => { if (state.job) state.job.cancel(); } }, "Stop");

  host.appendChild(modelRow);
  host.appendChild(el("div", { class: "img-row" },
    el("label", { class: "img-label" }, "Prompt"), promptInput));
  host.appendChild(el("div", { class: "img-row" },
    el("label", { class: "img-label" }, "Negative prompt"), negInput));
  host.appendChild(el("div", { class: "img-grid" },
    el("label", { class: "img-mini-label" }, "W", wInput),
    el("label", { class: "img-mini-label" }, "H", hInput),
    el("label", { class: "img-mini-label" }, "Steps", stepsInput),
    el("label", { class: "img-mini-label" }, "CFG", cfgInput),
    el("label", { class: "img-mini-label" }, "Seed", seedInput),
  ));
  host.appendChild(el("div", { class: "img-row img-actions" }, runBtn, stopBtn));
  host.appendChild(el("div", { id: "img-status", class: "img-status" }));
  host.appendChild(el("div", { id: "img-result", class: "img-result" }));

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
