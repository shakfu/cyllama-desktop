# cyllama-desktop

Electron desktop app that runs [cyllama](https://github.com/shakfu/cyllama) via a bundled Python sidecar.

## Concepts

- **Pane.** A UI surface in the app shell. Sidebar views (left
  nav-rail): Chats, Documents, Transcribe, Image, Server, Batch,
  Console. Right-sidebar tabs: Models, Agents, General. Panes whose
  underlying cyllama capability isn't present in the build hide
  themselves automatically via `/info.features`.
- **Workspace.** A *project*: a scoped bundle of inputs, outputs,
  chosen models, presets, agent tool sandbox, and config. Today only
  an implicit `default` workspace exists. Multi-workspace support and
  a workspace switcher land later (see `PLAN.md` S.9).
- **Sidecar.** The bundled Python process running cyllama via FastAPI
  on `127.0.0.1`, gated by a per-launch bearer token. The renderer is
  a thin client; the sidecar is the single source of truth for
  inference, models, and jobs.

See `PLAN.md` for the phased rollout and `CHANGELOG.md` for what has
shipped.

## Build

Quick path: `make` builds an installer for the host platform (macOS arm64 -> `.dmg`). Other targets:

```
make           Build a distributable installer (default = dmg on macOS)
make dev       npm install + build python env + npm start
make python    Build only the bundled Python env
make test      Run the sidecar pytest suite
make e2e       Run the Playwright per-pane smoke suite
make clean     Remove dist/ and build/
make reset     Also remove node_modules/
```

Manual phases (what `make` runs under the hood):

### 1. One-time setup

```bash
cd ~/projects/personal/cyllama-desktop
npm install
```

This installs Electron + electron-builder + `@electron/notarize`. ~200 MB in `node_modules/`.

### 2. Build the Python env (per target arch, on a matching host)

```bash
bash scripts/build-python-env.sh
```

What this does:

- Detects your triple (`aarch64-apple-darwin` on Apple Silicon).
- Downloads CPython 3.12 from python-build-standalone into `build/python-mac-arm64/`.
- `pip install`s `cyllama` from PyPI plus `fastapi`, `uvicorn[standard]`, and `python-multipart`. On macOS arm64 the PyPI wheel ships Metal as the default backend.
- Smoke-tests `import cyllama` and prunes caches.

Expect ~2-5 minutes the first time. Output ends with a `du -sh` of the resulting tree (typically 200-400 MB depending on which cyllama backends are linked).

To use a local cyllama checkout instead of PyPI (e.g. for development against unreleased changes), set `CYLLAMA_SOURCE`:

```bash
CYLLAMA_SOURCE=../cyllama bash scripts/build-python-env.sh
```

### 3a. Run in dev (no packaging)

```bash
npm start
```

Electron's main process spawns `build/python-mac-arm64/bin/python3 python-sidecar/sidecar.py`, waits for `/health` to respond, then opens the window. Pick a `.gguf` file via the Browse button and type a prompt.

If something is wrong, watch the terminal — sidecar stdout/stderr is forwarded with `[sidecar]` / `[sidecar:err]` prefixes.

### 3b. Build a distributable `.dmg`

```bash
npm run build:mac-arm64
```

This calls `electron-builder --mac --arm64`, which:

- Reads `electron-builder.yml`.
- Copies `build/python-mac-arm64/` into `Resources/python/` inside the `.app`.
- Copies `python-sidecar/sidecar.py` into `Resources/python-sidecar/`.
- Bundles your JS into `Resources/app.asar`.
- Produces `dist/Cyllama Desktop-0.1.0-arm64.dmg`.

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
- **Empty model error in chat**: the renderer requires both a model path and a prompt before sending — no implicit default.
- **`electron-builder install-app-deps` runs forever on `npm install`**: that postinstall is harmless on a fresh tree (no native Node deps), but if it hangs, remove the `postinstall` line — you don't have native modules.

## Layout

Source tree:

```
cyllama-desktop/
  package.json                      Electron + electron-builder + @electron/notarize
  electron-builder.yml              bundle config (mac dmg arm64 by default)
  src/
    main/index.js                   spawns sidecar, allocates port, generates auth token, runs layout migration
    preload/index.js                exposes safe IPC to renderer
    renderer/
      index.html                    chat UI with strict CSP
      src/                          renderer source (esbuild input)
        main.js                       chat hot path, KaTeX/marked rendering
        lib/{sidecar,jobs,models}.js  bearer-auth HTTP, /jobs SSE client, model list
        features/                     model-picker, models tab, presets, right-sidebar tabs, ...
      dist/renderer.js              esbuild output bundle (loaded by index.html)
      vendor/                       marked + katex vendored under CSP 'self'
  python-sidecar/
    sidecar.py                      FastAPI app -- see endpoint list below
    pyproject.toml                  cyllama + fastapi + uvicorn + python-multipart
  tests/
    test_*.py                       pytest suite for the sidecar (~170 cases)
    e2e/                            Playwright per-pane smoke (boots Electron
                                    against tests/e2e/sidecar_launcher.py, which
                                    reuses the conftest cyllama stub so no real
                                    cyllama is required)
  playwright.config.js              e2e config; npm run test:e2e
  .github/workflows/ci.yml          pytest + Playwright on push + PR
  scripts/
    build-python-env.sh             python-build-standalone bundler -> build/python-<os>-<arch>/
    notarize.js                     afterSign hook (no-op unless APPLE_ID set)
  resources/
    entitlements.mac.plist          hardened-runtime entitlements for dlopen + JIT
```

Sidecar endpoints (all bearer-auth gated except `/health`):

```
GET  /health                          -- liveness probe
GET  /info                            -- version, backends, devices, features, paths
POST /chat                            -- SSE chat (text-only or multimodal route)
POST /tokenize                        -- count tokens for a prompt
POST /unload                          -- release the cached LLM slot
POST /grammar/from-schema             -- JSON schema -> GBNF
POST /hardware/estimate-layers        -- VRAM-aware n_gpu_layers estimate
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
GET  /artifacts/{id}/{name}           -- serve a job's artifact (registry-free)
POST /jobs/agent/run                  -- ReActAgent runner with tool catalog (job)
POST /server/start /server/stop       -- start/stop OpenAI-compat server
GET  /server/status                   -- current server state
POST /chat/upload                     -- multipart image upload (multimodal)
GET  /chat/upload/{name}              -- serve an uploaded image
GET  /jobs                            -- list / GET /jobs/{id}, /events, /result
POST /jobs/{id}/cancel                -- cancel a running job
```

Runtime data (under `app.getPath('userData')`):

```
<userData>/
  models/                           global GGUF cache (workspaces pin a default by path; never duplicated)
  .layout_version                   migration stamp (currently "1")
  workspaces/
    default/                        the implicit default workspace
      chats/<chatId>.json             persisted chat history (atomic tmp+rename writes)
      artifacts/<jobId>/...           job outputs (HF downloads, image txt2img, batch JSONL, ...)
      rag/<collId>.sqlite             per-RAG-collection vector store + collections.json manifest
      uploads/<uuid>.<ext>            multimodal chat attachments (served via /chat/upload/<name>)
      presets/                        reserved (presets currently live in localStorage)
      sandbox/                        reserved (per-workspace agent file-tool sandbox root)
      settings.json                   reserved (per-workspace model pin, preset, system prompt)
```

Pre-existing top-level `chats/` and `artifacts/` from earlier installs are migrated into `workspaces/default/` automatically on first launch after the layout-aware build; the migration is gated by `.layout_version` so it runs at most once per install.
