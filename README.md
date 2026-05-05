# cyllama-desktop

Electron desktop app that runs [cyllama](https://github.com/shakfu/cyllama) via a bundled Python sidecar.

## Concepts

- **Pane.** A UI surface in the app shell. Today: Chat (the main pane) plus three right-sidebar tabs (Models, Agents, General). Future panes (Documents/RAG, Transcribe, Image, Agents, Batch, Server) get their own nav-rail entries.
- **Workspace.** A *project*: a scoped bundle of inputs, outputs, chosen models, presets, agent tool sandbox, and config. Today only an implicit `default` workspace exists. Multi-workspace support and a workspace switcher land later (see `PLAN.md` S.9).
- **Sidecar.** The bundled Python process running cyllama via FastAPI on `127.0.0.1`, gated by a per-launch bearer token. The renderer is a thin client; the sidecar is the single source of truth for inference, models, and jobs.

See `PLAN.md` for the phased rollout and `CHANGELOG.md` for what has shipped.

## Build

Quick path: `make` builds an installer for the host platform (macOS arm64 -> `.dmg`). Other targets:

```
make           Build a distributable installer (default = dmg on macOS)
make dev       npm install + build python env + npm start
make python    Build only the bundled Python env
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
- `pip install`s `cyllama` from PyPI plus `fastapi` and `uvicorn[standard]`. On macOS arm64 the PyPI wheel ships Metal as the default backend.
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
    sidecar.py                      FastAPI: /health, /info, /chat (SSE), /tokenize, /unload, /models/*, /jobs/*, /hardware/estimate-layers
    pyproject.toml                  declares cyllama + fastapi + uvicorn
  tests/                            pytest suite for the sidecar (50 cases)
  scripts/
    build-python-env.sh             python-build-standalone bundler -> build/python-<os>-<arch>/
    notarize.js                     afterSign hook (no-op unless APPLE_ID set)
  resources/
    entitlements.mac.plist          hardened-runtime entitlements for dlopen + JIT
```

Runtime data (under `app.getPath('userData')`):

```
<userData>/
  models/                           global GGUF cache (workspaces pin a default by path; never duplicated)
  .layout_version                   migration stamp (currently "1")
  workspaces/
    default/                        the implicit default workspace
      chats/<chatId>.json             persisted chat history (atomic tmp+rename writes)
      artifacts/<jobId>/...           outputs from /jobs (HF downloads now; image/video/batch later)
      presets/                        reserved (presets currently live in localStorage)
      sandbox/                        reserved (per-workspace agent file-tool sandbox root)
      settings.json                   reserved (per-workspace model pin, preset, system prompt)
```

Pre-existing top-level `chats/` and `artifacts/` from earlier installs are migrated into `workspaces/default/` automatically on first launch after the layout-aware build; the migration is gated by `.layout_version` so it runs at most once per install.
