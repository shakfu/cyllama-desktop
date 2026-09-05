# Changelog

All notable changes to cyllama-desktop are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (bundled cyllama 0.2.15 -> 0.4.4)

- **Bundled cyllama bumped to 0.4.4.**
  ``scripts/build-python-env.sh`` pinned ``0.2.15``; the last three
  cyllama minor releases (llama.cpp ``b9352`` -> ``v0.3.0``,
  stable-diffusion.cpp ``master-652`` -> ``master-816``, whisper.cpp
  ``v1.8.4`` -> ``v1.9.2``) shipped only additively for the API the
  sidecar calls -- ``LLM`` / ``GenerationConfig`` / ``text_to_image`` /
  ``RAG`` / ``estimate_gpu_layers`` / ``WhisperFullParams`` /
  ``stream_agent`` all keep their signatures, and the removals
  (``LlamaModelParams.use_mmap`` and friends, the SD
  ``keep_*_on_cpu`` flags, ``Prediction.FLUX2_FLOW``) are on api the
  sidecar never touched. ``PY_VERSION`` stays at 3.12.7: from 0.3.0
  cyllama publishes ``cp312-abi3`` wheels only. Verified against a
  real 0.4.2 install -- chat streaming, ``/tokenize``,
  ``/models/inspect``, ``/hardware/estimate-layers``,
  ``/grammar/from-schema``, ``/jobs/agent/run`` and
  ``/jobs/workflow/run``. ``python-sidecar/pyproject.toml`` gains the
  matching ``cyllama>=0.4.4`` floor and raises ``requires-python`` to
  ``>=3.12``.

- **0.4.2 -> 0.4.4 is additive for the sidecar.** Re-verified against a
  real 0.4.4 install (built from source): every ``/info.features`` flag
  resolves exactly as it did on 0.4.2 -- ``grammar`` / ``speculative`` /
  ``ngram`` stay ``false`` because ``GenerationConfig`` still rejects
  those kwargs (the standing TODO), and all twenty others stay ``true``,
  so no probe was silently orphaned by a rename. Smoke-tested end to
  end: chat streaming, ``/tokenize``, ``/models/cached``,
  ``/models/inspect``, ``/hardware/estimate-layers``,
  ``/grammar/from-schema``, ``/jobs/transcribe``, ``/jobs/agent/run``,
  ``/jobs/workflow/run`` and RAG ingest + retrieve. The one behavioural
  change on the sidecar's path is 0.4.3's numpy removal:
  ``load_wav_file`` returns a stdlib ``array('f')`` instead of an
  ``ndarray``, which ``/jobs/transcribe`` hands straight to
  ``WhisperContext.full()`` unchanged -- jfk.wav transcribes correctly.

### Added (per-GPU build variants)

- **The app can be built against any of cyllama's per-backend
  distributions.** cyllama ships the same import package under five
  distribution names -- ``cyllama`` (CPU, and Metal on macOS arm64),
  ``cyllama-cuda12``, ``cyllama-vulkan``, ``cyllama-rocm``,
  ``cyllama-sycl`` -- but ``scripts/build-python-env.sh`` only ever
  installed the one *named* ``cyllama``, so every Linux and Windows
  bundle was CPU-only with no way to ask for anything else. On the
  numbers in this project's own 0.4.2 Windows entry that is 10.6 tok/s
  where the GPU build gives 53.1.

  ``make variant-cuda`` (and ``-cpu`` / ``-vulkan`` / ``-rocm`` /
  ``-sycl``) now switches the bundled distribution and rebuilds the
  Python env; ``make app-<backend>`` goes on to build the installer;
  ``make variant`` prints the current selection. The choice is recorded
  in a single line of ``python-sidecar/pyproject.toml`` by
  ``scripts/set-cyllama-variant.py``, and ``build-python-env.sh`` reads
  the distribution name back out of that same line -- so the wheel that
  gets installed and the sidecar's own dependency metadata cannot drift
  apart. That matters more than it sounds: two cyllama distributions own
  the same ``site-packages/cyllama/`` directory, so a disagreement would
  have ``pip install ./python-sidecar`` quietly install the *other*
  backend over the one just installed. For the same reason a switch
  wipes the env instead of upgrading in place.

  The selector refuses combinations cyllama does not publish (CUDA on
  macOS, ROCm or SYCL on Windows) rather than failing later at pip, and
  says where each backend *is* available; ``--platform`` targets a host
  other than the current one. It also refuses a variant pin combined
  with ``CYLLAMA_SOURCE``, since a local source build installs as plain
  ``cyllama`` whatever backend it was compiled with. Both checks run
  before the ~30 MB runtime download and before the env is wiped, so a
  bad combination costs 8 ms and leaves the existing env intact.

  Variants get their own installer filename
  (``artifactName: ${productName}-${version}-<backend>-${arch}.${ext}``)
  so a CUDA build and a CPU build coexist in ``dist/``. ``appId`` and
  ``productName`` are deliberately untouched: these are one application
  with a different accelerator, not competing apps, so they share config
  and user data. Which backend a given bundle actually has is visible at
  runtime in General -> backends, now that that row reports cyllama's
  build config (see below).

  Apple silicon needs none of this and the tooling says so: the default
  distribution's macOS arm64 wheel already has ``metal: true`` compiled
  in, so ``cpu`` is the GPU build there.

  The targets are written out one per backend rather than as
  ``variant-%`` / ``app-%`` pattern rules. A pattern rule is the obvious
  way to write ten near-identical targets and it does not work here:
  GNU make excludes ``.PHONY`` targets from implicit-rule search, so
  every one of them matched nothing and reported "Nothing to be done"
  while appearing to succeed. The recipes share two canned recipes
  (``switch_variant`` and ``BUILD_APP``) instead.

  20 tests in ``tests/test_variants.py`` cover the round trip, that the
  version floor and every other line survive a switch, that installer
  names don't accumulate backend suffixes, that refusals leave both
  files untouched, and two that exist because the obvious test would
  have missed the real failure: that the ``sed`` in
  ``build-python-env.sh`` and the selector's own regex still agree on
  which line they are reading, and that every ``variant-*`` / ``app-*``
  target actually resolves to a recipe -- asked of ``make --dry-run``
  itself, since being listed in ``.PHONY`` is not the same as being
  buildable, and the ``.PHONY`` check alone passed against targets that
  did nothing.

  Verified end to end on Linux + CUDA (RTX 4060, driver 595.84):
  ``make variant-cuda`` installs ``cyllama_cuda12-0.4.4``, ``cyllama
  info`` reports ``built: CUDA`` and ``registries: CUDA, CPU``,
  ``/info.backends`` reports ``cuda: true``, and Qwen3-4B-Q8 runs at
  54.4 tok/s against 9.7 on the CPU path of the same bundle -- within
  noise of cyllama's own 53.1/10.6 figures for that model. The packaged
  AppImage loads ``libggml-cuda-*.so`` from its own
  ``cyllama_cuda12.libs``, so the accelerator ships inside the bundle
  and needs only the host's NVIDIA driver.

### Fixed (backends row was empty on every build; numpy dropped)

- **``/info.backends`` was always ``{}``.** ``_backend_flags()`` probed
  ``cyllama._backend`` / ``cyllama.backend``, and no released cyllama
  has ever exported either -- so the "backends" row in the General tab
  and the Preferences window read "(none enabled)" on every build,
  CUDA and Metal ones included. It now reads
  ``cyllama._internal.build_config.backend()``, the generated build
  config, reporting ``hip`` under its common name ``rocm`` and adding
  ``blas``; ``openmp`` is skipped (the config reports it as ``None``,
  not a backend entry). The import is guarded, so a cyllama without
  the module degrades to ``{}`` instead of taking ``/info`` down.
  Same root cause as the workflow-path bug below, and hidden the same
  way: **the conftest cyllama stub exported ``_backend``, a namespace
  the real package never had**, so the test asserting a populated
  backends dict passed against a shape that only existed in the stub.
  The stub now mirrors 0.4.4's ``cyllama._internal.build_config``,
  including its ``{name: {"enabled": bool, ...}}`` detail-dict shape.

- **``numpy`` dropped from the sidecar's dependencies.** It was there
  only because ``cyllama.whisper.cli.load_wav_file`` used to return
  ``ndarray``; cyllama 0.4.3 removed its own numpy import and the
  helper now returns a stdlib ``array('f')``. The sidecar never
  imported numpy itself (``_jsonify`` is duck-typed on ``.item()``),
  so this removes ~30 MB from the bundle with no code change.
  Verified numpy-free end to end: ``/jobs/transcribe`` returns the
  correct jfk.wav transcript and ``/models/inspect`` still coerces
  GGUF metadata.

### Added (libgomp backstop for local source builds on Linux)

- **``make python-local`` can produce an env that cannot import
  cyllama, and now repairs itself.** On a host where the prebuilt ggml
  archives in ``thirdparty/*/lib`` were compiled with OpenMP but the
  extension's own CMake configure could not detect it, the extensions
  are left with undefined ``omp_*`` / ``GOMP_*`` symbols and no
  matching ``DT_NEEDED``, and every entry point dies at
  ``import cyllama`` with ``undefined symbol: omp_get_thread_num``.
  cyllama's CMake links ``OpenMP::OpenMP_CXX`` on Linux only when
  ``find_package(OpenMP)`` succeeds and degrades silently when it does
  not; the trigger seen here was CMake selecting ``clang`` on a host
  with no ``libomp-dev`` while the archives had been built with gcc
  (``OpenMP_CXX_FLAGS:STRING=NOTFOUND`` in the cache). The published
  wheels cannot land in this state -- they are built where OpenMP is
  detected, link libgomp themselves, and auditwheel then vendors it as
  ``libgomp-<hash>.so.1``. (auditwheel rewrites existing ``DT_NEEDED``
  entries and never adds a missing one, so it would not have rescued
  such a build either.)

  The actual fix is to give the compiler CMake picks its OpenMP dev
  headers and rebuild cyllama. ``scripts/build-python-env.sh`` now
  carries a backstop for hosts in that state: it vendors the host
  ``libgomp.so.1`` into ``cyllama/.libs-local/`` and patches each
  affected extension with ``--add-needed`` plus an ``$ORIGIN``-relative
  RPATH (prepended, so the build's own RPATH survives), keeping the env
  self-contained rather than dependent on the build host at run time.
  Runs only for ``CYLLAMA_SOURCE`` builds on Linux, is a no-op on a
  correctly linked build (it skips extensions that reference no
  OpenMP), and warns rather than fails if ``patchelf`` is absent.
  Verified on a clean rebuild of an affected host: four extensions
  patched, ``import cyllama`` clean with no ``LD_PRELOAD``.

### Fixed (agent-workflow row was unreachable on every released cyllama)

- **``Workflow`` / ``workflow_node`` / ``agent_node`` were probed at a
  path no released cyllama has.** ``cyllama.agents`` re-exports the
  agent classes but not the graph api, which lives at
  ``cyllama.agents.workflow``. All three probes therefore resolved to
  ``None``, ``/info.features.workflow`` reported ``false``, and the
  agent-workflow row in the Agents pane rendered its "unavailable"
  placeholder. Both paths are probed now.

- **The shipped example workflow imported ``Workflow`` from the same
  wrong path**, so ``/workflows`` would have listed
  ``word_count.py`` with an ``ImportError`` once the flag flipped;
  ``docs/guide-to-agents.md`` taught users the same import. The
  conftest cyllama stub is what hid both: it exported the graph api one
  level up, from a namespace the real package never had. It now mirrors
  0.4.2's module layout, and
  ``test_resources_example_workflows_import_and_compile`` loads every
  shipped example through the ``_resolve_workflow`` path the pane uses.

### Changed (right-pane polish: Hardware label, sections default-closed, preset semantics)
- **Right-pane section renamed.** The collapsible block holding
  ``GPU layers / Context / Batch / Main GPU / Split mode /
  Tensor split`` was titled "Settings", which collided with the
  ``Cmd+,`` macOS-style Preferences window. Now reads
  **"Hardware"** to match the underlying form id (``hwForm``) and
  the section's actual content. n_ctx / n_batch are CPU-relevant
  too so "Hardware" is more accurate than "GPU Settings".
- **System Prompt and Sampling start collapsed** in the right pane
  (both were ``<details open>``). Every section in the tab is closed
  now, so it opens as a list of section heads: most users pick a
  preset rather than tweaking individual sliders, and the open
  sampling list pushed the sections under it off screen on smaller
  windows. ``Parameters right-tab renders LMStudio-style sections``
  (e2e) asserts the closed default and that expanding one section
  leaves its siblings shut.
- **Presets no longer clobber the system prompt by default.**
  Built-in presets (``Default``, ``Creative``, ``Precise``,
  ``Long-context``) now carry ``system_prompt: null`` ("don't
  touch") rather than ``""`` ("set to empty"), so switching among
  them leaves whatever the user typed alone. ``Code`` continues
  to set its coding-assistant prompt explicitly. ``applyPreset``
  was already guarded with ``typeof === "string"`` so ``null``
  short-circuits cleanly. User-created presets keep capturing as
  a string -- saving with an empty textarea + applying later is
  still a valid way to deliberately wipe the prompt.

### Added (voice prompts, Quarto render, local-file link opener)
- **Voice prompts in the composer** (TODO Multimodal #2). Mic
  button next to the paperclip; click to record, click again or
  press Esc to stop. Recording state shows a pulsing red icon
  with an ``MM:SS`` elapsed counter. The renderer captures audio
  via ``MediaRecorder`` (webm/opus on Chromium), decodes +
  resamples to 16 kHz mono in-browser via ``OfflineAudioContext``,
  packs as a canonical RIFF/PCM WAV blob, uploads to a new
  ``POST /audio/upload`` endpoint, then fires ``/jobs/transcribe``
  and appends the transcript to whatever was already typed. No
  ffmpeg dependency -- the conversion is pure JS. The whisper
  model is resolved in priority order:
  ``localStorage["voice_whisper_model"]`` (set by the Transcribe
  pane) ⇒ first whisper-classified model from
  ``/models?kinds=whisper`` ⇒ actionable error.
- **Quarto render as an opt-in agent tool** wired alongside
  ``search_wikipedia``. New ``agents.quarto_render`` capability
  flag gated on both the cyllama ``@tool`` resolving AND the
  ``quarto`` CLI being on PATH; either missing → renderer hides
  the toggle. Toggle in the Agents pane below "Search Wikipedia"
  with a confirm dialog ("can write files and run the quarto
  CLI") matching the strong-side-effect trust posture.
  ``_build_agent_tools`` surfaces "tool missing" vs "CLI missing"
  as distinct 501s so the renderer can show the right install
  hint.
- **Local-file link opener.** Clicking a ``file://`` (or absolute
  ``/...``) link in the chat log -- e.g. the markdown link
  ``quarto_render`` pastes pointing at the generated .pptx --
  now opens the file in its OS default app via ``shell.openPath``
  instead of triggering Electron's default Save-As download
  dialog. Implemented as a delegated click listener on ``#log``
  (reads ``getAttribute("href")`` to avoid bundle-relative
  re-anchoring), an ``ipcMain.handle("shell:openPath", ...)``
  wrapper, and ``window.cyllama.openPath(path)`` exposed via
  preload. http/https/mailto fall through to Electron's defaults
  (untouched).

### Changed (stock cyllama tools as a group toggle, not a misleading per-tool one)
- **The Agents pane's "Calculator" checkbox is replaced by
  "Stock cyllama tools"** (hint: ``calculator · current_time ·
  word_count``). The old single toggle was misleading after the
  auto-injection landed -- unchecking it didn't actually remove
  calculator from the catalog, and the other two stock tools
  weren't even surfaced. Now the toggle accurately controls the
  whole group. Default on. Useful to flip off when a task-specific
  tool like ``quarto_render`` is the real target and small models
  get tempted by the stock distractors.
- New ``stock_tools: false`` spec key on agent runs suppresses
  the stock auto-injection. ``stock_tools: true`` (or omitted)
  preserves the current behavior. Legacy ``calculator: true``
  still works as a fallback when the cyllama stock ``@tool`` is
  missing (older bundles); otherwise it dedups against the stock
  group by name.

### Fixed (whisper feature flag was silently false in bundled builds)
- ``numpy>=1.26`` added to ``python-sidecar/pyproject.toml``.
  Cyllama's ``cyllama.whisper.cli.load_wav_file`` returns numpy
  arrays of audio samples; without numpy the probe failed and
  ``/info.features.whisper`` was reported as ``False`` -- which
  silently hid the Transcribe pane *and* the new composer mic
  button. Bundled builds (``scripts/build-python-env.sh``) now
  install it via the existing ``pip install ./python-sidecar``
  step; smoke test extended to import it.
- ``mainWindow.webContents.session.setPermissionRequestHandler``
  added so the renderer's ``getUserMedia`` call for the mic
  button is granted (and only ``media`` is granted; every other
  permission is denied by default). Without this Electron silently
  rejects the request.

### Added (context-window warning for document attachments)
- **Doc chips now flag context-window pressure.** Renderer
  computes a rough ``chars / 4`` token estimate against the active
  model's context window; chip border + background turn amber at
  ``≥40%`` of the window (``ctx-tight``) and red at ``≥100%``
  (``ctx-over``). Same styling applies to composer-pending chips
  (before send) and folded chips in the chat log (after send), so
  old conversations with overflowing docs flag visually on replay.
- **Active context resolution** (`getActiveContextWindow`, in
  priority order): user override on the ``n_ctx`` Parameters input
  ⇒ GGUF ``<arch>.context_length`` from a cached
  ``/models/inspect`` response (fetched on ``setModel``) ⇒ a
  pessimistic ``4096`` fallback so unknown / probe-failed models
  trip the warning rather than silently overflow.
- **Live re-evaluation.** Pending doc chips re-render when the
  user types into the ``n_ctx`` override and when ``setModel``
  swaps the active model (the inspect fetch dispatches a refresh
  on success). Tunables (``APPROX_CHARS_PER_TOKEN = 4``,
  ``DOC_CONTEXT_WARN_FRACTION = 0.4``) live next to the helper.
- Caveats called out in the chip's expanded-body text: the
  estimate is directional (English-prose-tuned), and the warning
  doesn't yet account for chat history + system prompt +
  ``max_tokens`` already in flight -- it just says "this doc
  consumes X% of context".

### Added (composer document attachment -- PDF / text / markdown)
- **Drop any of `.pdf` / `.txt` / `.md` / `.markdown` / `.json`
  / `.jsonl` on the composer card** (or pick via the paperclip
  button) to attach a document to the next message. The sidecar
  extracts the text and the renderer inlines it as a
  ``[Document: foo.pdf]\n<content>`` block into the message
  ``content`` the model sees. Image attachment (already shipped)
  works unchanged alongside.
- **Paperclip button surfaces on either path** -- previously
  gated on multimodal + mmproj, now also surfaces when
  ``features["documents.extract"]`` is true. Tooltip names the
  attachment types the current config supports. The e2e spec that
  asserted "hidden until an mmproj is pinned" now asserts the
  tooltip instead: documents-only before pinning, images after the
  ``mmproj:changed`` event.
- **Drag-and-drop on the composer** (`drag-active` outline
  highlight while hovering). Files-only filter; rejects text
  drags and other non-file payloads.
- **Folded chips in chat render**: instead of dumping a 50k-char
  PDF into the user-message body, each attached doc renders as a
  collapsed chip (filetype badge · filename · char count). The
  expanded chip shows metadata only (backend, page count, file
  path under ``UPLOADS_DIR``, "Full content was inlined into the
  message sent to the model."); the full text is in the message
  ``content`` field, not duplicated in the chip body. Model-facing
  context unchanged; regenerate replays the composed content
  verbatim.
- **Per-message persisted shape** for docs:
  ``message.content`` is the composed string (typed + inlined
  docs), ``message.prompt`` is the typed-only portion the chat
  log displays, ``message.documents`` is metadata only
  (``{filename, filetype, char_count, truncated, backend,
  pages, path}``, no ``text`` field). Old chats from before this
  change have neither ``prompt`` nor ``documents`` and fall back
  to rendering ``content`` directly -- no regression.
- **New sidecar endpoint `POST /documents/extract`** (multipart
  ``file`` field). Returns ``{filename, filetype, backend, text,
  char_count, truncated, pages, path}``. v1 caps: 32 MiB raw
  upload, 200k chars extracted with a ``truncated`` flag.
  Failure modes: 400 (missing file), 413 (oversize), 415
  (unsupported extension), 501 (.pdf dropped but no PDF backend
  installed; surfaces the cyllama install_hint).
- **New `/info.features.documents.{extract,pdf}` capability
  flags** and **`/info.pdf_backends`** array with per-backend
  ``{name, available, capabilities, install_hint}`` shaped from
  cyllama's registry. Renderer uses ``documents.extract`` to gate
  the paperclip; ``documents.pdf`` and the backend list will gate
  a future "install pypdf for PDF support" affordance.
- **Sidecar dependency: `pypdf>=4.0`** added to
  `python-sidecar/pyproject.toml`. Bundled builds
  (`scripts/build-python-env.sh`) install it via the existing
  ``pip install ./python-sidecar`` step; smoke test extended to
  import it. Pure Python, MIT licensed, first in cyllama's
  ``_PDF_BACKEND_PRIORITY``. Richer extraction (docling for OCR
  / tables) is opt-in by the user via a future Settings action.

### Changed (agent jobs consolidated behind `cyllama.agents.runner.stream_agent`)
- **All five `/jobs/agent/*` endpoints now dispatch through
  `cyllama.agents.runner.stream_agent(kind, ...)`** -- the
  per-class branching and the duplicated plan / reflect
  orchestration loops in the sidecar are gone. Plan and reflect
  alone dropped ~140 lines of hand-rolled planner / executor /
  worker / critic scaffolding (and helpers `_DEFAULT_PLANNER_PROMPT`,
  `_DEFAULT_CRITIC_PROMPT`, `_DEFAULT_CRITIQUE_PREFIX`,
  `_parse_plan`, `_reflection_revision`). Endpoint URLs and
  response shapes preserved.
- **New `_drain_agent_stream` helper** centralises the SSE
  trace-emission loop used by every endpoint. Holds back the
  runner's synthetic ``metadata.source == "final"`` event so its
  content / metadata fold into the ``result`` envelope rather
  than double-emitting on the trace stream.
- **`/info.features.agents.runner`** capability flag added.
  `agents.plan` / `agents.reflect` flags now satisfied by either
  the legacy class/helper or the new runner, so older cyllama
  bundles still see the endpoints work via the legacy path while
  modern bundles transparently upgrade.

### Added (stock cyllama tool auto-injection + search_wikipedia opt-in)
- **Stock cyllama `@tool` catalog auto-injected** into every
  agent run when present in `cyllama.agents.tools`:
  `current_time`, `calculator`, `word_count`. The renderer no
  longer has to opt into these per-call -- `_build_agent_tools`
  prepends them to the resolved Tool list and dedupes by name,
  so the existing `{calculator: true}` spec key becomes a no-op
  (the stock @tool already provides it). Each tool is probed
  independently; older cyllama builds missing one don't lose
  the others. The sidecar's local `_make_calculator_tool` is
  retained as a fallback when the stock @tool is absent.
- **`search_wikipedia` opt-in tool** wired through
  `tools: {search_wikipedia: true}`. Sources cyllama's stock
  `@tool` directly (query + limit pass through as structured
  args). New `/info.features.agents.search_wikipedia` capability
  flag gates the renderer toggle; **"Search Wikipedia" checkbox
  added to the Agents pane** below "Web fetch", hidden when the
  flag is false. Off by default (network side effect, even if
  scoped to en.wikipedia.org).


- **`Agents` nav-rail pane** (network-graph icon, gated on
  `/info.features.agents`). Three-column layout mirroring the
  Models pane: left subnav lists six agent types
  (`agent` / `agent-strict` / `agent-contract` / `agent-plan` /
  `agent-reflect` / `agent-workflow`), middle column holds the
  selected type's defaults form, right detail rail shows
  per-type last-run summary (workflow row only today; general
  per-type run history deferred to TODO.md).
- **`features/agents-pane.js`** is the new single source of truth
  for every agent type's defaults. Common section (tools +
  max_iterations) renders on every non-workflow row; per-type
  sections below: `strict` (format / allow_reasoning),
  `contract` (preset / policy), `plan` (max_steps / stop_on_error
  / planner prompt / executor prompt), `reflect` (max_attempts /
  acceptance marker / critic prompt). The `agent-workflow` row
  hosts the former Workflows pane verbatim (file list + spec
  preview + initial-state form + Run + live trace).
- **Per-call modal** (`features/agent-modal.js`) opens when
  `/agent-strict`, `/agent-contract`, `/agent-plan`, or
  `/agent-reflect` is invoked from the chat composer. Modal is
  pre-filled with the Agents pane's defaults so Enter runs with
  current values; the user can tweak any field for this one
  invocation. Schema-driven (`{key, label, type, default,
  options?, min?, max?, placeholder?, hint?}`) with field types
  `text` / `number` / `select` / `checkbox` / `textarea`. The
  task field at the top is **editable** -- pre-filled from the
  slash body, refined-in-place text on submit becomes the run's
  task. Esc / Cancel / backdrop-click cancels.
- `/agent` and `/agent-workflow` skip the modal (no per-call
  knobs / navigation slash respectively).
- **Slash command family rename**: `/constrained` ->
  `/agent-constrained`, `/contract` -> `/agent-contract`,
  `/plan` -> `/agent-plan`, `/reflect` -> `/agent-reflect`.
  Tab autocomplete from `/agent` now surfaces every variant.
  `/agent-strict` registered as a friendlier alias for
  `agent-constrained` (same handler; the cyllama class is still
  `ConstrainedAgent`).
- **`/agent-workflow [<name>]`** (navigation slash): with a
  name, navigates to the Agents pane with that workflow row
  selected; without a name, navigates to the workflow row for
  discovery. Slash bypasses the Send-button gate (works without
  a model loaded).
- Enter on any slash command now bypasses the model-loaded gate
  so the agent modal can open before a model is picked
  (handlers themselves enforce preconditions). Raw chat still
  requires the Send button enabled.

### Changed (Agents pane consolidation removes the right-sidebar tab and standalone Workflows pane)
- **Right-sidebar `Agents` tab removed** -- the pane is now the
  only config surface. `rt-tabs` collapses to just `Parameters`
  (a single-tab tab-strip; revisit when another right-sidebar
  surface lands). `features/agents-tab.js` deleted; the
  `getAgentConfig` / `getStrictConfig` / `getContractConfig` /
  `getPlanConfig` / `getReflectConfig` / `validateAgentConfig`
  exports moved to `features/agents-pane.js` as the same shape.
- **Standalone Workflows pane removed** -- folded into the
  Agents pane as the `agent-workflow` subnav row.
  `features/workflows-pane.js` deleted; everything it owned
  (discovery, spec preview, initial-state form, Run, live trace,
  per-run detail) renders under the workflow row of the new
  Agents pane.
- `right-tabs.js` `VALID` set shrunk from
  `{models, agents, general}` to `{models}`; the
  setActive/getActive API stays so the Preferences window and
  other consumers compose unchanged.

### Added (cyllama agent surface -- Phases A-E)
- **`make python-local`** target rebuilds the bundled Python
  env using a local cyllama checkout instead of the PyPI pin.
  Defaults to `../cyllama`; override with
  `CYLLAMA_SOURCE=/path/to/cyllama`. Wipes the existing env
  first (the plain `python` target is gated on the env existing,
  so a re-bump without `python-local` would no-op).
- **Granular agent feature flags** at sidecar module load
  (`/info.features`): `agents.constrained`, `agents.contract`,
  `agents.reflect`, `agents.plan`, `agents.rag_tool`,
  `agents.memory`, `workflow`. Each is independently probed via
  `_resolve_attr` so the bundle's actual capabilities surface to
  the renderer (which uses them to gate slash commands + UI
  rows).
- **`POST /jobs/agent/constrained`** wraps
  `cyllama.agents.ConstrainedAgent`. Same SSE shape as
  `/jobs/agent/run`; extra body fields `format` (one of
  `json` / `json_array` / `function_call`) and
  `allow_reasoning`. 8 sidecar tests.
- **`POST /jobs/agent/contract`** wraps `ContractAgent` with a
  **named preset registry** (`none` / `task-nonempty` /
  `answer-quality`) and a policy (`IGNORE` / `OBSERVE` /
  `ENFORCE` / `QUICK_ENFORCE`). The registry lives in the
  sidecar (`_contract_presets()`) so the renderer doesn't ship
  Python callables across the wire. New `GET
  /info/contract-presets` exposes the preset/policy lists for UI
  pickers. Emits `CONTRACT_CHECK` / `CONTRACT_VIOLATION` events
  alongside the usual THOUGHT / ACTION / OBSERVATION /
  ANSWER. 11 sidecar tests.
- **`POST /jobs/agent/plan`** orchestrates a planner + N
  executors itself (bypassing cyllama's `plan_and_execute`
  helper which uses `.run()` not `.stream()`) so events flow in
  real time. Each event carries `metadata.source = "planner"`
  or `"step-<n>"` so the trace renderer distinguishes phases.
  Configurable planner / executor system prompts; default
  planner prompt asks for newline-separated steps. Result
  payload carries the full plan + per-step `{plan, answer,
  success, events}`. 7 sidecar tests.
- **`POST /jobs/agent/reflect`** orchestrates a worker + critic
  reflection loop (also bypassing cyllama's `ReflectionLoop`
  wrapper for incremental events). Worker inherits the tool
  catalog; critic always runs tool-less. Event source tags
  `worker-<n>` / `critic-<n>`. Loop terminates on
  case-insensitive substring match of the configurable
  acceptance marker (default `ACCEPT`) in the critic's answer,
  or hits `max_attempts` (default 3, clamped 1-10). Result
  payload carries `accepted: bool`, `attempts: int`, the last
  draft as `answer`, and per-round `{draft, critique, accepted,
  worker_events, critic_events}`. 8 sidecar tests.
- **Workflow surface** (`POST /jobs/workflow/run` +
  `GET /workflows` + `GET /workflows/{id}/spec`). Workflows are
  workspace-scoped `*.py` files under
  `<workspace>/workflows/`, discovered on demand. Each file
  exports either `flow: Workflow` or `make_flow()` returning a
  Workflow; the module docstring becomes the description.
  Module loading caches by `(path, mtime)` so iterative
  authoring doesn't pay validation cost on every run. Broken
  files surface with an `error` field in the discovery list
  rather than poisoning sibling files. The execute endpoint
  forwards the workflow's native event stream
  (`WORKFLOW_START` / `NODE_START` / `NODE_END` / `ANSWER` /
  `WORKFLOW_END`) plus any sub-event nesting set by `agent_node`
  / `workflow_node` (`metadata.source` / `parent_event_id`
  preserved). 19 sidecar tests covering discovery (broken file
  isolation, invalid-filename skipping, factory-form workflow,
  feature gate), spec (404/400/501), execute (validation +
  event sequence + state pass-through).
- **`semantic_memory` agent tool** -- adds `remember(text)` +
  `recall(query, k?)` to the agent tool catalog when configured
  with `{collection_id, namespace?, top_k?}`. Backed by a
  `_MemoryRagShim` around the existing RAG `Embedder` +
  `SqliteVectorStore` (avoids needing a generation model that
  `cyllama.rag.RAG` requires but the sidecar's embed-only RAG
  collections don't have). Different namespaces over the same
  collection are isolated by design. 9 sidecar tests.
- **Bundled Python env at cyllama 0.2.16** -- bumped from
  0.2.15 (PyPI pin) to the local checkout via
  `make python-local`. All Phase 1-5 workflow surface verified
  importable (`Workflow.as_agent`, `workflow_node`,
  `ReflectionLoop`, `SemanticMemory`, etc.).
- **First-launch example workflow seeding**: Electron main
  process copies `resources/example-workflows/*.py` into the
  workspace's `workflows/` dir on first launch (tracked by a
  `.seeded` marker so a user who deletes the seeded files
  doesn't get them back). Skipped under the e2e harness so the
  stub doesn't see real-cyllama Layer-C code. One example
  shipped: `word_count.py` -- linear Layer-C pipeline that
  tokenises an input string, counts tokens, emits a summary.
  Verified end-to-end against the real cyllama runtime.
  `electron-builder.yml` ships `resources/example-workflows/`
  under `process.resourcesPath/example-workflows/`.

### Added (user docs)
- **`docs/guide-to-agents.md`** -- end-user guide to the agent
  layer. Covers the five slash-commands (`/agent`,
  `/agent-constrained` aka `/agent-strict`, `/agent-contract`,
  `/agent-plan`, `/agent-reflect`), per-call modals and what
  each field does, the Agents pane (subnav + Common section +
  per-type defaults), the Tools catalog (calculator / read_file
  / web_fetch / rag_query / semantic_memory) with sandbox notes,
  and the Workflows pane (file authoring + trust boundary).
  Common-patterns and troubleshooting sections cover the
  most-likely first-time-user questions. Linked from `README.md`.
- **`docs/dev/agent_plan.md`** -- developer plan covering
  Phases A-E (bundle bump, slash agents, ReflectionLoop,
  Workflows pane, Memory + RAG tool). Survey of the cyllama
  agent surface, priority-ordered integration table, uniform
  endpoint shape, feature-detection probes, workflow authoring
  decision (workspace Python + trust boundary), UX split
  (slashes for chat-shaped agents, pane for workflows). 5-phase
  rollout each shipped with sidecar pytest + Playwright smoke +
  CHANGELOG entry + feature-flag expansion.

### Fixed
- **`prefsWindow.show()` race on `ready-to-show`** -- the
  preferences window's `ready-to-show` callback unconditionally
  called `.show()` on the window. Playwright tears down the app
  faster than Electron's window load completes, so the callback
  could fire after the window was destroyed; calling `.show()`
  on a destroyed window triggers
  `BrowserWindow.visibilityChanged` -> `emit` and crashes the
  main process with "Object has been destroyed". Guarded with
  `if (!prefsWindow.isDestroyed())` (same pattern as
  `pushLog`). Predates this session -- the bug has been there
  since the very first commit, surfaced only because the new
  Playwright tests cycle the app faster.
- **Stale `#ag-run` Playwright assertion** -- a panes-suite
  test asserted the Agents right-tab had an inline Run button
  (`#ag-run`), but the slash-command refactor in 2026-05-07
  removed the inline button (agents now invoked via the
  chat composer's `/agent <task>`). Test updated to assert the
  current settings surface (`/agent <task>` hint + Max
  iterations + Tools rows).

### Changed (left nav rail is now app-wide only)
- The leftmost control bar previously mixed two contracts: Chats /
  Models switched the whole app, while Documents / Transcribe / Image
  / Server / Batch only re-skinned the chat sidebar. The rail now
  carries app-wide actions only (Chats, Models, Console, Settings).
- Documents, Transcribe, Image, and Batch moved into a tab strip
  (`.lt-tabs`, mirrors the right panel's `.rt-tabs`) at the top of
  the chat sidebar. Per-view headers were dropped since the active
  tab serves as the title; Chats keeps its header for the New-chat
  button. Visibility flags now toggle tabs (`#tabTranscribe`,
  `#tabImage`, `#tabBatch`) instead of nav buttons.
- Server controls moved to **Preferences -> Sidecar tab**. The
  OpenAI-compatible server is a sidecar-hosted concern, not a
  per-chat tool. `server-pane.js` is now host-agnostic
  (`show(host)`); the preferences bundle imports it directly. The
  preferences preload exposes `pickModel`. The `openai_server`
  feature gate moved from the module's old `applyVisibility` into
  the preferences mount.
- E2E suite updated: `_harness.js` clicks `.lt-tab` instead of
  `.nav-btn[data-sidebar-view]`; the Server pane test became a
  Preferences-window test.

### Added (slash commands in the chat composer)
- **`/agent <task>`** routes the composer through `/jobs/agent/run`
  instead of `/chat`. The run renders inline in the chat stream as a
  user bubble + agent block with a collapsible Trace `<details>` and a
  markdown-rendered Answer area. Stop button cancels the agent job
  via `JobHandle.cancel()` instead of the `/chat` fetch abort.
- **Persisted agent turns** carry their full event trace. Replay on
  chat reload reconstructs the collapsible trace + answer. Renderer
  strips the `agent` field from outgoing `/chat` messages so the
  sidecar only sees the `{role, content}` shape.
- **Slash registry** at `src/renderer/src/features/slash.js` (pure
  parse / matches / lcp helpers) backs a runtime registry in
  `main.js`. `send()` delegates to the registry; unknown slashes
  pass through as plain chat by design.
- **Tab autocomplete** in the composer: typing `/a<Tab>` expands to
  `/agent ` when a single command matches, completes to the longest
  common prefix when several do, surfaces candidates as a transient
  system line.
- **Reset chat button** (trash icon, topbar) wipes the active chat's
  messages in place with a confirm. Persists the now-empty state if
  the chat already has an id.
- **`docs/slash-commands.md`** — plan covering taxonomy
  (action / nav / chat-augmenting), proposed registry, parsing rules,
  autocomplete behavior, phasing, and open questions.

### Changed (Agents tab is now config-only)
- Sidebar Agents tab no longer hosts task input, Run / Stop buttons,
  Status, Trace, or Answer sections. Those moved to the chat
  composer via `/agent`. The tab now exposes Tools and Max iterations
  only, plus a hint pointing users at `/agent <task>`.
- Removed the per-tab model picker. Agent runs inherit the currently
  loaded chat model via the `last_model_path` localStorage key that
  `setModel()` already writes.
- `setModel()` dispatches a window-level `cyllama:model-changed`
  event so other panes can react without polling.
- `getAgentConfig()` and `validateAgentConfig()` are exported from
  `agents-tab.js` so the chat composer can read tool toggles and
  iteration cap at run time.

### Changed (Settings collapsible polish)
- **"Estimate GPU Layers"** button moved out of the Settings section
  summary row into the GPU layers `.param` block (was clipping
  against the chevron when the section was collapsed). Relabeled
  from the ambiguous `est` to `Estimate GPU Layers` and switched
  from `.icon-btn` to `.btn`.

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
  primary content rendered (header / form fields / structural
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
