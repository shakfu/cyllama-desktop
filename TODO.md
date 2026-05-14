# TODO

Roughly ordered by user impact and effort. Strike through items as they ship,
or move them to `CHANGELOG.md` under `[Unreleased]`.

## Conversation

- [ ] **Edit and Branch** message actions. Edit: in-place editable
      user-text + regenerate-after-edit flow. Branch: clone history
      up to the exchange and create a new chat.

## Sampling and model parameters

- [ ] **Forward-looking sampler fields** still waiting on cyllama for
      `grammar`, `speculative`, `ngram` (as `GenerationConfig` kwargs).
      0.2.17 rejects all three as unexpected keyword arguments. UI +
      sidecar whitelist are already in place; rows surface
      automatically once `/info.supported_params` and `/info.features`
      advertise them. (Penalty + mirostat fields landed in 0.2.17 and
      are live.) Verification on bump:
      `build/python-mac-arm64/bin/python3 -c "from cyllama import
      GenerationConfig; GenerationConfig(grammar='', speculative=1,
      ngram=1)"` should not raise. Grammar already has a separate path
      (`/grammar/from-schema` + GBNF builder); the missing piece is
      per-chat enforcement on `GenerationConfig`.

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

- [ ] HF browse / search inside the app (currently URL-paste only).
      Needs HF API surface + rate-limit handling.
- [ ] Resumable HF downloads (current job re-fetches from byte 0).

## Multimodal

### Composer attachments + speech I/O (priority-sorted)

Composer-level UX. The underlying primitives -- Whisper transcription,
MTMD vision, RAG ingest -- already ship in dedicated panes; the gap is
in-composer plumbing. Image attachment already works via the
paperclip button when an MTMD model + mmproj are loaded; everything
else below is still missing.

- [ ] **1. Document attachment in composer** (.pdf, .md, .txt,
      .docx). Drop into the messagebox, sidecar parses to text
      via the existing `load_document` primitive in cyllama.rag
      (no collection persisted -- one-shot extraction). Resulting
      text inlined as a ``[Document: foo.pdf]\n<content>`` block
      before the user prompt. Decision needed for files that
      would blow the context window (~10k+ tokens): truncate with
      a warning, or fall back to an ephemeral in-memory RAG
      collection scoped to the chat. Highest user-value gap;
      "summarise this PDF" is the dominant request once a chat
      flow is established.
- [ ] **2. Voice prompts** (microphone button in composer).
      Record via the renderer's MediaRecorder API, ship to the
      sidecar via the existing transcribe path, drop the text
      into the prompt textarea on completion. Red-dot + duration
      indicator while capturing; Esc cancels. Whisper machinery
      is already shipped; this is purely composer plumbing.
- [ ] **3. Audio file attachment in composer**. Drop a .wav (and,
      after the ffmpeg fallback lands, .mp3/.m4a/.flac/.ogg) into
      the messagebox, sidecar transcribes via Whisper, transcript
      becomes the message text. Largely shares code with the
      voice-prompts item minus the MediaRecorder front-end.
- [ ] **4. Text-to-speech of assistant replies**. Speaker icon
      on each assistant message; click to read aloud. Use the
      browser `speechSynthesis` Web Speech API for v1 -- offline
      on macOS/Windows, no model load, picks up system voices.
      Per-voice + rate picker in Preferences. v2 could route to a
      local TTS GGUF once cyllama exposes one.
- [ ] **5. Streaming-token multimodal answers**
      (`VisionLanguageChat` generator) so long vision answers
      don't block. Lower urgency than the composer-attachment
      items; users rarely hit the blocking window with single-image
      Q&A but it bites with long-form description.
- [ ] **6. Audio decode fallback for Transcribe**: shell out to
      `ffmpeg` when input is non-WAV. Detect at probe time,
      surface via `/info.features.audio_decode`. Strictly a
      prerequisite for item 3 covering non-WAV audio drops; can
      ship before or after the in-composer attachment.

## Agents

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

- [ ] CSP currently includes `'unsafe-eval'` for KaTeX. Investigate
      `katex.min.js` builds without `Function()` use to drop it.
- [ ] Move sidecar's per-model `LLM` slot to a real LRU; current
      single-slot eviction churns when alternating between two
      models.
- [ ] Conftest's `_install_cyllama_stub()` runs `import pytest` at
      module level, which forces the Playwright e2e launcher to
      install pytest just to load the stub. Extract the stub into a
      pytest-free helper so the e2e env stays leaner.
