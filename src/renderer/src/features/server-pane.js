// Server sidebar view (Phase 8). Start/stop a cyllama OpenAI-compatible
// server (embedded C++ flavour or pure-Python flavour). Reuses the
// .dp-* sidebar primitives shared with Documents / Transcribe / Image.

import { listModels } from "../lib/models.js";
import { sidecarFetch, sidecarJson, getInfo } from "../lib/sidecar.js";

const state = {
  models: [],
  modelPath: "",
  kind: "embedded",
  port: 8080,
  exposeLan: false,
  // /server/status snapshot. Refreshed on show() and after each
  // start/stop call.
  status: { running: false },
  available: [],
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

function setStatusLine(s, kind = "info") {
  const node = document.getElementById("sv-status");
  if (node) {
    node.textContent = s || "";
    node.dataset.kind = kind;
  }
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
    updateStartEnabled();
  });
  return sel;
}

function kindSelect() {
  const sel = el("select", { class: "dp-select" });
  for (const k of state.available) {
    sel.appendChild(el("option", { value: k },
      k === "embedded" ? "Embedded (C++)" : "Python"));
  }
  if (state.available.length === 0) {
    sel.appendChild(el("option", { value: "" }, "(none available)"));
    sel.disabled = true;
  }
  if (state.available.includes(state.kind)) sel.value = state.kind;
  else if (state.available[0]) state.kind = sel.value = state.available[0];
  sel.addEventListener("change", () => { state.kind = sel.value; });
  return sel;
}

function updateStartEnabled() {
  const btn = document.getElementById("sv-start");
  if (!btn) return;
  btn.disabled = state.status.running || !state.modelPath || state.available.length === 0;
}

async function refreshStatus() {
  try {
    const r = await sidecarFetch("/server/status");
    if (r.ok) state.status = await r.json();
  } catch (e) {
    state.status = { running: false, error: e.message };
  }
}

async function start() {
  if (state.status.running) return;
  if (!state.modelPath) { setStatusLine("Pick a model."); return; }
  if (state.exposeLan) {
    if (!confirm(
      "Expose the server on your local network?\n\n"
      + "Anyone on this network will be able to send requests to your "
      + "loaded model. Make sure you trust the network."
    )) {
      const cb = document.getElementById("sv-expose");
      if (cb) cb.checked = false;
      state.exposeLan = false;
      return;
    }
  }
  setStatusLine("starting...");
  try {
    const j = await sidecarJson("/server/start", {
      kind: state.kind,
      model_path: state.modelPath,
      port: state.port,
      expose_lan: state.exposeLan,
    });
    state.status = j;
    setStatusLine(`running at ${j.url}`, "ok");
  } catch (e) {
    setStatusLine(`failed: ${e.message}`, "err");
  }
  redraw();
}

async function stop() {
  if (!state.status.running) return;
  setStatusLine("stopping...");
  try {
    await sidecarJson("/server/stop", {});
    state.status = { running: false };
    setStatusLine("stopped");
  } catch (e) {
    setStatusLine(`stop failed: ${e.message}`, "err");
  }
  redraw();
}

async function copyText(text, btn) {
  try {
    await navigator.clipboard.writeText(text);
    const orig = btn.textContent;
    btn.textContent = "Copied";
    setTimeout(() => { btn.textContent = orig; }, 900);
  } catch (e) {
    setStatusLine(`copy failed: ${e.message}`, "err");
  }
}

function curlExample(url) {
  // Mirror the /v1/chat/completions shape any OpenAI-compatible client
  // would use; gives the user a one-liner they can paste into a
  // terminal to verify the server before pointing real code at it.
  return [
    `curl ${url}/v1/chat/completions \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -d '{`,
    `    "model": "gpt-3.5-turbo",`,
    `    "messages": [{"role":"user","content":"Hello!"}]`,
    `  }'`,
  ].join("\n");
}

function buildRunningSection() {
  const url = state.status.url || "";
  const curl = url ? curlExample(url) : "";
  return el("div", { class: "dp-section" },
    el("h3", {}, "Running"),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "URL"),
      el("div", { class: "sv-url-row" },
        el("code", { class: "sv-url mono" }, url),
        el("button", {
          type: "button", class: "btn btn-mini",
          onclick: (e) => copyText(url, e.target),
        }, "Copy"),
      ),
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Kind"),
      el("div", {}, state.status.kind || "?"),
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Model"),
      el("div", { class: "mono sv-model-path" }, state.status.model_path || ""),
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "curl example"),
      el("div", { class: "sv-curl-row" },
        el("pre", { class: "sv-curl mono" }, curl),
        el("button", {
          type: "button", class: "btn btn-mini",
          onclick: (e) => copyText(curl, e.target),
        }, "Copy"),
      ),
    ),
    el("div", { class: "dp-row dp-actions" },
      el("button", { type: "button", class: "btn", onclick: stop }, "Stop"),
    ),
  );
}

function buildStartSection() {
  const portInput = el("input", {
    type: "number", class: "dp-input",
    min: 1, max: 65535, step: 1, value: state.port,
  });
  portInput.addEventListener("input", () => {
    const n = parseInt(portInput.value, 10);
    if (Number.isFinite(n)) state.port = Math.max(1, Math.min(65535, n));
  });

  return el("div", { class: "dp-section" },
    el("h3", {}, "Start a server"),

    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Model"),
      modelSelect(),
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Kind"),
      kindSelect(),
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-label" }, "Port"),
      portInput,
    ),
    el("div", { class: "dp-row" },
      el("label", { class: "dp-check-row" },
        el("input", {
          type: "checkbox", id: "sv-expose",
          checked: state.exposeLan,
          onchange: (e) => { state.exposeLan = e.target.checked; },
        }),
        el("span", {}, "Expose on local network (0.0.0.0)"),
      ),
      el("div", { class: "dp-hint" },
        "Off = loopback only. On = anyone on your network can reach the server."),
    ),
    el("div", { class: "dp-row dp-actions" },
      el("button", { type: "button", id: "sv-start", class: "btn primary",
        onclick: start, disabled: true }, "Start"),
    ),
    el("div", { id: "sv-status", class: "dp-status" }, ""),
  );
}

function redraw() {
  const host = document.getElementById("serverBody");
  if (!host) return;
  host.replaceChildren();
  if (state.status.running) {
    host.appendChild(buildRunningSection());
  } else {
    host.appendChild(buildStartSection());
    updateStartEnabled();
  }
}

async function refreshAll() {
  const [m, info] = await Promise.all([
    listModels({ kinds: ["chat"] }).catch(() => ({ models: [] })),
    getInfo().catch(() => ({})),
  ]);
  state.models = m.models || [];
  state.available = (info && info.server_kinds) || [];
  if (state.available.length && !state.available.includes(state.kind)) {
    state.kind = state.available[0];
  }
  await refreshStatus();
}

let mounted = false;
export async function show() {
  await refreshAll();
  if (!mounted) { mounted = true; }
  redraw();
}
export function hide() {}

export function applyVisibility(features) {
  const btn = document.getElementById("navServer");
  if (!btn) return;
  btn.hidden = !(features && features.openai_server);
}
