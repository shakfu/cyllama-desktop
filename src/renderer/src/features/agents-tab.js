// Agents tab (Phase 7). Lives as a tab in the right sidebar (not a
// sidebar-view), so it follows the .rt-section layout the General /
// Models tabs use rather than the .dp-section primitives the
// sidebar-view panes use.
//
// Wire shape: see /jobs/agent/run in the sidecar. The tab posts a
// {model_path, task, tools, max_iterations} body and renders the SSE
// trace events as a typed timeline.

import { startJob } from "../lib/jobs.js";
import { listModels } from "../lib/models.js";
import { listCollections } from "../lib/rag.js";
import { getInfo } from "../lib/sidecar.js";

const state = {
  task: "",
  modelPath: "",
  maxIterations: 10,
  tools: {
    calculator: true,
    read_file: { enabled: false, sandbox_dir: "" },
    web_fetch: false,
    rag_query: { enabled: false, collection_id: "", top_k: 3 },
  },
  job: null,
  events: [],
  answer: null,
  status: "",
  features: { agents: true },
  collections: [],
  models: [],
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

function setStatus(s) {
  state.status = s;
  const node = document.getElementById("ag-status");
  if (node) node.textContent = s || "";
}

function updateRunEnabled() {
  const btn = document.getElementById("ag-run");
  if (!btn) return;
  btn.disabled = !!state.job || !state.modelPath || !state.task.trim();
}

function modelSelect() {
  const sel = el("select", { class: "dp-select" });
  sel.appendChild(el("option", { value: "" }, "Pick a chat model..."));
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

function ragCollectionSelect() {
  const sel = el("select", { class: "dp-select" });
  sel.appendChild(el("option", { value: "" }, "Pick a collection..."));
  for (const c of state.collections) {
    sel.appendChild(el("option", { value: c.id }, `${c.name} · ${c.chunk_count} chunks`));
  }
  sel.value = state.tools.rag_query.collection_id || "";
  sel.addEventListener("change", () => { state.tools.rag_query.collection_id = sel.value; });
  return sel;
}

function buildToolsBlock() {
  // Calculator: simple toggle.
  const calc = el("label", { class: "ag-tool-row" },
    el("input", { type: "checkbox", checked: state.tools.calculator,
      onchange: (e) => { state.tools.calculator = e.target.checked; } }),
    el("span", {}, "Calculator"),
    el("span", { class: "ag-tool-hint" }, "arithmetic"),
  );

  // Read file: toggle + folder picker.
  const sandboxLabel = el("span", { class: "ag-tool-path mono" },
    state.tools.read_file.sandbox_dir
      ? basename(state.tools.read_file.sandbox_dir)
      : "(no folder)");
  const sandboxBtn = el("button", { type: "button", class: "btn btn-mini",
    onclick: async () => {
      const p = await window.cyllama.pickFolder();
      if (p) {
        state.tools.read_file.sandbox_dir = p;
        state.tools.read_file.enabled = true;
        sandboxLabel.textContent = basename(p);
        const cb = document.getElementById("ag-tool-read-file");
        if (cb) cb.checked = true;
      }
    },
  }, "Choose...");
  const readFile = el("div", { class: "ag-tool-row-stack" },
    el("label", { class: "ag-tool-row" },
      el("input", {
        type: "checkbox", id: "ag-tool-read-file",
        checked: state.tools.read_file.enabled,
        onchange: (e) => { state.tools.read_file.enabled = e.target.checked; },
      }),
      el("span", {}, "Read file"),
      el("span", { class: "ag-tool-hint" }, "sandboxed"),
    ),
    el("div", { class: "ag-tool-sub" }, sandboxBtn, sandboxLabel),
  );

  // Web fetch: toggle. Off by default per PLAN.md S.10.
  const webFetch = el("label", { class: "ag-tool-row" },
    el("input", { type: "checkbox", checked: state.tools.web_fetch,
      onchange: (e) => {
        if (e.target.checked) {
          if (!confirm("Enable network access for this agent run? The model can fetch arbitrary URLs.")) {
            e.target.checked = false; return;
          }
        }
        state.tools.web_fetch = e.target.checked;
      } }),
    el("span", {}, "Web fetch"),
    el("span", { class: "ag-tool-hint" }, "network · off by default"),
  );

  // RAG query: toggle + collection picker.
  const ragQuery = el("div", { class: "ag-tool-row-stack" },
    el("label", { class: "ag-tool-row" },
      el("input", {
        type: "checkbox",
        checked: state.tools.rag_query.enabled,
        onchange: (e) => { state.tools.rag_query.enabled = e.target.checked; },
      }),
      el("span", {}, "RAG query"),
      el("span", { class: "ag-tool-hint" }, "retrieve from a collection"),
    ),
    el("div", { class: "ag-tool-sub" }, ragCollectionSelect()),
  );

  return el("div", { class: "ag-tools" }, calc, readFile, webFetch, ragQuery);
}

function fmtEventType(t) {
  return String(t || "").toLowerCase().replace(/_/g, "-");
}

function renderEvent(ev) {
  return el("div", { class: `ag-event ag-ev-${fmtEventType(ev.event_type)}` },
    el("span", { class: "ag-ev-type" }, ev.event_type || "?"),
    el("span", { class: "ag-ev-content" }, ev.content || ""),
  );
}

function clearTrace() {
  state.events = [];
  state.answer = null;
  const host = document.getElementById("ag-trace");
  if (host) host.replaceChildren();
  const ans = document.getElementById("ag-answer");
  if (ans) ans.replaceChildren();
}

function appendEvent(ev) {
  state.events.push(ev);
  const host = document.getElementById("ag-trace");
  if (host) host.appendChild(renderEvent(ev));
  if (ev.event_type === "ANSWER") {
    state.answer = ev.content || "";
    const ans = document.getElementById("ag-answer");
    if (ans) ans.textContent = state.answer;
  }
}

function buildToolsSpec() {
  const t = state.tools;
  const out = {};
  if (t.calculator) out.calculator = true;
  if (t.read_file.enabled && t.read_file.sandbox_dir) {
    out.read_file = { sandbox_dir: t.read_file.sandbox_dir };
  }
  if (t.web_fetch) out.web_fetch = true;
  if (t.rag_query.enabled && t.rag_query.collection_id) {
    out.rag_query = {
      collection_id: t.rag_query.collection_id,
      top_k: t.rag_query.top_k || 3,
    };
  }
  return out;
}

async function run() {
  if (state.job) return;
  if (!state.modelPath) { setStatus("Pick a model."); return; }
  if (!state.task.trim()) { setStatus("Describe a task."); return; }
  // Validate read_file: if checked but no sandbox, surface here rather
  // than letting the sidecar 400.
  if (state.tools.read_file.enabled && !state.tools.read_file.sandbox_dir) {
    setStatus("read_file needs a sandbox folder.");
    return;
  }
  if (state.tools.rag_query.enabled && !state.tools.rag_query.collection_id) {
    setStatus("rag_query needs a collection.");
    return;
  }
  clearTrace();
  setStatus("starting...");

  let job;
  try {
    job = await startJob("agent.run", {
      model_path: state.modelPath,
      task: state.task,
      max_iterations: state.maxIterations,
      tools: buildToolsSpec(),
    });
  } catch (e) {
    setStatus(`failed: ${e.message}`);
    return;
  }
  state.job = job;
  toggleStop(true);
  updateRunEnabled();

  const off = job.onEvent((ev) => {
    if (ev.type === "trace") {
      appendEvent({
        event_type: ev.event_type,
        content: ev.content,
        metadata: ev.metadata,
      });
    } else if (ev.type === "error") {
      setStatus(`error: ${ev.message}`);
    } else if (ev.type === "cancelled") {
      setStatus("cancelled");
    }
  });

  try {
    const result = await job.done;
    setStatus(`done · ${state.events.length} events · ${result?.iterations ?? 0} actions`);
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
  const r = document.getElementById("ag-run");
  const s = document.getElementById("ag-stop");
  if (r) r.hidden = running;
  if (s) s.hidden = !running;
}

async function refresh() {
  try {
    const [m, c, info] = await Promise.all([
      listModels({ kinds: ["chat"] }).catch(() => ({ models: [] })),
      listCollections().catch(() => ({ collections: [] })),
      getInfo().catch(() => ({})),
    ]);
    state.models = m.models || [];
    state.collections = c.collections || [];
    state.features = info.features || {};
  } catch (e) {
    console.warn("agents-tab refresh:", e);
  }
}

async function build() {
  await refresh();
  const host = document.getElementById("agentsTabHost");
  if (!host) return;

  if (!state.features.agents) {
    host.innerHTML = "";
    host.appendChild(el("div", { class: "rt-section" },
      el("div", { class: "rt-section-head" }, el("h3", {}, "Agents")),
      el("div", { class: "rt-placeholder" },
        el("p", {}, "Agents not available in this cyllama build."),
      ),
    ));
    return;
  }

  host.innerHTML = "";

  // Inputs
  const taskInput = el("textarea", {
    class: "dp-textarea", rows: 4,
    placeholder: "Describe a task. The agent will reason step-by-step and call tools as needed.",
  });
  taskInput.value = state.task;
  taskInput.addEventListener("input", () => {
    state.task = taskInput.value; updateRunEnabled();
  });

  const iterInput = el("input", {
    type: "number", class: "dp-input", min: 1, max: 50, step: 1,
    value: state.maxIterations,
  });
  iterInput.addEventListener("input", () => {
    const n = parseInt(iterInput.value, 10);
    if (Number.isFinite(n)) state.maxIterations = Math.max(1, Math.min(50, n));
  });

  const inputs = el("section", { class: "rt-section" },
    el("div", { class: "rt-section-head" }, el("h3", {}, "Run")),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Model"),
      modelSelect(),
    ),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Task"),
      taskInput,
    ),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Max iterations"),
      iterInput,
    ),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Tools"),
      buildToolsBlock(),
    ),
    el("div", { class: "ag-actions" },
      el("button", { type: "button", id: "ag-run", class: "btn primary",
        onclick: run, disabled: true }, "Run"),
      el("button", { type: "button", id: "ag-stop", class: "btn", hidden: true,
        onclick: () => { if (state.job) state.job.cancel(); } }, "Stop"),
    ),
    el("div", { id: "ag-status", class: "ag-status" }, state.status),
  );

  const trace = el("section", { class: "rt-section" },
    el("div", { class: "rt-section-head" }, el("h3", {}, "Trace")),
    el("div", { id: "ag-trace", class: "ag-trace" }),
  );

  const answer = el("section", { class: "rt-section" },
    el("div", { class: "rt-section-head" }, el("h3", {}, "Answer")),
    el("div", { id: "ag-answer", class: "ag-answer" }),
  );

  host.appendChild(inputs);
  host.appendChild(trace);
  host.appendChild(answer);

  updateRunEnabled();
}

let mounted = false;
export function mount() {
  // Build async; the host slot stays empty until the first refresh
  // resolves which also gives us the feature flag.
  if (mounted) return;
  mounted = true;
  build();
}

// Re-rendered when /info.features arrives (e.g. for the not-available
// branch). Kept distinct from mount() so callers can re-issue without
// re-running the heavy refresh.
export function applyVisibility(features) {
  state.features = features || state.features;
  // The tab is part of the static layout; we don't hide it. When
  // features.agents is false, the tab body shows a not-available note
  // (handled by build()).
  if (!mounted) return;
  build();
}
