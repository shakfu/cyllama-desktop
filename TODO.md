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
- [x] **Copy / regenerate** message actions wired. Edit and Branch
      still pending: edit needs an in-place editable user-text and a
      regenerate-after-edit flow; branch clones history up to the
      exchange and creates a new chat.

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
- [x] Eject releases the `LLM` and frees GPU memory via the new
      `/unload` sidecar endpoint.

## Multimodal

- [ ] Image input (cyllama supports MTMD/LLAVA). Composer attach button +
      multipart sidecar endpoint.
- [ ] Whisper integration (transcribe audio in composer; voice prompts).
- [ ] Stable Diffusion: separate workspace tab via the nav rail.

## App shell

- [ ] Wire remaining nav-rail buttons (Models — Chats and Console done).
- [x] Console / log view: live tail of sidecar stdout/stderr. Slide-up
      panel toggled by the Terminal nav-rail button. 2000-line ring
      buffer in main, lazy-populated on first open, color-coded stderr,
      sticky-scroll-aware.
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
