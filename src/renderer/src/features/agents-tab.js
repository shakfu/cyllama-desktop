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
import { getInfo, sidecarFetch } from "../lib/sidecar.js";

const state = {
  maxIterations: 10,
  tools: {
    calculator: true,
    read_file: { enabled: false, sandbox_dir: "" },
    web_fetch: false,
    rag_query: { enabled: false, collection_id: "", top_k: 3 },
    // Phase E: semantic_memory exposes remember/recall as two tools to
    // the agent, backed by a RAG collection + a namespace string. Gated
    // by features['agents.memory']; the row stays hidden when off.
    semantic_memory: { enabled: false, collection_id: "", namespace: "default", top_k: 5 },
  },
  // Contract settings shared across /contract runs. Mirrors the sidecar's
  // /info/contract-presets surface; rendered as <select> rows when the
  // ``agents.contract`` feature flag is on.
  contract: {
    preset: "none",
    policy: "OBSERVE",
  },
  contractPresets: [],
  contractPolicies: [],
  // Reflection settings for /reflect runs. Defaults match the sidecar's.
  reflect: {
    maxAttempts: 3,
    acceptanceMarker: "ACCEPT",
    criticPrompt: "",  // empty -> sidecar uses its default
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

  // Phase E: Semantic memory (remember + recall). Gated by
  // features['agents.memory']; not rendered when the bundle is missing
  // cyllama.agents.SemanticMemory.
  const children = [calc, readFile, webFetch, ragQuery];
  if (state.features["agents.memory"]) {
    const memColl = el("select", { class: "dp-select", id: "ag-memory-coll" });
    memColl.appendChild(el("option", { value: "" }, "Pick a collection..."));
    for (const c of state.collections) {
      const opt = el("option", { value: c.id }, `${c.name} · ${c.chunk_count} chunks`);
      if (c.id === state.tools.semantic_memory.collection_id) opt.selected = true;
      memColl.appendChild(opt);
    }
    memColl.addEventListener("change", () => {
      state.tools.semantic_memory.collection_id = memColl.value || "";
    });

    const memNs = el("input", {
      type: "text", class: "dp-input", id: "ag-memory-ns",
      value: state.tools.semantic_memory.namespace,
      placeholder: "default",
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
          memColl,
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
  return {
    tools: buildToolsSpec(),
    maxIterations: state.maxIterations,
  };
}

// Used by /contract slash: returns the preset + policy chosen in the
// Contract row of the Agents tab. Defaults are safe (no rules, observe
// violations) so the slash works before the user touches the row.
export function getContractConfig() {
  return {
    preset: state.contract.preset || "none",
    policy: state.contract.policy || "OBSERVE",
  };
}

// Used by /reflect slash: returns max_attempts + acceptance_marker +
// optional critic prompt. The sidecar applies its own default critic
// prompt when criticPrompt is empty.
export function getReflectConfig() {
  return {
    maxAttempts: state.reflect.maxAttempts || 3,
    acceptanceMarker: state.reflect.acceptanceMarker || "ACCEPT",
    criticPrompt: state.reflect.criticPrompt || "",
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
  if (state.tools.semantic_memory.enabled && !state.tools.semantic_memory.collection_id) {
    return "semantic_memory needs a collection.";
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
  // Best-effort: load the contract preset/policy lists from the sidecar
  // when the feature is available. Failure is silent -- the slash will
  // fall back to the default preset.
  if (state.features["agents.contract"]) {
    try {
      const r = await sidecarFetch("/info/contract-presets");
      if (r.ok) {
        const body = await r.json();
        state.contractPresets = body.presets || [];
        state.contractPolicies = body.policies || [];
      }
    } catch (e) {
      console.warn("agents-tab contract-presets:", e);
    }
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

  // Contract row -- only rendered when the bundle includes ContractAgent.
  // Two <select>s feeding ``state.contract``; the /contract slash reads
  // them via :func:`getContractConfig`.
  if (state.features["agents.contract"]) {
    const presetSel = el("select", { class: "dp-select", id: "ag-contract-preset" });
    const presets = state.contractPresets.length ? state.contractPresets : ["none"];
    for (const p of presets) {
      const opt = el("option", { value: p }, p);
      if (p === state.contract.preset) opt.selected = true;
      presetSel.appendChild(opt);
    }
    presetSel.addEventListener("change", () => {
      state.contract.preset = presetSel.value || "none";
    });

    const policySel = el("select", { class: "dp-select", id: "ag-contract-policy" });
    const policies = state.contractPolicies.length
      ? state.contractPolicies
      : ["IGNORE", "OBSERVE", "ENFORCE", "QUICK_ENFORCE"];
    for (const p of policies) {
      const opt = el("option", { value: p }, p);
      if (p === state.contract.policy) opt.selected = true;
      policySel.appendChild(opt);
    }
    policySel.addEventListener("change", () => {
      state.contract.policy = policySel.value || "OBSERVE";
    });

    const contractSection = el("section", { class: "rt-section" },
      el("div", { class: "rt-section-head" }, el("h3", {}, "Contracts")),
      el("div", { class: "rt-placeholder" },
        el("p", {}, "Use ", el("code", {}, "/contract <task>"), " for pre/post-condition checked runs."),
      ),
      el("div", { class: "ag-row" },
        el("label", { class: "ag-label" }, "Preset"),
        presetSel,
      ),
      el("div", { class: "ag-row" },
        el("label", { class: "ag-label" }, "Policy"),
        policySel,
      ),
    );
    host.appendChild(contractSection);
  }

  // Reflection row -- only rendered when the bundle includes ReflectionLoop.
  if (state.features["agents.reflect"]) {
    const attemptsInput = el("input", {
      type: "number", class: "dp-input",
      id: "ag-reflect-attempts", min: 1, max: 10, step: 1,
      value: state.reflect.maxAttempts,
    });
    attemptsInput.addEventListener("input", () => {
      const n = parseInt(attemptsInput.value, 10);
      if (Number.isFinite(n)) state.reflect.maxAttempts = Math.max(1, Math.min(10, n));
    });

    const markerInput = el("input", {
      type: "text", class: "dp-input",
      id: "ag-reflect-marker",
      value: state.reflect.acceptanceMarker,
    });
    markerInput.addEventListener("input", () => {
      state.reflect.acceptanceMarker = markerInput.value.trim() || "ACCEPT";
    });

    const criticInput = el("textarea", {
      class: "dp-input", id: "ag-reflect-critic-prompt", rows: 3,
      placeholder: "Custom critic prompt (blank = use sidecar default)",
    });
    criticInput.value = state.reflect.criticPrompt;
    criticInput.addEventListener("input", () => {
      state.reflect.criticPrompt = criticInput.value;
    });

    const reflectSection = el("section", { class: "rt-section" },
      el("div", { class: "rt-section-head" }, el("h3", {}, "Reflection")),
      el("div", { class: "rt-placeholder" },
        el("p", {}, "Use ", el("code", {}, "/reflect <task>"),
          " for a worker/critic loop. Worker drafts, critic accepts or asks for revision."),
      ),
      el("div", { class: "ag-row" },
        el("label", { class: "ag-label" }, "Max attempts"),
        attemptsInput,
      ),
      el("div", { class: "ag-row" },
        el("label", { class: "ag-label" }, "Accept marker"),
        markerInput,
      ),
      el("div", { class: "ag-row" },
        el("label", { class: "ag-label" }, "Critic prompt"),
        criticInput,
      ),
    );
    host.appendChild(reflectSection);
  }
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
