# PLAN: Exposing cyllama's Features in cyllama-desktop

Status: living document. Phases 0-9 shipped (the txt2img slice of 6;
ReActAgent slice of 7). Owner: @shakfu. Last updated: 2026-05-06.

Companion docs: `README.md` (build), `TODO.md` (in-flight work), `CHANGELOG.md`.
This document supersedes the relevant sections of `TODO.md` once accepted; the
TODO becomes a tactical checklist while this file is the strategic map.

---

## 1. Goal

Systematically expose the full cyllama Python API to desktop users, with two
explicit personas served by one app:

- **Basic user** -- wants a chat window, picks a model, types, gets text back.
  Should never see speculative-decoding params, GBNF grammars, or backend
  flags. Sensible defaults; progressive disclosure.

- **Advanced user** -- wants every major knob cyllama exposes: sampler internals,
  speculative drafts, n-gram cache, multi-GPU split, grammar-constrained
  decoding, agents, RAG over local docs, embedded OpenAI-compatible server,
  Whisper, Stable Diffusion, GGUF inspection, batch jobs.

Both personas share one binary; the difference is UI mode, not feature gating.

## 2. Non-goals

- Re-implementing cyllama features in JS. The renderer is a thin client; the
  Python sidecar is the single source of truth. (Drift between an
  Electron-side reimplementation and cyllama proper is the failure mode this
  plan most needs to prevent.)
- A cloud / multi-user mode. This is a local-first single-user app; auth is
  loopback-bearer only.
- Replacing `cyllama` the CLI. The desktop is additive, not a substitute.

## 3. Current state (2026-05-06)

Sidecar (`python-sidecar/sidecar.py`):

- Endpoints: `/health`, `/info` (with `features` + `devices` +
  `server_kinds`), `/chat`, `/tokenize`, `/unload`, `/grammar/from-schema`,
  `/hardware/estimate-layers`, `/models/{cached,inspect,import}`,
  `/models/hf/{peek,download}`, `/rag/{collections,query,retrieve}` +
  `/jobs/rag.ingest`, `/jobs/transcribe`, `/jobs/image/txt2img`,
  `/jobs/agent/run`, `/server/{start,stop,status}`, `/jobs/batch`,
  `/jobs/models/quantize`, `/quantize/ftypes`, plus the `/jobs/*`
  registry (list/get/cancel/result/events/artifact).
- Sampler whitelist now includes the forward-looking fields
  (`presence_penalty`, `frequency_penalty`, `mirostat*`, `grammar`)
  filtered through `_GC_ACCEPTED` so they no-op cleanly on cyllama
  builds that don't accept them. Capability flags surface what's live.
- LLM cache keyed by `(model_path, hardware_signature)` so a sampling
  tweak doesn't evict the model but a load-time field change does.
- RAG retrieve cache is distinct from RAG query cache (no double model
  load when chat-side context injection runs alongside Documents).
- Server pane runs a single OpenAI-compatible server slot, loopback by
  default, with signal/atexit cleanup so the C-side thread doesn't
  outlive the desktop.

Renderer:

- Left nav-rail switches sidebar views: Chats, Documents, Transcribe,
  Image, Server, Batch, Console. Right sidebar tabs: Models, Agents,
  General. Panes that depend on optional cyllama capabilities hide
  themselves via `/info.features`.
- Chat: streaming, multi-turn, persistent chats, copy/regenerate,
  presets, full sampling surface (basic + Advanced disclosure for
  grammar / speculative / n-gram cache), retrieval injection from a
  RAG collection, ModelPicker, sampling and load-time hardware
  controls (multi-GPU rows hidden on single-GPU rigs), GBNF generation
  from JSON schema.
- Documents: collections + ingest jobs + streaming RAG query with
  expandable / Reveal-able source rows.
- Transcribe: WAV-only Whisper job with timestamped segment table and
  TXT / SRT / VTT copy.
- Image: stable-diffusion txt2img with auth-fetched blob preview.
- Server: start/stop EmbeddedServer or PythonServer + URL + curl.
- Batch: prompts in, results out, CSV/JSONL/server-artifact export.
- Models tab: cached models list, drag-drop import, HF download,
  metadata inspector, Tools section with quantize.
- Agents tab: ReActAgent runner with tool catalog (calculator,
  sandboxed read_file, web_fetch off-by-default, RAG query) and a
  type-tagged live trace.
- General tab: cyllama version, backends, paths, devices.

## 4. Feature surface to expose

This is the inventory of cyllama capabilities the app will surface, sourced
from the cyllama README. Each row maps a capability to the pane where it
will live (Section 5) and the sidecar endpoints it will need (Section 7).

Per-row status as of 2026-05-06: most basic + advanced rows ship
(see Section 8 phase markers and CHANGELOG). Notable rows still open:
`complete()` one-shot mode, multimodal LLAVA/MTMD image input, TTS,
embeddings as a first-class endpoint (RAG path uses them internally),
HF browse/search inside the app, video generation, ControlNet /
inpaint / LoRA / ESRGAN, ContractAgent UI. Forward-looking sampler
fields (presence/frequency/mirostat) are wired but inert until cyllama
exposes them; see TODO.md.

| Cyllama capability | Pane | New sidecar endpoint(s) | Persona |
|---|---|---|---|
| `LLM.chat()` streaming (already done) | Chat | `/chat` (exists) | basic + adv |
| `complete()` one-shot | Chat (mode toggle) | `/complete` | basic |
| `batch_generate()` | Batch | `/batch` (job-style) | adv |
| Sampler params (existing whitelist) | Chat right panel | `/chat` `params` | basic |
| Mirostat, presence/freq penalty | Chat advanced panel | `/chat` `params` | adv |
| Speculative decoding (`Speculative`, `SpeculativeParams`) | Chat advanced panel | `/chat` `speculative` | adv |
| N-gram cache | Chat advanced panel | `/chat` `ngram` | adv |
| Grammar / GBNF / JSON-schema constrained decoding | Chat advanced panel | `/chat` `grammar` + `/grammar/from-schema` | adv |
| Stop sequences (already in API; needs UI) | Chat right panel | `/chat` `params` | basic |
| Multi-GPU: `main_gpu`, `split_mode`, `tensor_split` | Settings -> Hardware | model-load options | adv |
| `n_gpu_layers` + `estimate_gpu_layers()` | Settings -> Hardware | `/hardware/estimate-layers` | adv |
| Multimodal LLAVA / MTMD (image input) | Chat composer | `/chat` multipart | basic + adv |
| `WhisperContext` (transcribe) | Transcribe | `/transcribe` (multipart) | basic |
| Whisper word/segment timestamps, lang detect | Transcribe advanced | same | adv |
| `text_to_image()` / `SDContext` (SD/SDXL/SD3/FLUX) | Image | `/image/txt2img`, `/image/img2img` | basic + adv |
| ControlNet / inpaint / LoRA / ESRGAN | Image advanced | image endpoints w/ flags | adv |
| Video gen (Wan, CogVideoX) | Image (mode) | `/video/generate` | adv |
| TTS (`cyllama tts`) | Speak | `/tts` | basic |
| Embeddings | Embed (or hidden util) | `/embed` | adv |
| RAG (`RAG`, `Embedder`, `HybridStore`) | Documents | `/rag/*` | basic + adv |
| `load_directory()` ingestion | Documents | `/rag/ingest` | basic |
| Agents (`ReActAgent`, `ConstrainedAgent`, `ContractAgent`, `@tool`) | Agents | `/agent/run` (SSE trace) | adv |
| `EmbeddedServer` / `PythonServer` (OpenAI-compat) | Settings -> Server | `/server/start`, `/server/stop`, `/server/status` | adv |
| `GGUFContext.from_file().get_all_metadata()` | Models manager | `/models/inspect` | basic + adv |
| HF download (`download_model`, `get_hf_file`, `list_cached_models`) | Models manager | `/models/hf/*` | basic |
| `model_quantize()`, `model_save_to_file()` | Models -> Tools | `/models/quantize` (job) | adv |
| `_backend.*` flags + `cyllama info` | Settings -> About | `/info` | basic + adv |
| Perf data (`get_perf_data`) | Console / footer | `/perf` or in-stream | adv |
| Cancel in-flight generation | Already wired | `LLM.cancel()` | both |

Out of scope until later (not on cyllama's stable surface or low ROI for a
desktop app): MCP client wiring, OpenAI/LangChain compat shims (those belong
in user code, not in the app), `AsyncLLM` (sidecar already async-bridges).

## 5. UI architecture: panes over a single chat

Vocabulary: a **pane** is a UI surface (Chat, Documents, Transcribe, ...).
A **workspace** is a project-level container of inputs, outputs, models,
and config that *spans* panes — see Section 9. One workspace contains many
panes' worth of state.

The current single-page chat will not scale to ~10 disjoint feature areas.
Proposal: keep the existing left nav-rail (already half-implemented per
`TODO.md`) and turn each rail icon into a *pane* with its own renderer
module. Panes share the global sidecar connection, the loaded model
slot, and a global Console drawer.

Not every surface is a pane. The current implementation puts Models,
Agents, and General as **tabs in the right sidebar**, adjacent to the
Chat pane rather than replacing it. That's a deliberate divergence from
the original draft (which had Models as a full pane #2): chat is the
dominant surface and model picking / config are side activities. Future
heavy surfaces (Documents, Transcribe, Image) remain full nav-rail panes.

Panes, in order of priority:

1. **Chat** (exists) -- with progressive disclosure for advanced sampling.
2. ~~**Models**~~ -- shipped as a right-sidebar tab, not a pane.
3. **Documents / RAG** -- collections, ingestion progress, query box.
4. **Transcribe** -- drag-drop audio, language picker, segment view.
5. **Image** -- txt2img / img2img / inpaint / video; gallery of outputs.
6. **Agents** -- agent runner with live tool-call trace.
7. **Batch** -- run a prompt list against a model, CSV/JSONL out.
8. **Server** -- start/stop the embedded OpenAI-compatible server, show URL.
9. **Console** (exists, drawer) -- sidecar logs, perf, backend info.
10. **Settings** -- shipped as the General right-sidebar tab.

Cross-cutting UI primitives (build once, reuse):

- **ModelPicker** component: model dropdown sourced from the Models tab,
  replacing the file dialog in every pane. Falls back to file dialog for
  "Other". (Shipped.)
- **JobRunner** component: long-running tasks (batch, ingest, quantize,
  HF download) all funnel through one job list with progress + cancel +
  result link. Backed by a `/jobs` family of endpoints (Section 7).
- **ParamPanel** with `basic | advanced` toggle. Persona is per-pane,
  saved in `localStorage`. Default: basic.

## 6. Personas via progressive disclosure (not a separate "advanced mode")

A global "advanced mode" hides things from basic users but also annoys power
users who want everything visible. Reject that.

Use *progressive disclosure per panel*:

- ParamPanel shows 5-6 essentials by default; a "More" twirl reveals the
  rest. The expanded state is remembered per pane.
- Pre-supplied **Presets** (already on the TODO list) give the basic user
  tuned bundles without ever touching individual sliders. Ship at least:
  `Default`, `Creative`, `Precise`, `Code`, `Long-context`. Per-model
  override possible.
- Advanced-only features (speculative, n-gram, grammar, multi-GPU split)
  live behind a single "Advanced" header in the same panel rather than a
  hidden mode. Users self-select by scrolling.

The tradeoff to be explicit about: a user who scrolls into `tensor_split`
and types nonsense can wedge model loading. The mitigation is server-side
validation that returns a structured 400, plus a "Reset to defaults" button
in every advanced section.

## 7. Sidecar API design

Endpoint additions, grouped. All are `POST` unless noted, all bearer-auth
gated, all loopback-only. Long-running ones return a `job_id` and stream
progress over SSE on `GET /jobs/{id}/events`.

```
GET  /info                              -> {backends, build, version}
GET  /models/cached                     -> [{path, name, size, quant}]
POST /models/inspect       {path}       -> {arch, n_params, ctx, kv, ...}
POST /models/hf/peek       {repo, file} -> file metadata, no download
POST /models/hf/download   {repo, file} -> {job_id}
POST /models/quantize      {src, dst, type} -> {job_id}
POST /hardware/estimate-layers {path, vram_mb} -> {n_layers}

POST /complete             {model_path, prompt, params}      -> SSE
POST /chat                 (existing; extend with grammar, speculative, ngram)
POST /grammar/from-schema  {schema}    -> {grammar}
POST /tokenize             (exists)

POST /embed                {model_path, texts}                -> {vectors}

POST /rag/collections      (CRUD)
POST /rag/ingest           {coll, sources, glob}              -> {job_id}
POST /rag/query            {coll, q, k, vector_w, fts_w}      -> SSE w/ sources

POST /transcribe           multipart audio + lang             -> SSE segments
POST /image/txt2img        {prompt, neg, w, h, steps, cfg, lora, controlnet} -> {job_id}
POST /image/img2img        multipart init + params            -> {job_id}
POST /video/generate       {...}                              -> {job_id}
POST /tts                  {text, voice}                      -> {job_id, audio_url}

POST /agent/run            {agent_type, tools, task, policy}  -> SSE trace
POST /batch                {model_path, prompts[], params}    -> {job_id}

POST /server/start         {model_path, port, kind}           -> {url}
POST /server/stop
GET  /server/status

GET  /jobs                                                    -> [{id,kind,state}]
GET  /jobs/{id}/events                                         -> SSE
POST /jobs/{id}/cancel
GET  /jobs/{id}/result                                         -> bytes or json
```

Design rules:

- **One LLM slot remains the default**, but model-load options (multi-GPU,
  n_gpu_layers) are stored *with* the cached slot so the renderer doesn't
  need to repeat them every call. Reloading with different load options
  evicts. (TODO already calls out replacing the single slot with an LRU;
  that lands as part of the Models tab, not before.)
- **All long jobs go through the `/jobs` machinery**, so the renderer has
  one progress/cancel pattern across panes. Avoid bespoke per-feature
  streaming protocols.
- **Whitelist stays.** Every new `params`-style field added to `/chat` etc.
  goes through the same `_ALLOWED_PARAMS`-style guard. Never `**body`.
- **Errors** return `{error: {code, message, detail?}}`, never bare 500s
  to the renderer. Map cyllama exceptions to codes (`MODEL_NOT_FOUND`,
  `OUT_OF_VRAM`, `GRAMMAR_PARSE`, `BACKEND_UNSUPPORTED`).
- **Output artifacts** (images, audio, video, quantized models) are written
  to `<userData>/artifacts/<job_id>/...` and surfaced to the renderer as
  loopback URLs `http://127.0.0.1:<port>/artifacts/<id>/<file>`.

## 8. Phased delivery

Each phase is independently shippable. Don't start phase N+1 until N is
green on `make test` and a manual smoke pass.

Status legend: ✅ shipped · 🟡 partial (slice noted) · ⬜ not started.
See `CHANGELOG.md` for the per-slice notes.

**Phase 0 -- foundations** ✅ (no user-visible feature, enables the rest)
- `/jobs` infrastructure in sidecar (Python `asyncio.Task` registry, SSE
  events, cancel, result download, artifact dir).
- Introduce **esbuild** and split `renderer.js` into per-pane modules
  with a shared `lib/` (sidecar client, ParamPanel, ModelPicker, JobRunner).
  Done now rather than piecemeal later (Section 12 Q4).
- Renderer `JobRunner` component + `cyllama.jobs` IPC surface.
- `/info` endpoint and Settings -> About panel showing backends.
- Pytest suite for sidecar (currently absent per `TODO.md`).

**Phase 1 -- Models tab + ModelPicker** ✅
- `/models/cached`, `/models/inspect`, `/models/hf/peek`,
  `/models/hf/download`.
- Models view: list cached, drag-drop import, HF URL input -> download job,
  GGUF metadata side panel.
- Replace file-dialog flow in Chat with ModelPicker.

**Phase 2 -- Chat parity with cyllama sampler surface** ✅
- Add presence/freq penalty, mirostat, full stop-sequence UI.
- Presets store + dropdown (already on TODO).
- Grammar input (raw GBNF text + JSON-schema -> grammar via
  `/grammar/from-schema`) behind Advanced.
- Speculative decoding panel (draft model picker reuses ModelPicker).
- N-gram cache toggle.

**Phase 3 -- Hardware controls** ✅
- `/hardware/estimate-layers` + Settings -> Hardware panel.
- Multi-GPU `split_mode`, `tensor_split`, `main_gpu` controls
  (only shown if `_backend` reports >1 device).
- Model load uses these; eviction on change.

**Phase 4 -- Documents / RAG** ✅
- `/rag/*` endpoints; per-collection SQLite under `<userData>/rag/`.
- Ingest jobs via JobRunner.
- Query box that streams answer + clickable source list.
- "Use this collection in Chat" toggle that injects retrieval as a
  pre-message system context (transparent to chat code).

**Phase 5 -- Transcribe** 🟡 (WAV input only; ffmpeg fallback for
mp3/m4a/flac/ogg deferred. Timestamp scrubbing -- click-to-play -- not
yet wired; segment table + TXT/SRT/VTT copy shipped.)
- `/transcribe` with Whisper; segment table, timestamp scrubbing,
  copy-as-SRT/VTT.

**Phase 6 -- Image / Video** 🟡 (txt2img slice shipped. Gallery view
of past artifacts, img2img / inpaint canvas, ControlNet upload, LoRA
selector, ESRGAN upscale, and video generation deferred.)
- `/image/*`, `/video/generate`. Gallery view backed by artifacts dir.
- Advanced reveals: ControlNet image upload, inpaint mask canvas,
  LoRA selector, ESRGAN upscale post-step.

**Phase 7 -- Agents** 🟡 (ReActAgent + tool catalog shipped.
ContractAgent pre/post UI tracked in `TODO.md`.)
- `/agent/run` with live tool-call trace SSE.
- Pre-shipped tool catalog: web fetch (off by default), file read inside
  a chosen sandbox dir, RAG-collection query, calculator.
- ContractAgent UI: pre/post conditions as text fields.

**Phase 8 -- Server pane** ✅
- Start/stop EmbeddedServer or PythonServer with the loaded model.
- Show OpenAI-compatible URL + curl example.
- Local-only by default; explicit "expose on LAN" checkbox with warning.

**Phase 9 -- Batch + Tools** ✅
- Batch pane: paste/import prompts, run via `batch_generate`,
  download CSV/JSONL.
- Models -> Tools: quantize job, GGUF re-save.

Phases 4-7 are independently parallelizable once 0-3 are in.

---

What's next: the partial phases (5/6/7) have specific sub-features
tracked in `TODO.md`. Once cyllama exposes a richer image API
(progress callbacks, img2img, video) the slice notes in Phase 6
become discrete follow-up phases rather than open questions.

## 9. Workspaces (projects), persistence, and config

A **workspace** is a project: a scoped bundle of inputs (markdown, code,
PDFs, other documents), an output directory, a pinned default model +
sampling preset + system prompt, RAG collections, agent tool sandbox,
and the workflows configured to operate on them. This is distinct from
a *pane* (Section 5), which is a UI surface. One workspace contains
many panes' worth of state.

**Staging.** Ship a single implicit `default` workspace now. Mature the
contained surfaces (chat, RAG, agents, image) inside it. Add
multi-workspace support and a workspace switcher only after the
contained surfaces are proven. The storage layout below is designed so
that promotion is a UI addition, not a data migration.

### Scoped vs global

| Scope | Items |
|---|---|
| Per-workspace | chats, RAG collections, output artifacts, presets, system prompt, default model pin + load options, agent tool sandbox dir, workspace `settings.json` |
| Global | GGUF model cache, HF download cache, hardware settings, theme, sidecar port, bearer token, global `settings.json` |

GGUFs are too large to duplicate per workspace, so the model cache is
global; a workspace just *pins* a default by path. The agent tool sandbox
is per-workspace because it's the security-relevant boundary -- a tool
configured in one project should not see another project's files.

### Storage layout

All app data under `app.getPath('userData')`:

```
<userData>/
  models/                       # global GGUF cache (HF mirror or symlink to ~/.cache/llama.cpp/)
  settings.json                 # global: hardware, theme, server, sidecar
  workspaces/
    default/
      chats/<uuid>.json
      rag/<collection>.sqlite
      artifacts/<job_id>/...
      presets/
      sandbox/                  # agent file-tool sandbox root (refuses paths outside)
      settings.json             # workspace-scoped: default model pin, sampling preset, system prompt, RAG default
```

Existing per-project paths (`chats/`, `artifacts/`, `presets/`, `rag/`)
move under `workspaces/default/` in a one-time migration on first launch
after this lands. Subsequent launches treat the new layout as
authoritative.

### settings.json shapes (versioned, atomic writes)

Global `<userData>/settings.json`:
```
{ "version": 1,
  "hardware": { "n_gpu_layers": "auto", "split_mode": 1, ... },
  "ui": { "theme": "system", "advancedExpanded": {...} },
  "server": { "kind": "embedded", "port": 0, "exposeLan": false } }
```

Per-workspace `<userData>/workspaces/<id>/settings.json`:
```
{ "version": 1,
  "model": { "default": "<path-into-global-cache>", "loadOptions": {...} },
  "presetId": "...",
  "systemPrompt": "...",
  "ragDefaultCollection": "..." }
```

Existing chat schema is unaffected; chats gain optional fields
(`grammarId`, `presetId`, `ragCollectionId`) on a forward-compatible basis.

## 10. Security & sandboxing

- Loopback-only bearer remains. Reject `0.0.0.0` binds even when the user
  enables Server -> expose-on-LAN unless they confirm a second time.
- Agent file-read tool refuses paths outside the user-chosen sandbox dir.
- HF downloads validate the redirected origin is on a known allowlist
  (`huggingface.co`, its CDN). Hash check if a manifest is available.
- CSP today includes `'unsafe-eval'` for KaTeX (per TODO). Keep that scoped
  and don't widen it for new panes.
- Image / video / quantize jobs can fill disk fast; show a usage gauge in
  the Jobs panel and refuse new jobs when the artifact dir exceeds a
  configurable cap (default 20 GB).

## 11. Testing

- **Sidecar (pytest)**: every endpoint above; bearer enforcement; SSE
  framing; job cancel; error envelope shape; whitelist rejection of
  unknown params.
- **Renderer (Playwright)**: per-pane happy path. Each phase ships
  with at least one e2e.
- **Smoke**: `make test && make python && make dev && curl /health`
  pre-commit hook for now; CI when distribution work begins.
- Per CLAUDE.md house rules: zero tolerance for failing tests; root-cause
  before weakening assertions.

## 12. Resolved decisions

The original draft listed these as open questions; resolutions are recorded
here so future-readers see the call without spelunking git history.

1. **Sidecar process model.** Single process now; revisit per-pane
   sub-processes (image, video, server) once SD lands and stability data
   exists. The `/jobs` machinery in Phase 0 is designed so a future broker
   can fan out to sub-processes without changing the renderer contract.
2. **HF download UX.** Paste-URL only in Phase 1. In-app browse / search
   deferred to a later phase (needs HF API surface + rate-limit handling).
3. **OpenAI / LangChain compat shims.** Dropped from scope. Target users
   write code, not click buttons; document the shim as a README example
   instead of building UI.
4. **Pane renderer split.** esbuild + per-pane modules land in
   Phase 0 alongside JobRunner; see Section 8.
5. **Bundle size.** No fixed budget -- size is a function of which
   cyllama backend(s) are linked into the wheel. Per-target installer size
   is tracked in `CHANGELOG.md` per release; act if a regression appears,
   not on a preset cap.
6. **Windows backend strategy** (CUDA vs CPU/Vulkan, two installers vs
   runtime detection). Parked. macOS arm64 ships first; Windows decision
   re-opens before any Windows phase work begins.

## 13. Definition of done (per phase)

- Endpoint(s) merged with pytest coverage.
- Pane shipped behind no flag (or behind `experimental: true` in
  settings.json if explicitly called out).
- Manual smoke documented in `CHANGELOG.md` under `[Unreleased]`.
- Updated `README.md` if a user-visible flow changed.
- `TODO.md` items checked or moved.

## 14. References

- cyllama: https://github.com/shakfu/cyllama (README is the API source of
  truth as of this writing; pin a version once Phase 1 lands).
- llama.cpp grammars / GBNF: upstream docs, mirrored in cyllama.
- python-build-standalone: used by `scripts/build-python-env.sh`.
