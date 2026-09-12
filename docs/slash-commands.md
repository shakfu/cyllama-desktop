# Slash Commands

Slash-prefixed commands typed into the chat composer. A command either **runs an action** (kicks off a job, results render inline in the chat stream) or **navigates** (switches the renderer to another pane and produces no chat output).

Seven commands are registered today, all in the agent family. The parsing, Tab completion and registry plumbing are built; the wider command set below Phase 1 is still a proposal. Sections are marked accordingly.

## Registered today

| Command | Kind | Args | Behaviour |
|---|---|---|---|
| `/agent` | action | `<task>` | ReAct loop with the Agents pane's tool config. Runs inline, no modal. |
| `/agent-constrained` | action | `<task>` | Grammar-constrained tool calls. Opens the per-call modal. |
| `/agent-strict` | action | `<task>` | Alias for `/agent-constrained`; the friendlier name. Same handler, same `ConstrainedAgent` underneath. |
| `/agent-contract` | action | `<task>` | Runs under a named pre/post-condition preset and a violation policy. Opens the modal. |
| `/agent-plan` | action | `<task>` | Planner emits steps, executor runs them. Opens the modal. |
| `/agent-reflect` | action | `<task>` | Worker drafts, critic accepts or revises. Opens the modal. |
| `/agent-workflow` | navigation | `[<name>]` | Switches to the Agents pane on the workflow row. With a name, preselects that workflow. |

`/agent` keeps the bare name because it is the default path. Everything else takes the `/agent-` prefix so Tab from `/agent` surfaces the whole family.

Anything not in the registry stays a plain chat turn -- no error, no intercept -- so a message can begin with a literal slash.

## Goals

- One typing surface. No duplicated run UIs across sidebar tabs and composer.

- Inline results in the chat stream where it makes sense, so a conversation can mix chat turns and tool runs.

- Discoverable: Tab completes the unique prefix.

- Cheap to extend: adding a command is one registry entry plus a handler.

## Non-goals

- Full shell-like argument parsing. No quoting rules, no flags. Args are "everything after the first space".

- Pipelines or chaining (`/agent ... | /transcribe ...`).

- Slash-command history beyond what the chat already records.

## Command taxonomy

| Kind | What it does | Registered |
|---|---|---|
| `action` | Runs a job; the trace and answer render inline as an exchange. | six agent commands |
| `navigation` | Switches the active pane; clears the composer; adds nothing to the chat. | `/agent-workflow` |
| `augment` | Would mutate chat state without running a job (`/system`, `/clear`, `/new`, `/preset`). | none -- proposed |

## Parsing rules

Implemented in `src/renderer/src/features/slash.js`.

- A command is optional leading whitespace, `/`, then a name starting with an ASCII letter and continuing with letters, digits, `_` or `-`. So `   /agent x` is a command.

- Names are matched case-insensitively; the parsed name is lowercased.

- A word boundary ends the name. Everything after it, trimmed, is the body, passed to the handler as one string. Handlers parse their own args.

- An empty body is allowed. The handler decides whether that is an error.

- A name not in the registry returns `null` from `parse()` and the prompt is sent as chat. There is no escape syntax for sending a registered name literally; nobody has asked.

## Autocomplete

Tab, in the composer, when the prompt is a slash plus an optional partial name and nothing else:

1. **One match** -- completes it and appends a space, so the body can be typed immediately.

2. **Several matches** -- completes to the longest common prefix, then lists the candidates as a transient system line below the chat. `/a` + Tab gives `/agent` and lists all seven.

3. **No match** -- swallows the Tab so focus does not leave the composer mid-type.

Enter on a recognised slash command bypasses the Send button's model-loaded gate, so the per-call modal can open for `/agent-strict`, `/agent-contract`, `/agent-plan` and `/agent-reflect` before any model is loaded. Raw chat still requires a loaded model.

**Not built:** the dropdown. Typing `/` was to open a filtered, absolutely-positioned list above the composer with arrow-key navigation. The transient candidate line is the stand-in.

## Per-call modals

Four commands open a modal pre-filled from the Agents pane's defaults: `/agent-constrained` (and its `/agent-strict` alias), `/agent-contract`, `/agent-plan`, `/agent-reflect`. Enter runs with the defaults unchanged; any field can be tweaked for that one invocation; Esc drops the run.

`/agent` runs inline with the current defaults and no modal. `/agent-workflow` navigates and runs nothing.

## Capability gating

Registration is unconditional: all seven names autocomplete regardless of what the bundled cyllama provides. Gating happens in two other places.

- The Agents pane's nav-rail button appears only when `/info.features.agents` is true.

- A command whose cyllama class is missing fails at the sidecar, which returns 501 naming the capability. That message surfaces as an error line in the chat.

So Tab completing a command is not evidence the bundle supports it. The flag map is not shown in the UI; read it from `/info` over the loopback API, or watch the sidecar log in the Console.

## Implementation shape

`features/slash.js` holds pure parsing and matching: `parse(prompt, names)`, `typingPrefix(prompt)`, `matches(prefix, names)`, `lcp(strs)`. It has no handlers and no registry.

The runtime registry lives in `main.js` as `SLASH_COMMANDS`, each entry `{ name, kind, hint, run }`. This is a deliberate departure from the original plan, which put the registry in `slash.js`: the handlers touch chat state directly -- `messages`, `persistActiveChat`, the composer DOM -- so they stay next to it. `main.js#send()` calls `parse()` and delegates to `cmd.run(body, raw)`.

## Remaining phases

Phase 1 -- registry plumbing and Tab completion -- is done. The rest is unbuilt:

- **`/system`, `/clear`, `/new`, `/help`.** Chat-augmenting commands, the smallest blast radius. `/help` needs the registry to expose names and hints, which it already does.

- **`/transcribe`, `/server`, `/batch`, `/docs` (alias `/rag`), `/models`.** Navigation commands, mostly pane-router calls. `/transcribe <file>` needs the pane to accept a prefilled path.

- **`/image <prompt>`.** An inline txt2img turn. Bigger than the rest because the result is an image attachment; reuses the multimodal attachment renderer.

- **Dropdown UI and `/preset`.**

Nothing above is scheduled. Each is a registry entry plus a handler, which is the point of the shape.

## Open questions

- **Argument grammar.** Sub-flags (`/agent --no-tools <task>`) or positional only? Positional, in practice: every registered command takes one body string, and tool config lives in the Agents pane. Settled unless a command needs two arguments.

- **`/help` rendering.** Inline assistant bubble, modal, or a transient line. The transient-line machinery already exists (`systemLine`, used by Tab completion) and keeps help out of persisted chat state, so that is the cheap answer.

- **Cancellation.** The Send button doubles as Stop and dispatches to whichever handler is current -- `currentAgentJob.cancel()` for agent runs, the fetch abort controller for raw chat. A future `/image` would join the job path.

Answered by what shipped: navigation commands add nothing to scrollback. `/agent-workflow` clears the composer and switches pane silently.
