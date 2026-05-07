# Slash Commands

A unified plan for slash-prefixed commands typed into the chat composer.
Slash commands are a single, discoverable entry point that can either
**run an action** (kicks off a job, results render inline) or **navigate**
(switches the renderer to a different pane / view).

## Goals

- One typing surface. No duplicated run UIs across sidebar tabs and
  composer.
- Inline results in the chat stream where it makes sense, so a
  conversation can mix chat turns and tool runs.
- Discoverable: `/` opens an autocomplete dropdown; Tab completes the
  unique prefix.
- Cheap to extend: adding a command is one entry in a registry plus a
  handler.

## Non-goals (for v1)

- Full shell-like argument parsing (no quoting rules, no flags). Args
  are "everything after the first space".
- Pipelines / chaining (`/agent ... | /transcribe ...`). Defer.
- Persistent slash-command history beyond what the chat already
  records.

## Command taxonomy

Two kinds, both registered the same way:

| Kind         | What it does                                       | Example uses                |
|--------------|----------------------------------------------------|-----------------------------|
| `action`     | Runs a job; result renders inline as an exchange.  | `/agent`, `/image`          |
| `navigate`   | Switches the active pane / view; no chat output.   | `/transcribe`, `/server`    |

A command can also be **chat-augmenting** (mutates chat state without
running a job): `/system`, `/clear`, `/new`, `/preset`.

## Proposed registry

| Command       | Kind     | Args                | Behavior                                                                                  |
|---------------|----------|---------------------|-------------------------------------------------------------------------------------------|
| `/agent`      | action   | `<task>`            | Run ReAct agent with sidebar tool config; trace + answer inline. **Done.**                |
| `/image`      | action   | `<prompt>`          | txt2img using the active SD model; render generated image as the assistant turn.          |
| `/transcribe` | nav      | (optional file)     | Switch to Transcribe pane; if a path is supplied, prefill it.                             |
| `/server`     | nav      | (none)              | Switch to Server pane.                                                                    |
| `/batch`      | nav      | (none)              | Switch to Batch pane.                                                                     |
| `/docs`       | nav      | (none)              | Switch to Documents (RAG) pane. Alias: `/rag`.                                            |
| `/models`     | nav      | (none)              | Switch to Models pane.                                                                    |
| `/preset`     | augment  | `<name>`            | Load a saved parameter preset for this chat.                                              |
| `/system`     | augment  | `<prompt>`          | Replace the system prompt for this chat (renders a small system bubble).                  |
| `/clear`      | augment  | (none)              | Clear the current chat's messages (with confirm).                                         |
| `/new`        | augment  | (none)              | Start a new chat.                                                                         |
| `/help`       | augment  | (optional `<cmd>`)  | List commands / show help for one.                                                        |

Anything not in the registry stays a plain chat turn (no error, no
intercept) so users can type literal slashes.

## Parsing rules

- A slash command requires the prompt to begin with `/` followed by an
  ASCII letter. Whitespace before counts: `   /agent x` is also a
  command. (Matches typical chat-app behavior.)
- Token 1 (up to the first space) is the command name.
- Everything after the first space (trimmed) is the body, passed as
  one string. Commands parse their own args.
- Empty body is allowed — the handler decides if it's an error.
- To send a literal `/agent` as chat (rare), users prefix with a
  zero-width or escape: deferred until anyone asks.

## Autocomplete (Phase 1)

When the prompt matches `^\s*/[A-Za-z]*$`:

1. **Tab** completes the unique prefix. `\a` + Tab → `/agent`.
   - Single match: replace, append a space.
   - Multiple matches: complete to the longest common prefix; show the
     options inline below the composer.
   - No match: bell (visual flash on the composer border).
2. **Dropdown** (optional, Phase 1.5): typing `/` opens a small
   absolutely-positioned list above the composer with the registry,
   filtered by the current prefix. Arrow keys navigate, Enter selects,
   Esc dismisses.
3. **Enter** without disambiguation behaves as today (sends the prompt
   verbatim). The handler resolution happens in `parseSlashCommand`.

## Implementation shape

A single registry module, e.g. `src/renderer/src/features/slash.js`:

```js
export const COMMANDS = [
  { name: "agent",      kind: "action",  hint: "<task>",   run: (body) => sendAgent(body, raw) },
  { name: "image",      kind: "action",  hint: "<prompt>", run: (body) => sendImage(body, raw) },
  { name: "transcribe", kind: "nav",     hint: "(file?)",  run: (body) => openTranscribe(body) },
  // ...
];

export function parse(prompt) { /* returns {cmd, body, raw} or null */ }
export function match(prefix) { /* returns COMMANDS filtered by prefix */ }
```

`main.js#send()` calls `parse(prompt)`; if a registry entry matches, it
delegates to `cmd.run(body, raw)`. `sendAgent` etc. become
`run` handlers and lose their direct coupling to `send`.

## Phasing

- **Phase 1 — autocomplete + registry plumbing.** Move the existing
  `/agent` handling into the registry. Implement Tab completion. No
  new commands.
- **Phase 2 — `/system`, `/clear`, `/new`, `/help`.** Chat-augmenting
  commands; smallest blast radius after Phase 1. `/help` requires the
  registry to expose names + hints.
- **Phase 3 — `/transcribe`, `/server`, `/batch`, `/docs`, `/models`.**
  Navigation commands; mostly pane-router calls. `/transcribe <file>`
  needs the pane to accept a prefilled path.
- **Phase 4 — `/image <prompt>`.** Inline txt2img turn. Bigger because
  the result is an image attachment, not text; reuses the multimodal
  attachment renderer.
- **Phase 5 — dropdown UI and `/preset`.** UX polish + presets.

## Open questions

- **Argument grammar.** Do we want sub-flags (`/agent --no-tools <task>`)
  or keep it positional? Recommendation: positional only; tool config
  stays in the sidebar.
- **History semantics for nav commands.** Should `/transcribe` show up
  in chat scrollback at all? Recommendation: no — nav commands clear
  the composer and switch panes silently. They're not "turns".
- **`/help` rendering.** Inline assistant-style bubble vs. modal vs.
  console-style line. Recommendation: a transient line (matches
  `errorLine` style) so it doesn't pollute persisted chat state.
- **Cancellation.** Same Stop button must dispatch to whichever
  long-running handler is current. Already handled for `/agent`;
  `/image` will join the same path.
