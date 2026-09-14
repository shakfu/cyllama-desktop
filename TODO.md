# TODO

Roughly ordered by user impact and effort. When an item ships, write it up in `CHANGELOG.md` under `[Unreleased]` and delete it from here; this file tracks only what is outstanding.

## Critical

## High

### Distribution

- [ ] **Ship a `deb` alongside the AppImage.** Ubuntu 24.04 sets `kernel.apparmor_restrict_unprivileged_userns=1`, which denies Electron the namespace sandbox; it falls back to the SUID helper, and `chrome-sandbox` inside a user-owned FUSE mount can never be root-owned `4755`, so the AppImage aborts with `FATAL:setuid_sandbox_host.cc(163)` before the window opens. The only workaround is `--no-sandbox`, which disables renderer isolation. dpkg installs to `/opt` with `chrome-sandbox` root-owned `4755`, so the sandbox works with no flag and no sysctl change. One entry in `electron-builder.yml`'s `linux.target` plus a repack (no Python env rebuild). Verified as a real wall on 24.04, not a theoretical one; it affects every Electron AppImage, so the AppImage should become the portable fallback rather than the primary Linux artifact.

## Medium

### Conversation

- [ ] **Edit and Branch** message actions. Edit: in-place editable user-text + regenerate-after-edit flow. Branch: clone history up to the exchange and create a new chat.

### Markdown / rendering

- [ ] Track `\[...\]` display math and 4-space indented code blocks in `findStableSplit`; today only fenced code and `$$...$$` are tracked, so `\[...\]` math could be prematurely committed.

- [ ] Syntax highlighting in fenced code blocks. The library question is settled: `highlight.js` core is already a dependency and bundled for the script viewer, with the Python grammar registered. Chat code blocks need the grammars for whatever languages are worth supporting plus a hook in the marked renderer, and each grammar adds to the bundle.

- [ ] Decide what to do about remote markdown images. The CSP is `img-src 'self' data:` (`src/renderer/index.html:9`), so an `![](https://...)` in a model's reply renders as a broken image with a console error -- it is not a styling gap, it is the policy. Either widen the CSP for images (and accept that a reply can make the renderer fetch an arbitrary URL), proxy them through the sidecar, or strip them in the marked renderer and show the URL as a link. Doing nothing is defensible; looking broken is not.

### Models

- [ ] Resumable HF downloads (current job re-fetches from byte 0).

### Composer attachments + speech I/O (priority-sorted)

- [ ] **1. Audio file attachment in composer**. Drop a .wav (and, after the ffmpeg fallback lands, .mp3/.m4a/.flac/.ogg) into the messagebox, sidecar transcribes via Whisper, transcript becomes the message text. The upload and transcribe path already exists, built for voice prompts; what is missing is the drop target and the non-WAV decode (item 4).

- [ ] **2. Text-to-speech of assistant replies**. Speaker icon on each assistant message; click to read aloud. Use the browser `speechSynthesis` Web Speech API for v1 -- offline on macOS/Windows, no model load, picks up system voices. Per-voice + rate picker in Preferences. v2 could route to a local TTS GGUF once cyllama exposes one.

- [ ] **4. Audio decode fallback for Transcribe**: shell out to `ffmpeg` when input is non-WAV. Detect at probe time, surface via `/info.features.audio_decode`. Strictly a prerequisite for item 1 covering non-WAV audio drops; can ship before or after the in-composer attachment.

### App shell

- [ ] Settings panel: theme override and default sampling. The Preferences window (`Cmd+,`) now has four tabs -- General (About + devices), Models (extra model search roots, which covers the model-directory item), Sidecar (workspace paths + Python runtime + the OpenAI-compatible server), and Logs. Only the General tab's theme and default-sampling controls are still a placeholder; both settings live in localStorage today rather than `<userData>/settings.json` (see `docs/dev/plan.md` S9).

### Distribution

- [ ] Auto-update wiring. `electron-updater` is in deps but unused. Decide on a publish channel (GitHub Releases is the default).

- [ ] Validate the macOS arm64 build pipeline end-to-end: build, sign, notarize, install on a clean machine, verify Gatekeeper passes.

- [ ] Windows installer: the CUDA vs CPU/Vulkan strategy is settled -- one installer per backend, not one installer carrying several Python envs with runtime detection. The variant targets already build them (`make app-cuda` / `app-vulkan` / `app-cpu` on a Windows host produce NSIS installers named per backend), so what is left is CI and verification, not design: a Windows runner per backend, `cyllama-cuda12` and `cyllama-vulkan` install-and-import checked on a clean host (0.4.2 shipped Windows GPU wheels that silently ran on the CPU -- confirm `cyllama info` reports `registries: CPU, CUDA`, not just `built: CUDA`), and a decision on which artifact the download page offers by default. cyllama publishes no `rocm` or `sycl` wheel for Windows, so those two targets are Linux-only there.

- [ ] Linux AppImage: validate cyllama's Linux wheel + GPU backend story.

### Scripts and workflows

- [ ] **Verify the Windows process-tree kill for script cancel.** `_signal_child` terminates a kill-on-close job object on Windows, falling back to the direct child when the job could not be created. Written, never run: CI is Linux-only and the cancel test's liveness probe is POSIX-only. Needs a Windows runner, or a manual pass with a script that spawns its own children. See `docs/dev/scripting.md` S15.3 and Q6.

### Testing

- [ ] Real-cyllama smoke suite (opt-in): a small `tests/smoke/` set that runs against the bundled `build/python-mac-arm64/` python env, exercising the actual cyllama API surface so capability probes catch shape drifts on cyllama bumps. Slow + expensive, so kept out of `make test`.

- [ ] Deeper Playwright flows: model-picker round-trip, send + abort, sidebar collapse persistence, KaTeX render verification. The suite now drives real interactions (modal edit and submit, script run with streamed output, install and uninstall, workflow input gating), so it is no longer rendering-only -- but those four flows are still untested, and all of them need either a real model or a stub that returns tokens.

### Tech debt

- [ ] CSP currently includes `'unsafe-eval'` for KaTeX. Investigate `katex.min.js` builds without `Function()` use to drop it.

## Low

### Sampling and model parameters

- [ ] **Forward-looking sampler fields** still waiting on cyllama for `grammar`, `speculative`, `ngram` (as `GenerationConfig` kwargs). Still rejected as unexpected keyword arguments on the bundled 0.4.6 (re-probed 2026-09-12). UI + sidecar whitelist are already in place; rows surface automatically once `/info.supported_params` and `/info.features` advertise them. (Penalty + mirostat fields landed in 0.2.17 and are live.) Verification on bump: `build/python-mac-arm64/bin/python3 -c "from cyllama import GenerationConfig; GenerationConfig(grammar='', speculative=1, ngram=1)"` should not raise. Grammar already has a separate path (`/grammar/from-schema` + GBNF builder); the missing piece is per-chat enforcement on `GenerationConfig`.

### Markdown / rendering

- [ ] Copy-code button on each `<pre>` block.

### Models

- [ ] HF browse / search inside the app (currently URL-paste only). Needs HF API surface + rate-limit handling.

### Composer attachments + speech I/O (priority-sorted)

- [ ] **3. Streaming-token multimodal answers** (`VisionLanguageChat` generator) so long vision answers don't block. Lower urgency than the composer-attachment items; users rarely hit the blocking window with single-image Q&A but it bites with long-form description.

### Agents

- [ ] **Phase F.4: per-agent-type run history pane.** The Agents pane's right detail rail shows a real result for the workflow and scripts rows and a placeholder for the six agent types. F.4 fills this with a list of recent runs (per agent type), each clickable to drill into the full trace + final state + error. Needs:

      - Sidecar: persist per-job `kind` / `state` / `result_summary` beyond the in-memory `Job` registry so a renderer reload doesn't drop history. SQLite at `<workspace>/run_history.db` with a small migration is the natural shape.

      - Renderer: subscribe to `job:done` events and refresh the right rail. Click a row to open a read-only trace viewer (similar to the chat-stream inline trace renderer).

      - Retention: cap rows per type (last 50?) and surface a "Clear history" action in the rail.

      Defer until the rest of Phase F (slash rename + pane +
      modal) has lived in real use long enough to know whether
      history is the right shape, or whether per-run linkbacks
      from the chat stream are enough.

### App shell

- [ ] Update window title to active chat name.

- [ ] Hidden-titlebar mode on macOS for a more polished feel (`titleBarStyle: 'hiddenInset'`); requires draggable header region.

### Distribution

- [ ] macOS x86_64 build (PBS triple `x86_64-apple-darwin`); requires a matching CI runner.

- [ ] Crash reporting (Sentry or similar) with sidecar/renderer separation.

### Scripts and workflows

- [ ] **Expire "has been read" when a file changes.** Run on a file the user has not opened shows the code first; that is remembered per file id, so a file replaced by different content under a name already read runs without the detour. Keying on content would re-show the code on every save, which breaks the authoring loop; a workable middle is to re-show when mtime moves and the pane was not what wrote it. Not obviously worth the complexity -- decide before adding it.

- [ ] **Decide whether the scripts Arguments field should be conditional.** It is one line until focused, but it still renders for every script, and most take no arguments. Hiding it would need the sidecar to report whether a script reads stdin, which means guessing intent from the ast. Left visible on purpose; revisit if the field proves to be noise.

### Testing

- [ ] Cross-platform smoke: `make python && make dev && curl /health` in CI for each target platform once Windows / Linux distribution lands.

### Tech debt

- [ ] Move sidecar's per-model `LLM` slot to a real LRU; current single-slot eviction churns when alternating between two models.

- [ ] Conftest's `_install_cyllama_stub()` runs `import pytest` at module level, which forces the Playwright e2e launcher to install pytest just to load the stub. Extract the stub into a pytest-free helper so the e2e env stays leaner.

## Multimodal

### Composer attachments + speech I/O (priority-sorted)

Composer-level UX. The underlying primitives -- Whisper transcription, MTMD vision, RAG ingest -- already ship in dedicated panes; the gap is in-composer plumbing. Image and document attachment work via the paperclip, and voice prompts via the mic button; everything below is still missing.
