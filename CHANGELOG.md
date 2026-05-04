# Changelog

All notable changes to cyllama-desktop are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (Phase 3 - Hardware controls)
- **Hardware section in the Models tab.** Six controls: `n_gpu_layers`
  (-1 = all on GPU), `n_ctx` (blank = model default), `n_batch`,
  `main_gpu`, `split_mode` (None / Layer / Row tensor-parallel),
  `tensor_split` (CSV ratios). Persisted via the existing
  `PARAM_DEFAULTS` machinery; treated as load-time fields, not
  per-call sampling.
- **`POST /hardware/estimate-layers`** wraps `cyllama.estimate_gpu_layers`.
  Body: `{model_path, gpu_memory_mb, ctx_size?, batch_size?, ...}`.
  Returns the `MemoryEstimate` shape (`n_gpu_layers, n_layers_total,
  model_size_mb, kv_cache_mb, compute_buffer_mb, fits_fully, notes`)
  flattened to JSON. Surfaces a typed **501** when the helper is missing
  in the installed cyllama.
- **Estimate Layers button** in the Hardware section header. Prompts
  for available VRAM, calls the endpoint, drops the suggested
  `n_gpu_layers` into the input, and shows a one-line breakdown
  (model size / KV cache / fits-fully / notes) underneath.
- **Hardware-aware LLM cache.** `_get_llm` is now keyed on
  `(model_path, hw_signature)` rather than just `model_path`. Sampling
  tweaks (temperature etc.) reuse the cached LLM; **changing any
  load-time field evicts and reloads** because cyllama applies them at
  construction time. `_LOAD_KEYS` enumerates the load-time fields;
  `_hw_signature(params)` produces a stable hashable digest of just
  those fields. `tensor_split` is normalised through
  `_coerce_tensor_split` (accepts list[number] or CSV string).
- LLM construction now passes the load-config:
  `LLM(model_path, config=load_cfg)`. Same `_GC_ACCEPTED` filter
  applies, so a hardware field cyllama doesn't accept is silently
  dropped instead of raising `TypeError`.

### Added (Phase 2 - Presets + signature introspection)
- **Presets**: built-in seeds (Default / Creative / Precise / Code /
  Long-context) plus user-saved bundles in `localStorage` under
  `presets_v1`. Active preset persisted in `presets_v1_active`.
  Dropdown lives at the top of the Models tab Sampling section, with
  Save (`+`) and Delete (`×`) actions; delete is disabled for built-ins.
  Bundles cover sampling fields + system prompt only -- a
  `SAMPLING_KEYS` allowlist excludes hardware so switching presets
  doesn't clobber the user's hardware setup. Old bundles with
  hardware keys are forward-compatible (those keys are skipped on
  apply).
- **`GenerationConfig` signature introspection.** Sidecar reads
  `inspect.signature(GenerationConfig.__init__)` once at module load
  into `_GC_ACCEPTED`. `_build_config` filters whitelisted kwargs
  through this set: a renderer slider for a forward-looking field
  (e.g. `presence_penalty`, `mirostat`) is silently dropped instead
  of raising `TypeError` mid-chat. Auto-adapts to future cyllama
  upgrades without sidecar changes.
- **`/info.supported_params`** advertises the names the installed
  cyllama actually accepts. Renderer's `applySupportedParams()` calls
  this on init and hides any `.param` row whose key isn't supported,
  so the UI surfaces what the runtime can actually do.
- Sampler whitelist gained `presence_penalty`, `frequency_penalty`,
  `mirostat`, `mirostat_tau`, `mirostat_eta`, but cyllama 0.2.15
  doesn't accept these in `GenerationConfig` -- the rows render in
  the HTML and stay hidden until cyllama exposes the fields. See
  TODO.md "Forward-looking sampler fields" for the verification
  command on next bump.
- **Mirostat visibility toggle**: tau/eta rows (`data-mirostat-only`)
  hide when Mirostat is Off or when the parent row is itself hidden
  by `applySupportedParams()`.

### Added (Phase 1 - Models tab)
- Right sidebar tabbed: **Models / Agents / General**. Tab strip is
  sticky at the top; body scrolls. Active tab persisted in
  `localStorage` under `right_tab_active`. Cog button on the nav rail
  jumps to the General tab (`data-tab-jump="general"`).
- **Models tab** packs everything model-related: cached models list
  (compact rows, double-click to use), HuggingFace download box with
  debounced peek-on-input + live progress bar, drag-drop dropzone,
  selected-model card with `Use in Chat` / `Reveal` / collapsible
  metadata. Below it: System Prompt (preserved verbatim from the old
  panel) and Sampling sliders.
- **`GET /models/cached`**: lists local + HF-cache GGUF files, deduped,
  local-first. Items: `{path, name, size, source: 'local'|'hf', dir}`.
- **`POST /models/inspect`**: returns GGUF metadata via
  `cyllama.GGUFContext` with defensive ctor / getter probing across
  versions; reports `{error: ...}` rather than crashing when the
  helper is missing.
- **`POST /models/import`**: server-side copy of a dropped path into
  `MODELS_DIR`. No-op when the path is already inside; rejects
  non-`.gguf`; 409 on collision.
- **`POST /models/hf/peek`**: HEAD against the HF resolve URL for size
  + existence + already-local check. Returns
  `{repo, revision, file, size, exists, target_path, already_local}`.
- **`POST /jobs/models.hf-download`**: streaming download with
  per-percent progress events through the Phase 0 `/jobs` machinery.
  Writes to `MODELS_DIR/_hf/<repo_slug>/<file>`. `_parse_hf_target`
  accepts a full `https://huggingface.co/<repo>/resolve/<rev>/<path>`
  URL, `{repo, file, revision}`, or `user/repo:path/file.gguf`
  shorthand.
- **General tab**: About info from `/info` (cyllama version, backends,
  models/artifacts dirs) plus a Preferences placeholder. Replaces the
  earlier Settings modal overlay.
- **`GET /info`** result is computed once at module load (`_INFO_CACHE`)
  rather than re-probing `_backend` per request -- the data is static
  for the lifetime of the process.
- **Agents tab** placeholder describing the later-phase agent / persona
  / tool surfaces.
- **`ModelPicker` dropdown** attached to the chat top-bar pill. Lists
  Local + HuggingFace cache groups with sizes. Ends in a
  "Browse..." item that falls back to the OS file dialog. Replaces
  the previous direct file-dialog click handler.
- New IPC `shell:revealItem` (preload `window.cyllama.revealItem`)
  for Reveal-in-Finder/Explorer on selected models.
- Main creates `<userData>/models/`, passes via
  `CYLLAMA_SIDECAR_MODELS`. `/info` reports `sidecar.models_dir`.

### Added (Phase 0 - Foundations)
- **`GET /info`**: `cyllama.__version__`, GPU backend flags via defensive
  `_backend` introspection, sidecar artifact + models paths.
- **`/jobs/*` infrastructure** for any long-running work the renderer
  needs progress / cancel / result for. Stable shape:
  - `Job` class (id, kind, state, queue, subscribers).
  - `register_job_kind(name)` gate; unknown kinds 400.
  - `run_job(kind, coro_factory)` spawns + manages an `asyncio.Task`.
  - `_emit(job, event)` pushes events with `{type: "progress" |
    "log" | "result" | "error" | "done"}` semantics. `done` is
    appended automatically.
  - `GET /jobs` lists; `GET /jobs/{id}`, `GET /jobs/{id}/events`
    (single-subscriber SSE), `POST /jobs/{id}/cancel`,
    `GET /jobs/{id}/result`, `GET /jobs/{id}/artifact/{name}` (regex-
    validated names + traversal guard against `MODELS_DIR/<id>`).
  - GC drops terminal jobs older than 1 hour.
  - Demo job kind (`POST /jobs/demo`) for renderer SSE smoke tests.
- **Artifact dir env wiring.** Main creates `<userData>/artifacts/`,
  passes via `CYLLAMA_SIDECAR_ARTIFACTS`. Sidecar falls back to
  `~/.cache/cyllama-desktop/artifacts/` for non-Electron smoke tests.
- **esbuild + renderer module split.** `src/renderer/src/{main.js,
  lib/sidecar.js, lib/jobs.js}` -> bundles to
  `src/renderer/dist/renderer.js` via
  `esbuild ... --bundle --format=iife --target=chrome120`.
  `index.html` loads the bundle. New scripts: `npm run build:renderer`,
  `npm run watch:renderer`. `start` / `dev` / `build:*` gate on
  `build:renderer`.
- **`lib/sidecar.js`**: loopback bearer-auth HTTP client.
  `getSidecarInfo()` lazy-resolves the preload bridge; `sidecarFetch`,
  `sidecarJson`, `getInfo` are the consumer-facing helpers.
  `window.cyllamaLib.sidecar` exposes them on a global namespace for
  feature modules added in later phases.
- **`lib/jobs.js`**: `JobHandle` class + `startJob(kind, body)` that
  POSTs `/jobs/<kind>`, opens an SSE stream, dispatches events to
  listeners, and exposes `done` Promise + `cancel()`. Used by the
  Models tab HF download wiring.
- **Pytest suite** (`tests/`, 50 cases). Stubs `cyllama` so tests run
  without the real install. Coverage: bearer auth, `/health`, `/info`,
  `/tokenize`, `/chat` validation + SSE framing, `/jobs` lifecycle
  (success / failure / cancel / artifact), `/models/*` endpoints,
  HF parsing, HF peek/download (httpx mocked), hardware fields,
  `_get_llm` cache reuse vs reload semantics, `/hardware/estimate-
  layers` happy/error/501 paths.
- `make test` target: installs `pytest + fastapi + httpx` into the
  bundled env on demand.

### Added (general)
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
- **Auth middleware bug fix.** The middleware previously raised
  `HTTPException(401)` from inside Starlette's `BaseHTTPMiddleware`,
  which doesn't propagate cleanly: TestClient surfaced it as an
  unhandled 500. Returns `JSONResponse({"detail": "unauthorized"},
  status_code=401)` directly now. Caught by the new pytest suite.
- **cyllama pinned to `0.2.15`.** `scripts/build-python-env.sh` reads
  `CYLLAMA_VERSION="${CYLLAMA_VERSION:-0.2.15}"` and runs
  `pip install cyllama==${CYLLAMA_VERSION}`. Bump in one place to
  roll the bundler forward; existing envs upgrade in place via
  `build/python-mac-arm64/bin/python3 -m pip install --upgrade
  cyllama==0.2.15`.
- **`/chat` routing.** `_get_llm` now takes the `params` dict so the
  cache match logic can see the hardware signature. `/tokenize` and
  `/chat` both forward it.
- **Sidecar `/info` is cached.** Built once at module import into
  `_INFO_CACHE`; the endpoint is now a constant-time read of that
  dict instead of re-probing `_backend` per request.
- **Renderer entry moved.** `src/renderer/renderer.js` -> bundled
  output at `src/renderer/dist/renderer.js`. Source under
  `src/renderer/src/`. Existing chat logic preserved verbatim in
  `main.js` so the chat hot path is unchanged.
- **Right sidebar reshaped from a single panel to tabbed layout.**
  Old `.params-panel` was a flat scrolled list (System Prompt +
  Sampling). New layout has a sticky tab strip (Models / Agents /
  General) and per-tab scrolling body. Existing param IDs
  (`#p-temperature`, `#p-system_prompt`, ...) preserved verbatim so
  the chat code didn't need touching.
- **Postinstall hook trimmed** from `electron-builder install-app-deps
  && npm run vendor` down to just `npm run vendor`. The
  `install-app-deps` step had no native modules to rebuild and was
  hanging on fresh trees (the README's known-issue note). Removing
  it makes `make` reliable.
- Sidecar SSE wire format: each chunk is now `data: {"text": "..."}` JSON
  rather than ad-hoc backslash-escaped text. Errors emit `data: {"error": "..."}`.
- cyllama installed from PyPI by default in the bundler script; local
  checkouts opt in via `CYLLAMA_SOURCE=/path/to/cyllama`.
- Chat UI moved off iMessage-style bubbles; messages are document blocks with
  role labels, no avatars or chat-bubble styling.

### Removed
- **Workspace router and full-pane Models workspace.** A short-lived
  Phase 1 attempt put Models on its own nav-rail entry as a workspace
  spanning the right three grid columns. Replaced by the tabbed-right-
  sidebar layout, which keeps chat as the single middle-pane content
  and packs all model-related UI into the Models tab.
- **Settings modal overlay.** Replaced by the General tab in the right
  sidebar, which absorbs the About info (cyllama version, backends,
  paths) and reserves space for future preferences.
- **Models nav-rail entry.** No longer needed; the Models tab is the
  single surface for model browsing / inspection / download.
- Non-functional placeholder UI: New Folder row, sidebar overflow menu,
  chat-bar split/more buttons, attach/tools composer buttons, hammer
  parameters tab, six fake collapsible parameter rows. Replaced the right
  panel content with an honest "No parameters wired yet" empty state.

### Infrastructure
- **esbuild** added as a devDependency. `npm run build:renderer`
  bundles `src/renderer/src/main.js` (IIFE, target `chrome120`,
  inline sourcemap) into `src/renderer/dist/renderer.js`. Watcher
  available via `npm run watch:renderer`.
- **`make test`** target: introspects whether pytest/fastapi/httpx
  are present in the bundled env (or a host `python3`) and pip-
  installs them on demand before running the test suite.
- **PLAN.md** added: phased rollout for exposing cyllama's full
  feature surface (chat, embeddings, RAG, agents, server, multimodal,
  GGUF tools, HF download). Workspace personas covered via
  progressive disclosure rather than a global advanced mode. Section
  12 records the resolved architectural calls (single sidecar
  process, paste-URL HF download, esbuild in Phase 0, no
  OpenAI/LangChain shims in-app, Windows backend strategy parked).
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
