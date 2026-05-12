# TODO

Roughly ordered by user impact and effort. Strike through items as they ship,
or move them to `CHANGELOG.md` under `[Unreleased]`.

## Conversation

- [x] **Persistent chat history.** One JSON file per chat under
      `<userData>/chats/<uuid>.json`, atomic writes, IPC-gated path with
      ID allowlist, sidebar lists real chats sorted by updatedAt, hover-
      reveal delete with confirm, click-to-switch, auto-save after each
      completed turn, active-chat-id restored on launch via localStorage.
      Still TODO: rename UI, search, export/import, full-text search.
- [x] **System prompt.** Per-chat: stored in the chat's JSON, loaded
      into the right-panel textarea on switch. New chats inherit a
      default seed kept in `localStorage`.
- [x] **Multi-turn context.** Renderer keeps an in-memory `messages` list,
      sends full history on each turn. Sidecar always uses `llm.chat()`.
      Persistence to disk is the next task (history resets on app reload).
- [x] **Real token counts.** Sidecar `/tokenize` returns true token
      counts; renderer uses them on every save. Estimate kept as fallback.
- [x] **Stop-generation that actually stops.** Done: cyllama exposes
      `LLM.cancel()` (Python event + nogil ggml_abort_callback), sidecar
      calls it on `asyncio.CancelledError`. Bump pin to `cyllama>=0.2.14`.
- [x] **Copy / regenerate** message actions wired.
- [ ] **Edit and Branch** message actions. Edit: in-place editable
      user-text + regenerate-after-edit flow. Branch: clone history
      up to the exchange and create a new chat.

## Sampling and model parameters

- [x] Sampling controls in the right panel: temperature, top_p, top_k,
      min_p, repeat_penalty, max_tokens, seed, stop sequences. Pass through
      `/chat` body via `params` object, sidecar whitelists into
      `GenerationConfig`.
- [x] **Presets**: built-in seeds (Default/Creative/Precise/Code/Long-context)
      plus user-saved bundles in `localStorage` under `presets_v1`. Active
      preset persisted in `presets_v1_active`. Lives at the top of the
      Models tab Sampling section.
- [x] **Stop sequences** UI (Phase 2).
- [x] **Grammar / GBNF** input (Phase 2). `/grammar/from-schema`
      endpoint + advanced UI shipped. End-to-end `LLM.chat` grammar
      enforcement still depends on cyllama exposing it in
      `GenerationConfig`; the sidecar's `_GC_ACCEPTED` gate makes it
      auto-light up when that lands.
- [x] **Speculative + n-gram** UI rows (Phase 2). UI + sidecar wire
      shipped behind `data-feature` gates that consume
      `/info.features.{speculative,ngram}`; rows hidden until cyllama
      threads the classes through `LLM.chat`.
- [ ] **Forward-looking sampler fields** still waiting on cyllama for
      `presence_penalty`, `frequency_penalty`, `mirostat`,
      `mirostat_tau`, `mirostat_eta`, `grammar`, `speculative`,
      `ngram` to actually take effect. UI + sidecar whitelist are
      already in place; rows surface automatically once
      `/info.supported_params` and `/info.features` advertise them.
      Verification on bump: `build/python-mac-arm64/bin/python3 -c
      "from cyllama import GenerationConfig;
       print(GenerationConfig(presence_penalty=0.3, mirostat=2))"`
      should not raise. If cyllama uses different names, remap in
      `_ALLOWED_PARAMS` and the renderer's `PARAM_DEFAULTS`.

## Markdown / rendering

- [ ] Track `\[...\]` display math and 4-space indented code blocks in
      `findStableSplit`; today only fenced code and `$$...$$` are tracked, so
      `\[...\]` math could be prematurely committed.
- [ ] Syntax highlighting in fenced code blocks (Highlight.js or Shiki —
      Shiki is heavier but renders better; needs vendoring).
- [ ] Copy-code button on each `<pre>` block.
- [ ] Image rendering (markdown `![]()` already works; verify with a real
      response, add max-width).
- [ ] Tables: horizontal scroll on overflow rather than column collapse.

## Models

- [x] **Model manager**: cached models list, drag-drop import, HF
      URL downloader with /jobs progress (Phase 1).
- [x] ModelPicker dropdown replaces the file-picker-only flow
      (Phase 1).
- [x] Per-model GGUF metadata side panel (Phase 1).
- [x] Quantize tool: source picker + ftype dropdown + dest filename
      (Phase 9). The endpoint is shipped; the UI surface needs to
      be re-attached to the new full-area Models pane (the old
      right-sidebar Models tab implementation lives in
      models-tab.js but is no longer mounted).
- [x] Eject releases the `LLM` and frees GPU memory via `/unload`.
- [ ] HF browse / search inside the app (currently URL-paste only).
      Needs HF API surface + rate-limit handling.
- [ ] Resumable HF downloads (current job re-fetches from byte 0).

## Multimodal

- [x] **Image input** (LLAVA / MTMD). Composer paperclip + image
      uploads + chat routing through `ImageAnalyzer`. Single-shot
      answer; streaming-token multimodal via `VisionLanguageChat`
      deferred. Multi-image per message also deferred.
- [x] **Whisper transcription** as its own sidebar view with
      timestamped segments and TXT/SRT/VTT copy. WAV-only for now;
      ffmpeg fallback for mp3/m4a/flac/ogg deferred.
- [x] **Stable Diffusion** as its own sidebar view (txt2img +
      gallery). Img2img / inpaint / ControlNet / LoRA / ESRGAN /
      video deferred.
- [ ] Voice prompts: capture audio in the composer, run Whisper,
      drop transcribed text into the prompt textarea.
- [ ] Audio decode fallback for Transcribe: shell out to `ffmpeg`
      when input is non-WAV. Detect ffmpeg at probe time, surface
      via `/info.features.audio_decode`.
- [ ] Streaming-token multimodal answers (`VisionLanguageChat`
      generator) so long answers don't block.

## Agents

- [x] **ContractAgent UI** (Phase 7 follow-up). Landed as Phase B
      `/agent-contract` slash + the Contract section in the Agents
      pane (preset + policy from a named registry, modal at
      invocation). Custom user-authored contracts (vs. shipped
      presets) remain workflow-shaped -- write a workspace Python
      file and run it via `/agent-workflow`.

- [ ] **Phase F.4: per-agent-type run history pane.** Right
      detail-rail column of the Agents pane currently shows a "Run
      a workflow to see its result here" placeholder for every
      type except workflow. F.4 fills this with a list of recent
      runs (per agent type), each clickable to drill into the full
      trace + final state + error. Needs:

      - Sidecar: persist per-job `kind` / `state` / `result_summary`
        beyond the in-memory `Job` registry so a renderer reload
        doesn't drop history. SQLite at `<workspace>/run_history.db`
        with a small migration is the natural shape.
      - Renderer: subscribe to `job:done` events and refresh the
        right rail. Click a row to open a read-only trace viewer
        (similar to the chat-stream inline trace renderer).
      - Retention: cap rows per type (last 50?) and surface a
        "Clear history" action in the rail.

      Defer until the rest of Phase F (slash rename + pane +
      modal) has lived in real use long enough to know whether
      history is the right shape, or whether per-run linkbacks
      from the chat stream are enough.

## App shell

- [x] Nav-rail wired for Chats, Documents, Transcribe, Image,
      Server, Batch, Console, plus the cog → General tab jump.
- [x] Console / log view: live tail of sidecar stdout/stderr. Slide-up
      panel toggled by the Terminal nav-rail button. 2000-line ring
      buffer in main, lazy-populated on first open, color-coded stderr,
      sticky-scroll-aware.
- [ ] Settings panel: theme override, default sampling, model
      directory. The General tab is the home; About + Devices are
      there now, Preferences is still a placeholder.
- [ ] Update window title to active chat name.
- [ ] Hidden-titlebar mode on macOS for a more polished feel
      (`titleBarStyle: 'hiddenInset'`); requires draggable header region.

## Distribution

- [ ] Auto-update wiring. `electron-updater` is in deps but unused. Decide
      on a publish channel (GitHub Releases is the default).
- [ ] Validate the macOS arm64 build pipeline end-to-end: build, sign,
      notarize, install on a clean machine, verify Gatekeeper passes.
- [ ] macOS x86_64 build (PBS triple `x86_64-apple-darwin`); requires a
      matching CI runner.
- [ ] Windows installer: pick CUDA vs CPU/Vulkan strategy. Either two
      installers or one installer with both Python envs and runtime
      detection. Verify cyllama's CUDA install on a Windows CI host first.
- [ ] Linux AppImage: validate cyllama's Linux wheel + GPU backend story.
- [ ] Crash reporting (Sentry or similar) with sidecar/renderer separation.

## Testing

- [x] **Sidecar pytest** (~170 cases): every endpoint, bearer-token
      enforcement, `/health`, `/chat` SSE framing, error envelope,
      whitelist + capability gating.
- [x] **Renderer Playwright** per-pane smoke (10 specs): each
      sidebar view + right-tab boots a fresh Electron + stubbed
      sidecar and asserts the primary content renders.
- [x] **CI** (`.github/workflows/ci.yml`): pytest + Playwright run
      on push to `main` and pull_request, with `ubuntu-latest` +
      `xvfb-run` for the e2e job.
- [ ] Real-cyllama smoke suite (opt-in): a small `tests/smoke/` set
      that runs against the bundled `build/python-mac-arm64/` python
      env, exercising the actual cyllama API surface so capability
      probes catch shape drifts on cyllama bumps. Slow + expensive,
      so kept out of `make test`.
- [ ] Cross-platform smoke: `make python && make dev && curl /health`
      in CI for each target platform once Windows / Linux distribution
      lands.
- [ ] Deeper Playwright flows: model-picker round-trip, send +
      abort, sidebar collapse persistence, KaTeX render verification.
      Current suite is rendering-only.

## Tech debt

- [x] Renderer split into per-feature modules under
      `src/renderer/src/features/` and bundled with esbuild.
- [ ] CSP currently includes `'unsafe-eval'` for KaTeX. Investigate
      `katex.min.js` builds without `Function()` use to drop it.
- [ ] Move sidecar's per-model `LLM` slot to a real LRU; current
      single-slot eviction churns when alternating between two
      models.
- [ ] Conftest's `_install_cyllama_stub()` runs `import pytest` at
      module level, which forces the Playwright e2e launcher to
      install pytest just to load the stub. Extract the stub into a
      pytest-free helper so the e2e env stays leaner.
