// Agents tab. Sidebar config surface for agent runs. Run/trace/answer
// rendering moved to the chat composer (`/agent <task>`); this tab now
// only owns tool toggles and max_iterations.
//
// Public API:
//   - mount(): build the sidebar UI.
//   - applyVisibility(features): re-render on feature-flag change.
//   - getAgentConfig(): { tools, maxIterations } for `/agent` runs.
//   - validateAgentConfig(): null if ok, else an error string for the
//     caller to surface.

import { listCollections } from "../lib/rag.js";
import { getInfo } from "../lib/sidecar.js";

const state = {
  maxIterations: 10,
  tools: {
    calculator: true,
    read_file: { enabled: false, sandbox_dir: "" },
    web_fetch: false,
    rag_query: { enabled: false, collection_id: "", top_k: 3 },
  },
  features: { agents: true },
  collections: [],
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
  const calc = el("label", { class: "ag-tool-row" },
    el("input", { type: "checkbox", checked: state.tools.calculator,
      onchange: (e) => { state.tools.calculator = e.target.checked; } }),
    el("span", {}, "Calculator"),
    el("span", { class: "ag-tool-hint" }, "arithmetic"),
  );

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

export function getAgentConfig() {
  return {
    tools: buildToolsSpec(),
    maxIterations: state.maxIterations,
  };
}

// Surface tool-config errors before the sidecar 400s. Returns null when ok.
export function validateAgentConfig() {
  if (state.tools.read_file.enabled && !state.tools.read_file.sandbox_dir) {
    return "read_file needs a sandbox folder.";
  }
  if (state.tools.rag_query.enabled && !state.tools.rag_query.collection_id) {
    return "rag_query needs a collection.";
  }
  return null;
}

async function refresh() {
  try {
    const [c, info] = await Promise.all([
      listCollections().catch(() => ({ collections: [] })),
      getInfo().catch(() => ({})),
    ]);
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

  const iterInput = el("input", {
    type: "number", class: "dp-input", min: 1, max: 50, step: 1,
    value: state.maxIterations,
  });
  iterInput.addEventListener("input", () => {
    const n = parseInt(iterInput.value, 10);
    if (Number.isFinite(n)) state.maxIterations = Math.max(1, Math.min(50, n));
  });

  const config = el("section", { class: "rt-section" },
    el("div", { class: "rt-section-head" }, el("h3", {}, "Agents")),
    el("div", { class: "rt-placeholder" },
      el("p", {}, "Run an agent from the chat composer with ", el("code", {}, "/agent <task>"), ". Settings below apply to those runs."),
    ),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Max iterations"),
      iterInput,
    ),
    el("div", { class: "ag-row" },
      el("label", { class: "ag-label" }, "Tools"),
      buildToolsBlock(),
    ),
  );

  host.appendChild(config);
}

let mounted = false;
export function mount() {
  if (mounted) return;
  mounted = true;
  build();
}

export function applyVisibility(features) {
  state.features = features || state.features;
  if (!mounted) return;
  build();
}
