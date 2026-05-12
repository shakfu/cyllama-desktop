// Full-area Workflows pane. Three columns:
//
//   #workflowsPaneSubnav  -- discovered-workflow list (id + entry node);
//                            broken files show with a "(error)" tag.
//   #workflowsPaneMain    -- selected workflow's spec preview (entry,
//                            exits, levels, inputs), initial-state
//                            form, Run button, live trace pane.
//   #workflowsPaneDetail  -- per-run summary (success/error, final state).
//
// Workflows are workspace-scoped *.py files under ``<workspace>/workflows/``.
// The sidecar discovers them on demand (``GET /workflows``); see
// docs/dev/agent_plan.md §7 for the authoring shape and trust boundary.

import { sidecarFetch } from "../lib/sidecar.js";
import { startJob } from "../lib/jobs.js";

const state = {
  workflows: [],
  workflowsDir: "",
  selectedId: null,
  spec: null,           // /workflows/{id}/spec response for the selection
  initialState: {},     // user-supplied initial state for the selected workflow
  liveEvents: [],       // accumulating events for the in-progress run
  lastResult: null,     // last completed run's result payload
  currentJob: null,
};

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") {
      node.addEventListener(k.slice(2).toLowerCase(), v);
    } else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// ---------------------------------------------------------------------------
// Subnav (left column): workflow list
// ---------------------------------------------------------------------------

function renderSubnav() {
  const host = document.getElementById("workflowsPaneSubnav");
  if (!host) return;
  host.innerHTML = "";
  host.appendChild(el("div", { class: "mp-subnav-head" }, "Workflows"));
  if (!state.workflows.length) {
    host.appendChild(el("div", { class: "rt-placeholder" },
      el("p", {}, "No workflow files found."),
      el("p", { class: "ag-hint" },
        "Author Python files under ",
        el("code", {}, state.workflowsDir || "<workspace>/workflows/"),
        " exporting ", el("code", {}, "flow"), " or ", el("code", {}, "make_flow()"), "."),
    ));
    return;
  }
  for (const w of state.workflows) {
    // Reuse the Models pane's ``mp-subnav-row`` button shape so we
    // inherit the existing flex / hover / active / count-tag CSS.
    // Row text on the left, entry-node or ``error`` tag on the right.
    const row = el("button", {
      type: "button",
      class: "mp-subnav-row" + (w.id === state.selectedId ? " active" : ""),
      id: `wf-item-${w.id}`,
      "data-workflow-id": w.id,
      disabled: w.error ? true : undefined,
    },
      el("span", {}, w.id),
      el("span", { class: "mp-subnav-count" }, w.error ? "error" : (w.entry || "")),
    );
    if (!w.error) {
      row.addEventListener("click", () => select(w.id));
    }
    host.appendChild(row);
  }
  host.appendChild(el("div", { class: "mp-subnav-foot", style: "margin-top: 12px;" },
    el("button", {
      class: "btn btn-mini",
      id: "wf-refresh",
      onclick: () => refresh(),
    }, "Refresh"),
  ));
}

// ---------------------------------------------------------------------------
// Main column: spec + initial-state + Run + trace
// ---------------------------------------------------------------------------

function renderMain() {
  const host = document.getElementById("workflowsPaneMain");
  if (!host) return;
  host.innerHTML = "";

  if (!state.selectedId) {
    host.appendChild(el("div", { class: "rt-placeholder", id: "wf-empty" },
      el("p", {}, "Select a workflow on the left to view its plan and run it."),
    ));
    return;
  }

  const wf = state.workflows.find((w) => w.id === state.selectedId);
  if (!wf) return;

  // Header.
  host.appendChild(el("div", { class: "mp-main-head" },
    el("h2", {}, wf.id),
    wf.doc ? el("p", { class: "mp-main-doc" }, wf.doc) : null,
  ));

  // Spec preview (entry, exits, inputs, mermaid). Loaded async; we
  // render placeholders + replace when the fetch resolves.
  const specHost = el("section", { class: "mp-section", id: "wf-spec" },
    el("h3", {}, "Plan"),
    el("div", { class: "rt-placeholder" }, "loading..."),
  );
  host.appendChild(specHost);

  // Initial-state form. One row per inputs_required key; values are
  // POSTed as JSON, so we coerce numbers/bools/strings on submit.
  const inputs = wf.inputs_required || [];
  const formHost = el("section", { class: "mp-section", id: "wf-form" });
  formHost.appendChild(el("h3", {}, "Initial state"));
  if (inputs.length) {
    for (const key of inputs) {
      const input = el("input", {
        type: "text", class: "dp-input", "data-state-key": key,
        placeholder: key,
        value: state.initialState[key] != null ? String(state.initialState[key]) : "",
      });
      input.addEventListener("input", () => {
        state.initialState[key] = input.value;
      });
      formHost.appendChild(el("div", { class: "ag-row" },
        el("label", { class: "ag-label" }, key),
        input,
      ));
    }
  } else {
    formHost.appendChild(el("p", { class: "ag-hint" }, "No inputs required."));
  }
  const runBtn = el("button", {
    type: "button", class: "btn btn-primary", id: "wf-run",
    onclick: () => runSelected(),
  }, "Run");
  formHost.appendChild(el("div", { class: "ag-row" }, runBtn));
  host.appendChild(formHost);

  // Live trace pane.
  host.appendChild(el("section", { class: "mp-section", id: "wf-trace" },
    el("h3", {}, "Trace"),
    el("div", { class: "agent-events", id: "wf-events" }),
  ));

  // Fire the spec fetch.
  loadSpec(wf.id, specHost);
}

async function loadSpec(id, host) {
  try {
    const r = await sidecarFetch(`/workflows/${id}/spec`);
    if (!r.ok) {
      host.innerHTML = "";
      host.appendChild(el("h3", {}, "Plan"));
      host.appendChild(el("p", { class: "ag-error" }, `Spec load failed: ${r.status}`));
      return;
    }
    const spec = await r.json();
    state.spec = spec;
    host.innerHTML = "";
    host.appendChild(el("h3", {}, "Plan"));
    host.appendChild(el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Entry"),
      el("code", {}, spec.entry || "(none)"),
    ));
    host.appendChild(el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Exits"),
      el("code", {}, (spec.exits || []).join(", ") || "(derived)"),
    ));
    host.appendChild(el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Levels"),
      el("code", {}, (spec.levels || []).map((lvl) => `[${lvl.join(", ")}]`).join(" -> ")),
    ));
    if (spec.mermaid) {
      host.appendChild(el("details", { class: "mp-mermaid" },
        el("summary", {}, "Mermaid source"),
        el("pre", {}, el("code", {}, spec.mermaid)),
      ));
    }
  } catch (e) {
    host.innerHTML = "";
    host.appendChild(el("h3", {}, "Plan"));
    host.appendChild(el("p", { class: "ag-error" }, `Spec load failed: ${e.message || e}`));
  }
}

// ---------------------------------------------------------------------------
// Detail column: per-run result
// ---------------------------------------------------------------------------

function renderDetail() {
  const host = document.getElementById("workflowsPaneDetail");
  if (!host) return;
  host.innerHTML = "";
  host.appendChild(el("div", { class: "mp-detail-head" },
    el("h3", {}, "Last run"),
  ));
  if (!state.lastResult) {
    host.appendChild(el("div", { class: "rt-placeholder" },
      el("p", {}, "Run a workflow to see its result here."),
    ));
    return;
  }
  const r = state.lastResult;
  host.appendChild(el("div", { class: "ag-row" },
    el("label", { class: "ag-label" }, "Status"),
    el("span", {}, r.success ? "succeeded" : "failed"),
  ));
  if (r.answer) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Answer"),
      el("pre", {}, r.answer),
    ));
  }
  if (r.error) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Error"),
      el("pre", { class: "ag-error" }, r.error),
    ));
  }
  if (r.state && Object.keys(r.state).length) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Final state"),
      el("pre", {}, JSON.stringify(r.state, null, 2)),
    ));
  }
}

// ---------------------------------------------------------------------------
// Run loop: starts the job, streams events into #wf-events
// ---------------------------------------------------------------------------

function renderTraceEvent(ev) {
  const host = document.getElementById("wf-events");
  if (!host) return;
  const row = el("div", {
    class: `ag-event ag-ev-${(ev.event_type || "").toLowerCase().replace(/_/g, "-")}`,
  });
  const md = ev.metadata || {};
  const source = md.source || md.node;
  if (source) row.appendChild(el("span", { class: "ag-ev-source" }, source));
  row.appendChild(el("span", { class: "ag-ev-type" }, ev.event_type || "?"));
  row.appendChild(el("span", { class: "ag-ev-content" }, ev.content || ""));
  host.appendChild(row);
  host.scrollTop = host.scrollHeight;
}

async function runSelected() {
  if (!state.selectedId) return;
  if (state.currentJob) return;
  const runBtn = document.getElementById("wf-run");
  if (runBtn) runBtn.disabled = true;
  state.liveEvents = [];
  state.lastResult = null;
  const eventsHost = document.getElementById("wf-events");
  if (eventsHost) eventsHost.innerHTML = "";

  let job;
  try {
    job = await startJob("workflow/run", {
      workflow_id: state.selectedId,
      initial_state: { ...state.initialState },
    });
  } catch (e) {
    state.lastResult = { success: false, error: e.message || String(e) };
    renderDetail();
    if (runBtn) runBtn.disabled = false;
    return;
  }
  state.currentJob = job;

  const off = job.onEvent((ev) => {
    if (ev.type === "trace") {
      const item = {
        event_type: ev.event_type, content: ev.content, metadata: ev.metadata || {},
      };
      state.liveEvents.push(item);
      renderTraceEvent(item);
    } else if (ev.type === "result") {
      state.lastResult = ev.result || null;
      renderDetail();
    } else if (ev.type === "error") {
      state.lastResult = { success: false, error: ev.message || "unknown error" };
      renderDetail();
    }
  });
  try { await job.done; }
  catch (e) {
    if (!state.lastResult) {
      state.lastResult = { success: false, error: e.message || String(e) };
      renderDetail();
    }
  } finally {
    off();
    state.currentJob = null;
    if (runBtn) runBtn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

function select(id) {
  if (state.selectedId === id) return;
  state.selectedId = id;
  state.initialState = {};
  state.spec = null;
  renderSubnav();
  renderMain();
}

export async function refresh() {
  try {
    const r = await sidecarFetch("/workflows");
    if (!r.ok) {
      state.workflows = [];
      state.workflowsDir = "";
    } else {
      const body = await r.json();
      state.workflows = body.workflows || [];
      state.workflowsDir = body.dir || "";
    }
  } catch (e) {
    console.warn("workflows-pane refresh:", e);
    state.workflows = [];
  }
  // Auto-select first valid workflow if none selected (or selected one
  // disappeared).
  const validIds = state.workflows.filter((w) => !w.error).map((w) => w.id);
  if (!state.selectedId || !validIds.includes(state.selectedId)) {
    state.selectedId = validIds[0] || null;
  }
  renderSubnav();
  renderMain();
  renderDetail();
}

let mounted = false;
export function mount() {
  if (mounted) return;
  mounted = true;
  refresh();
}

export async function show() {
  await refresh();
}

export function hide() {}
