# Changelog

All notable changes to cyllama-desktop are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Per-chat system prompt: prompt is now stored on the chat's JSON file and loaded into the right-panel textarea when switching chats. Editing the textarea on the unsaved working chat also updates the default seed in `localStorage`, so a future "New chat" inherits the change. Saved chats snapshot their prompt on first save and don't retroactively change when the default is edited.
- Real token counts via a new `/tokenize` sidecar endpoint (`{model_path, text}` -> `{count}`). The renderer calls it in `persistActiveChat()` so the count stored on disk and shown in the sidebar row is the true tokenizer count, not the `chars/4` estimate. Estimate retained as a fallback for the unsaved chat row and for cases where no model is loaded.
- Eject actually unloads the model. New `/unload` endpoint releases the cached `LLM` instance (frees GPU memory). The renderer's eject button calls it before clearing the model path.
- Message hover actions: copy and regenerate. Copy yields the original markdown (raw stream content stashed on the exchange's `data-asst-raw` attribute) rather than the rendered HTML's `textContent`. Regenerate trims history back to before this exchange's user turn, removes the corresponding DOM nodes, and re-runs the send path with the original prompt.
- Console panel: the nav-rail Terminal icon now toggles a slide-up sidecar log. Main process captures stdio in a 2000-line ring buffer with per-stream carryover (so partial lines get joined cleanly), pushes each completed line to the renderer via `webContents.send('sidecar:log', entry)`. Panel is lazy-populated on first open from `log:recent`; live updates accumulate after that. stderr lines are color-coded; auto-scroll only when the user is near the bottom.
- Last-used model is restored on app launch via `localStorage["last_model_path"]`. Eject clears the persisted value (so an explicit unload sticks across restarts). On launch, the path is validated via a new `fs:exists` IPC handler; stale paths (file moved or deleted between sessions) are dropped silently instead of showing a phantom name in the pill.
- Persistent chat history: each chat is stored as a single JSON file under `app.getPath('userData')/chats/<uuid>.json`, written atomically via `tmp + rename` so a crash mid-write can't leave a torn file. Main-process IPC surface (`chats:list / load / save / delete`) is gated by a strict ID allow-pattern (`/^[A-Za-z0-9_-]{6,128}$/`) to prevent path traversal. The renderer's sidebar lists real chats sorted by `updatedAt`, hover reveals a destructive delete button (with confirm), click switches chats, replay re-renders message history through the same markdown + KaTeX pipeline as the live stream. Auto-save fires after each completed assistant turn (first save assigns the id and `createdAt`). The active chat ID survives reload via `localStorage`; falls back to "most recent" or empty state if not found.
- "New chat" creates an unsaved working chat that lives only in memory until its first turn completes; an unsaved working chat shows up at the top of the sidebar with the same UI affordances minus delete.
- Title auto-derives from the first user message (truncated to 60 chars). No rename UI yet.
- Multi-turn chat context: renderer maintains an in-memory `messages: [{role, content}, ...]` list per chat. Each `/chat` request sends the full history (`{messages: [...]}`); the sidecar always uses `llm.chat()`. Mid-stream aborts persist the partial assistant turn (without the visual `(stopped)` marker, so the model doesn't see UI sugar on the next turn). Aborts before any tokens drop the matching user turn so history stays balanced.
- "New chat" button in the sidebar header resets the message list, clears the conversation pane, restores the empty state, and zeroes the token counter.
- `/chat` body schema bumped: `messages` is the new canonical field; legacy `prompt` + `system_prompt` still accepted for ad-hoc curl callers but no longer emitted by the renderer. Validation rejects malformed message lists with `400`.
- Fixed: short user prompts no longer render in a doubled-height box. The user-prompt CSS used `white-space: pre-wrap` left over from the plain-text rendering era; combined with `marked.parse()`'s trailing `\n`, that newline rendered as a visible blank line. Switched to `display: flex; gap` for paragraph spacing so it's robust against trailing text nodes from marked.
- Sampling controls in the right panel: `temperature`, `top_p`, `top_k`,
  `min_p`, `repeat_penalty`, `max_tokens` (sliders with live readouts),
  and `seed` (number input; blank = non-deterministic). Reset-to-defaults
  button next to the parameters tab. Values persist across sessions in
  `localStorage` under the `params` key.
- `/chat` request body grew a `params` object. The sidecar coerces it
  through a server-side whitelist (`_ALLOWED_PARAMS`) into a cyllama
  `GenerationConfig`, which is passed to `llm(..., config=)`. Unknown
  keys and unparseable values are silently dropped (defense in depth
  against stale `localStorage` and arbitrary client input).
- Stop sequences: comma-separated input in the Sampling section. Renderer
  splits/trims into a list, sidecar accepts either list or CSV string and
  threads through `GenerationConfig.stop_sequences`. Persists with the
  rest of the params bundle.
- System prompt: dedicated textarea in the right panel above Sampling,
  persisted under its own `localStorage` key so it can't be polluted by
  legacy params blobs. Sent at the top level of the request body. When
  non-empty, the sidecar switches from `llm(prompt, ...)` to
  `llm.chat(messages, ...)`, which formats the prompt through the
  model's GGUF chat template (cyllama's Jinja path covers Gemma etc.).
- Real cancellation: stop button now aborts generation on the cyllama side,
  not just the HTTP stream. Client disconnect (renderer `AbortController`)
  triggers `asyncio.CancelledError` in the SSE handler, which calls
  `llm.cancel()`. Two layers fire:
  - Python `threading.Event` polled between tokens (sub-millisecond latency
    in the steady-state generation loop).
  - C-level `bint` flag read by a nogil ggml_abort_callback to cancel
    mid-`llama_decode` (matters for long prompt prefill).
  Requires cyllama >= 0.2.14 (`LLM.cancel()` and `LlamaContext.install_cancel_callback()`).
- Incremental markdown rendering: a stable-boundary detector splits the
  streaming buffer into a committed prefix (parsed once, frozen) and a tail
  (re-parsed each animation frame). Boundaries are last `\n\n` outside open
  fenced code blocks and `$$...$$` math.
- KaTeX live-rendering inside both the user prompt and the assistant block,
  with `$..$`, `$$..$$`, `\(..\)`, `\[..\]` delimiters.
- User prompt is now markdown-rendered (previously displayed as raw text).
- Collapsible left (chat list) and right (parameters) panels via topbar
  toggles; collapse state persisted in `localStorage`.
- Send button gating via `updateSendEnabled()`: disabled when no model is
  picked or sidecar is not connected; tooltip explains why. Enter respects
  the disabled state.
- Auto-dismissing system/error toasts (4s fade-out).
- Stop button: in-flight requests can be aborted via `AbortController`.
- LM Studio-style three-pane workspace: 52px nav rail, chat list, document-
  style conversation, parameters panel.
- Centered model pill in topbar with file-picker click-through and eject.
- Status indicator (idle / ready / busy / error) with port suffix.

### Changed
- Sidecar SSE wire format: each chunk is now `data: {"text": "..."}` JSON
  rather than ad-hoc backslash-escaped text. Errors emit `data: {"error": "..."}`.
- cyllama installed from PyPI by default in the bundler script; local
  checkouts opt in via `CYLLAMA_SOURCE=/path/to/cyllama`.
- Chat UI moved off iMessage-style bubbles; messages are document blocks with
  role labels, no avatars or chat-bubble styling.

### Removed
- Non-functional placeholder UI: New Folder row, sidebar overflow menu,
  chat-bar split/more buttons, attach/tools composer buttons, hammer
  parameters tab, six fake collapsible parameter rows. Replaced the right
  panel content with an honest "No parameters wired yet" empty state.

### Infrastructure
- Vendored `marked` and `katex` into `src/renderer/vendor/` via a
  `postinstall` script so the strict `script-src 'self'` CSP can stay.
- Makefile orchestrates the full build: `make` -> dmg, `make dev`, `make
  python`, `make clean`, `make reset`.
- `python-build-standalone` based bundler at
  `scripts/build-python-env.sh` produces `build/python-<os>-<arch>/`.
- electron-builder config wired for macOS (arm64 dmg by default), with stub
  targets for Windows (NSIS) and Linux (AppImage).
- `scripts/notarize.js` afterSign hook: no-op unless `APPLE_ID`,
  `APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID` are set.

## [0.1.0] - 2026-04-25

Initial scaffold. Electron shell + Python sidecar (FastAPI/uvicorn) wrapping
`cyllama.LLM` with bearer-token auth, `/health` and `/chat` endpoints, parent-
PID watchdog, and SSE streaming to a minimal renderer.
