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
- [x] **System prompt.** Right-panel textarea, persisted in `localStorage`.
      When non-empty, sidecar dispatches to `llm.chat(messages, ...)` so
      the model's GGUF chat template applies. Per-chat override still TODO
      once persistent chat history exists.
- [x] **Multi-turn context.** Renderer keeps an in-memory `messages` list,
      sends full history on each turn. Sidecar always uses `llm.chat()`.
      Persistence to disk is the next task (history resets on app reload).
- [ ] **Real token counts.** Replace the `chars/4` estimate with actual
      counts from cyllama's tokenizer (expose via a sidecar endpoint or
      include in chat responses).
- [x] **Stop-generation that actually stops.** Done: cyllama exposes
      `LLM.cancel()` (Python event + nogil ggml_abort_callback), sidecar
      calls it on `asyncio.CancelledError`. Bump pin to `cyllama>=0.2.14`.
- [ ] **Branch / regenerate / edit / copy** message actions. Hover affordance
      already styled; needs wiring.

## Sampling and model parameters

- [x] Sampling controls in the right panel: temperature, top_p, top_k,
      min_p, repeat_penalty, max_tokens, seed, stop sequences. Pass through
      `/chat` body via `params` object, sidecar whitelists into
      `GenerationConfig`.
- [ ] **Presets**: save/load named parameter bundles (per-model defaults).
      Pure UI/persistence concern — does not need cyllama-side support.
      Implementation: store bundles in `localStorage` (or
      `app.getPath('userData')/presets/*.json` once chat history lands)
      keyed by name; a small dropdown above the Sampling header to load
      and a Save button to capture the current state. Optionally
      auto-suggest a default preset when a model is loaded for the first
      time.
- [ ] Speculative decoding configuration (cyllama supports it; UI does not).
- [ ] Structured output / grammar UI (JSON schema, GBNF).
- [ ] Stop sequences.

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

- [ ] **Model manager**: list models in `app.getPath('userData')/models`,
      drag-and-drop import, HuggingFace direct-URL downloader with progress
      + resumable downloads.
- [ ] Replace the file-picker-only flow with a model browser modal.
- [ ] Per-model metadata: arch, quantization, parameter count, context size.
      Read from GGUF header.
- [ ] Eject should release `LLM` and free GPU memory; today it only clears
      the model path on the renderer side.

## Multimodal

- [ ] Image input (cyllama supports MTMD/LLAVA). Composer attach button +
      multipart sidecar endpoint.
- [ ] Whisper integration (transcribe audio in composer; voice prompts).
- [ ] Stable Diffusion: separate workspace tab via the nav rail.

## App shell

- [ ] Wire the nav rail buttons (Chats / Console / Models). Currently only
      Chats is meaningful.
- [ ] Console / log view: live tail of sidecar stdout/stderr. Already
      forwarded with `[sidecar]` prefix in main process; just needs a UI.
- [ ] Settings panel: theme override, default sampling, model directory.
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

- [ ] Smoke test: `make python && make dev && curl /health` in CI for each
      target platform.
- [ ] Renderer e2e with Playwright: model-picker, send, abort, sidebar
      collapse persistence, KaTeX render verification.
- [ ] Sidecar pytest: bearer-token enforcement, `/health`, `/chat` SSE
      framing, error envelope, parent-PID watchdog.

## Tech debt

- [ ] Renderer is a single ~300-line file. Will outgrow that with the items
      above; break out into modules and add a bundler (esbuild) when it
      starts hurting.
- [ ] CSP currently includes `'unsafe-eval'` for KaTeX. Investigate
      `katex.min.js` builds without `Function()` use to drop it.
- [ ] Move sidecar's per-model `LLM` slot to a real LRU; current single-slot
      eviction churns when alternating between two models.
