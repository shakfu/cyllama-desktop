# Changelog

All notable changes to cyllama-desktop are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (system-style Preferences window)
- **`Settings...` menu item** with the standard `Cmd+,` accelerator,
  available from the application menu on macOS and the File menu on
  Windows / Linux. Routes through a new `prefs:open` IPC.
- **Standalone Preferences `BrowserWindow`** under
  `src/preferences/{index.html, preload.js, main.js, prefs.css}`,
  bundled by a second esbuild target appended to `npm run
  build:renderer`. Single-instance: re-focuses the existing window
  when the user picks Settings... again. Loads the main renderer's
  stylesheet for shared `--*` variables + `.btn` / `.btn-mini` /
  `.mono` so the visual language stays consistent without
  duplicating CSS.
- **Scoped preload** exposes only what Preferences needs:
  `settings.{get,set}`, `getSidecarInfo`, `restartSidecar`,
  `pickFolder`, `revealItem`, `log.{recent,subscribe}`. The chat
  IPC (chats / pickModel / pickAudio / pickImage) is deliberately
  omitted -- the Preferences window has no business with chat
  state.
- **Four tabs** in the Preferences sidebar:
  - **General** placeholder for theme / default sampling preset /
    window behavior settings (lands here in follow-ups).
  - **Models** -- model directories editor (was the General right
    tab's Preferences section). Adds / removes extra read-only
    scan roots, persists via `settings.set`, restarts the sidecar
    so the new env splice takes effect.
  - **Sidecar** -- read-only About (cyllama version + backends) +
    Paths (models, artifacts, rag, uploads -- each clickable to
    Reveal in the OS file manager) + Devices + Capabilities.
  - **Logs** -- live tail of the sidecar stdout/stderr ring
    buffer, same source as the main window's Console drawer.
    Lazy-fetches the recent backlog on first click; live updates
    subscribe at window load so tabs are current the moment they
    open.

### Changed (right sidebar -> chat-side controls only)
- **General right-tab removed.** Right sidebar now shows two tabs:
  Parameters and Agents. The cog nav-rail button is rebranded
  "Settings (Cmd+,)" and opens the Preferences window via
  `window.cyllama.openPreferences()`.
- The `general-tab.js` module is no longer imported / mounted from
  main.js. The file is retained on disk in case a follow-up wants
  to re-mount Preferences-shaped controls inline somewhere; the
  underlying `<userData>/settings.json` schema is unchanged.
- Two Playwright specs updated: "right-sidebar tabs are Parameters
  + Agents only" replaces the old General right-tab smoke; "cog
  nav-rail opens the Preferences window" exercises the second
  BrowserWindow end-to-end via Playwright's `electron.waitForEvent
  ("window")`, asserting all four category tabs render with
  General as the default-active.

### Changed (Models promoted to a full-area pane)
- **`.app[data-pane]` mode** introduced. Default `"chats"` keeps the
  original `nav-rail | sidebar | main | params-panel` grid; switching
  to `"models"` hides those children and renders a full-area pane
  spanning grid columns `2 / -1`. `data-pane-jump="<name>"` on
  nav-rail buttons drives the switch; `setPane()` mirrors
  `setSidebarView()` for the new layout mode. Other sidebar-views
  (Documents, Transcribe, Image, Server, Batch) are still
  sidebar-view-shaped today; promoting them to full panes is one
  CSS rule + one button-attribute change away.
- **New Models pane** (`features/models-pane.js`) replaces the
  Models *sidebar-view*. Three columns:
  1. **Subnav** -- kind-filtered category list (View All / Chat /
     Multimodal / Whisper / SD / Embedding / Unknown), each with a
     count, empty buckets hidden.
  2. **Main** -- header with filter input + an "Add a model"
     inline block (HF URL → Download with peek + progress bar);
     table with kind / name / size / source columns, sticky
     header, click-to-select rows; footer with total count + disk
     usage + revealable models-dir path.
  3. **Detail rail** -- "Use in Chat" + "Set as mmproj" actions,
     structured Model Information populated from
     `/models/inspect` (Arch / Quantization / Parameters / Context
     / Name), Source File path with Reveal button.
- **Drag-drop import** rebound to the pane root: drop a `.gguf`
  anywhere on the Models pane and it copies into MODELS_DIR via
  `/models/import`.
- The right-tab "Models" sidebar-view (one-column accordion)
  introduced in the previous IA change is gone; that role is fully
  served by the full-area pane now.
- Two Playwright specs added: full-area layout shows subnav +
  table + detail rail with the chat children hidden; switching
  back to Chats restores composer + send.
- **Regression to track**: the quantize tool from the old
  right-sidebar Models tab isn't reattached to the new Models pane
  yet. The `/jobs/models/quantize` endpoint is unchanged and the
  Tools UI from `models-tab.js` lives on for reference; needs a
  small re-mount as a Tools subsection (or a per-row "Quantize..."
  action in the detail rail). Logged in TODO.md.

### Changed (right-tab IA: LMStudio-style "Parameters")
- **Right-sidebar tab renamed Models → Parameters.** The
  `data-tab="models"` identifier stays for back-compat (router +
  Playwright `openRightTab` keep working), only the visible label
  changes. The tab is now strictly *per-chat session
  configuration*, no model-management content.
- **Sections restructured as collapsibles** (LMStudio order):
  Preset (presets bar promoted to its own section at the top),
  System Prompt (open), Sampling (open) -- now without the
  "Advanced" disclosure -- Structured Output (Grammar GBNF + From
  JSON Schema; was nested under Sampling Advanced), Speculative
  Decoding (was under Advanced), N-gram Cache (was under
  Advanced), Settings (renamed from Hardware), Retrieval. Each
  collapsible is a native `<details>` with a custom rotating
  chevron and click-blocked icon-btn affordances (Reset / Refresh
  / est) so toolbar interactions don't accidentally toggle the
  fold.
- **Model picker / HF download / metadata inspector / quantize /
  mmproj projector pin moved out of the right tab** into a new
  **Models** left-nav-rail sidebar-view. `#modelsTabHost` →
  `#modelsPaneHost`. `models-tab.js` retained as the
  implementation; it just mounts into a different container now.
  The sidebar-view's `onShow` hook re-runs `models-tab.refresh()`
  so a freshly downloaded / quantized / dropped GGUF surfaces
  without manual reload.
- The existing presets module's mount target switched from the
  Sampling section to the new Preset section, with a fallback to
  Sampling so a future layout change can drop the dedicated
  header without breaking the bar.
- Two Playwright specs updated: the "Models right-tab" test
  becomes "Parameters right-tab renders LMStudio-style sections"
  (asserts the open-by-default System Prompt + Sampling and the
  collapsed-by-default Speculative). New "Models sidebar-view
  absorbs the model picker + tools" spec covers the new left-nav
  destination.

### Added (extra read-only model directories)
- **Preferences -> Model directories** in the General tab. The
  primary `<userData>/models/` (read-only label "primary") plus any
  number of user-added extras (Add directory... opens a folder
  picker; per-row Remove). Apply persists the list to
  `<userData>/settings.json` and restarts the sidecar so the new
  scan roots take effect immediately.
- **`<userData>/settings.json`**, schema
  `{ version: 1, models_extra: [string, ...] }`. Atomic
  tmp+rename writes; defensively filtered to absolute paths only so
  a hand-edited file can't smuggle relative or non-string entries
  into the launcher's env splice.
- **`settings:get` / `settings:set` / `sidecar:restart` IPC**
  exposed on `window.cyllama.settings.{get,set}` and
  `window.cyllama.restartSidecar`. The renderer fires a
  `sidecar:restarted` event after restart so the chat hot path
  refreshes its cached `{port, token}` (those rotate on each
  spawn).
- **`CYLLAMA_SIDECAR_MODELS_EXTRA`** env var, pathsep-joined
  (`:` on Unix, `;` on Windows). The sidecar's `_resolve_models_extra`
  parses it forgivingly: empty entries from leading/trailing
  delimiters, repeated paths, and the primary `MODELS_DIR` itself
  are all stripped.
- **Catalog scan widened** to include each extra root with
  `source: "external"`. Precedence on path collision (mostly via
  symlinks) is `local > external > hf` so a file accessible from
  multiple roots surfaces once with the most-authoritative tag.
  `_scan_gguf` now `Path.resolve()`s before keying the dedup map
  so a symlinked file dedups regardless of which root it was
  walked from.
- **`/info.sidecar.models_extra`** advertises the resolved roots so
  the Preferences UI can display the live state without re-reading
  `settings.json`.
- 5 new pytest cases in `tests/test_classification.py` cover the
  external source tag, the local-wins symlink dedup, the `/info`
  surface, and `_resolve_models_extra`'s blank/dup/primary
  filtering.

### Added (capability-aware model catalog)
- **`/models/cached` items gain a `kind` field**: `"chat"`, `"mmproj"`,
  `"embedding"`, `"whisper"`, `"sd"`, or `"unknown"`. Classification
  layers cheap signals first (filename `mmproj-*` → mmproj; `.bin`
  extension → whisper; `.safetensors` → sd) and falls back to
  `cyllama.GGUFContext` metadata (`general.architecture` against a
  list of embedding-family hints, plus a secondary
  `*.pooling_type`-key heuristic). Per-path cache keyed on
  `(path, mtime)` so dropdown opens don't re-inspect.
- **`?kinds=` query param** on `/models/cached`. Each picker passes
  the kinds it actually supports; the server filters server-side.
  `unknown` always passes the filter (a misclassified model
  shouldn't lock the user out of a picker), and `?kinds=all` is the
  explicit no-op for the catalog view itself.
- **Scan widened** from `*.gguf` only to `*.gguf` + `*.bin`. Whisper
  models in their conventional `.bin` form now show up in the
  Transcribe picker without manual conversion.
- **Every picker narrows by kind**:
  - Chat top-bar pill, Speculative draft, Server, Batch, Agents,
    RAG generation: `["chat"]`.
  - Multimodal projector pin (Models tab): `["mmproj"]`.
  - RAG embedding (collection create): `["embedding"]`.
  - Transcribe: `["whisper"]`.
  - Image: `["sd"]`.
  - Quantize source (Models tab): `.gguf` files only, excluding
    whisper/sd kinds (the C-side quantize path doesn't accept them).
  - Models tab catalog: no filter; the kind label is shown in the
    dropdown alongside size + source.
- **Multimodal projector picker rebuilt** as a catalog dropdown
  (mmproj-classified models from the cached list) instead of OS
  dialog only. Browse... stays as the escape hatch for files
  outside MODELS_DIR.
- 13 new pytest cases in `tests/test_classification.py` cover the
  classification heuristics (mmproj filename, whisper `.bin`,
  bert-family embedding arch, pooling_type meta hint, unknown
  fallback), the per-(path, mtime) cache invalidation on rewrite,
  and the `/models/cached?kinds=` server-side filter (single
  kind, multi-kind, unknown-passes-through, `kinds=all`,
  scan-includes-bin-files).

### Added (multimodal chat — LLAVA / MTMD)
- **Composer attach button**. A paperclip lives next to the send
  button in the chat composer; clicking it opens a hidden
  `<input type=file>` accepting PNG / JPEG / WebP / GIF / BMP. Each
  picked file is uploaded to `/chat/upload` and rendered as a
  thumbnail chip with a remove button in the composer attachments
  strip. The button is gated on both `/info.features.multimodal`
  *and* a pinned mmproj path (so attaching without a projector
  configured isn't a silent no-op).
- **`POST /chat/upload`** (multipart) writes the file to
  `<workspaces/default/uploads>/<uuid>.<ext>` with a 16 MiB cap
  enforced via streaming chunks, an extension allowlist, and a
  resolve-then-relative_to sandbox check. Returns
  `{id, name, size, path, url}`.
- **`GET /chat/upload/{name}`** serves a previously-uploaded image
  with bearer auth and the same path-traversal sandbox.
- **`/chat` body extensions**: `mmproj_path` at the top level and
  `images` per user message. When the latest user message has an
  image and an mmproj is configured, the request routes through
  `cyllama.llama.mtmd.ImageAnalyzer.answer_question` instead of
  `LLM.chat()`. The text-only message list is stripped of the
  `images` field before being passed to `llm.chat()` so cyllama's
  Jinja templater doesn't choke on unexpected dict keys. Sandbox:
  image paths must resolve under `UPLOADS_DIR` -- a hand-crafted
  body asking the analyzer to ingest `/etc/passwd` is rejected
  with 400.
- **Single-shot multimodal answers**. cyllama's
  `answer_question` returns a string rather than streaming, so
  the SSE response is one `{text}` chunk followed by `[DONE]`.
  Streaming-token multimodal would need the lower-level
  `VisionLanguageChat` generator and is deferred.
- **Multimodal projector pin** in the Models tab. A new section
  with Browse / Clear buttons stores the user-picked mmproj path
  in `localStorage` (`mmproj_path`). Setting / clearing dispatches
  a `mmproj:changed` window event which the chat init listener
  consumes to re-evaluate the paperclip's visibility without a
  reload.
- **Chat persistence schema** gains `images: [{id, name, url, path}]`
  on user messages. Replay walks the array and renders each
  attachment via the same auth-fetched blob URL pipeline used at
  send time. The blob URL cache is keyed on the artifact `url` so
  re-renders / regenerate paths reuse the same blob.
- **`<userData>/workspaces/default/uploads/`** is the new
  per-workspace uploads root, surfaced via `CYLLAMA_SIDECAR_UPLOADS`
  + `/info.sidecar.uploads_dir`. Files survive sidecar restarts so
  chat replay can serve them.
- New runtime dep: `python-multipart` (FastAPI multipart parser).
  Added to `python-sidecar/pyproject.toml`, the `make test-deps`
  install line, and both CI jobs.
- 18 new pytest cases in `tests/test_multimodal.py` cover the
  feature flag + uploads_dir info field, upload validation
  (missing file / unsupported extension / size cap / auth gate),
  the upload→serve roundtrip, the chat routing branch (image
  attached + mmproj set goes through ImageAnalyzer with the right
  args), the no-mmproj fallback to `llm.chat()`, sandbox refusal
  of out-of-uploads paths, and 400s for missing image / missing
  mmproj. Conftest grows `_FakeImageAnalyzer`,
  `cyllama.llama.mtmd` module stub, and a `model` attribute on
  `_FakeLLM` (the analyzer takes the underlying model, not the
  LLM wrapper).
- One new Playwright spec asserts the paperclip is hidden by
  default and surfaces after `mmproj_path` is set + the
  `mmproj:changed` event fires. Required restoring
  `[hidden] { display: none }` on the button + attachments strip
  (the `display: inline-flex` class rule was overriding the
  user-agent rule for the HTML `hidden` attribute).

### Added (CI workflow + image gallery)
- **`.github/workflows/ci.yml`**. Two parallel jobs on `ubuntu-latest`:
  `test` runs the pytest suite (with `--ignore=tests/e2e`), `e2e`
  runs the Playwright Electron smoke under `xvfb-run`. Both trigger
  on push-to-main and pull_request, with concurrency cancelling
  in-flight runs when a new push lands. The e2e job uploads
  `playwright-report/` + `test-results/` as a build artifact on
  failure for offline triage.
- **`GET /artifacts/image`** lists past txt2img runs as
  `[{job_id, name, size, mtime, url}]` sorted most-recent first.
  Reads `<ARTIFACTS_DIR>/<job_id>/output.png` directly off the
  filesystem rather than the job registry so the gallery survives
  sidecar restarts and the 1-hour finished-job GC.
- **`GET /artifacts/{job_id}/{name}`** serves a job artifact file
  bypassing the job registry. Distinct from
  `/jobs/{id}/artifact/{name}` for the same reason -- the gallery
  needs to load PNGs from long-finished jobs. Path traversal is
  locked down via the existing `_ARTIFACT_NAME_RE` plus a
  `relative_to(ARTIFACTS_DIR)` check.
- **Gallery section in the Image pane**. Grid of past-render
  thumbnails fetched via the existing bearer-auth flow into a blob
  URL cache (keyed on artifact URL so re-renders don't refetch).
  Click a thumbnail to load it into the main result viewer. The
  cache GCs blob URLs whose artifacts disappear on disk between
  refreshes. Refreshes after every successful new generation so the
  user sees the new render slot in immediately.
- Tests in `tests/test_image.py` cover the empty-list, populated-
  list (with mtime ordering + per-item shape), serve-returns-png,
  404 on missing artifact, 400 on regex-rejected names, and the
  auth gate.

### Added (Playwright per-pane smoke suite)
- **`tests/e2e/`** with `@playwright/test`. Nine smoke tests, one per
  surface (Chat, Documents, Transcribe, Image, Server, Batch,
  Models tab, Agents tab, General tab). Each boots Electron via
  `_electron.launch`, switches into the pane, and asserts the
  primary content rendered (header / form fields / load-bearing
  buttons). Catches the layer pytest can't reach: the IPC bridge,
  the sidecar handshake, the `/info.features` → nav-button visibility
  wiring, and per-pane lifecycle hooks.
- **Stubbed sidecar via existing pytest stubs.** Two main-process
  env hooks: `CYLLAMA_E2E_PYTHON` selects the python binary and
  `CYLLAMA_E2E_LAUNCHER` swaps the sidecar entry script. The
  Playwright harness (`tests/e2e/_harness.js`) sets both to point
  at `tests/e2e/sidecar_launcher.py`, which imports
  `tests/conftest.py` (running its `_install_cyllama_stub()` side
  effect) before importing the real `python-sidecar/sidecar.py`.
  Net effect: the renderer talks to the same FastAPI app the pytest
  suite drives, against the same stubbed `cyllama` module. No real
  cyllama, no GGUFs, no `make python` required.
- **`ELECTRON_USER_DATA_DIR` env hook** in `src/main/index.js` so
  each test gets an isolated `userData` (chats / artifacts /
  workspaces / models cache) -- mirrors `tmp_path` for Electron.
- **`make e2e`** + **`npm run test:e2e`** scripts. The e2e suite is
  a separate target; `make test` keeps stubbing pytest only and
  excludes `tests/e2e/` from collection. Suite finishes in ~10 s on
  a warm cache (Electron cold-start dominates).

### Added (Phase 9 - batch + quantize tooling)
- **`POST /jobs/batch`** wraps `cyllama.batch_generate`. Body
  `{model_path, prompts, params?, batch_size?, n_seq_max?}` where
  `prompts` accepts either a list of strings or a newline-delimited
  string (forgiving for ad-hoc curl). 1024-prompt cap. Per-result
  events stream as `{type:"result_row", index, prompt, response}`;
  the full set is also written to `outputs.jsonl` under the job
  artifact dir for download. Final `result` event carries the row
  count + artifact URL. cyllama 0.2.x doesn't expose a progress
  callback, so the wall-clock progress is coarse (one big jump
  after `batch_generate` returns); the JSONL writes incrementally
  to keep memory bounded for large batches.
- **`POST /jobs/models/quantize`** wraps
  `cyllama.llama.llama_cpp.model_quantize`. Body
  `{src_path, dst_name, ftype, nthread?, allow_requantize?,
  only_copy?}`. Output always lands in `MODELS_DIR`; `dst_name` is
  a bare filename (slashes / leading dots / paths that resolve out
  of MODELS_DIR all 400) so a hand-crafted POST can't escape the
  cache. `.gguf` extension auto-appended. 409 on collision.
  Half-written destinations are unlinked on failure so the next
  attempt isn't blocked.
- **`GET /quantize/ftypes`** returns the label→int ftype map (Q2_K
  through F32) so the renderer's dropdown drives off a single
  source of truth instead of a magic-number table.
- **`/info.features.batch`** + **`/info.features.quantize`** flags.
- **Batch sidebar view** (`features/batch-pane.js`). Reuses
  `.dp-*` primitives. Model picker, prompts textarea + Import...
  button (accepts `.txt` line-per-prompt or `.jsonl` with `prompt`
  field), Run/Stop. Live result cards stream in as the job emits
  `result_row` events. Three export buttons: client-side CSV /
  JSONL of the in-memory rows, plus a "Server JSONL" button that
  downloads the canonical artifact via the authenticated sidecar
  fetch (so it's always identical to what the sidecar wrote, even
  if the renderer's accumulator dropped a row).
- **Tools section in the Models tab.** Source model picker reuses
  the cached models list, ftype dropdown sourced from
  `/quantize/ftypes`, dest filename auto-suggested from the source
  basename + ftype. Quantize button spawns the job with live status
  + Stop. On completion fires `models:cache-changed` so the chat
  ModelPicker, Speculative draft picker, and Server / Batch / Image
  pickers all refresh to show the new file.
- Tests in `tests/test_batch_quantize.py` cover both feature flags,
  the `/quantize/ftypes` endpoint, batch validation 400s (missing
  model / missing prompts / empty prompts / over the 1024 cap),
  the 501 paths, per-row streaming + final artifact, accepting a
  newline-string prompts field, quantize validation (missing src /
  missing dst / path separators in dst / unknown ftype label /
  missing ftype / 501), success path writing into MODELS_DIR, and
  the 409 collision path. Conftest grows `_fake_batch_generate`,
  `_FakeBatchResponse`, `_fake_model_quantize`,
  `_FakeQuantizeParams`, and a `cyllama.llama.llama_cpp` module
  stub.

### Added (Phase 8 - OpenAI-compatible server)
- **`POST /server/start`**, **`POST /server/stop`**,
  **`GET /server/status`**. The start endpoint accepts
  `{kind, model_path, port?, host?, expose_lan?, n_ctx?, n_batch?,
  n_threads?, n_gpu_layers?, n_parallel?, model_alias?}` and wraps
  either `cyllama.llama.server.embedded.EmbeddedServer` (C++) or
  `cyllama.llama.server.python.PythonServer` (pure-Python). Single
  slot; double-start returns 409 with "stop it first". `/server/stop`
  is idempotent (`{ok:true, wasRunning:false}` when nothing's
  running). `/server/status` returns
  `{running, kind?, url?, model_path?, host?, port?}`.
- **Loopback by default.** Non-loopback hosts (anything other than
  `127.0.0.1` / `localhost` / `::1`) require an explicit
  `expose_lan: true` body field; otherwise 400. Setting
  `expose_lan: true` while leaving `host` at the loopback default
  promotes the bind to `0.0.0.0` so the toggle isn't a silent no-op.
  The renderer's "expose on local network" checkbox prompts for
  confirmation before flipping; the server-side gate is the
  defence-in-depth backstop.
- **`/info.features.openai_server`** flag plus
  **`/info.server_kinds`** — the latter lists per-flavour
  availability (`["embedded","python"]`, just one, or empty) so the
  renderer's kind picker hides options the build doesn't include.
- **Server sidebar view** (`features/server-pane.js`). Reuses the
  shared `.dp-*` primitives. Idle: model picker, kind selector,
  port input, expose-LAN checkbox with hint copy, Start. Running:
  URL pill + curl example (both with one-tap copy buttons), kind +
  model display, Stop. Status line color-coded for ok / err. Status
  refreshes on view show + after each start/stop, so navigating
  away and back picks up an externally-stopped server. Nav-rail
  button hidden when `features.openai_server` is false.
- Server is shut down on sidecar SIGINT/SIGTERM and via `atexit`,
  so the C-side embedded thread doesn't keep the port bound after
  the desktop quits unexpectedly.
- Tests in `tests/test_server.py` cover the feature flag, status
  default, validation 400s (missing model / missing kind / unknown
  kind / bad port), embedded + python happy paths, double-start 409,
  loopback enforcement, expose-LAN promotion to 0.0.0.0, the 500
  path when the underlying `start()` returns False, idempotent
  stop, state-clears-on-stop, the 501 path when the feature is
  unavailable, and auth gating. Conftest grows
  `_FakeServerConfig` / `_FakeServer` / `_FakeEmbeddedServer` /
  `_FakePythonServer` and a `cyllama.llama.server.{embedded,python}`
  module stub.

### Added (Phase 7 - agents)
- **`POST /jobs/agent/run`** wraps `cyllama.agents.ReActAgent.stream`.
  Body: `{model_path, task, tools, max_iterations?, system_prompt?,
  params?}`. Streams each `AgentEvent` as a `{type:"trace",
  event_type, content, metadata}` SSE frame; final result event
  carries the full event list, the answer, and a count of ACTION
  events (= effective tool-call iterations). Reuses `_get_llm` so a
  back-to-back agent run against the same model doesn't reload it.
  `max_iterations` clamped to `[1, 50]`.
- **`/info.features.agents`** flag, true when both
  `cyllama.agents.ReActAgent` and `cyllama.agents.Tool` resolve.
- **Server-side tool catalog** (`_build_agent_tools`):
  - `calculator` -- arithmetic via an AST walker that whitelists
    `BinOp`/`UnaryOp` over `int`/`float` constants. Names, calls,
    and attribute access are rejected, so
    `__import__('os').system(...)` and `(1).__class__` both surface
    as `error: ...`.
  - `read_file` -- reads UTF-8 text under a user-chosen sandbox dir.
    `Path.relative_to` checks every resolved target so a `..`
    traversal or out-of-sandbox symlink returns
    `error: refusing path outside sandbox`. 1 MiB cap.
  - `web_fetch` -- `httpx.Client.stream` GET with a 1 MiB cap and
    only `http(s)` URLs. Off by default; renderer requires explicit
    user confirmation before the toggle flips on.
  - `rag_query` -- top-k retrieve over an existing RAG collection
    via the `_get_retrieve` cache (Embedder + SqliteVectorStore),
    so it doesn't pull a second generation model into memory.
  Tool selection is "key present = intent to enable" rather than
  truthy-value -- a misconfigured tool (e.g. `read_file: {}` with no
  sandbox) returns 400 instead of silently dropping.
- **`dialog:pickFolder` IPC** (preload `window.cyllama.pickFolder`)
  for the read_file sandbox picker.
- **Agents right-sidebar tab** rebuilt from the placeholder. Form:
  model picker (cached + Browse fallback), task textarea, max-iter
  input, and a tool block with toggleable Calculator / Read file
  (sandbox folder picker) / Web fetch (confirms before enabling) /
  RAG query (collection dropdown). Run/Stop spawn or cancel the
  agent job. Live trace below the form renders each event with a
  type-tagged colored left border (THOUGHT grey, ACTION blue,
  OBSERVATION green, ANSWER amber, ERROR / CONTRACT_VIOLATION red);
  the final ANSWER also flows into a separate Answer card. Tab
  shows a "not available" notice when `features.agents` is false.
- Tests in `tests/test_agents.py` cover the feature flag, validation
  (missing model / missing task / blank task), 501 path,
  trace-then-result event ordering, max_iterations clamping, the
  read_file tool's path-escape refusal, calculator AST safety,
  rag_query 404/400 paths, and the tool-list pass-through.
  Conftest grows `_FakeReActAgent` / `_FakeAgentTool` /
  `_FakeAgentEvent` / `_FakeAgentEventType` and a `cyllama.agents`
  module stub.
### Changed (sidebar-pane UI consistency)
- The Transcribe and Image panes now reuse the same base primitives
  the Documents pane defines (`.dp-section`, `.dp-row`, `.dp-label`,
  `.dp-input`, `.dp-textarea`, `.dp-select`, `.dp-status`,
  `.dp-check-row`, `.btn` / `.btn.primary`). Bespoke `.tx-*` /
  `.img-*` layout/control classes are gone; only genuinely
  domain-specific bits remain (`.tx-segment` row + copy bar,
  `.img-grid` parameter grid, `.img-result-img` preview).
  Inputs now sit inside bordered cards with consistent label
  typography and form-control borders, matching Documents.
- `.btn` / `.btn.primary` promoted out of `.params-panel` scope so
  every sidebar view shares the same button look. `:disabled` style
  added so disabled actions read as inactive.
- Narrow-sidebar overrides (`.sv-transcribe-body`,
  `.sv-image-body`) mirror `.sv-documents-body`: `.dp-row` switches
  to column flow, `.dp-actions` stays row-aligned, `.dp-section`
  padding tightens for the ~268px column.

### Added (Phase 6 - image txt2img)
- **`POST /jobs/image/txt2img`** runs stable-diffusion text-to-image
  via `cyllama.sd.text_to_image` as a /jobs job. Body
  `{model_path, prompt, negative_prompt?, width?, height?,
  sample_steps?, cfg_scale?, seed?}`. PNG written to
  `<ARTIFACTS_DIR>/<job_id>/output.png`; result event carries
  `{artifact_name, artifact_url, width, height, seed, sample_steps,
  cfg_scale}` so the renderer doesn't have to guess the path.
  Dimensions clamped to `[64, 4096]` and steps to `[1, 200]` before
  reaching cyllama -- the C lib will accept arbitrary sizes but a
  16k×16k request silently OOMs the host.
- **`/info.features.image`** flag, true when both
  `cyllama.sd.text_to_image` and `cyllama.sd.SDImage` resolve.
- **Image sidebar view** (`features/image-pane.js`). Model picker
  (cached models + Browse... fallback), prompt + negative-prompt
  textareas, W/H/Steps/CFG/Seed grid, Generate button. Spawns the
  job; live status updates on log + progress events. On completion
  the artifact is fetched with bearer auth into a `blob:` URL so it
  satisfies the existing CSP (`img-src 'self' data:`) without
  widening to allow loopback HTTP. Stop cancels via `JobHandle`.
  Nav-rail button hidden when `features.image` is false.
- The sidecar runs `text_to_image` synchronously inside the producer
  coroutine (not via `loop.run_in_executor`) for the same reason
  RAG ingest does -- FastAPI TestClient's portal model cancels the
  producer task at request boundaries, so an executor-bound call
  races the boundary and the final result event never lands.
  Production callers route through the /jobs SSE so the synchronous
  call only blocks the job's own consumer.
- Tests in `tests/test_image.py` cover the feature flag, validation
  (missing model / missing prompt / blank prompt), 501 when
  cyllama.sd is absent, result event + artifact-on-disk shape, the
  artifact endpoint serving a PNG, and dimension/step clamping.
  Conftest grows `_FakeSDImage` + `_fake_text_to_image` and a
  `cyllama.sd` module stub.

### Added (Phase 5 - transcribe)
- **`POST /jobs/transcribe`** runs whisper transcription as a /jobs
  job. Body `{audio_path, model_path, language?, translate?,
  n_threads?}`. Producer loads the WAV via
  `cyllama.whisper.cli.load_wav_file`, resamples to 16 kHz, runs
  `WhisperContext.full` on a worker thread (releases the GIL so the
  event loop stays responsive), then emits one
  `{type: "segment", index, t0_ms, t1_ms, text}` event per segment
  with absolute-millisecond timestamps. Final `result` event carries
  the full segment list plus the detected language so a late
  subscriber can reconstruct without replaying the stream.
- **`/info.features.whisper`** flag. True only when the context class
  *and* the WAV loader are present in this cyllama build; the loader
  alone can't produce audio samples and the context alone can't ingest
  a file.
- **Transcribe sidebar view** (`features/transcribe-pane.js`). Pick a
  whisper model (cached models dropdown + Browse... fallback) and
  audio file via the new `dialog:pickAudio` IPC. Common-language
  selector with auto-detect default; translate-to-English toggle.
  Run button spawns the job and streams segments into a timestamped
  table; Stop cancels via `JobHandle.cancel`. Three copy buttons
  produce TXT / SRT / VTT to the clipboard.
- The Transcribe nav-rail button is hidden when
  `/info.features.whisper` is false, so users on cyllama builds
  without whisper don't see a dead pane.
- WAV-only first cut: non-WAV inputs surface a typed error in the
  job stream pointing at `ffmpeg -ar 16000 -ac 1` rather than
  silently failing. `dialog:pickAudio` still allows mp3/m4a/flac/ogg
  through the file picker so the error message is visible.
- Tests in `tests/test_transcribe.py` cover the feature flag,
  validation 400s, the 501 path, segment + result event shape,
  non-WAV rejection, and option pass-through to `WhisperFullParams`.
  Conftest grows `_FakeWhisperContext` / `_FakeWhisperFullParams`
  / `_FakeWhisperContextParams` plus `_fake_load_wav_file` /
  `_fake_resample_audio`, and a `fake_wav` fixture.

### Added (Phase 4 - clickable sources)
- **Expandable source rows** in the Documents query view. Each source
  collapses to 3 lines by default; click (or Enter / Space when
  focused) toggles the full chunk text. Border-left + cursor cue
  signal that the row is interactive.
- **Reveal button** on rows whose metadata carries a filesystem path
  (`source` / `path` / `file` / `filename` keys, covering the variants
  cyllama loaders emit). Calls `window.cyllama.revealItem(path)`,
  which opens the source in the OS file manager. `stopPropagation`
  so a Reveal click doesn't also toggle the row.
- The source filename (basename of the path) is shown in the row head
  alongside the index and score so users can see at a glance which
  document a chunk came from without having to expand it.

### Added (Phase 3 - hardware controls)
- **`/info.devices`** lists ggml backend devices as
  `[{name, description, type}, ...]`. Probed once at module load via
  `cyllama.llama.llama_cpp.{llama_backend_init, ggml_backend_load_all,
  ggml_backend_dev_info}`; failures return an empty list so the
  renderer falls back to always-show rather than mis-hiding controls
  on a real multi-GPU rig with an old probe.
- **Multi-GPU controls auto-hide on single-GPU machines.** The
  renderer counts GPU/iGPU-typed entries in `/info.devices`; if there
  is at most one and the probe returned a non-empty list, it hides
  `main_gpu`, `split_mode`, and `tensor_split`. `n_gpu_layers`,
  `n_ctx`, `n_batch` stay visible (they're useful on single-GPU and
  CPU-only setups). Empty device list = unknown = leave visible.
- **Devices section** in the General tab listing each device's name,
  description, and type. Lives alongside About so users can see at a
  glance what backends cyllama linked.
- Tests: `/info.devices` shape and probe override in
  `tests/test_hardware.py` (existing eviction-on-load-change tests
  already cover the model-reload path).

### Added (Phase 2 - chat parity with cyllama sampler surface)
- **`POST /grammar/from-schema`** wraps
  `cyllama.utils.json_schema_to_grammar.json_schema_to_grammar`. Accepts
  either a parsed object or a JSON-encoded string in `schema`;
  optional `force_gbnf` flag. Returns `{grammar: "..."}`. 501 when the
  helper is missing, 400 on bad JSON / non-object / helper rejection.
- **`/info.features`** capability flags: `grammar`,
  `json_schema_to_grammar`, `speculative`, `ngram`. Reflect end-to-end
  usability — `grammar` requires both the helper *and* a matching
  `GenerationConfig` field; `speculative` and `ngram` require both
  helper class and matching GC field. The renderer hides Advanced
  rows whose flag is false. `json_schema_to_grammar` is independent
  so the Grammar field stays usable for copy/paste GBNF generation
  even when chat-side grammar isn't wired yet.
- **Advanced disclosure** in the Sampling section: collapsed
  `<details>` block with Grammar (GBNF textarea + "From JSON
  Schema..." button), Speculative decoding (draft-model picker reusing
  cached models, `n_max` / `n_min` / `p_split` / `p_min`), and an
  n-gram cache toggle. Sub-rows that depend on a draft-model
  selection (`data-spec-only`) collapse when no draft model is set.
- **`params.grammar` / `params.speculative` / `params.ngram`** wire
  shape on `/chat`. `_build_config` threads them into
  `GenerationConfig` only when the installed cyllama accepts the
  matching kwargs (same `_GC_ACCEPTED` gate as existing fields).
  `_coerce_speculative` builds a `SpeculativeParams` when the class
  is available, otherwise passes the raw dict through.
  `draft_model_path` is a chat-time parameter, stripped before params
  construction.
- Capability probes (`_resolve_attr`) walk known cross-version paths
  for `Speculative`, `SpeculativeParams`, `NgramCache`, and
  `json_schema_to_grammar` so the sidecar can advertise capabilities
  without crashing on older cyllama builds.
- Tests: grammar endpoint and `_build_config` grammar-drop in
  `tests/test_grammar.py`; `/info.features` shape in
  `tests/test_health_info.py`; `/chat` tolerating
  grammar/speculative/ngram params without 500 in `tests/test_chat.py`.

### Added (Phase 4 - RAG, slice 4d: chat integration)
- **`POST /rag/retrieve`** — retrieve-only endpoint. Body
  `{collection_id, query, top_k?, similarity_threshold?}`, returns
  `{sources: [...]}`. Uses `cyllama.rag.Embedder` +
  `SqliteVectorStore.search` directly so the chat-side context path
  never has to load a generation model. Single-slot
  `_RETRIEVE_INSTANCE` cache keyed on `collection_id`, distinct from
  the `_RAG_INSTANCE` cache used by `/rag/query`, so context retrieval
  doesn't evict the user's chat-side RAG (or vice versa).
- **Retrieval section** in the Models tab (right sidebar): collection
  dropdown ("— none —" default) + Top-K slider (1-10, default 3) +
  refresh icon. Selection persists in `localStorage`
  (`chat_rag_collection_id`, `chat_rag_top_k`).
- **Chat send hook.** `buildOutgoingMessages()` replaces the inline
  system-prompt builder. When a collection is set, the latest user
  message is used as the retrieval query; the returned chunks are
  prepended to the system prompt as a context block ("Use the
  following retrieved context..." + numbered chunks). The visible user
  message is unchanged. Retrieve failures soft-fail with a
  `console.warn`; RAG never blocks the chat hot path.
- **`rag:collections-changed` window event.** Documents view fires it
  after create / delete; the chat-side picker listens and refreshes,
  so a freshly-created collection is immediately available in chat
  without reload.
- New `cyllamaRag.retrieve()` wrapper in `src/renderer/src/lib/rag.js`.

### Added (Phase 4 - RAG, slice 4c: Documents sidebar view)
- **Documents sidebar view.** Replaces the prior modal/full-pane
  attempts. Lives as a `<div class="sidebar-view" data-view="documents">`
  inside the existing `.sidebar` column; the nav-rail's stack icon
  carries `data-sidebar-view="documents"`. Adding more views later is
  one nav-rail button + one `<section class="sidebar-view">`.
- **`setSidebarView(name)` switcher** in main.js. Toggles
  `[data-view]` panels by `hidden`, mirrors the active state on the
  matching `.nav-btn[data-sidebar-view]`. Per-view lifecycle hooks
  (`SIDEBAR_VIEW_HOOKS`) dispatch `onShow` / `onHide` so
  `documents-pane.show()` refreshes the collection list when the view
  becomes active and `hide()` aborts an in-flight streaming query when
  it doesn't.
- Sidebar-width CSS for `.sv-documents-body` (label-above-control rows,
  wrapping header, tighter section padding) so the existing `.dp-*`
  full-pane layout reflows cleanly into the ~268px column.

### Added (Phase 4 - RAG, slice 4b: query)
- **`POST /rag/query`** — streaming RAG query, SSE wire shape mirrors
  `/chat`: one `{sources: [...]}` frame followed by `{text: "..."}`
  token frames, terminated by `[DONE]`. Errors emit
  `{error: "..."}`. Body:
  `{collection_id, generation_model_path, question, top_k?,
  similarity_threshold?, max_tokens?, temperature?, system_prompt?}`.
- **Single-slot RAG cache** keyed on
  `(collection_id, generation_model_path)`. Embedding model is fixed
  per collection so it doesn't enter the key. Eviction calls
  `RAG.close()` to release the prior generation model.
- `_build_rag_config` whitelists query-time fields into a
  `cyllama.rag.RAGConfig`; `_serialize_sources` converts
  `SearchResult`s to JSON-safe dicts.
- Cancellation: on client disconnect, best-effort
  `rag.llm.cancel()` (or `rag._llm.cancel()`) if cyllama exposes it;
  otherwise the producer thread runs to completion against an empty
  queue.
- 7 new pytest cases covering invalid id / unknown / missing question /
  missing generation model / source+token framing / cache reuse /
  eviction. `_FakeRAG`, `_FakeRAGConfig`, `_FakeSearchResult` added to
  conftest.

### Added (Phase 4 - RAG, slice 4a: collections + ingest)
- **`<userData>/workspaces/default/rag/`** is the per-workspace RAG
  state root. Main creates it and passes via `CYLLAMA_SIDECAR_RAG`;
  sidecar falls back to `~/.cache/cyllama-desktop/rag/` for direct
  smoke tests. `/info.sidecar.rag_dir` reports the active path.
- **Manifest + collection lifecycle.** `RAG_DIR/collections.json`
  (atomic tmp+rename writes) is the registry; each collection is one
  `<id>.sqlite` file. `GET /rag/collections`, `POST /rag/collections`
  ({name, embedding_model_path}), `DELETE /rag/collections/{id}`.
  Collection IDs are slug+uuid; ID format gated by
  `_RAG_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/`.
- **Ingest job (`POST /jobs/rag.ingest`).** Body
  `{collection_id, paths[], glob?, chunk_size?, chunk_overlap?}`.
  Pipeline: `load_directory` / `load_document` -> per-document
  fan-out -> `TextSplitter.split_documents` -> `Embedder.embed_batch`
  (BATCH=32) -> `SqliteVectorStore.add`. Per-batch progress events
  through the existing `/jobs` SSE; `doc_count` / `chunk_count` /
  `updated_at` written back to the manifest on completion.
- **Architecture decision:** ingest uses `Embedder` +
  `SqliteVectorStore` directly (not the full `RAG` class), so a
  generation model isn't loaded until query time. Inline rather than
  `asyncio.to_thread` because FastAPI TestClient's portal model
  cancels orphan thread-bound tasks at request boundary; per-batch
  `await _emit(...)` keeps the loop responsive enough for SSE.
- **`cyllama.GGUFContext` resolver.** 0.2.15 doesn't re-export it at
  the top-level `cyllama` namespace; it lives at
  `cyllama.llama.llama_cpp`. `_resolve_gguf_context()` probes a
  candidate list and caches the result, fixing the "GGUFContext not
  available" error in the Models tab metadata view.
- **14 new pytest cases** in `tests/test_rag.py` (collection CRUD,
  manifest persistence, ID validation, ingest happy path / missing
  paths / no-docs / unknown collection / SSE event ordering, /info
  shape). conftest stubs added: `_FakeDocument`, `_FakeChunk`,
  `_FakeEmbedder`, `_FakeSqliteVectorStore` (with `_rows_by_path`
  shared map so ingest writes are visible to retrieve), `_FakeRAG`,
  `_FakeRAGConfig`, `_FakeSearchResult`, `_FakeTextSplitter`,
  `_fake_load_directory`, `_fake_load_document`. The whole
  `cyllama.rag` submodule is now stubbed.

### Changed (Phase 4 - storage layout migration)
- **`<userData>/workspaces/default/{chats,artifacts,rag,...}`** is now
  the per-project state root, anticipating multi-workspace later (see
  PLAN.md S.9). Per-workspace state lives under
  `workspaces/<id>/`; the model cache stays global at
  `<userData>/models/`.
- **One-shot migration on launch.** `migrateLayoutIfNeeded()` in
  main.js promotes pre-existing top-level `chats/` and `artifacts/`
  into `workspaces/default/`. Idempotent (per-dir guard) + gated by a
  version stamp at `<userData>/.layout_version` so it runs at most
  once per install. Partial-failure safe: stamp written only after
  every rename succeeds.
- `chatsDir()` and the sidecar's artifact root now resolve under
  `workspaceDir()`. Models dir unchanged. Chat / inspect / job
  endpoints all see the new paths transparently.

### Changed (UI iterations during Phase 4)
- **Documents was a full pane, then a modal, now a sidebar view.** The
  pane-router approach (Phase-4 first draft) collided with the chat
  grid-template-columns rules (e.g. `data-left="0"][data-right="0"]`
  beats `[data-pane="documents"]` on specificity), squashing the
  Documents pane into a 0-width column. Switched to a centered modal
  dialog as a sidestep; then to a sidebar view (current) which uses
  the same `data-sidebar-view` swap pattern as the rest of the
  sidebar. The sidebar approach swaps content inside one grid column
  rather than rewriting the grid template, which is what kept failing.
- **`data-sidebar-view` switcher pattern.** Each top-level surface in
  the sidebar (chats, documents, future transcribe / batch / ...) is
  one `<button class="nav-btn" data-sidebar-view="X">` + one
  `<div class="sidebar-view" data-view="X">`. Adding a new view is
  zero JS unless it needs an `onShow`/`onHide` hook, in which case it
  registers in `SIDEBAR_VIEW_HOOKS`.
- **Console panel reseated.** Lives inside `<main class="main-col">`
  (in-flow flex item, `flex-shrink: 0`, `max-height: 38vh`,
  `min-height: 160px`) so the sidecar log is bounded by the middle
  column and sits below the composer, not stretching the full window.
- **Models tab redesign.** "CACHED MODELS" header gone; the cached
  list is now a single `<select>` dropdown with refresh icon next to
  it. Drag-drop attaches to the entire `#modelsTabHost`; a CSS
  `::after` overlay shows "Drop .gguf to import" on dragover with
  `pointer-events: none` so the underlying drop target still receives
  events. Models dir caption moved to a small mono footnote under the
  HF download row.
- **Metadata table reflow.** `.mt-meta-list` switched from a two-column
  `max-content / 1fr` grid (which pushed values to a one-character
  column in the narrow sidebar) to a stacked vertical list -- 10px
  faint key on top, full-width value below, `overflow-wrap: anywhere`.

### Removed
- **Pane router (`src/renderer/src/features/pane-router.js`)** is no
  longer imported. Kept in tree as a record of the prior approach;
  delete at your discretion.
- **Documents modal scaffold** (`#docsBackdrop`, `#docsDialog`,
  `.modal-*` CSS) removed when Documents moved to a sidebar view.

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
