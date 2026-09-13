# cyllama-desktop

Electron desktop app for local AI inference: chat, retrieval, agents, transcription and image generation, driven from GGUF model files you hold on disk. It runs [cyllama](https://github.com/shakfu/cyllama) in a bundled Python sidecar, so nothing needs installing alongside it and it works with no account and no API key.

Chat can also run against an external provider -- OpenAI, Anthropic, OpenRouter, or any OpenAI-compatible endpoint -- when you add a key. Local is the default and everything else in the app is local only; see [External providers](#external-providers).

## What cyllama is

[cyllama](https://github.com/shakfu/cyllama) is a zero-dependency Python library for local inference over the `llama.cpp`, `whisper.cpp` and `stable-diffusion.cpp` ecosystem. It ships compiled extension modules -- `llama_cpp`, `whisper_cpp`, `stable_diffusion`, and an embedded OpenAI-compatible server -- plus higher layers in pure Python: `cyllama.rag`, `cyllama.agents`, `cyllama.memory`, `cyllama.batching`. It publishes one wheel per GPU backend, each carrying its own compiled llama.cpp.

cyllama is the engine. This app is the UI, the job runner, and the persistence around it: it bundles one of those wheels with its own CPython build and drives it over a loopback HTTP API. Whatever the bundled wheel does not provide is hidden rather than broken -- the sidecar reports its capabilities at `/info.features` and panes and slash commands that need a missing one do not appear.

## Key features

- **Chat against a local GGUF.** One model resident at a time (a single-slot cache, to bound VRAM), streamed over SSE, with sampling presets, a system prompt, and per-chat history persisted to disk.

- **Multimodal and document input.** Drop an image when a model and its mmproj are loaded; drop a PDF, Markdown or JSON file and the sidecar extracts the text into the prompt; dictate with a mic button that transcribes locally through Whisper.

- **Model management.** List cached GGUFs, inspect their metadata, import a local file, download from HuggingFace as a cancellable job, and quantize to any supported ftype.

- **Retrieval.** Build RAG collections backed by per-collection sqlite vector stores, ingest documents as a job, then query with sources or retrieve top-k chunks without touching a model.

- **Agents.** Five slash commands -- a ReAct loop, grammar-constrained tool calls, contract-checked runs, plan-and-execute, and a worker/critic loop -- with a tool catalog you choose per run and the trace rendered inline in the chat.

- **Workflows.** Multi-node DAGs with typed state, parallel branches and conditional routing, authored as Python in the workspace and run with a live trace.

- **Scripts.** Plain Python run as a job in a child process, which reaches the app's resident model over the loopback API. A 50-cell parameter sweep costs one model load. Streamed output, a working cancel, and files it writes served as artifacts.

- **Transcription and image generation.** Whisper transcription and stable-diffusion text-to-image, both as jobs, both gated on the bundled wheel providing them.

- **An OpenAI-compatible server.** Start and stop cyllama's server from the app to point other tools at the loaded model.

- **Jobs everywhere.** Every long operation is a job: streamed events, progress, cancel, retained log replay after a dropped stream, and downloadable artifacts.

- **Chat against an external provider.** OpenAI, Anthropic, OpenRouter, or any OpenAI-compatible endpoint (Ollama, LM Studio, Groq, Together, a llama.cpp server). Keys live in the OS encrypted store; the sidecar makes every call. Chat only for now -- see [`docs/dev/providers.md`](docs/dev/providers.md) for what comes next.

- **Local by default.** The sidecar binds `127.0.0.1` behind a per-launch bearer token. Nothing leaves the machine except model downloads you ask for, the two agent tools that are explicitly opt-in (`web_fetch`, `search_wikipedia`), and a chat you send to a provider you configured.

- **GPU backends.** One build per backend -- Metal, CUDA 12, Vulkan, ROCm, SYCL -- selected at build time. See [GPU variants](#gpu-variants).

## Concepts

- **Pane.** A UI surface in the app shell. Left nav-rail: Chats, Models, Agents (full-area pane covering agent-type defaults, workflow files, and scripts), Console. Right-sidebar tab: Parameters (system-style Settings opens in a separate Preferences window via `Cmd+,`). Panes whose underlying cyllama capability isn't present in the build hide themselves automatically via `/info.features`.

- **Workspace.** A *project*: a scoped bundle of inputs, outputs, chosen models, presets, agent tool sandbox, and config. Today only an implicit `default` workspace exists. Multi-workspace support and a workspace switcher land later (see [`docs/dev/plan.md`](docs/dev/plan.md) S.9).

- **Sidecar.** The bundled Python process running cyllama via FastAPI on `127.0.0.1`, gated by a per-launch bearer token. The renderer is a thin client; the sidecar is the single source of truth for inference, models, and jobs.

- **Script.** A Python file in `<workspace>/scripts/`, run as a job from the Agents pane. It executes in a child process with the bundled interpreter and talks back to the app over the loopback API, so `cyllama_desktop.app.chat()` reuses the model the sidecar already has loaded instead of loading a second copy. Output streams into the pane, cancel kills the process group, and files the script writes are downloadable as job artifacts. Scripts run with your full privileges -- the child process is for crash containment and a working cancel, not a sandbox -- so every row has a View button that opens the file read-only and syntax highlighted, and Run on a file you have not read shows you the code first. See [`docs/dev/scripting.md`](docs/dev/scripting.md).

- **Shipped examples.** The scripts and workflow rows list the examples the app ships alongside your own files. Install copies one into the workspace, where it is yours to edit; Uninstall removes it, and asks first if you have changed it. Nothing is written to the workspace until you install something, and Uninstall never offers to delete a file you wrote.

- **Provider.** An external inference endpoint the chat can run against instead of a local GGUF. Four kinds: `openai`, `anthropic`, `openrouter`, and a user-supplied OpenAI-compatible endpoint. Only Anthropic differs on the wire; the other three share one client and differ by base URL. See [External providers](#external-providers).

- **Slash commands.** A `/`-prefixed entry in the chat composer routes the prompt to a specific handler instead of `/chat`. The agent family of commands (`/agent`, `/agent-constrained` -- aliased `/agent-strict`, `/agent-contract`, `/agent-plan`, `/agent-reflect`) runs an agent loop against the loaded chat model with the sidebar's tool config; the trace + answer render inline in the chat stream. Tab autocompletes a unique prefix (`/a<Tab>` -> `/agent `). See `docs/slash-commands.md` for the taxonomy and roadmap.

See [`docs/dev/plan.md`](docs/dev/plan.md) for the phased rollout and `CHANGELOG.md` for what has shipped.

## User documentation

- [`docs/guide-to-agents.md`](docs/guide-to-agents.md) -- end-user guide to the agent slash-commands (`/agent`, `/agent-constrained`, `/agent-contract`, `/agent-plan`, `/agent-reflect`), the Tools catalog, and the workflow row of the Agents pane. Read this first if you want to *use* the agent layer; skip to [`docs/dev/agent_plan.md`](docs/dev/agent_plan.md) if you want to *extend* it.

- [`docs/slash-commands.md`](docs/slash-commands.md) -- slash-command design + taxonomy. How commands are registered, how autocomplete works, what kinds of commands exist.

### Composer capabilities

The chat messagebox accepts more than text:

- **Images** -- drop a `.png` / `.jpg` / `.webp` / `.gif` / `.bmp` onto the composer (or use the paperclip) when a multimodal model and its mmproj are loaded. Routed through cyllama's `ImageAnalyzer`.

- **Documents** -- drop a `.pdf` / `.md` / `.txt` / `.markdown` / `.json` / `.jsonl`; the sidecar extracts text via `cyllama.rag.loaders.load_document` (PDFs use the `pypdf` backend bundled by default; install `pymupdf` / `docling` for richer extraction). The text is inlined into the message the model sees and the chat log shows a folded chip with a context-window warning when the doc is large.

- **Voice prompts** -- mic button next to the paperclip records via `MediaRecorder`, transcribes via Whisper, appends the text to the typed prompt. Requires a whisper model in the Transcribe pane.

- **Quarto rendering** -- the `quarto_render` agent tool (opt-in in the Agents pane) lets the model generate `.pptx` / `.pdf` / `.docx` / `.html` files. Requires the `quarto` CLI on PATH. The resulting `file://` link in the assistant reply opens in your OS default app.

## External providers

Open Preferences (`Cmd+,`) -> Providers, or pick *Add a provider...* at the bottom of the model menu. Paste a key for OpenAI, Anthropic or OpenRouter and that provider appears in the model pill beside your local GGUFs. Picking one switches the chat backend; the pill then names the provider and the model, so which backend served a turn is always on screen.

**Any OpenAI-compatible endpoint** works too -- Ollama, LM Studio, Groq, Together, Fireworks, a `llama.cpp` server. Add it under *OpenAI-compatible endpoints* with a name and a base URL. The URL must be `https`, or `http` on `localhost` / `127.0.0.1` / `::1`: sending a key in clear to a remote host is refused rather than offered as a choice. A remote endpoint needs a key saved; one on localhost does not, since those servers authenticate nothing -- it is usable as soon as you add it, and a token is still used if you save one. Each endpoint keeps its own key and its own model list, keyed by name.

**Model lists** come from the provider's own list endpoint, cached per account under `<userData>/providers/`. Refresh is on demand, on a newly entered key, or past 24 hours; a failed refresh serves the cached list and says it may be stale. A model id typed by hand always works, which matters when a provider's list lags a release. The last model you used with each provider is remembered separately.

**What keys are.** They are encrypted with the OS store (Keychain, DPAPI, the session keyring) at `<userData>/credentials.json`, decrypted only to be handed to the sidecar over loopback, and held in its memory for the session. The renderer never receives one, and the sidecar deliberately does not read them from its environment -- it passes its own environment to every script it runs, so a key there would reach user scripts. On a system with no encrypted store available (a Linux session with no keyring) the tab says so and saves nothing.

**Scripts and workflows** reach providers too. `app.chat("...", provider="openai", model="gpt-5.4")` in a workspace script runs against a configured provider, and `app.providers()` lists what is reachable; `model=` is required there, since there is no local file to default to. A workflow node can import the same handle. The key stays in the sidecar either way -- a script names a provider and never sees a credential, though it can spend one, which is what the usage rows below are for. See [`docs/dev/scripting.md`](docs/dev/scripting.md) S7.5.

**Usage.** Every provider call books a row in `<workspace>/usage.db` -- account, model, token counts, and who asked (chat, a script, a workflow). Preferences -> Providers shows the totals with a Clear action. Tokens rather than money: a price table would go stale, and varies by tier and by cached input. An assistant turn served by a provider also carries that attribution in the chat's own history and shows it under the message, so reopening an old chat still says where those tokens went.

**Scope.** Chat, scripts and workflows. Everything else -- retrieval ingest, agents, transcription, image generation, quantize, GGUF inspection, layer estimates -- runs against local models, and each says why rather than failing when a provider is active. Sampling controls follow the active backend: rows a provider has no equivalent for are hidden, and the advanced section (grammar, speculative decoding, n-gram cache, multi-GPU split) is local-only in full. Retrieval in the composer is the exception: it retrieves locally and injects the sources, so it works with a provider already.

[`docs/dev/providers.md`](docs/dev/providers.md) carries the roadmap for the rest, ordered by value over effort.

## Build

Quick path: `make` builds an installer for the host platform (macOS arm64 -> `.dmg`). Other targets:

```text
make           Build a distributable installer (default = dmg on macOS)
make dev       npm install + build python env + npm start
make python    Build only the bundled Python env
make python-local
               Rebuild the env against a local cyllama checkout
make variant   Show which cyllama GPU variant the build is pinned to
make test      Run the sidecar pytest suite
make e2e       Run the Playwright per-pane smoke suite
make release-notes
               Write release-notes.md from the CHANGELOG section
make help      List every target
make clean     Remove dist/ and build/
make reset     clean + rebuild the bundled Python env
make remake    reset + run the app in dev mode
               (e.g. CYLLAMA_VERSION=0.4.7 make remake)
make reset-full
               clean + remove node_modules/
```

### GPU variants

cyllama publishes the same import package under one distribution name per backend, so the app can be built against whichever one matches the target machine. Pick it with a variant target, which rewrites the sidecar's pin, rebuilds the bundled Python env, and (for `app-*`) produces an installer named after the backend so builds don't overwrite each other in `dist/`:

```text
make variant-cpu     make app-cpu       # cyllama          (also Metal on macOS arm64)
make variant-cuda    make app-cuda      # cyllama-cuda12   (Linux, Windows)
make variant-vulkan  make app-vulkan    # cyllama-vulkan   (Linux, Windows, macOS x86_64)
make variant-rocm    make app-rocm      # cyllama-rocm     (Linux)
make variant-sycl    make app-sycl      # cyllama-sycl     (Linux)
```

`make variant` prints the current selection. **On Apple silicon there is nothing to choose**: the default `cpu` distribution's macOS arm64 wheel already has Metal compiled in, so it *is* the GPU build there. The variant targets matter on Linux and Windows, where the default wheel is CPU-only.

GPU runtimes are not bundled; PyPI's wheel size limit rules them out. The target machine needs the vendor runtime installed:

| Variant | Linux | Windows |
|-|-|-|
| cuda | CUDA 12 runtime + cuBLAS, NVIDIA driver | same |
| vulkan | Vulkan loader (`libvulkan1`) + GPU driver | GPU driver |
| rocm | ROCm 6 (HIP, hipBLAS, rocBLAS) | -- |
| sycl | Intel oneAPI runtime (SYCL, MKL, TBB) | -- |

On Linux, the cuda, rocm and sycl bundles fail at startup without it; cyllama links those runtimes directly. The Windows builds load the GPU backend lazily.

Only one distribution can be installed at a time -- they all own the same `cyllama/` directory -- so switching wipes and rebuilds the env rather than upgrading in place. The selected backend shows up at runtime in General -> backends (sourced from cyllama's own build config), which is the quickest way to confirm a bundle is what you think it is.

The choice lives in one line of `python-sidecar/pyproject.toml`; `scripts/build-python-env.sh` reads it back from there, so the wheel that gets installed and the sidecar's dependency metadata cannot disagree. Building from a local cyllama checkout (`make python-local`) is CPU-variant only, since a source build installs under the plain `cyllama` name whatever backend its own build flags selected.

Manual phases (what `make` runs under the hood):

### 1. One-time setup

```bash
cd ~/projects/personal/cyllama-desktop
npm install
```

This installs Electron + electron-builder + `@electron/notarize`, plus esbuild and Playwright. ~570 MB in `node_modules/`.

### 2. Build the Python env (per target arch, on a matching host)

```bash
bash scripts/build-python-env.sh
```

What this does:

- Detects your triple (`aarch64-apple-darwin` on Apple Silicon).

- Downloads CPython 3.12 from python-build-standalone into `build/python-mac-arm64/`.

- `pip install`s `cyllama` from PyPI plus the sidecar's own dependencies from `python-sidecar/pyproject.toml` (`fastapi`, `uvicorn[standard]`, `python-multipart`, `openai`, `anthropic`, `pypdf`). On macOS arm64 the PyPI wheel ships Metal as the default backend.

- Smoke-tests `import cyllama` and prunes caches.

Expect ~2-5 minutes the first time. Output ends with a `du -sh` of the resulting tree: ~175 MB for the default cpu/metal variant, more when a GPU backend is linked.

To use a local cyllama checkout instead of PyPI (e.g. for development against unreleased changes), set `CYLLAMA_SOURCE`:

```bash
CYLLAMA_SOURCE=../cyllama bash scripts/build-python-env.sh
```

### 3a. Run in dev (no packaging)

```bash
npm start
```

Electron's main process spawns `build/python-mac-arm64/bin/python3 python-sidecar/sidecar.py`, waits for `/health` to respond, then opens the window. Pick a model from the picker (or `Browse...` for a `.gguf` outside the cache) and type a prompt.

Dev runs store settings, chats and the model cache in `~/Library/Application Support/Cyllama Desktop Dev/`. Installed builds use `Cyllama Desktop/`.

If something is wrong, watch the terminal -- sidecar stdout/stderr is forwarded with `[sidecar]` / `[sidecar:err]` prefixes.

### 3b. Build a distributable `.dmg`

```bash
npm run build:mac-arm64
```

This calls `electron-builder --mac --arm64`, which:

- Reads `electron-builder.yml`.

- Copies `build/python-mac-arm64/` into `Resources/python/` inside the `.app`.

- Copies `python-sidecar/sidecar.py` into `Resources/python-sidecar/`.

- Bundles your JS into `Resources/app.asar`.

- Produces `dist/cyllama-desktop-0.3.1-cyllama-0.4.6-metal-arm64.dmg` (app version, bundled cyllama version, variant, arch). `scripts/set-cyllama-variant.py` rewrites that name whenever the variant changes; the `cpu` variant is labelled `metal` on Apple silicon.

**Unsigned build** (for local testing only): nothing else needed. Gatekeeper will warn the first time you open it; right-click -> Open to bypass.

**Signed + notarized build** (for distribution):

```bash
export CSC_LINK="/path/to/DeveloperID.p12"     # or base64 in CI
export CSC_KEY_PASSWORD="..."
export APPLE_ID="you@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="abcd-efgh-ijkl-mnop"
export APPLE_TEAM_ID="ABCD123456"
npm run build:mac-arm64
```

electron-builder signs every `.dylib`/`.so` under `Resources/python/`, then `scripts/notarize.js` submits to Apple via notarytool. Allow ~5-15 min for notarization to return.

## Releasing

Releases are tag-driven. Tags are bare semver equal to `package.json`'s version (`0.3.1`, not `v0.3.1`).

```bash
# after bumping package.json and renaming "## [Unreleased]" in CHANGELOG.md
make release-notes          # preview the release body
git tag 0.3.1 && git push origin 0.3.1
```

`.github/workflows/build.yml` builds all 9 installers, creates the release, attaches them, and sets the body from the version's CHANGELOG section (falling back to `## [Unreleased]`, then to GitHub's generated notes). One failed build publishes nothing. To redo a release, run the workflow manually with the existing tag.

## Quick smoke test of just the sidecar (no Electron)

```bash
CYLLAMA_SIDECAR_PORT=8765 \
CYLLAMA_SIDECAR_TOKEN=test \
CYLLAMA_SIDECAR_PARENT_PID=$$ \
build/python-mac-arm64/bin/python3 python-sidecar/sidecar.py &
curl -H "Authorization: Bearer test" http://127.0.0.1:8765/health
```

Should return `{"ok":true}`. Useful when isolating sidecar issues from Electron issues.

## Common failure modes

- **`Bundled Python not found`** on `npm start`: you skipped step 2, or built it for the wrong arch.

- **Sidecar fails to start within timeout**: open `python-sidecar/sidecar.py` in the bundled env directly (the smoke test above) and read the real traceback. Almost always either a missing native lib or a cyllama import error.

- **Empty model error in chat**: the renderer requires both a model path and a prompt before sending -- no implicit default.

- **`electron-builder install-app-deps` runs forever on `npm install`**: that postinstall is harmless on a fresh tree (no native Node deps), but if it hangs, remove the `postinstall` line -- you don't have native modules.

## Layout

Source tree:

```text
cyllama-desktop/
  Makefile                          build orchestration; see the target list above
  package.json                      Electron + electron-builder + @electron/notarize
  electron-builder.yml              bundle config (mac dmg arm64 by default)
  src/
    main/index.js                   spawns sidecar, allocates port, generates auth token, runs layout migration
    preload/index.js                exposes safe IPC to renderer
    preferences/                    the separate Preferences window (Cmd+,)
    renderer/
      index.html                    chat UI with strict CSP
      src/                          renderer source (esbuild input)
        main.js                          chat hot path, KaTeX/marked rendering
        lib/{sidecar,jobs,models,rag}.js bearer-auth HTTP, /jobs SSE client, model list, RAG
        features/                        agents pane, model-picker, models pane,
                                         documents, transcribe, image, server, presets, ...
      dist/renderer.js              esbuild bundle, minified with a linked
                                    sourcemap (gitignored; the .map is not packaged)
      vendor/                       marked + katex vendored under CSP 'self'
  python-sidecar/
    sidecar.py                      FastAPI app -- see endpoint list below
    cyllama_desktop.py              client library scripts import (stdlib only)
    pyproject.toml                  cyllama + fastapi + uvicorn + python-multipart + openai + anthropic + pypdf
  tests/
    test_*.py                       pytest suite for the sidecar (366 cases)
    e2e/                            Playwright per-pane smoke (boots Electron
                                    against tests/e2e/sidecar_launcher.py, which
                                    reuses the conftest cyllama stub so no real
                                    cyllama is required)
  playwright.config.js              e2e config; npm run test:e2e
  .github/workflows/ci.yml          pytest + Playwright on push + PR
  .github/workflows/build.yml       unsigned installer per platform x variant;
                                    a tag push publishes them as a release
  docs/
    guide-to-agents.md              end-user guide to the agent layer
    slash-commands.md               slash-command design + taxonomy
    dev/{plan,agent_plan,scripting}.md  design records
  scripts/
    build-python-env.sh             python-build-standalone bundler -> build/python-<os>-<arch>/
    set-cyllama-variant.py          rewrites the cyllama pin + installer name
    release_notes.py                CHANGELOG section -> release-notes.md
    notarize.js                     afterSign hook (no-op unless APPLE_ID set)
  resources/
    example-scripts/*.py            scripts offered by Install in the Agents pane
    example-workflows/*.py          workflows offered by Install
    entitlements.mac.plist          hardened-runtime entitlements for dlopen + JIT
```

Sidecar endpoints (all bearer-auth gated except `/health`):

```text
GET  /health                          -- liveness probe
GET  /info                            -- cyllama version, backends, features, paths
GET  /info/contract-presets           -- named pre/post-condition presets
POST /chat                            -- SSE chat (text-only or multimodal route)
POST /tokenize                        -- count tokens for a prompt
POST /unload                          -- release the cached LLM slot
POST /grammar/from-schema             -- JSON schema -> GBNF
POST /hardware/estimate-layers        -- VRAM-aware n_gpu_layers estimate
POST /documents/extract               -- composer document drop -> text
POST /chat/upload                     -- multipart image upload (multimodal)
GET  /chat/upload/{name}              -- serve an uploaded image
POST /audio/upload                    -- multipart audio upload (voice prompt)
GET  /models/cached                   -- list cached + HF-cached GGUFs
POST /models/inspect                  -- GGUF metadata
POST /models/import                   -- copy a local .gguf into MODELS_DIR
POST /models/hf/peek                  -- HEAD a HuggingFace resolve URL
POST /jobs/models.hf-download         -- download from HuggingFace (job)
POST /jobs/models/quantize            -- model_quantize wrapper (job)
GET  /quantize/ftypes                 -- ftype label -> int map
POST /jobs/batch                      -- batch_generate wrapper (job)
GET  /rag/collections                 -- list RAG collections
POST /rag/collections                 -- create
DEL  /rag/collections/{id}            -- delete
POST /jobs/rag.ingest                 -- ingest documents into a collection (job)
POST /rag/query                       -- streaming RAG query w/ sources (SSE)
POST /rag/retrieve                    -- retrieve-only top-k (no LLM)
POST /jobs/transcribe                 -- whisper transcription (job)
POST /jobs/image/txt2img              -- stable-diffusion text-to-image (job)
GET  /artifacts/image                 -- list past txt2img outputs
POST /jobs/agent/run                  -- ReActAgent runner with tool catalog (job)
POST /jobs/agent/constrained          -- grammar-constrained tool calls (job)
POST /jobs/agent/contract             -- runs under a contract preset (job)
POST /jobs/agent/plan                 -- planner + executor (job)
POST /jobs/agent/reflect              -- worker + critic loop (job)
GET  /workflows                       -- workspace workflow files + shipped examples
GET  /workflows/{id}/spec             -- static plan (levels, entry, exits, inputs)
POST /jobs/workflow/run               -- run a workflow in-process (job)
POST /workflows/examples/{id}/copy    -- install a shipped workflow
DEL  /workflows/examples/{id}         -- uninstall one (409 if locally edited)
GET  /scripts                         -- workspace script files + shipped examples
POST /jobs/script/run                 -- run a script in a child process (job)
POST /scripts/examples/{id}/copy      -- install a shipped script
DEL  /scripts/examples/{id}           -- uninstall one (409 if locally edited)
POST /server/start /server/stop       -- start/stop OpenAI-compat server
GET  /server/status                   -- current server state
POST /jobs/demo                       -- fake job emitting progress then a result
GET  /jobs                            -- list jobs
GET  /jobs/{id}                       -- one job's state
GET  /jobs/{id}/events                -- SSE event stream (single subscriber)
GET  /jobs/{id}/log?after=<seq>       -- replay retained events after a gap
GET  /jobs/{id}/result                -- terminal result
POST /jobs/{id}/cancel                -- cancel a running job
GET  /artifacts/{id}/{name}           -- serve a job's artifact off the filesystem
GET  /jobs/{id}/artifact/{name}       -- same, but 404s once the job is GC'd
```

Runtime data (under `app.getPath('userData')`):

```text
<userData>/
  models/                           global GGUF cache (workspaces pin a default by path; never duplicated)
  settings.json                     global Preferences (currently: extra model search roots)
  .layout_version                   migration stamp (currently "1")
  workspaces/
    default/                        the implicit default workspace
      chats/<chatId>.json             persisted chat history (atomic tmp+rename writes)
      artifacts/<jobId>/...           job outputs (HF downloads, image txt2img, batch JSONL, ...)
      rag/<collId>.sqlite             per-RAG-collection vector store + collections.json manifest
      uploads/<uuid>.<ext>            multimodal chat attachments (served via /chat/upload/<name>)
      scripts/*.py                    your scripts, plus any shipped ones you installed
      workflows/*.py                  your workflows, plus any shipped ones you installed
      presets/                        reserved (presets currently live in localStorage)
      sandbox/                        reserved (per-workspace agent file-tool sandbox root)
      settings.json                   reserved (per-workspace model pin, preset, system prompt)
```

Pre-existing top-level `chats/` and `artifacts/` from earlier installs are migrated into `workspaces/default/` automatically on first launch after the layout-aware build; the migration is gated by `.layout_version` so it runs at most once per install.
