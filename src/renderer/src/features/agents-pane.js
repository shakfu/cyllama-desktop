// Full-area Agents pane. Single source of truth for agent
// configuration (defaults editor) and workflow file management.
// Replaces the standalone Workflows pane and the right-sidebar
// Agents tab.
//
// Three columns:
//   #agentsPaneSubnav  -- list of six agent types (agent, agent-strict,
//                         agent-contract, agent-plan, agent-reflect,
//                         agent-workflow).
//   #agentsPaneMain    -- common section (tools + max_iterations) plus
//                         the selected type's per-type defaults form.
//                         The agent-workflow row replaces both with
//                         the workflow file list + spec preview +
//                         initial-state + Run + live trace.
//   #agentsPaneDetail  -- per-run summary (workflow row only for now;
//                         general run history is the F.4 TODO entry).
//
// Defaults edited here flow into the per-call modal in F.3, which
// pre-fills with these values so an Enter-press runs the agent
// without further configuration.

import { listCollections } from "../lib/rag.js";
import { getInfo, sidecarFetch } from "../lib/sidecar.js";
import { startJob } from "../lib/jobs.js";


// ---------------------------------------------------------------------------
// Agent types -- left subnav.
// ---------------------------------------------------------------------------

const AGENT_TYPES = [
  { id: "agent",          label: "agent",          desc: "ReAct loop. The model thinks, optionally calls tools, and emits an answer." },
  { id: "agent-strict",   label: "agent-strict",   desc: "Grammar-constrained tool calls. Eliminates malformed-call retries on small models." },
  { id: "agent-contract", label: "agent-contract", desc: "Runs the agent under a named pre/post-condition preset and a violation policy." },
  { id: "agent-plan",     label: "agent-plan",     desc: "Planner emits ordered steps; an executor runs each step in sequence." },
  { id: "agent-reflect",  label: "agent-reflect",  desc: "Worker drafts; critic accepts or asks for revisions. Loops until accepted or budget hits." },
  { id: "agent-workflow", label: "agent-workflow", desc: "Run a workspace-scoped DAG workflow (Python files under <workspace>/workflows/)." },
  // Not an agent type and not a slash command: a script is plain Python
  // run as a job in a child process. It shares this pane because it
  // shares the file-list / run / result shape.
  { id: "scripts", label: "scripts", slash: false, desc: "Run a workspace Python script as a job (files under <workspace>/scripts/)." },
];


// ---------------------------------------------------------------------------
// State -- single source of truth for all agent defaults.
// ---------------------------------------------------------------------------

const state = {
  selectedId: "agent",
  features: { agents: true },
  collections: [],
  // Shared across every agent-type's invocation:
  maxIterations: 10,
  tools: {
    stock_tools: true,
    read_file: { enabled: false, sandbox_dir: "" },
    web_fetch: false,
    search_wikipedia: false,
    quarto_render: false,
    rag_query: { enabled: false, collection_id: "", top_k: 3 },
    semantic_memory: { enabled: false, collection_id: "", namespace: "default", top_k: 5 },
  },
  // agent-strict per-type defaults:
  strict: { format: "json", allowReasoning: false },
  // agent-contract per-type defaults:
  contract: { preset: "none", policy: "OBSERVE" },
  contractPresets: [],
  contractPolicies: [],
  // agent-plan per-type defaults:
  plan: {
    maxSteps: 10,
    stopOnError: true,
    plannerPrompt: "",  // empty -> sidecar uses its default planner prompt
    executorPrompt: "", // empty -> default ReActAgent system prompt
  },
  // agent-reflect per-type defaults:
  reflect: { maxAttempts: 3, acceptanceMarker: "ACCEPT", criticPrompt: "" },
  // agent-workflow row state:
  workflows: [],
  workflowsDir: "",
  selectedWorkflowId: null,
  workflowSpec: null,
  initialState: {},
  liveEvents: [],
  lastResult: null,
  currentJob: null,
  // scripts row state:
  scripts: [],
  scriptsDir: "",
  selectedScriptId: null,
  scriptArgsText: "",
  scriptArgsError: "",
  // Log lines are kept here, not only in the DOM, so switching panes
  // mid-run and coming back rebuilds what already arrived.
  scriptLog: [],
  scriptSeq: 0,
  scriptStatus: "",
  scriptResult: null,
  scriptJob: null,
  runningScriptId: null,
};

// Cap on retained log lines. The sidecar's own ring is 1000; this only
// has to bound the DOM.
const SCRIPT_LOG_MAX = 400;


// ---------------------------------------------------------------------------
// DOM helper
// ---------------------------------------------------------------------------

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

function basename(p) { return p ? p.split(/[\\/]/).pop() : ""; }


// ---------------------------------------------------------------------------
// Shared tools block -- common across every non-workflow agent type.
// ---------------------------------------------------------------------------

function ragCollectionSelect(targetTool /* "rag_query" | "semantic_memory" */) {
  const tool = state.tools[targetTool];
  const sel = el("select", { class: "dp-select" });
  sel.appendChild(el("option", { value: "" }, "Pick a collection..."));
  for (const c of state.collections) {
    const opt = el("option", { value: c.id }, `${c.name} · ${c.chunk_count} chunks`);
    if (c.id === tool.collection_id) opt.selected = true;
    sel.appendChild(opt);
  }
  sel.addEventListener("change", () => { tool.collection_id = sel.value || ""; });
  return sel;
}

function buildToolsBlock() {
  // Stock cyllama tools group: calculator + current_time + word_count.
  // Auto-injected as a baseline so the agent always has cheap primitives
  // available. Turn off when a task-specific tool (e.g. quarto_render)
  // is the real target -- the stock tools can tempt small models away
  // from it.
  const stock = el("label", { class: "ag-tool-row" },
    el("input", {
      type: "checkbox", checked: state.tools.stock_tools,
      onchange: (e) => { state.tools.stock_tools = e.target.checked; },
    }),
    el("span", {}, "Stock cyllama tools"),
    el("span", { class: "ag-tool-hint" }, "calculator · current_time · word_count"),
  );

  const sandboxLabel = el("span", { class: "ag-tool-path mono" },
    state.tools.read_file.sandbox_dir
      ? basename(state.tools.read_file.sandbox_dir)
      : "(no folder)");
  const sandboxBtn = el("button", {
    type: "button", class: "btn btn-mini",
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

  const webFetch = el("label", { class: "ag-tool-row" },
    el("input", {
      type: "checkbox", checked: state.tools.web_fetch,
      onchange: (e) => {
        if (e.target.checked) {
          if (!confirm("Enable network access for this agent run? The model can fetch arbitrary URLs.")) {
            e.target.checked = false; return;
          }
        }
        state.tools.web_fetch = e.target.checked;
      },
    }),
    el("span", {}, "Web fetch"),
    el("span", { class: "ag-tool-hint" }, "network · off by default"),
  );

  // search_wikipedia: scoped network access (Wikipedia API only). Less
  // dangerous than free-form web_fetch but still off by default.
  const searchWikipedia = el("label", { class: "ag-tool-row" },
    el("input", {
      type: "checkbox", checked: state.tools.search_wikipedia,
      onchange: (e) => { state.tools.search_wikipedia = e.target.checked; },
    }),
    el("span", {}, "Search Wikipedia"),
    el("span", { class: "ag-tool-hint" }, "en.wikipedia.org only"),
  );

  // quarto_render: writes files + runs the ``quarto`` CLI. Strongest
  // trust posture of the lot -- a confirm dialog matches the
  // web_fetch pattern. The model picks the output path at decode
  // time; renderer doesn't sandbox that today.
  const quartoRender = el("label", { class: "ag-tool-row" },
    el("input", {
      type: "checkbox", checked: state.tools.quarto_render,
      onchange: (e) => {
        if (e.target.checked) {
          if (!confirm("Enable Quarto rendering? The agent can write files to your filesystem and run the quarto CLI.")) {
            e.target.checked = false; return;
          }
        }
        state.tools.quarto_render = e.target.checked;
      },
    }),
    el("span", {}, "Quarto render"),
    el("span", { class: "ag-tool-hint" }, "writes files · off by default"),
  );

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
    el("div", { class: "ag-tool-sub" }, ragCollectionSelect("rag_query")),
  );

  const children = [stock, readFile, webFetch];
  if (state.features["agents.search_wikipedia"]) {
    children.push(searchWikipedia);
  }
  if (state.features["agents.quarto_render"]) {
    children.push(quartoRender);
  }
  children.push(ragQuery);
  if (state.features["agents.memory"]) {
    const memNs = el("input", {
      type: "text", class: "dp-input", id: "ag-memory-ns",
      value: state.tools.semantic_memory.namespace, placeholder: "default",
    });
    memNs.addEventListener("input", () => {
      state.tools.semantic_memory.namespace = memNs.value.trim() || "default";
    });

    const memory = el("div", { class: "ag-tool-row-stack" },
      el("label", { class: "ag-tool-row" },
        el("input", {
          type: "checkbox", id: "ag-memory-enable",
          checked: state.tools.semantic_memory.enabled,
          onchange: (e) => { state.tools.semantic_memory.enabled = e.target.checked; },
        }),
        el("span", {}, "Semantic memory"),
        el("span", { class: "ag-tool-hint" }, "remember/recall facts across runs"),
      ),
      el("div", { class: "ag-tool-sub" },
        el("div", { class: "ag-row" },
          el("label", { class: "ag-label" }, "Collection"),
          ragCollectionSelect("semantic_memory"),
        ),
        el("div", { class: "ag-row" },
          el("label", { class: "ag-label" }, "Namespace"),
          memNs,
        ),
      ),
    );
    children.push(memory);
  }

  return el("div", { class: "ag-tools" }, ...children);
}

function buildCommonSection() {
  // Tools + max iterations -- the per-run knobs every agent variant
  // shares. Rendered above the per-type form on every non-workflow row.
  const iterInput = el("input", {
    type: "number", class: "dp-input", id: "ag-max-iterations",
    min: 1, max: 50, step: 1, value: state.maxIterations,
  });
  iterInput.addEventListener("input", () => {
    const n = parseInt(iterInput.value, 10);
    if (Number.isFinite(n)) state.maxIterations = Math.max(1, Math.min(50, n));
  });

  return el("section", { class: "mp-section" },
    el("h3", {}, "Common"),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Max iterations"),
      iterInput,
    ),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Tools"),
      buildToolsBlock(),
    ),
  );
}


// ---------------------------------------------------------------------------
// Per-type defaults forms
// ---------------------------------------------------------------------------

function buildStrictSection() {
  const fmtSel = el("select", { class: "dp-select", id: "ag-strict-format" });
  for (const f of ["json", "json_array", "function_call"]) {
    const opt = el("option", { value: f }, f);
    if (f === state.strict.format) opt.selected = true;
    fmtSel.appendChild(opt);
  }
  fmtSel.addEventListener("change", () => { state.strict.format = fmtSel.value; });

  const reasonCb = el("input", {
    type: "checkbox", id: "ag-strict-allow-reasoning",
    checked: state.strict.allowReasoning,
    onchange: (e) => { state.strict.allowReasoning = e.target.checked; },
  });

  return el("section", { class: "mp-section" },
    el("h3", {}, "Strict / constrained"),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Format"),
      fmtSel,
    ),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Allow reasoning"),
      el("label", { class: "ag-tool-row" }, reasonCb,
        el("span", { class: "ag-tool-hint" }, "permit a reasoning field on tool calls")),
    ),
  );
}

function buildContractSection() {
  if (!state.features["agents.contract"]) {
    return el("section", { class: "mp-section" },
      el("h3", {}, "Contract"),
      el("p", { class: "ag-hint" }, "ContractAgent not available in this cyllama build."),
    );
  }
  const presetSel = el("select", { class: "dp-select", id: "ag-contract-preset" });
  const presets = state.contractPresets.length ? state.contractPresets : ["none"];
  for (const p of presets) {
    const opt = el("option", { value: p }, p);
    if (p === state.contract.preset) opt.selected = true;
    presetSel.appendChild(opt);
  }
  presetSel.addEventListener("change", () => { state.contract.preset = presetSel.value || "none"; });

  const policySel = el("select", { class: "dp-select", id: "ag-contract-policy" });
  const policies = state.contractPolicies.length
    ? state.contractPolicies : ["IGNORE", "OBSERVE", "ENFORCE", "QUICK_ENFORCE"];
  for (const p of policies) {
    const opt = el("option", { value: p }, p);
    if (p === state.contract.policy) opt.selected = true;
    policySel.appendChild(opt);
  }
  policySel.addEventListener("change", () => { state.contract.policy = policySel.value || "OBSERVE"; });

  return el("section", { class: "mp-section" },
    el("h3", {}, "Contract"),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Preset"), presetSel),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Policy"), policySel),
  );
}

function buildPlanSection() {
  const maxSteps = el("input", {
    type: "number", class: "dp-input", id: "ag-plan-max-steps",
    min: 1, max: 20, step: 1, value: state.plan.maxSteps,
  });
  maxSteps.addEventListener("input", () => {
    const n = parseInt(maxSteps.value, 10);
    if (Number.isFinite(n)) state.plan.maxSteps = Math.max(1, Math.min(20, n));
  });
  const stopOnError = el("input", {
    type: "checkbox", id: "ag-plan-stop-on-error",
    checked: state.plan.stopOnError,
    onchange: (e) => { state.plan.stopOnError = e.target.checked; },
  });
  const plannerPrompt = el("textarea", {
    class: "dp-input", id: "ag-plan-planner-prompt", rows: 3,
    placeholder: "Custom planner prompt (blank = sidecar default).",
  });
  plannerPrompt.value = state.plan.plannerPrompt;
  plannerPrompt.addEventListener("input", () => {
    state.plan.plannerPrompt = plannerPrompt.value;
  });
  const executorPrompt = el("textarea", {
    class: "dp-input", id: "ag-plan-executor-prompt", rows: 3,
    placeholder: "Custom executor prompt (blank = default).",
  });
  executorPrompt.value = state.plan.executorPrompt;
  executorPrompt.addEventListener("input", () => {
    state.plan.executorPrompt = executorPrompt.value;
  });
  return el("section", { class: "mp-section" },
    el("h3", {}, "Plan / execute"),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Max steps"), maxSteps),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Stop on error"),
      el("label", { class: "ag-tool-row" }, stopOnError,
        el("span", { class: "ag-tool-hint" }, "abort after the first failed step"))),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Planner prompt"), plannerPrompt),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Executor prompt"), executorPrompt),
  );
}

function buildReflectSection() {
  if (!state.features["agents.reflect"]) {
    return el("section", { class: "mp-section" },
      el("h3", {}, "Reflection"),
      el("p", { class: "ag-hint" }, "ReflectionLoop not available in this cyllama build."),
    );
  }
  const attempts = el("input", {
    type: "number", class: "dp-input", id: "ag-reflect-attempts",
    min: 1, max: 10, step: 1, value: state.reflect.maxAttempts,
  });
  attempts.addEventListener("input", () => {
    const n = parseInt(attempts.value, 10);
    if (Number.isFinite(n)) state.reflect.maxAttempts = Math.max(1, Math.min(10, n));
  });
  const marker = el("input", {
    type: "text", class: "dp-input", id: "ag-reflect-marker",
    value: state.reflect.acceptanceMarker,
  });
  marker.addEventListener("input", () => {
    state.reflect.acceptanceMarker = marker.value.trim() || "ACCEPT";
  });
  const critic = el("textarea", {
    class: "dp-input", id: "ag-reflect-critic-prompt", rows: 3,
    placeholder: "Custom critic prompt (blank = use sidecar default)",
  });
  critic.value = state.reflect.criticPrompt;
  critic.addEventListener("input", () => {
    state.reflect.criticPrompt = critic.value;
  });
  return el("section", { class: "mp-section" },
    el("h3", {}, "Reflection"),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Max attempts"), attempts),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Accept marker"), marker),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Critic prompt"), critic),
  );
}

const TYPE_FORMS = {
  "agent": () => null,
  "agent-strict": buildStrictSection,
  "agent-contract": buildContractSection,
  "agent-plan": buildPlanSection,
  "agent-reflect": buildReflectSection,
};


// ---------------------------------------------------------------------------
// Render: subnav, main, detail
// ---------------------------------------------------------------------------

function renderSubnav() {
  const host = document.getElementById("agentsPaneSubnav");
  if (!host) return;
  host.innerHTML = "";
  host.appendChild(el("div", { class: "mp-subnav-head" }, "Agents"));
  for (const t of AGENT_TYPES) {
    const row = el("button", {
      type: "button",
      class: "mp-subnav-row" + (t.id === state.selectedId ? " active" : ""),
      id: `agt-row-${t.id}`,
      "data-agent-type": t.id,
      onclick: () => selectType(t.id),
    }, el("span", {}, (t.slash === false ? "" : "/") + t.label));
    host.appendChild(row);
  }
}

function selectType(id) {
  if (state.selectedId === id) return;
  state.selectedId = id;
  renderSubnav();
  renderMain();
  renderDetail();
  if (id === "agent-workflow") refreshWorkflows();
  if (id === "scripts") refreshScripts();
}

function renderMain() {
  const host = document.getElementById("agentsPaneMain");
  if (!host) return;
  host.innerHTML = "";

  const t = AGENT_TYPES.find((x) => x.id === state.selectedId) || AGENT_TYPES[0];
  host.appendChild(el("div", { class: "mp-main-head" },
    el("h2", {}, (t.slash === false ? "" : "/") + t.label),
    el("p", { class: "mp-main-doc" }, t.desc),
  ));

  if (t.id === "agent-workflow") {
    renderWorkflowMain(host);
    return;
  }
  if (t.id === "scripts") {
    renderScriptsMain(host);
    return;
  }
  // Common (tools + max_iterations) for every other agent type.
  host.appendChild(buildCommonSection());
  // Per-type-specific section (may be null for plain /agent).
  const typeForm = TYPE_FORMS[t.id];
  const typeSection = typeForm ? typeForm() : null;
  if (typeSection) host.appendChild(typeSection);
}

function renderDetail() {
  const host = document.getElementById("agentsPaneDetail");
  if (!host) return;
  host.innerHTML = "";
  host.appendChild(el("div", { class: "mp-detail-head" }, el("h3", {}, "Last run")));

  if (state.selectedId === "agent-workflow") {
    renderWorkflowDetail(host);
    return;
  }
  if (state.selectedId === "scripts") {
    renderScriptsDetail(host);
    return;
  }
  host.appendChild(el("div", { class: "rt-placeholder" },
    el("p", {}, "Per-type run history lands in a later phase. Recent runs render inline in chat for now."),
  ));
}


// ===========================================================================
// agent-workflow row: discovery + spec + initial-state + Run + trace
// ===========================================================================

function renderWorkflowMain(host) {
  const listSection = el("section", { class: "mp-section" },
    el("h3", {}, "Workflow files"));
  if (!state.workflows.length) {
    listSection.appendChild(el("div", { class: "rt-placeholder" },
      el("p", {}, "No workflow files found."),
      el("p", { class: "ag-hint" },
        "Author Python files under ",
        el("code", {}, state.workflowsDir || "<workspace>/workflows/"),
        " exporting ", el("code", {}, "flow"), " or ", el("code", {}, "make_flow()"), "."),
    ));
  } else {
    const fileList = el("div", { class: "agt-workflow-list" });
    for (const w of state.workflows) {
      const row = el("button", {
        type: "button",
        class: "mp-subnav-row" + (w.id === state.selectedWorkflowId ? " active" : ""),
        id: `wf-item-${w.id}`,
        "data-workflow-id": w.id,
        disabled: w.error ? true : undefined,
      },
        el("span", {}, w.id),
        el("span", { class: "mp-subnav-count" }, w.error ? "error" : (w.entry || "")),
      );
      if (!w.error) row.addEventListener("click", () => selectWorkflow(w.id));
      fileList.appendChild(row);
    }
    listSection.appendChild(fileList);
  }
  listSection.appendChild(el("div", { style: "margin-top: 8px;" },
    el("button", { class: "btn btn-mini", id: "wf-refresh",
      onclick: () => refreshWorkflows() }, "Refresh"),
  ));
  host.appendChild(listSection);

  if (!state.selectedWorkflowId) return;
  const wf = state.workflows.find((w) => w.id === state.selectedWorkflowId);
  if (!wf) return;

  if (wf.doc) {
    host.appendChild(el("section", { class: "mp-section" },
      el("h3", {}, wf.id),
      el("p", { class: "mp-main-doc" }, wf.doc),
    ));
  }
  const specHost = el("section", { class: "mp-section", id: "wf-spec" },
    el("h3", {}, "Plan"),
    el("div", { class: "rt-placeholder" }, "loading..."),
  );
  host.appendChild(specHost);

  const inputs = wf.inputs_required || [];
  const formHost = el("section", { class: "mp-section", id: "wf-form" },
    el("h3", {}, "Initial state"));
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
        el("label", { class: "ag-label" }, key), input));
    }
  } else {
    formHost.appendChild(el("p", { class: "ag-hint" }, "No inputs required."));
  }
  formHost.appendChild(el("div", { class: "ag-row" },
    el("button", { type: "button", class: "btn btn-primary", id: "wf-run",
      onclick: () => runSelectedWorkflow() }, "Run")));
  host.appendChild(formHost);

  host.appendChild(el("section", { class: "mp-section", id: "wf-trace" },
    el("h3", {}, "Trace"),
    el("div", { class: "agent-events", id: "wf-events" })));

  loadWorkflowSpec(wf.id, specHost);
}

async function loadWorkflowSpec(id, host) {
  try {
    const r = await sidecarFetch(`/workflows/${id}/spec`);
    if (!r.ok) {
      host.innerHTML = "";
      host.appendChild(el("h3", {}, "Plan"));
      host.appendChild(el("p", { class: "ag-error" }, `Spec load failed: ${r.status}`));
      return;
    }
    const spec = await r.json();
    state.workflowSpec = spec;
    host.innerHTML = "";
    host.appendChild(el("h3", {}, "Plan"));
    host.appendChild(el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Entry"),
      el("code", {}, spec.entry || "(none)")));
    host.appendChild(el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Exits"),
      el("code", {}, (spec.exits || []).join(", ") || "(derived)")));
    host.appendChild(el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Levels"),
      el("code", {}, (spec.levels || []).map((lvl) => `[${lvl.join(", ")}]`).join(" -> "))));
    if (spec.mermaid) {
      host.appendChild(el("details", { class: "mp-mermaid" },
        el("summary", {}, "Mermaid source"),
        el("pre", {}, el("code", {}, spec.mermaid))));
    }
  } catch (e) {
    host.innerHTML = "";
    host.appendChild(el("h3", {}, "Plan"));
    host.appendChild(el("p", { class: "ag-error" }, `Spec load failed: ${e.message || e}`));
  }
}

function renderWorkflowDetail(host) {
  if (!state.lastResult) {
    host.appendChild(el("div", { class: "rt-placeholder" },
      el("p", {}, "Run a workflow to see its result here.")));
    return;
  }
  const r = state.lastResult;
  host.appendChild(el("div", { class: "ag-row" },
    el("label", { class: "ag-label" }, "Status"),
    el("span", {}, r.success ? "succeeded" : "failed")));
  if (r.answer) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Answer"), el("pre", {}, r.answer)));
  }
  if (r.error) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Error"), el("pre", { class: "ag-error" }, r.error)));
  }
  if (r.state && Object.keys(r.state).length) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Final state"), el("pre", {}, JSON.stringify(r.state, null, 2))));
  }
}

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

function selectWorkflow(id) {
  if (state.selectedWorkflowId === id) return;
  state.selectedWorkflowId = id;
  state.initialState = {};
  state.workflowSpec = null;
  renderMain();
}

async function refreshWorkflows() {
  try {
    const r = await sidecarFetch("/workflows");
    if (!r.ok) {
      state.workflows = []; state.workflowsDir = "";
    } else {
      const body = await r.json();
      state.workflows = body.workflows || [];
      state.workflowsDir = body.dir || "";
    }
  } catch (e) {
    console.warn("agents-pane refreshWorkflows:", e);
    state.workflows = [];
  }
  const validIds = state.workflows.filter((w) => !w.error).map((w) => w.id);
  if (!state.selectedWorkflowId || !validIds.includes(state.selectedWorkflowId)) {
    state.selectedWorkflowId = validIds[0] || null;
  }
  if (state.selectedId === "agent-workflow") {
    renderMain();
    renderDetail();
  }
}

async function runSelectedWorkflow() {
  if (!state.selectedWorkflowId || state.currentJob) return;
  const runBtn = document.getElementById("wf-run");
  if (runBtn) runBtn.disabled = true;
  state.liveEvents = []; state.lastResult = null;
  const eventsHost = document.getElementById("wf-events");
  if (eventsHost) eventsHost.innerHTML = "";

  let job;
  try {
    job = await startJob("workflow/run", {
      workflow_id: state.selectedWorkflowId,
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
      const item = { event_type: ev.event_type, content: ev.content, metadata: ev.metadata || {} };
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


// ===========================================================================
// scripts row: file list + args + Run / Cancel + streamed log
//
// A script is a child process, so unlike the agent rows there is real
// output to stream and a real cancel. Log lines live in state as well as
// the DOM: leaving the pane mid-run and coming back rebuilds them, and
// /jobs/<id>/log fills any gap if the event stream was interrupted.
// ===========================================================================

function renderScriptsMain(host) {
  const listSection = el("section", { class: "mp-section" },
    el("h3", {}, "Script files"));
  if (!state.scripts.length) {
    listSection.appendChild(el("div", { class: "rt-placeholder" },
      el("p", {}, "No script files found."),
      el("p", { class: "ag-hint" },
        "Author Python files under ",
        el("code", {}, state.scriptsDir || "<workspace>/scripts/"),
        ". Import ", el("code", {}, "cyllama_desktop"), " to reach the loaded model."),
    ));
  } else {
    const fileList = el("div", { class: "agt-workflow-list" });
    for (const sc of state.scripts) {
      const isRunning = state.runningScriptId === sc.id;
      const row = el("div", {
        class: "mp-subnav-row scr-row" + (sc.id === state.selectedScriptId ? " active" : ""),
        "data-script-id": sc.id,
      });
      const select = el("button", {
        type: "button", class: "scr-select",
        id: `scr-item-${sc.id}`,
        disabled: sc.error ? true : undefined,
        title: sc.error || sc.doc || "",
      },
        el("span", {}, sc.id),
        el("span", { class: "mp-subnav-count" }, sc.error ? "error" : `${sc.bytes} B`),
      );
      if (!sc.error) select.addEventListener("click", () => selectScript(sc.id));
      row.appendChild(select);
      // Run where the script is, rather than past the docstring. Running
      // an unselected row selects it first, which clears the arguments
      // box -- so the arguments you can see are always the ones used.
      if (!sc.error) {
        row.appendChild(el("button", {
          type: "button", class: "scr-run",
          id: `scr-run-${sc.id}`,
          disabled: (state.scriptJob && !isRunning) ? true : undefined,
          title: isRunning ? "Cancel this run" : "Run this script",
          onclick: () => (isRunning ? cancelScript() : runScript(sc.id)),
        }, isRunning ? "Cancel" : "Run"));
      }
      fileList.appendChild(row);
    }
    listSection.appendChild(fileList);
  }
  listSection.appendChild(el("div", { style: "margin-top: 8px;" },
    el("button", { class: "btn btn-mini", id: "scr-refresh",
      onclick: () => refreshScripts() }, "Refresh"),
  ));
  host.appendChild(listSection);

  if (!state.selectedScriptId) return;
  const sc = state.scripts.find((x) => x.id === state.selectedScriptId);
  if (!sc) return;

  if (sc.doc) {
    host.appendChild(el("section", { class: "mp-section" },
      el("h3", {}, sc.id),
      el("p", { class: "mp-main-doc" }, sc.doc),
    ));
  }

  const argsArea = el("textarea", {
    class: "dp-input scr-args", id: "scr-args", rows: 4,
    spellcheck: "false",
    placeholder: "{}",
  });
  argsArea.value = state.scriptArgsText;
  argsArea.addEventListener("input", () => {
    state.scriptArgsText = argsArea.value;
    state.scriptArgsError = "";
    const err = document.getElementById("scr-args-error");
    if (err) err.textContent = "";
  });

  const running = Boolean(state.scriptJob);
  const formHost = el("section", { class: "mp-section", id: "scr-form" },
    el("h3", {}, "Arguments"),
    el("p", { class: "ag-hint" }, "A JSON object, handed to the script on stdin. Leave empty for none."),
    argsArea,
    el("p", { class: "ag-error", id: "scr-args-error" }, state.scriptArgsError || ""),
    el("div", { class: "ag-row" },
      el("button", {
        type: "button", class: "btn btn-primary", id: "scr-run",
        disabled: running ? true : undefined,
        onclick: () => runSelectedScript(),
      }, "Run"),
      el("button", {
        type: "button", class: "btn btn-mini", id: "scr-cancel",
        disabled: running ? undefined : true,
        onclick: () => cancelScript(),
      }, "Cancel"),
      el("span", { class: "ag-hint", id: "scr-status" }, state.scriptStatus || ""),
    ),
  );
  host.appendChild(formHost);

  const logHost = el("div", { class: "agent-events scr-log", id: "scr-log" });
  host.appendChild(el("section", { class: "mp-section", id: "scr-log-section" },
    el("h3", {}, "Output"), logHost));
  for (const line of state.scriptLog) logHost.appendChild(scriptLogRow(line));
  logHost.scrollTop = logHost.scrollHeight;
}

function scriptLogRow(line) {
  return el("div", { class: `ag-event ag-ev-${line.stream === "stderr" ? "error" : "log"}` },
    el("span", { class: "ag-ev-type" }, line.stream || "out"),
    el("span", { class: "ag-ev-content" }, line.message || ""),
  );
}

function pushScriptLog(line) {
  state.scriptLog.push(line);
  if (state.scriptLog.length > SCRIPT_LOG_MAX) {
    state.scriptLog.splice(0, state.scriptLog.length - SCRIPT_LOG_MAX);
  }
  const host = document.getElementById("scr-log");
  if (!host) return;
  host.appendChild(scriptLogRow(line));
  while (host.childElementCount > SCRIPT_LOG_MAX) host.removeChild(host.firstChild);
  host.scrollTop = host.scrollHeight;
}

function setScriptStatus(text) {
  state.scriptStatus = text;
  const node = document.getElementById("scr-status");
  if (node) node.textContent = text;
}

function renderScriptsDetail(host) {
  const r = state.scriptResult;
  if (!r) {
    host.appendChild(el("div", { class: "rt-placeholder" },
      el("p", {}, "Run a script to see its result here.")));
    return;
  }
  host.appendChild(el("div", { class: "ag-row" },
    el("label", { class: "ag-label" }, "Status"),
    el("span", {}, r.error ? "failed" : "succeeded")));
  if (r.error) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Error"), el("pre", { class: "ag-error" }, r.error)));
    return;
  }
  host.appendChild(el("div", { class: "ag-row" },
    el("label", { class: "ag-label" }, "Duration"),
    el("span", {}, `${r.duration_s}s`)));
  if (r.dropped_events) {
    host.appendChild(el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Dropped"),
      el("span", {}, `${r.dropped_events} events`)));
  }
  if (r.result_error) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "result.json"), el("pre", { class: "ag-error" }, r.result_error)));
  }
  if (r.result != null) {
    host.appendChild(el("div", { class: "mp-detail-section" },
      el("h4", {}, "Result"), el("pre", {}, JSON.stringify(r.result, null, 2))));
  }
  const artifacts = r.artifacts || [];
  if (artifacts.length) {
    const list = el("div", { class: "mp-detail-section" }, el("h4", {}, "Artifacts"));
    for (const a of artifacts) {
      list.appendChild(el("div", { class: "ag-row" },
        el("a", {
          href: "#", class: "scr-artifact",
          onclick: (e) => { e.preventDefault(); openScriptArtifact(r.job_id, a.name); },
        }, a.name),
        el("span", { class: "ag-hint" }, `${a.bytes} B`)));
    }
    host.appendChild(list);
  }
}

async function openScriptArtifact(jobId, name) {
  if (!jobId) return;
  try {
    const r = await sidecarFetch(`/artifacts/${jobId}/${encodeURIComponent(name)}`);
    if (!r.ok) return;
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank");
    // Revoke on the next tick; the new context has already taken it.
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  } catch (e) {
    console.warn("agents-pane openScriptArtifact:", e);
  }
}

function selectScript(id) {
  if (state.selectedScriptId === id) return;
  state.selectedScriptId = id;
  state.scriptArgsText = "";
  state.scriptArgsError = "";
  renderMain();
}

async function refreshScripts() {
  try {
    const r = await sidecarFetch("/scripts");
    if (!r.ok) {
      state.scripts = []; state.scriptsDir = "";
    } else {
      const body = await r.json();
      state.scripts = body.scripts || [];
      state.scriptsDir = body.dir || "";
    }
  } catch (e) {
    console.warn("agents-pane refreshScripts:", e);
    state.scripts = [];
  }
  const validIds = state.scripts.filter((x) => !x.error).map((x) => x.id);
  if (!state.selectedScriptId || !validIds.includes(state.selectedScriptId)) {
    state.selectedScriptId = validIds[0] || null;
  }
  if (state.selectedId === "scripts") {
    renderMain();
    renderDetail();
  }
}

// Fill gaps left by an interrupted event stream. The sidecar retains
// recent progressive events per job; ``after`` asks for anything newer
// than the last sequence number this pane rendered.
async function catchUpScriptLog() {
  const job = state.scriptJob;
  if (!job) return;
  try {
    const r = await sidecarFetch(`/jobs/${job.id}/log?after=${state.scriptSeq}`);
    if (!r.ok) return;
    const body = await r.json();
    for (const ev of body.events || []) applyScriptEvent(ev);
  } catch (e) {
    console.warn("agents-pane catchUpScriptLog:", e);
  }
}

function applyScriptEvent(ev) {
  if (typeof ev.seq === "number") {
    if (ev.seq <= state.scriptSeq) return;   // already rendered
    state.scriptSeq = ev.seq;
  }
  if (ev.type === "log") {
    pushScriptLog({ stream: ev.stream || "stdout", message: ev.message || "" });
  } else if (ev.type === "progress") {
    const pct = typeof ev.value === "number" ? `${Math.round(ev.value * 100)}% ` : "";
    setScriptStatus(pct + (ev.message || ""));
  }
}

// Row Run: select the script if it is not current, then run it. The
// selection change clears the arguments box, so a row button never runs
// with arguments the user cannot see.
async function runScript(id) {
  if (state.scriptJob) return;
  if (id !== state.selectedScriptId) selectScript(id);
  await runSelectedScript();
}

async function runSelectedScript() {
  if (!state.selectedScriptId || state.scriptJob) return;

  let args = {};
  const raw = (state.scriptArgsText || "").trim();
  if (raw) {
    try {
      args = JSON.parse(raw);
    } catch (e) {
      state.scriptArgsError = `Invalid JSON: ${e.message}`;
      renderMain();
      return;
    }
    if (args === null || typeof args !== "object" || Array.isArray(args)) {
      state.scriptArgsError = "Arguments must be a JSON object.";
      renderMain();
      return;
    }
  }

  state.scriptLog = [];
  state.scriptSeq = 0;
  state.scriptResult = null;
  state.scriptArgsError = "";
  setScriptStatus("starting...");
  renderDetail();

  let job;
  try {
    job = await startJob("script/run", { script_id: state.selectedScriptId, args });
  } catch (e) {
    state.scriptResult = { error: e.message || String(e) };
    setScriptStatus("");
    renderMain();
    renderDetail();
    return;
  }
  state.scriptJob = job;
  state.runningScriptId = state.selectedScriptId;
  renderMain();
  setScriptStatus("running");
  // The event stream attaches a moment after the job starts, and the
  // sidecar does not queue progressive events for an absent subscriber.
  // Pull anything emitted in that window from the ring.
  catchUpScriptLog();

  const off = job.onEvent((ev) => {
    if (ev.type === "log" || ev.type === "progress") {
      applyScriptEvent(ev);
    } else if (ev.type === "result") {
      state.scriptResult = { ...(ev.result || {}), job_id: job.id };
      renderDetail();
    } else if (ev.type === "error") {
      state.scriptResult = { error: ev.message || "unknown error", job_id: job.id };
      renderDetail();
    } else if (ev.type === "cancelled") {
      setScriptStatus("cancelled");
    }
  });
  try { await job.done; }
  catch (e) {
    if (!state.scriptResult) {
      state.scriptResult = { error: e.message || String(e), job_id: job.id };
      renderDetail();
    }
  } finally {
    off();
    state.scriptJob = null;
    state.runningScriptId = null;
    setScriptStatus(state.scriptResult && state.scriptResult.error ? "failed" : "done");
    renderMain();
  }
}

async function cancelScript() {
  const job = state.scriptJob;
  if (!job) return;
  setScriptStatus("cancelling...");
  await job.cancel();
}


// ---------------------------------------------------------------------------
// Public getters -- consumed by main.js's slash handlers.
// ---------------------------------------------------------------------------

function buildToolsSpec() {
  const t = state.tools;
  const out = {};
  // ``stock_tools`` defaults true on the sidecar; only send when off
  // so old chats persisted with no stock_tools key still get the
  // sensible default. Off means "drop the auto-injected stock group".
  if (t.stock_tools === false) out.stock_tools = false;
  if (t.read_file.enabled && t.read_file.sandbox_dir) {
    out.read_file = { sandbox_dir: t.read_file.sandbox_dir };
  }
  if (t.web_fetch) out.web_fetch = true;
  if (t.search_wikipedia) out.search_wikipedia = true;
  if (t.quarto_render) out.quarto_render = true;
  if (t.rag_query.enabled && t.rag_query.collection_id) {
    out.rag_query = { collection_id: t.rag_query.collection_id, top_k: t.rag_query.top_k || 3 };
  }
  if (t.semantic_memory.enabled && t.semantic_memory.collection_id) {
    out.semantic_memory = {
      collection_id: t.semantic_memory.collection_id,
      namespace: t.semantic_memory.namespace || "default",
      top_k: t.semantic_memory.top_k || 5,
    };
  }
  return out;
}

export function getAgentConfig() {
  return { tools: buildToolsSpec(), maxIterations: state.maxIterations };
}

export function getStrictConfig() {
  return { format: state.strict.format || "json", allowReasoning: !!state.strict.allowReasoning };
}

export function getContractConfig() {
  return {
    preset: state.contract.preset || "none",
    policy: state.contract.policy || "OBSERVE",
  };
}

export function getPlanConfig() {
  return {
    maxSteps: state.plan.maxSteps || 10,
    stopOnError: !!state.plan.stopOnError,
    plannerPrompt: state.plan.plannerPrompt || "",
    executorPrompt: state.plan.executorPrompt || "",
  };
}

export function getReflectConfig() {
  return {
    maxAttempts: state.reflect.maxAttempts || 3,
    acceptanceMarker: state.reflect.acceptanceMarker || "ACCEPT",
    criticPrompt: state.reflect.criticPrompt || "",
  };
}

export function validateAgentConfig() {
  if (state.tools.read_file.enabled && !state.tools.read_file.sandbox_dir) {
    return "read_file needs a sandbox folder.";
  }
  if (state.tools.rag_query.enabled && !state.tools.rag_query.collection_id) {
    return "rag_query needs a collection.";
  }
  if (state.tools.semantic_memory.enabled && !state.tools.semantic_memory.collection_id) {
    return "semantic_memory needs a collection.";
  }
  return null;
}


// ---------------------------------------------------------------------------
// Navigation helpers exposed for /agent-workflow slash.
// ---------------------------------------------------------------------------

export async function selectWorkflowById(id) {
  selectType("agent-workflow");
  await refreshWorkflows();
  if (state.workflows.some((w) => w.id === id && !w.error)) {
    selectWorkflow(id);
  }
}

export async function selectWorkflowRow() {
  selectType("agent-workflow");
  await refreshWorkflows();
}


// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

async function refresh() {
  try {
    const [c, info] = await Promise.all([
      listCollections().catch(() => ({ collections: [] })),
      getInfo().catch(() => ({})),
    ]);
    state.collections = c.collections || [];
    state.features = info.features || {};
  } catch (e) {
    console.warn("agents-pane refresh:", e);
  }
  if (state.features["agents.contract"]) {
    try {
      const r = await sidecarFetch("/info/contract-presets");
      if (r.ok) {
        const body = await r.json();
        state.contractPresets = body.presets || [];
        state.contractPolicies = body.policies || [];
      }
    } catch (e) {
      console.warn("agents-pane contract-presets:", e);
    }
  }
}

let mounted = false;
export function mount() {
  if (mounted) return;
  mounted = true;
  refresh().then(() => {
    renderSubnav(); renderMain(); renderDetail();
  });
}

// Called by main.js on /info-driven feature flag changes.
export function applyVisibility(features) {
  state.features = features || state.features;
  if (!mounted) return;
  renderSubnav(); renderMain(); renderDetail();
}

export async function show() {
  await refresh();
  renderSubnav(); renderMain(); renderDetail();
  if (state.selectedId === "agent-workflow") {
    await refreshWorkflows();
  }
  if (state.selectedId === "scripts") {
    await refreshScripts();
    await catchUpScriptLog();
  }
}

export function hide() {}
