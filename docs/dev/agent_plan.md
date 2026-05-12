# AGENT_PLAN: Wiring the New cyllama Agent Surface into cyllama-desktop

Status: proposed. Owner: @shakfu. Last updated: 2026-05-11.

Companion docs: `PLAN.md` (overall feature rollout), `TODO.md` (tactical
checklist), `CHANGELOG.md`. This document is scoped to the agent layer:
what's new in cyllama, what to wire, and how. The general phasing rules
from PLAN.md still apply.

---

## 1. Goal

Validate the new cyllama agent surface (Phase 7 and the Phase 1-5
workflow rollout) end-to-end through the desktop app. The bundled
cyllama is about to be bumped; every new agent type should have a
sidecar endpoint, a renderer surface, and at least one Playwright
smoke test before the bump is considered shipped.

Concrete acceptance criterion: a user can pick any of the new agent
types from the chat composer (or a dedicated pane) and see a live
event trace render in the chat stream, the same way today's
`/agent <task>` ReAct trace renders.

## 2. Non-goals

- Production tooling for advanced users (cron-scheduled agents,
  multi-tenant tool sandboxes, persistent agent state).
- Re-implementing cyllama agent logic in JS. The renderer is a thin
  client; every behavior decision lives in the sidecar.
- Replacing `/agent <task>` -- the new surfaces extend, not replace,
  the existing ReAct path.
- A workflow-authoring UI. Workflows are specified as Python in
  workspace-scoped files (Section 7); a visual graph editor is out of
  scope for this round.

## 3. Current state (2026-05-11)

Sidecar (`python-sidecar/sidecar.py`):

- **Wired:** `cyllama.agents.ReActAgent` via `/jobs/agent/run`
  (sidecar.py:2975), SSE-streamed. Feature-detected at module load
  via `_AGENT_REACT_CLS = _resolve_attr(...)`; gated by
  `_FEATURE_FLAGS["agents"]`.
- **Tool catalog:** `_build_agent_tools()` constructs file-read,
  file-write, fetch-url, and Python-exec tools from a sandbox dir.
  Each tool has a hard byte cap (`_AGENT_MAX_FILE_BYTES` etc).
- **Slash command:** `/agent <task>` in the chat composer routes
  through `/jobs/agent/run`; the renderer renders the agent trace
  inline under the chat stream
  (`src/renderer/dist/renderer.js:4236`).
- **Right-sidebar tab:** `agents` for configuring sandbox dir,
  max iterations, model pin, and tool catalog
  (`src/renderer/index.html:172`, `:397`).

cyllama (`~/projects/personal/cyllama`, currently in `[Unreleased]`
relative to the bundle):

- **New agent classes:** `ConstrainedAgent`, `ContractAgent`,
  `AsyncReActAgent`, `AsyncConstrainedAgent`, `ACPAgent`
  (experimental).
- **Composition primitives:** `agent_as_tool`, `TieredAgentTeam`,
  `ReflectionLoop`, `plan_and_execute`, `mcp_agent_tool`,
  `rag_as_tool`.
- **Long-term memory:** `SemanticMemory`.
- **DAG workflow runtime:** `Workflow` / `CompiledWorkflow` (Phases
  1-5), Layer B + Layer C authoring, streaming events, contracts,
  reducers, sub-workflows, `as_agent()` adapter.

None of the items in the second list are exercised by the desktop
app today. This document is the plan to get them there.

## 4. Surface to wire (priority order)

Ordered by "tested value per LoC of integration." Higher entries
exercise more cyllama internals for the same desktop-side effort.

1. **`ConstrainedAgent`** -- same loop as ReAct but grammar-enforced
   tool calls. Reuses tool catalog, sandbox, sidebar config. Only
   the agent class changes. ~50 sidecar LoC.

2. **`ContractAgent`** -- ReAct + pre/post-condition checks +
   `ContractPolicy`. Renders `CONTRACT_CHECK` /
   `CONTRACT_VIOLATION` events the existing trace renderer doesn't
   yet display. ~80 sidecar LoC, plus event-renderer extension.

3. **`AsyncReActAgent` / `AsyncConstrainedAgent`** -- async wrappers.
   Sidecar already runs the sync agent on a thread; the async
   variants would let us drop the thread hop. Probably a sidecar-
   internal refactor rather than a new endpoint -- defer unless
   profiling shows the thread hop matters.

4. **`Workflow` / `CompiledWorkflow`** -- the big new feature. New
   endpoint `/jobs/workflow/run`. Workflow specs live in workspace
   files (Section 7); the endpoint takes a workflow id + initial
   state and streams `WORKFLOW_START` / `NODE_START` / `NODE_END` /
   `ANSWER` / `WORKFLOW_END` / `CONTRACT_VIOLATION` events.

5. **`ReflectionLoop`** -- worker + critic loop. The shape is
   "two agents in sequence, repeat until accepted." A
   `/jobs/agent/reflect` endpoint takes worker config + critic
   config + max_attempts. Streams agent events from both with
   `source` tagging the role. ~120 sidecar LoC.

6. **`plan_and_execute`** -- planner emits steps, executor runs each.
   Endpoint streams planner trace, then per-step executor traces.
   Same `source` tagging pattern. ~100 sidecar LoC.

7. **`rag_as_tool`** -- bridge to the existing `/rag` collection
   surface. Adds a "rag_search" entry to the agent tool catalog
   gated on whether the workspace has an active collection. ~30
   sidecar LoC; the renderer just sees one more tool.

8. **`SemanticMemory`** -- long-term per-workspace memory backed by
   a workspace RAG collection. Exposed as a tool
   (`remember` / `recall`) for any agent type. Storage lives under
   `<workspace>/memory/`. ~80 sidecar LoC plus a small "Memory"
   surface in the right sidebar listing recalled hits.

9. **`mcp_agent_tool`** -- requires an MCP server to dispatch to.
   Defer unless we have a concrete remote agent to target; the
   wiring is trivial but the value depends on a real other side.

10. **`TieredAgentTeam`** -- supervisor + named workers, each with
    its own model pin. Big in feature scope (multi-model loading,
    GPU-budget concerns) and minimal vs. composing `agent_as_tool`
    manually. Defer until single-agent surfaces are validated.

11. **`ACPAgent`** -- experimental upstream; cyllama itself warns
    the API may change. Skip until upstream stabilizes.

## 5. Endpoint design (uniform shape)

Every new agent endpoint follows the `/jobs/agent/run` pattern so
the renderer's event-stream code stays DRY:

```
POST /jobs/<route>            { agent_type, model_path, task, ... }
   -> { job_id }                returned immediately
GET  /jobs/<job_id>/events    SSE: event_type + content + metadata
GET  /jobs/<job_id>/result    final answer + collected events
POST /jobs/<job_id>/cancel    cooperative cancellation
```

Concrete routes:

| Route | Wraps | Inputs (beyond `model_path`, `task`) |
|---|---|---|
| `/jobs/agent/run` (existing) | `ReActAgent` | `tools`, `max_iterations`, `system_prompt` |
| `/jobs/agent/constrained` | `ConstrainedAgent` | + `grammar` (optional; auto-derived from tools by default) |
| `/jobs/agent/contract` | `ContractAgent` | + `contract_spec`, `policy` |
| `/jobs/agent/reflect` | `ReflectionLoop` | `worker`, `critic`, `max_attempts`, `acceptance_marker` |
| `/jobs/agent/plan` | `plan_and_execute` | `planner`, `executor` |
| `/jobs/workflow/run` | `Workflow` | `workflow_id`, `initial_state` |

The "agent" sub-namespace groups all agent variants; workflows get
their own namespace because their state shape is fundamentally
different (typed dict, not a single task string).

All endpoints emit an `EventType.ANSWER` event as the canonical
"done" signal (this is now uniform across the cyllama agent and
workflow surfaces -- see Phase 5 of `cyllama/docs/agents/workflow.md`).
The renderer's existing terminal-event logic already handles ANSWER.

## 6. Feature detection (extend `_resolve_attr` block)

Add detection probes at module load alongside the existing
`_AGENT_REACT_CLS`:

```python
_AGENT_CONSTRAINED_CLS = _resolve_attr((("cyllama.agents", "ConstrainedAgent"),))
_AGENT_CONTRACT_CLS    = _resolve_attr((("cyllama.agents", "ContractAgent"),))
_REFLECTION_LOOP_CLS   = _resolve_attr((("cyllama.agents", "ReflectionLoop"),))
_PLAN_AND_EXECUTE_FN   = _resolve_attr((("cyllama.agents", "plan_and_execute"),))
_RAG_AS_TOOL_FN        = _resolve_attr((("cyllama.agents", "rag_as_tool"),))
_SEMANTIC_MEMORY_CLS   = _resolve_attr((("cyllama.agents", "SemanticMemory"),))
_WORKFLOW_CLS          = _resolve_attr((("cyllama.agents", "Workflow"),))
_WORKFLOW_NODE_FN      = _resolve_attr((("cyllama.agents", "workflow_node"),))
_AGENT_NODE_FN         = _resolve_attr((("cyllama.agents", "agent_node"),))
```

Extend `_FEATURE_FLAGS` with per-capability booleans:

```python
"agents":           bool(_AGENT_REACT_CLS and _AGENT_TOOL_CLS),
"agents.constrained": bool(_AGENT_CONSTRAINED_CLS),
"agents.contract":  bool(_AGENT_CONTRACT_CLS),
"agents.reflect":   bool(_REFLECTION_LOOP_CLS),
"agents.plan":      bool(_PLAN_AND_EXECUTE_FN),
"agents.rag_tool":  bool(_RAG_AS_TOOL_FN),
"agents.memory":    bool(_SEMANTIC_MEMORY_CLS),
"workflow":         bool(_WORKFLOW_CLS),
```

`/info/features` already returns this map; the renderer can hide
surfaces for features the bundle doesn't carry, same as today's
`agents` flag.

## 7. Workflow authoring surface

Workflows are richer than a single task string -- they're a Python
DAG. The desktop app needs an authoring story.

**Decision: workspace-scoped Python files, no UI editor.**

```
<userData>/workspaces/default/
  workflows/
    research_pipeline.py    # exports a `flow: Workflow` (or `make_flow()`)
    summarize_doc.py
    review_loop.py
```

Sidecar discovery:

- `GET /workflows` lists `*.py` files under `<workspace>/workflows/`
  with their `flow.entry`, declared `layer_c_inputs`, and a
  one-line docstring summary.
- `POST /jobs/workflow/run` takes `{ workflow_id, initial_state }`,
  imports the module under a restricted globals dict (same sandbox
  policy as `agent_exec_python` tool: no network, no parent-fs
  access except the workspace `sandbox/` dir).
- Module reload on file mtime change; cache the compiled workflow
  per (path, mtime) so iterative authoring doesn't re-validate on
  every run.

**Why not a JSON/YAML spec?** Workflows have callable bodies
(node functions, conditional routers, reducers). A JSON spec
would either need a separate registration step for the callables
(complexity) or a sandboxed expression language (re-invents
Python). Workspace-scoped Python is the simplest honest answer;
the sandbox layer already exists for agent tools.

**Trust boundary:** workflow files run in the sidecar's Python
process, so they have whatever capability the sidecar itself has
(file write within sandbox, network if enabled). Document this
clearly in the workflows surface; pre-warn on first run of a new
workflow file with "this code will execute -- confirm." Mirror the
existing network-enable confirmation in `agents-tab`.

**Examples to ship:** three reference workflows under
`resources/example-workflows/` (copied into a workspace on first
launch):

1. `summarize_doc.py` -- linear pipeline (fetch -> extract ->
   summarize). Exercises Layer C + sequential nodes.
2. `parallel_research.py` -- fan-out (wikipedia, local docs,
   calculator) + join. Exercises parallel level execution +
   reducer.
3. `review_loop.py` -- worker + critic with conditional routing.
   Exercises `add_conditional_edge` + END sentinel + max_steps.

## 8. UX choices (slash commands vs panes)

Two surface options:

**Option A: extend slash commands.** Mirror `/agent`:

- `/constrained <task>` -- ConstrainedAgent
- `/contract <task>` -- ContractAgent (uses sidebar contract spec)
- `/reflect <task>` -- ReflectionLoop (uses sidebar worker/critic config)
- `/plan <task>` -- plan_and_execute
- `/workflow <id> [json-state]` -- Workflow run

Pros: leverages existing chat-stream trace rendering; minimal new
UI. Cons: hides discovery (users have to know commands exist);
configuration lives in the right sidebar.

**Option B: new "Workflows" pane.** A left-nav-rail entry next to
Chats/Documents/etc. Lists discovered workflow files, "Run" button
per workflow opens a modal for initial state, trace renders in the
pane itself rather than the chat stream.

Pros: discoverable; matches the "pane per capability" model in
PLAN.md S.5; keeps long workflow traces out of the chat history.
Cons: more renderer LoC; duplicates the trace-rendering code.

**Recommendation: A for agent variants, B for workflows.**

The agent variants (Constrained, Contract, Reflect, Plan) are
"chat-shaped" -- short task in, answer out, trace as a sidebar to
the answer. The chat surface fits. Workflows are different: they
have typed state, multiple inputs, optional artifacts. A dedicated
pane is the right shape.

This is also the cheapest staging: Option A is ~3 hours of
renderer work per command (slash registration, validation, trace
adapter). Option B is ~2 days but only needs to be built once.

## 9. Phasing

Five short phases, each shippable on its own. Each ends with a
Playwright smoke test, a CHANGELOG entry, and a feature-flag
expansion. No phase blocks on cyllama-side changes -- the cyllama
work has landed.

**Phase A. Bundle bump.** Update `python-sidecar/pyproject.toml`
cyllama pin. Run existing pytest + Playwright suite. Resolve any
breakage from the bump itself (signature changes, removed APIs).
Add `_resolve_attr` probes from Section 6. No new endpoints. No
new UI. Single CHANGELOG entry: "bump cyllama; detect new agent
capabilities."

**Phase B. Three slash-command agents.**
`/constrained`, `/contract`, `/plan`. New endpoints
`/jobs/agent/constrained`, `/jobs/agent/contract`,
`/jobs/agent/plan`. Renderer extension: slash registration,
contract-violation event rendering (new event type the existing
renderer doesn't know about). Playwright tests follow the
`/agent` test shape. Sidebar `agents` tab gains a contract-spec
editor and a planner/executor selector.

**Phase C. ReflectionLoop.** `/reflect` command. New endpoint
`/jobs/agent/reflect`. Sidebar gains a worker/critic dual-config
section. Renderer renders the two-agent interleaved trace with
`source` tagging in the event chip (chip color or prefix).

**Phase D. Workflow pane.** New left-nav-rail pane "Workflows".
`/workflows` endpoint for discovery, `/jobs/workflow/run` for
execution, `/workflows/<id>/spec` for the static dry-run plan
(for the pane's preview panel). Workspace `workflows/` directory
+ first-launch seeding with the three example workflows.
Playwright tests: discovery, dry-run preview, execute,
real-time event streaming (the test from cyllama
`test_sub_events_arrive_before_node_end` translated to a UI
assertion).

**Phase E. Memory + RAG tool.** Extends the existing agent tool
catalog with `rag_search` (gated on a workspace having an active
RAG collection) and `remember`/`recall` (semantic memory). No new
endpoints; the tool catalog is the surface. Available to every
agent type from Phases B/C and any Workflow that opts in.

## 10. Risks

- **Bundle bump breakage.** cyllama's [Unreleased] section is long
  -- there may be API changes the sidecar hasn't tracked. Phase A
  exists to surface these before adding new endpoints.
- **Workflow trust boundary.** Executing workspace Python is the
  same trust level as `agent_exec_python` today. The risk is users
  not realizing this; the mitigation is a confirmation modal on
  first run and a visible "workflow file: <path>" header in the
  pane.
- **Renderer trace bloat.** Workflows can emit hundreds of events
  (each node, each contract check). The current trace renderer
  loads everything into the DOM. Phase D should virtualize the
  event list (or collapse-by-default per-node groups) if a real
  workflow blows past a few hundred events.
- **Multi-model loading (TieredAgentTeam).** Deferred to a later
  round; loading two 7B+ models is rarely viable on consumer
  hardware. PLAN.md S.9 workspaces will help (per-workspace
  pinned models) but the GPU-budget UX is its own design.

## 11. Testing

Every phase ships with:

- **Sidecar pytest.** One test per endpoint covering: happy path,
  missing-required-input 400, cancellation, error event capture.
  Pattern: `tests/sidecar/test_agent_constrained.py` etc.
- **Playwright smoke.** One test per slash command / pane that
  runs against a small stub model (`models/test-small.gguf`) and
  asserts: command opens, trace renders, ANSWER event lands, no
  console errors. Pattern: `tests/e2e/agent-constrained.spec.js`.
- **Workflow live-streaming.** Phase D adds an explicit assertion
  that a `NODE_END` for an inner node arrives *before* the outer
  `NODE_END` -- mirrors the cyllama-side regression guard.

## 12. Definition of done (per phase)

- Sidecar endpoint(s) exist; pytest green.
- Renderer surface exists; Playwright smoke green.
- Feature flag advertises the capability; the surface auto-hides
  when the flag is false.
- CHANGELOG entry written.
- TODO.md tactical items closed.

## 13. Open questions

- **Workflow file editor in-app, or external editor only?** Phase D
  ships read-only discovery; users author in their preferred
  editor. A in-app code editor (Monaco) is a separate Phase F if
  demand appears.
- **Workspace-level workflow vs global workflow?** Phase D assumes
  workspace-scoped. If a user wants to run the same workflow
  against multiple workspaces, they currently have to symlink or
  copy. PLAN.md S.9 workspaces is still mid-rollout; this gets
  resolved when workspace switching lands.
- **Workflow visualization in the pane?** `flow.to_mermaid()` and
  `flow.to_dot()` exist. Rendering Mermaid in the pane is ~100
  LoC of renderer work. Phase D should include this -- the
  visualization is exactly the kind of thing that makes the
  workflow surface feel substantive vs. "a JSON config that does
  some things."

## 14. References

- `cyllama/docs/agents/workflow.md` -- full workflow design spec.
- `cyllama/docs/agents/patterns.md` -- pattern catalog including
  the new helpers.
- `cyllama/docs/agents_overview.md` -- user-facing agent docs.
- `cyllama/CHANGELOG.md` `[Unreleased]` -- the agent surface this
  plan integrates.
- `PLAN.md` S.7 (Sidecar API design) -- conventions this plan
  mirrors.
- `PLAN.md` S.9 (Workspaces) -- where workflow files live.
