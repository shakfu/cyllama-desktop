# Guide to Agents

A user-facing tour of the five agent slash-commands and the Workflows
pane. Everything described here runs locally against the model you've
loaded in the topbar; no network is involved unless you explicitly
enable the `web_fetch` tool.

If you're looking for **how the agent layer is implemented** (sidecar
endpoints, feature flags, integration tests), see
[`dev/agent_plan.md`](dev/agent_plan.md). This document is for users.

---

## Table of contents

1. [What is an agent?](#what-is-an-agent)
2. [The basic `/agent` command](#the-basic-agent-command)
3. [Choosing the right command](#choosing-the-right-command)
4. [`/agent-constrained` -- strict tool calls](#agent-constrained----strict-tool-calls)
5. [`/agent-contract` -- rules the agent must obey](#agent-contract----rules-the-agent-must-obey)
6. [`/agent-plan` -- break a task into steps](#agent-plan----break-a-task-into-steps)
7. [`/agent-reflect` -- worker + critic loop](#agent-reflect----worker--critic-loop)
8. [The Agents pane](#the-agents-pane)
9. [Tools the agent can call](#tools-the-agent-can-call)
10. [Workflows pane](#workflows-pane)
11. [Common patterns](#common-patterns)
12. [Troubleshooting](#troubleshooting)

---

## What is an agent?

An **agent** is an LLM run in a loop: it thinks (THOUGHT), optionally
calls a tool (ACTION), reads the result (OBSERVATION), and either
continues thinking or emits a final answer (ANSWER). The trace of
those steps is what you see render inline under your chat turn when
you use a slash-command like `/agent`.

Compared to plain chat:

- A **chat turn** is one model call. The model speaks, you read, that's
  one round.
- An **agent run** is many model calls in sequence. Between each call
  the agent may invoke a tool (calculator, file read, RAG search,
  remembered facts...) and feed the result back into the next call.

The same model file backs both. The difference is in the wrapper around
the model, not the model itself.

---

## The basic `/agent` command

Type `/agent` in the chat composer followed by the task. Example:

```
/agent What is the cube root of 17576?
```

What you'll see:

1. A user bubble with your task.
2. A collapsible **Trace** under it. Click to expand. Inside you'll see
   one row per agent event:
   - **THOUGHT** rows -- the model's reasoning.
   - **ACTION** rows -- the tool call the model decided to make.
   - **OBSERVATION** rows -- the tool's response.
   - **ANSWER** -- the final result.
3. The **answer text** below the trace, rendered the same way a normal
   chat reply renders.

If the agent gets stuck (the same tool call repeated, max iterations
hit, model loaded but inference errored) you'll see an **ERROR** row in
the trace; the answer text stays empty.

You can cancel an in-flight run by pressing the Stop button in the
composer (same button that interrupts a chat turn).

The settings that apply to every `/agent` run live in the **Agents**
right-sidebar tab: max iterations, which tools to expose, sandbox
folder, etc. (See [The Agents pane](#the-agents-pane).)

---

## Choosing the right command

Five commands sit side by side. Pick by what you need from the run:

| Want this | Use this |
|---|---|
| A general-purpose ReAct loop -- LLM thinks, calls tools, answers. | `/agent` |
| Same as `/agent` but the model is *forced* by a grammar to produce well-formed tool calls. Eliminates "the model invented a tool that doesn't exist" failures. | `/agent-constrained` |
| The agent must satisfy named pre/post-conditions on the task or answer. Pre-conditions reject bad inputs; post-conditions catch bad outputs. | `/agent-contract` |
| A task that breaks into discrete steps (research, then summarise; refactor, then test...). One agent plans, another runs each step. | `/agent-plan` |
| A draft-then-review loop. Worker produces a draft; critic accepts it or asks for revisions; loop until accepted or budget hits. | `/agent-reflect` |
| A multi-node DAG with typed state, parallel branches, conditional routing, or sub-workflows. | Workflows pane |

If you're not sure which to start with, **use `/agent`**. The other
commands solve specific problems on top of it.

---

## `/agent-constrained` -- strict tool calls

Also reachable via the friendly alias **`/agent-strict`** -- both
slashes hit the same handler. Pick whichever reads better.

```
/agent-constrained What's the weather in Paris right now?
```

A per-call modal opens pre-filled with the Agents pane's strict
defaults:

- **Task** -- editable. Pre-filled with whatever you typed after the
  slash. Refine the wording, add constraints, or paste a longer task
  in place. Submitted text is what runs.
- **Format** -- `json` (default) / `json_array` / `function_call`.
  Selects the grammar the agent enforces on each tool call.
- **Allow reasoning** -- when on, the grammar permits a `reasoning`
  field alongside each tool call.

Press **Enter** inside any field to run with current values; **Esc**
cancels. Same trace shape as `/agent` -- the only runtime
difference is grammar-constrained decode, which the renderer doesn't
need to know about.

When this helps:

- Small or quantised models that frequently "hallucinate" tool names
  or argument shapes.
- Tasks where a malformed tool call would cascade (the agent retries,
  burns its iteration budget, then gives up).

When this doesn't help:

- The model doesn't actually need any tools to answer.
- You want freeform reasoning between tool calls (constrained mode
  still allows it -- but the constraint kicks in at the tool-call
  boundary).

Cost: grammar-constrained decode is slightly slower per token than
free decode. For most prompts the difference is invisible; for very
long traces it can be noticeable.

---

## `/agent-contract` -- rules the agent must obey

```
/agent-contract Explain the Treaty of Westphalia in two paragraphs.
```

A modal opens with three fields:

- **Task** -- editable; pre-filled from the slash body.
- **Preset** -- one of the named contract bundles shipped with the
  sidecar:
  - `none` -- no rules; the agent runs with policy machinery active
    but nothing to violate. Useful for verifying wiring.
  - `task-nonempty` -- precondition: the task must be a non-empty
    string.
  - `answer-quality` -- postcondition: the answer must be at least 10
    characters of non-whitespace.
- **Policy** -- how violations are handled:
  - `IGNORE` -- skip the check entirely.
  - `OBSERVE` -- emit a `CONTRACT_VIOLATION` event in the trace, keep
    running.
  - `ENFORCE` -- emit the violation event *and* terminate the run.
  - `QUICK_ENFORCE` -- terminate immediately without calling the
    violation handler.

Press **Enter** to run with the defaults from the Agents pane;
Cancel/Esc drops the run. Violations show up in the trace as
`CONTRACT_VIOLATION` rows alongside the THOUGHT / ACTION /
OBSERVATION rows.

When to use which policy:

- Use `OBSERVE` while you're developing a contract bundle or learning
  what an LLM tends to do wrong.
- Use `ENFORCE` once you trust the rules and want them to be hard
  errors.
- Use `IGNORE` to keep the contract bundle attached (for documentation)
  while temporarily disabling it.

The preset registry is server-side -- you can't write a contract from
the UI yet. If you need custom rules, see the [Workflows pane](#workflows-pane).

---

## `/agent-plan` -- break a task into steps

```
/agent-plan Refactor the authentication module to use bcrypt and update the tests.
```

Two agents run in sequence:

1. **Planner** -- an LLM with no tools and a "break this task into
   numbered steps" system prompt. Its answer is parsed line-by-line
   into a step list.
2. **Executor** -- an LLM with the Agents pane tool catalog. Runs once
   per step, in order, with the step text as its task.

The trace tags each event with a `source`:

- `planner` -- thoughts and the plan emission.
- `step-1`, `step-2`, ... -- per-step executor events.

The final **answer** is a numbered summary: one line per step plus its
executor result.

When to use:

- Multi-step tasks where the steps are independent or naturally
  sequential ("fetch X, then process Y, then write Z").
- Tasks where you want the model to commit to a plan upfront rather
  than improvise step-by-step inside `/agent`'s loop.

When not to use:

- Tasks that need data from step N to *decide* what step N+1 should
  be. Plan-and-execute fixes the plan upfront; if you need dynamic
  decisions between steps, use `/agent-reflect` or a workflow.
- Tasks better served by a single tool call. The planner+executor
  overhead is wasted on "what's 2 + 2?".

The modal carries five fields:

- **Task** -- editable.
- **Max steps** -- cap on the number of executor invocations
  (default 10, range 1-20).
- **Stop on error** -- when on (default), aborts the run on the
  first failing step.
- **Planner prompt** -- override the planner's system prompt. Blank
  uses the sidecar default ("break this task into clear ordered
  steps, one per line").
- **Executor prompt** -- override the executor's system prompt.
  Blank uses the default ReActAgent system prompt.

Defaults come from the Agents pane's `/agent-plan` row; Enter runs
with current values.

---

## `/agent-reflect` -- worker + critic loop

```
/agent-reflect Write a 3-sentence summary of the Cretaceous-Paleogene extinction event.
```

Two agents loop:

1. **Worker** -- with tools from the Agents pane -- produces a draft.
2. **Critic** -- *without* tools, with a "reply ACCEPT or list issues"
   prompt -- reviews the draft.

If the critic's reply contains the **acceptance marker** (default
`ACCEPT`, case-insensitive substring match), the loop ends and the
draft is the answer. Otherwise the critic's feedback is folded into
the next worker pass.

The modal exposes four fields:

- **Task** -- editable; pre-filled from the slash body.
- **Max attempts** -- hard ceiling on loop iterations (default 3,
  range 1-10).
- **Accept marker** -- the substring the critic must include to
  approve. Default `ACCEPT`. Change to `OK` or `Looks good` if your
  critic prompt steers the model that way.
- **Critic prompt** -- override the default reviewer system prompt.
  Leave blank to use the sidecar default ("respond with ACCEPT or
  list issues").

Defaults come from the Agents pane's `/agent-reflect` row.

Trace events are tagged `worker-1` / `critic-1` / `worker-2` / etc.
so you can see which role emitted which event.

When to use:

- Tasks where you'd manually iterate on the answer ("this is close,
  but adjust X").
- Writing tasks where the model often gets the structure right but
  needs a second pass on details.

When not to use:

- Tasks with no review criterion the critic can apply. If "looks
  right" is just gut feeling, the critic will accept everything.
- One-shot factual questions. The critic adds latency without value.

---

## The Agents pane

Open it via the **Agents** button in the left nav-rail (network-graph
icon, below Models). The pane is a full-area three-column surface:

- **Left subnav** lists the six agent types -- `/agent`,
  `/agent-strict`, `/agent-contract`, `/agent-plan`, `/agent-reflect`,
  `/agent-workflow`. Click a row to switch.
- **Main column** shows the selected type's defaults. The first
  section is **Common** (max iterations + tool catalog -- shared by
  every type). Below it sits a type-specific section: format /
  allow_reasoning for strict, preset / policy for contract, max_steps
  / stop_on_error / planner prompt / executor prompt for plan, max
  attempts / accept marker / critic prompt for reflect. Plain
  `/agent` has no type-specific section -- just the Common.
- **Right detail rail** shows per-type run history (placeholder for
  most types today; the `agent-workflow` row shows the last
  workflow run's final state + answer + error).

### Defaults vs. per-call modal

The pane holds **defaults**. The defaults flow into the per-call
modal that opens when you invoke `/agent-strict`, `/agent-contract`,
`/agent-plan`, or `/agent-reflect` from the chat composer -- you
edit them once in the pane, then every modal invocation starts with
those values pre-filled. Press Enter inside the modal to run with
the defaults unchanged; tweak any field for this one invocation.

`/agent` and `/agent-workflow` don't open a modal. `/agent` runs
inline with current defaults; `/agent-workflow` switches to this
pane on the workflow row.

The `agent-workflow` row of the subnav is the **Workflows pane** --
it's the same interface that used to live as a separate full-area
pane (file list + spec preview + initial-state form + Run + live
trace). See [Workflows](#workflows-pane) below.

---

## Tools the agent can call

Tools are concrete functions the model can invoke during a run. Pick
which ones to expose in the Agents pane's **Tools** row.

| Tool | What it does | When to enable |
|---|---|---|
| `calculator` | Evaluates a small expression language (arithmetic + a few functions). | Math-heavy tasks. Almost always on. |
| `read_file` | Reads a file from a sandbox directory you pick. Hard byte cap. | When the model needs to look at local content. Pick the sandbox folder carefully -- the model can read anything under it. |
| `web_fetch` | HTTP GET against a URL the model produces. Hard byte cap. | Tasks that need fresh web data. Network access is opt-in per run -- a confirmation dialog appears the first time you enable it. |
| `rag_query` | Searches one of your RAG collections and returns top-k chunks. | Tasks grounded in a body of documents you've already ingested via the Documents pane. |
| `semantic_memory` | Two tools (`remember`, `recall`) backed by a RAG collection + a namespace string. | Long-running interactions where the model should accumulate facts ("remember that the user prefers tabs") and surface them later ("recall what the user's preferences are"). |

**Sandbox boundary:** `read_file` strictly refuses paths outside the
configured sandbox folder. `web_fetch` is the only tool that touches
the network. The agent cannot run shell commands or write files unless
you author a workflow that does so (see below).

**Semantic memory tip:** the namespace string isolates entries within
the same collection. Use different namespaces for per-user / per-topic
buckets so a `recall` doesn't surface unrelated content. Two namespaces
on the same collection can't read each other's entries by design.

---

## Workflows pane

The chat composer's slash commands are a fixed shape: task in, agent
runs, answer out. When you need **multiple steps with typed state**,
**parallel branches**, **conditional routing**, or **sub-workflows**
nested inside other workflows, use the Workflows pane.

Click the network-graph icon in the left nav-rail to open it. (The
icon appears only when the bundled cyllama exposes the workflow
runtime.)

### Authoring a workflow

Workflows are **Python files** in your workspace under
`<workspace>/workflows/`. On first launch the app seeds an example
file (`word_count.py`) you can read as a starting point.

Each file exports either:

- a module-level `flow: Workflow` already configured, or
- a `make_flow()` function returning a `Workflow`.

The module's docstring becomes the description shown in the pane.

Minimal example:

```python
'''Count words in the input text.'''
from cyllama.agents import Workflow

flow = Workflow()

@flow.node
def tokens(text: str) -> list[str]:
    return text.lower().split()

@flow.node
def count(tokens: list[str]) -> int:
    return len(tokens)

flow.set_entry("tokens")
flow.set_exit("count")
```

Drop that in your workflows directory; the pane refreshes when you
click Refresh in the subnav. Parameter names (`text`, `tokens`) drive
the DAG: `count` depends on `tokens` because they share a name; `text`
is a required workflow input because no node produces it.

### Running a workflow

The pane is three columns:

- **Left** -- list of discovered workflow files. Broken files render
  with an "error" tag; click any non-broken file to select it.
- **Middle** -- the selected workflow's plan (entry node, exits,
  topological levels, optional Mermaid rendering), then a form with
  one row per required input, then a **Run** button, then the live
  trace.
- **Right** -- last-run summary: success/error, final answer, full
  final state pretty-printed.

Live trace events flow in real time as the workflow executes.
`WORKFLOW_START` opens the run; `NODE_START` / `NODE_END` bracket each
node; `ANSWER` carries the projected output; `WORKFLOW_END` closes the
run with the full state.

Sub-workflow events (workflows that nest other workflows via
`workflow_node`) or sub-agent events (workflows that wrap an
`AgentProtocol` via `agent_node`) forward into the outer trace with a
`source` chip so you can see which inner unit emitted each event.

### Trust boundary

**Workflow files execute as Python in the sidecar process.** They have
the same access the sidecar has -- file system within the workspace
sandbox, network if your tools enable it, and any other capability
Python exposes. Treat the workflows directory like any other code you
run on your machine: only put files there you'd be comfortable
running.

This is the same trust level as the `agent_exec_python` family of
tools. There is no sandboxed-Python option today; if you need one,
file an issue.

---

## Common patterns

**Q: I want the agent to read a file, summarise it, and write the
summary somewhere.**

A: Enable `read_file` (and pick a sandbox folder containing both the
source and the destination). Use `/agent` and ask explicitly: "Read
`/path/to/input.txt`, summarise it, write the summary to
`/path/to/summary.txt`." Note: there's no first-party `write_file`
tool yet -- you'll need a workflow for the write half.

**Q: I want the model to remember facts about me across conversations.**

A: Create a RAG collection in the Documents pane. Enable
`semantic_memory` in the Agents pane with that collection + a stable
namespace (e.g., `"user-preferences"`). Use `/agent` and tell the
model "remember that I prefer tabs over spaces"; later use `/agent`
again and ask "what do you remember about my code style?" -- the
recall tool will surface the prior fact.

**Q: My workflow keeps failing at one node.**

A: Open the live trace and find the failing `NODE_END` (or `ERROR`)
event. The error string surfaces the Python traceback. If the node
calls into the LLM and you've hit the per-iteration cap, raise it in
the Agents pane.

**Q: Which command should I use for code review?**

A: `/agent-reflect` is the natural fit: worker writes a code change, critic
reviews against your style guide. Set the critic prompt to spell out
what the reviewer should look for ("flag missing tests, unguarded
nulls, untyped externs"). Use the acceptance marker to set the bar:
`APPROVED` is stricter than the default `ACCEPT`.

---

## Troubleshooting

**The agent answers but the trace is empty.**

The agent skipped the loop because the model produced a final answer
on the first call (no tools needed). Trace events fire only when a
tool is invoked or thinking is verbose enough. Increase the model's
verbosity in its system prompt or enable a tool the question forces
the model to use.

**The agent's trace shows the same THOUGHT/ACTION over and over.**

Loop detection wasn't triggered fast enough. Lower `max_iterations`
in the Agents pane, or use `/agent-constrained` to make sure the model's
tool calls are well-formed (malformed calls often look identical to
the loop guard).

**`/agent-constrained` fails with a grammar error.**

The bundled cyllama wasn't built with grammar support. Check the
Agents pane -- if `/agent-constrained` works, you'll see the
slash autocomplete on Tab. If not, the bundle is missing
`ConstrainedAgent`.

**Workflow file shows up with "error" tag in the pane.**

Open the file in your editor and check for import errors. The pane
displays the exception message under "Spec load failed:" once you
click the broken file (though clicking is disabled when an error is
present; check the sidecar console output instead).

**Workflow runs but the state at the end is missing a key I expected.**

Each node's return dict is merged into state under the node's name.
If your node returns `{"foo": 42}` from a node called `bar`, the
state ends up with `{"bar": {"foo": 42}}`, not `{"foo": 42}`. To put
the value at the top level, write the node body to return `{"foo":
42}` from a node *named* `foo`, or use Layer-B `add_node(name, fn)`
where the function returns whatever shape you want.

**Workflow says "missing required workflow inputs: ['x']".**

A Layer-C node has a parameter named `x` that doesn't match any other
node's output name. Either supply `x` as an initial-state value in the
pane's form, or rename the parameter to match an upstream node.

---

## Where to next

- [`dev/agent_plan.md`](dev/agent_plan.md) -- implementation plan with
  the full surface inventory, sidecar endpoint shapes, and the
  granular feature-flag map.
- **cyllama's `docs/agents/workflow.md`** -- the design specification
  for the workflow runtime, useful when authoring complex workflow
  files.
- **cyllama's `docs/agents_overview.md`** -- the agent-layer reference
  documenting `ReActAgent`, `ConstrainedAgent`, `ContractAgent`,
  `ReflectionLoop`, `plan_and_execute`, `SemanticMemory`, `Workflow`,
  and all the pieces this guide covers from the user side.
- **`docs/slash-commands.md`** -- the slash-command taxonomy + how to
  add a new one.
