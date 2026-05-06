// Batch sidebar view (Phase 9). Run a list of prompts through a single
// model via /jobs/batch; render results live; export as CSV / JSONL.

import { startJob } from "../lib/jobs.js";
import { listModels } from "../lib/models.js";
import { sidecarFetch } from "../lib/sidecar.js";

const state = {
  models: [],
  modelPath: "",
  promptsText: "",
  job: null,
  rows: [],
  status: "",
  artifactUrl: "",
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
  const node = document.getElementById("bt-status");
  if (node) { node.textContent = s || ""; node.dataset.kind = kind; }
}

function updateRunEnabled() {
  const btn = document.getElementById("bt-run");
  if (!btn) return;
  const hasPrompts = state.promptsText.split("\n").some((l) => l.trim());
  btn.disabled = !!state.job || !state.modelPath || !hasPrompts;
}

function modelSelect() {
  const sel = el("select", { class: "dp-select" });
  sel.appendChild(el("option", { value: "" }, "Pick a model..."));
  for (const m of state.models) sel.appendChild(el("option", { value: m.path }, m.name));
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

function appendRow(row) {
  state.rows.push(row);
  const host = document.getElementById("bt-rows");
  if (!host) return;
  const empty = host.querySelector(".bt-empty");
  if (empty) empty.remove();
  host.appendChild(el("div", { class: "bt-row-card" },
    el("div", { class: "bt-row-prompt mono" }, row.prompt || ""),
    el("div", { class: "bt-row-response" }, row.response || ""),
  ));
}

function renderRows() {
  const host = document.getElementById("bt-rows");
  if (!host) return;
  host.replaceChildren();
  if (!state.rows.length) {
    host.appendChild(el("div", { class: "bt-empty" }, "No results yet."));
    return;
  }
  for (const r of state.rows) {
    host.appendChild(el("div", { class: "bt-row-card" },
      el("div", { class: "bt-row-prompt mono" }, r.prompt || ""),
      el("div", { class: "bt-row-response" }, r.response || ""),
    ));
  }
}

function asJsonl() {
  return state.rows.map((r) => JSON.stringify({
    index: r.index, prompt: r.prompt, response: r.response,
  })).join("\n");
}

function asCsv() {
  // Quote fields containing comma / quote / newline; the rest pass
  // through plain. Renderer-side so we don't have to round-trip the
  // sidecar's JSONL artifact through a converter.
  function q(s) {
    s = String(s ?? "");
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }
  const header = "index,prompt,response";
  const body = state.rows.map((r) => `${r.index},${q(r.prompt)},${q(r.response)}`).join("\n");
  return `${header}\n${body}`;
}

function downloadBlob(text, name, mime) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function importPrompts() {
  // Use a hidden <input type=file> rather than an IPC dialog -- the
  // renderer reads the bytes itself, so we don't have to plumb a new
  // pickFile/readText IPC.
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".txt,.jsonl,.json";
  input.addEventListener("change", async () => {
    const f = input.files && input.files[0];
    if (!f) return;
    const text = await f.text();
    const lines = [];
    if (f.name.endsWith(".jsonl")) {
      for (const line of text.split("\n")) {
        const t = line.trim();
        if (!t) continue;
        try {
          const o = JSON.parse(t);
          if (typeof o === "string") lines.push(o);
          else if (o && typeof o.prompt === "string") lines.push(o.prompt);
        } catch { /* skip bad lines */ }
      }
    } else {
      for (const line of text.split("\n")) {
        if (line.trim()) lines.push(line);
      }
    }
    if (lines.length) {
      const ta = document.getElementById("bt-prompts");
      if (ta) {
        ta.value = lines.join("\n");
        state.promptsText = ta.value;
      }
      setStatus(`imported ${lines.length} prompts`);
      updateRunEnabled();
    } else {
      setStatus(`no prompts found in ${f.name}`, "err");
    }
  });
  input.click();
}

async function run() {
  if (state.job) return;
  state.rows = [];
  state.artifactUrl = "";
  renderRows();
  updateExportEnabled();
  setStatus("starting...");

  let job;
  try {
    job = await startJob("batch", {
      model_path: state.modelPath,
      prompts: state.promptsText,
    });
  } catch (e) {
    setStatus(`failed: ${e.message}`, "err");
    return;
  }
  state.job = job;
  toggleStop(true);
  updateRunEnabled();

  const off = job.onEvent((ev) => {
    if (ev.type === "log" && ev.message) setStatus(ev.message);
    else if (ev.type === "progress" && typeof ev.value === "number") {
      setStatus(`${(ev.value * 100).toFixed(0)}%`);
    } else if (ev.type === "result_row") {
      appendRow({ index: ev.index, prompt: ev.prompt, response: ev.response });
      updateExportEnabled();
    } else if (ev.type === "error") {
      setStatus(`error: ${ev.message}`, "err");
    } else if (ev.type === "cancelled") {
      setStatus("cancelled");
    }
  });

  try {
    const result = await job.done;
    state.artifactUrl = result?.artifact_url || "";
    setStatus(`done · ${state.rows.length} rows`);
  } catch (e) {
    setStatus(`failed: ${e.message}`, "err");
  } finally {
    off();
    state.job = null;
    toggleStop(false);
    updateRunEnabled();
    updateExportEnabled();
  }
}

function toggleStop(running) {
  const r = document.getElementById("bt-run");
  const s = document.getElementById("bt-stop");
  if (r) r.hidden = running;
  if (s) s.hidden = !running;
}

function updateExportEnabled() {
  for (const id of ["bt-export-csv", "bt-export-jsonl", "bt-download-jsonl"]) {
    const b = document.getElementById(id);
    if (b) b.disabled = state.rows.length === 0;
  }
  // The download-from-server button only lights up after the artifact_url
  // arrives in the final result event.
  const dl = document.getElementById("bt-download-jsonl");
  if (dl) dl.disabled = !state.artifactUrl;
}

async function downloadServerArtifact() {
  if (!state.artifactUrl) return;
  try {
    const res = await sidecarFetch(state.artifactUrl);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const text = await res.text();
    downloadBlob(text, "outputs.jsonl", "application/jsonl");
  } catch (e) {
    setStatus(`download failed: ${e.message}`, "err");
  }
}

async function build() {
  const host = document.getElementById("batchBody");
  if (!host) return;
  host.replaceChildren();

  const promptsTa = el("textarea", {
    id: "bt-prompts", class: "dp-textarea", rows: 6,
    placeholder: "One prompt per line. Empty lines are ignored.",
  });
  promptsTa.value = state.promptsText;
  promptsTa.addEventListener("input", () => {
    state.promptsText = promptsTa.value; updateRunEnabled();
  });

  const inputs = el("div", { class: "dp-section" },
    el("h3", {}, "Run"),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Model"),
      modelSelect(),
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Prompts"),
      promptsTa,
      el("div", { class: "dp-hint" },
        el("button", { type: "button", class: "btn-mini", onclick: importPrompts }, "Import..."),
        " accepts .txt or .jsonl"),
    ),
    el("div", { class: "dp-row dp-actions" },
      el("button", { type: "button", id: "bt-run", class: "btn primary",
        onclick: run, disabled: true }, "Run batch"),
      el("button", { type: "button", id: "bt-stop", class: "btn", hidden: true,
        onclick: () => { if (state.job) state.job.cancel(); } }, "Stop"),
    ),
    el("div", { id: "bt-status", class: "dp-status" }, state.status),
  );

  const results = el("div", { class: "dp-section" },
    el("div", { class: "tx-seg-head" },
      el("h3", {}, "Results"),
      el("div", { class: "tx-copy-row" },
        el("button", { type: "button", id: "bt-export-csv", class: "btn-mini", disabled: true,
          onclick: () => downloadBlob(asCsv(), "batch.csv", "text/csv") }, "CSV"),
        el("button", { type: "button", id: "bt-export-jsonl", class: "btn-mini", disabled: true,
          onclick: () => downloadBlob(asJsonl(), "batch.jsonl", "application/jsonl") }, "JSONL"),
        el("button", { type: "button", id: "bt-download-jsonl", class: "btn-mini", disabled: true,
          title: "Download the sidecar's full JSONL artifact",
          onclick: downloadServerArtifact }, "Server JSONL"),
      ),
    ),
    el("div", { id: "bt-rows", class: "bt-rows" }),
  );

  host.appendChild(inputs);
  host.appendChild(results);

  renderRows();
  updateRunEnabled();
  updateExportEnabled();
}

let mounted = false;
export async function show() {
  if (!mounted) {
    try { state.models = (await listModels({ kinds: ["chat"] })).models || []; }
    catch { state.models = []; }
    mounted = true;
    await build();
  }
}
export function hide() {}

export function applyVisibility(features) {
  const btn = document.getElementById("navBatch");
  if (!btn) return;
  btn.hidden = !(features && features.batch);
}
