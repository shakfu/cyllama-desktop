// Transcribe sidebar view (Phase 5). Pick a whisper model + audio file,
// optionally a language, fire off a /jobs/transcribe job, render the
// resulting segments as they stream in. Copy as TXT / SRT / VTT.

import { startJob } from "../lib/jobs.js";
import { listModels } from "../lib/models.js";

// 99 ISO-639-1 entries is overkill for the dropdown -- ship a small,
// commonly-useful set plus "auto" for whisper's own detection. Power
// users can type a code into the input fallback below.
const COMMON_LANGS = [
  ["", "auto-detect"],
  ["en", "English"], ["es", "Spanish"], ["fr", "French"], ["de", "German"],
  ["it", "Italian"], ["pt", "Portuguese"], ["nl", "Dutch"], ["pl", "Polish"],
  ["ru", "Russian"], ["uk", "Ukrainian"], ["zh", "Chinese"], ["ja", "Japanese"],
  ["ko", "Korean"], ["ar", "Arabic"], ["hi", "Hindi"], ["tr", "Turkish"],
];

const state = {
  modelPath: "",
  audioPath: "",
  language: "",
  translate: false,
  job: null,
  segments: [],
  status: "",
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2).toLowerCase(), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

function basename(p) { return p ? p.split(/[\\/]/).pop() : ""; }

function fmtTimestamp(ms, sep = ",") {
  // SRT: HH:MM:SS,ms ; VTT: HH:MM:SS.ms. Same digits otherwise.
  const total = Math.max(0, Math.floor(ms));
  const hh = String(Math.floor(total / 3600000)).padStart(2, "0");
  const mm = String(Math.floor((total % 3600000) / 60000)).padStart(2, "0");
  const ss = String(Math.floor((total % 60000) / 1000)).padStart(2, "0");
  const f  = String(total % 1000).padStart(3, "0");
  return `${hh}:${mm}:${ss}${sep}${f}`;
}

function asTxt() {
  return state.segments.map((s) => s.text).join("\n");
}
function asSrt() {
  return state.segments.map((s, i) =>
    `${i + 1}\n${fmtTimestamp(s.t0_ms, ",")} --> ${fmtTimestamp(s.t1_ms, ",")}\n${s.text}\n`
  ).join("\n");
}
function asVtt() {
  const body = state.segments.map((s) =>
    `${fmtTimestamp(s.t0_ms, ".")} --> ${fmtTimestamp(s.t1_ms, ".")}\n${s.text}\n`
  ).join("\n");
  return `WEBVTT\n\n${body}`;
}

async function copyText(getText, btn) {
  try {
    await navigator.clipboard.writeText(getText());
    const orig = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = orig; }, 900);
  } catch (e) {
    setStatus(`copy failed: ${e.message}`);
  }
}

function setStatus(s) {
  state.status = s;
  const el = document.getElementById("tx-status");
  if (el) el.textContent = s || "";
}

async function modelSelect() {
  let models = [];
  try { const r = await listModels(); models = r.models || []; } catch {}
  // Whisper GGML files conventionally have "whisper" in the name and
  // live as `.bin` rather than `.gguf`. Don't filter -- the user might
  // have placed a whisper file under MODELS_DIR with any name.
  const sel = el("select", { class: "tx-select" });
  sel.appendChild(el("option", { value: "" }, "Pick a whisper model..."));
  for (const m of models) {
    const opt = el("option", { value: m.path }, m.name);
    sel.appendChild(opt);
  }
  // "Browse..." sentinel falls back to the OS file picker (whisper
  // .bin files often live outside MODELS_DIR; covers that case).
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

function languageSelect() {
  const sel = el("select", { class: "tx-select" });
  for (const [code, label] of COMMON_LANGS) {
    sel.appendChild(el("option", { value: code }, label));
  }
  sel.value = state.language || "";
  sel.addEventListener("change", () => { state.language = sel.value; });
  return sel;
}

function updateRunEnabled() {
  const btn = document.getElementById("tx-run");
  if (!btn) return;
  btn.disabled = !!state.job || !state.modelPath || !state.audioPath;
}

async function pickAudio() {
  const p = await window.cyllama.pickAudio();
  if (p) {
    state.audioPath = p;
    const lbl = document.getElementById("tx-audio-label");
    if (lbl) lbl.textContent = basename(p);
    updateRunEnabled();
  }
}

async function run() {
  if (state.job) return;
  state.segments = [];
  redrawSegments();
  setStatus("starting...");

  let job;
  try {
    job = await startJob("transcribe", {
      audio_path: state.audioPath,
      model_path: state.modelPath,
      language: state.language || null,
      translate: state.translate,
    });
  } catch (e) {
    setStatus(`failed: ${e.message}`);
    return;
  }
  state.job = job;
  updateRunEnabled();
  toggleStopBtn(true);

  const off = job.onEvent((ev) => {
    if (ev.type === "log" && ev.message) setStatus(ev.message);
    else if (ev.type === "progress" && typeof ev.value === "number") {
      setStatus(`transcribing ${(ev.value * 100).toFixed(0)}%`);
    } else if (ev.type === "segment") {
      state.segments.push({
        index: ev.index, t0_ms: ev.t0_ms, t1_ms: ev.t1_ms, text: ev.text,
      });
      redrawSegments();
    } else if (ev.type === "error") {
      setStatus(`error: ${ev.message}`);
    } else if (ev.type === "cancelled") {
      setStatus("cancelled");
    }
  });

  try {
    const result = await job.done;
    if (result && result.language) {
      setStatus(`done · ${state.segments.length} segments · ${result.language}`);
    } else {
      setStatus(`done · ${state.segments.length} segments`);
    }
  } catch (e) {
    setStatus(`failed: ${e.message}`);
  } finally {
    off();
    state.job = null;
    toggleStopBtn(false);
    updateRunEnabled();
  }
}

function toggleStopBtn(running) {
  const run = document.getElementById("tx-run");
  const stop = document.getElementById("tx-stop");
  if (run) run.hidden = running;
  if (stop) stop.hidden = !running;
}

function redrawSegments() {
  const host = document.getElementById("tx-segments");
  if (!host) return;
  host.replaceChildren();
  if (!state.segments.length) {
    host.appendChild(el("div", { class: "tx-empty" }, "No segments yet."));
    return;
  }
  for (const s of state.segments) {
    const row = el("div", { class: "tx-segment" },
      el("span", { class: "tx-ts mono" }, fmtTimestamp(s.t0_ms, ".")),
      el("span", { class: "tx-text" }, s.text),
    );
    host.appendChild(row);
  }
}

async function build() {
  const host = document.getElementById("transcribeBody");
  if (!host) return;
  host.replaceChildren();

  const modelRow = el("div", { class: "tx-row" },
    el("label", { class: "tx-label" }, "Model"),
    await modelSelect(),
  );

  const audioRow = el("div", { class: "tx-row" },
    el("label", { class: "tx-label" }, "Audio (WAV)"),
    el("div", { class: "tx-audio-row" },
      el("button", { type: "button", class: "btn", onclick: pickAudio }, "Choose..."),
      el("span", { id: "tx-audio-label", class: "tx-audio-label mono" },
        state.audioPath ? basename(state.audioPath) : "(none)"),
    ),
  );

  const langRow = el("div", { class: "tx-row" },
    el("label", { class: "tx-label" }, "Language"),
    languageSelect(),
  );

  const translateRow = el("div", { class: "tx-row" },
    el("label", { class: "tx-check-row" },
      el("input", { type: "checkbox", id: "tx-translate", onchange: (e) => { state.translate = e.target.checked; } }),
      el("span", {}, "Translate to English"),
    ),
  );

  const runBtn = el("button", { type: "button", id: "tx-run", class: "btn primary", onclick: run, disabled: true }, "Transcribe");
  const stopBtn = el("button", { type: "button", id: "tx-stop", class: "btn", hidden: true,
    onclick: () => { if (state.job) state.job.cancel(); } }, "Stop");
  const actionsRow = el("div", { class: "tx-row tx-actions" }, runBtn, stopBtn);

  const statusEl = el("div", { id: "tx-status", class: "tx-status" }, state.status);

  const segHeader = el("div", { class: "tx-seg-head" },
    el("h3", {}, "Segments"),
    el("div", { class: "tx-copy-row" },
      el("button", { type: "button", class: "btn-mini",
        onclick: (e) => copyText(asTxt, e.target) }, "TXT"),
      el("button", { type: "button", class: "btn-mini",
        onclick: (e) => copyText(asSrt, e.target) }, "SRT"),
      el("button", { type: "button", class: "btn-mini",
        onclick: (e) => copyText(asVtt, e.target) }, "VTT"),
    ),
  );
  const segHost = el("div", { id: "tx-segments", class: "tx-segments" });

  host.appendChild(modelRow);
  host.appendChild(audioRow);
  host.appendChild(langRow);
  host.appendChild(translateRow);
  host.appendChild(actionsRow);
  host.appendChild(statusEl);
  host.appendChild(segHeader);
  host.appendChild(segHost);

  redrawSegments();
  updateRunEnabled();
}

let mounted = false;
export async function show() {
  if (!mounted) { await build(); mounted = true; }
}
export function hide() {
  // If a job is in flight when the user navigates away, leave it
  // running -- they'll come back to a populated segments table. The
  // job stays reachable via the Console / future Jobs panel.
}

// Toggle the nav-rail button visibility based on whether the cyllama
// build supports whisper. Called by main.js with the /info payload.
export function applyVisibility(features) {
  const btn = document.getElementById("navTranscribe");
  if (!btn) return;
  btn.hidden = !(features && features.whisper);
}
