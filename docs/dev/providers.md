# PLAN: External providers (OpenAI, Anthropic, OpenRouter, OpenAI-compatible)

Status: R0 and R1 shipped, R2 next. Owner: @shakfu. Last updated: 2026-09-12.

Companion docs: `docs/dev/plan.md` (the local-feature map this extends), `CHANGELOG.md`, `TODO.md`.

Prior art: `~/projects/personal/infer-app`, `projects/infer/Sources/InferCore/Cloud/` (1509 lines, Swift). This plan ports its design. Divergences are recorded in section 7 with the reason.

---

## 1. Goal

One chat interface over local GGUFs and external providers, with every local-only feature explicitly gated rather than silently degraded.

Term: **provider** is an external inference endpoint. Four kinds -- `openai`, `anthropic`, `openrouter`, `compat`. The first three exist for the UI (one-click entry, its own model list, its own credential slot); only `anthropic` differs on the wire. `compat` is a user-supplied name plus base URL and covers Ollama, LM Studio, Groq, Together, Fireworks and anything else speaking `/v1/chat/completions`.

## 2. Non-goals

- Hiding where tokens went. Every remote turn is attributable in the UI and in persisted history.

- A provider abstraction over the local path. Local and remote diverge in load semantics; the sidecar branches on the request shape (`infer-app` reached the same conclusion, `Runner.swift:6`).

- Proxying provider traffic through anything but the sidecar. Keys never reach the renderer.

## 3. Positioning change this requires

`README.md` opens with "no account to create, no API key" and "Local by construction ... Nothing leaves the machine". `docs/dev/plan.md:23` lists a "cloud / multi-user mode" as a non-goal.

Both need an edit before R0 ships:

- README: local-first becomes the default, not the boundary. `infer-app`'s first sentence is the model -- "puts local and cloud LLM backends behind one interface".

- `plan.md:23`: the non-goal is multi-user and hosted, not egress. Reword so it does not read as a ban on this plan.

- `plan.md:435` (resolved decision 3, "OpenAI / LangChain compat shims -- dropped from scope") is about the opposite direction (other tools calling cyllama) and still stands. Add a pointer here so a reader does not mistake one for the other.

## 4. Phases

Ordered by user value over effort. Each is independently shippable; do not start N+1 until N is green on `make test`.

**R0 -- credentials, model lists, chat** [x]

- `python-sidecar/providers.py`: `Provider(kind, name, base_url)`, `endpoint_acceptable()`, `stream_chat(provider, key, model, messages, params) -> Iterator[str]`. One client for openai/openrouter/compat over the installed `openai` SDK; one for `anthropic`. Per-model-id parameter guards live inside the client (`Clients.swift:172`).

- `POST /chat` accepts `{provider, model}` as an alternative to `model_path`. Existing SSE frame shape is unchanged, so the renderer's stream reader needs no work.

- `POST /providers/credentials`: main pushes keys at unlock; the sidecar holds them in memory only.

- Key storage: Electron `safeStorage`, ciphertext at `<userData>/credentials.json`, one entry per account id (`compat.<normalized-name>` per `Provider.swift:47`, so two compat endpoints keep separate keys).

- `GET /providers/models` -- see section 5.

- `/info.remote.supported_params`, feeding the Parameters pane's existing row-hiding.

- Model picker gains a provider section; free-text model id always accepted.

- `requires: "local"` on every pane and slash command that cannot run remotely, rendering a reason string, not a 400 (`AgentController.swift:391`).

- Error bodies scrubbed of key bytes and truncated to 400 chars (`Clients.swift:33`).

- Tests: stubbed client, SSE framing, endpoint policy, key scrubbing, credential-absent path.

Landed as described, with three things worth recording:

- `providers.client_for` is the only test seam; the body builders are pure functions tested directly, which is where the per-kind mapping lives. The SDK call sites are three lines of glue each and are not covered by `make test` -- the real-cyllama smoke suite in `TODO.md` is where they belong.

- `max_tokens` splits by kind: OpenAI gets `max_completion_tokens` (o-series and gpt-5 reject the old name), OpenRouter and compat servers get `max_tokens` (servers predating the rename only know that one). Verify against current provider docs on the next bump.

- Retrieval already reaches providers. The composer's RAG path calls `/rag/retrieve` and injects sources into the system prompt (`main.js:buildOutgoingMessages`), and that endpoint needs no generation model. R3 is therefore only about the RAG pane's own `/rag/query`.

**R1 -- scripts and workflows** [x]

`app.chat(..., provider=, model=)` forwards the ref over the same `/chat`; `app.providers()` and `app.provider_models()` say what is reachable. `model=` is required with a provider, since there is no local file to default to. The script process still never sees a key -- it names a provider and the sidecar holds the credential.

Two things in the paragraph this replaces were wrong, both from writing the plan without reading the code:

- **"Workflow node specs take the same ref"** -- there is no node spec carrying a model. A workflow is arbitrary Python in the sidecar process, and a node that wanted an LLM had to build its own `cyllama.LLM`: `from cyllama_desktop import app` raised `NotRunningUnderDesktop`, because only `_script_env` set `CYLLAMA_SIDECAR_URL`. The sidecar now exports that variable for itself, which is what gives a workflow node the resident model and the configured providers in one move. Job-scoped members of that handle stay inert there (`args` empty, `progress()` a no-op, `artifact()` relative to the process cwd) and are documented as such. Safe on the event loop because cyllama dispatches sync nodes via `asyncio.to_thread`.

- **"nearly free"** -- true of the client library, not of the cross-cutting items below, which is where most of R1 went.

Also landed here, from S6:

- **Usage rows.** `providers.record_usage` / `usage_totals` / `clear_usage` over sqlite at `<workspace>/usage.db` (`CYLLAMA_SIDECAR_USAGE`), one row per billable call with account, model, `source` and token counts. `GET`/`DELETE /providers/usage`. OpenAI and OpenRouter get `stream_options={"include_usage": true}`; Anthropic reports on its final message. A compat endpoint gets neither -- absent usage rather than invented usage -- and a cancelled generation records nothing.

- **Retries.** `providers.MAX_RETRIES = 2`, set once at client construction. The item assumed hand-rolled HTTP; both SDKs already retry 429 and 5xx with backoff and honour `Retry-After`, so this is a policy constant rather than an implementation. Timeouts stay at SDK defaults on purpose: a long generation is not a stalled one.

- **Per-turn attribution.** An assistant message produced by a provider carries `backend: {kind, name, model, usage}` in the chat JSON and renders it under the turn, live and on replay. `chats:save` passes messages through unaltered, so this needed no main-process change.

Where usage surfaces: **Preferences -> Providers**, not "the Jobs panel" the S6 item named. There is no Jobs panel -- the Console is a sidecar log drawer. Providers is where the user already manages keys and endpoints, so it is where they will look to ask what those keys cost.

**R2 -- vision in the composer** [ ]  <- next

Remote vision beats local mmproj and needs no projector pairing. The one wire divergence: content becomes a parts array -- OpenAI takes `image_url` with a `data:` URI, Anthropic takes a base64 `source` block. Uploads already land sandboxed under `UPLOADS_DIR` (`sidecar.py:1614`), so the renderer side is done.

**R3 -- RAG generation** [ ]

`/rag/retrieve` (`sidecar.py:2996`) needs no generation model, so the remote path is retrieve-then-chat and bypasses `_get_rag()` entirely. Cost: the sidecar owns prompt assembly for the remote path, which can drift from cyllama's own RAG template. Record the template in this doc when it lands. Embeddings stay local (R7).

**R4 -- agents** [ ]

- `/agent` (ReAct): `react.py:328` is the only generation call site (`self.llm(prompt, config=, stream=False)`), so a duck-typed adapter is enough.

- `/agent-constrained`, `/agent-contract`: no grammar equivalent exists remotely. Native tool-calling plus JSON-schema structured outputs is a separate implementation with a weaker guarantee, not a port of `generate_with_grammar`.

- `/agent-plan`, `/agent-reflect`: composed generate calls; follow once the ReAct adapter exists.

- Each agent type declares what it needs, per `AgentRequirements.backend` (`AgentTypes.swift:142`).

**R5 -- batch** [ ]

`/jobs/batch` fans out remotely with a concurrency cap and the shared retry policy. The providers' native Batch APIs (offline, ~50% cheaper) are a different feature; do not conflate them.

**R6 -- image generation** [ ]

`gpt-image-1` alongside local SD, chosen independently of the chat backend (`ChatModels.swift:100`, `ImageClient.swift`).

**R7 -- embeddings for RAG collections** [ ]

The trap: a collection's vectors are only comparable to queries embedded by the same model. Switching embedding provider silently invalidates the store. So the embedding model becomes a property of the collection, recorded at create time, immutable, and enforced at query. Ranked below R3 because local embeddings already work and cost nothing.

**R8 -- transcription** [ ]

`whisper-1` / `gpt-4o-transcribe` as an alternative to local Whisper. Lowest value: local Whisper already works, and the upload is the expensive part either way.

**Never** -- local by construction, gated with a reason and never revisited: quantize, `/models/inspect`, `/hardware/estimate-layers`, `/tokenize`, GGUF scanning, and the OpenAI-compatible server pane (that one points the other way).

## 5. Model lists

Source: each provider's own list endpoint. Free-text entry stays accepted, so a model the list does not carry still works.

- `GET /providers/models?kind=<kind>[&refresh=1]`. The sidecar fetches, because the key lives there.

- Cache: `<providers>/models.<account>.json`, holding the list plus `fetched_at`. New `CYLLAMA_SIDECAR_PROVIDERS` env var pointing at `<userData>/providers`, consistent with the six dirs main already passes (`src/main/index.js:263`).

- Refresh: on an explicit user action, on first use of a newly entered key, and when the cache is older than 24h. Serve the cache otherwise; never block the picker on a network call.

- Filtering, in descending order of how well the provider cooperates: OpenRouter returns context length, pricing and modality; Anthropic returns chat models only; OpenAI returns embeddings, TTS and moderation models in the same list with no capability field, so it needs id-prefix heuristics -- the same shape as `_classify_model()` (`sidecar.py:2257`). Verify all three response shapes at implementation time rather than trusting this paragraph.

- Default model: the last one used, per provider. Renderer `localStorage`, key `provider.last_model.<account>`, matching the existing `last_model_path` pattern (`src/renderer/src/main.js:157`). Switching provider restores that provider's own last model, not a global one (`CloudSidebar.swift:14`).

## 6. Cross-cutting, due at R1 not R5

All four shipped: usage rows, retries and per-turn attribution with R1, credential management with R0. Kept here as the record of why they were pulled forward.

- **Usage and cost.** [x] Providers return usage at the end of the stream. Without somewhere to put it, every remote feature is a silent bill. One `usage.db` per workspace, one row per call, shown in Preferences -> Providers. Due with R1, because scripts are where a loop costs real money -- and because a script can spend a key it cannot read (S8.2), which these rows are what makes visible. Tokens, not money: a price table would go stale and vary by tier.

- **Rate limits.** [x] 429 plus `Retry-After`, one shared policy in `providers.py`. A per-call retry invented at each site is how a batch fan-out gets a provider account suspended.

- **Egress disclosure.** [x] The chat header names the backend serving the turn, and the per-message record persists it with its token cost, so old history stays auditable.

- **Credential management** [x] in Preferences: which providers are configured, set, replace, remove. No key is ever read back into the renderer -- configured is a boolean.

## 7. Divergences from infer-app, with reasons

1. **No `CloudRunner` equivalent.** `Runner.swift` is 251 lines of transcript, rewind, rollback and partial-commit management because it mirrors a stateful local runner. `/chat` here is already stateless and the renderer already owns per-chat history (`sidecar.py:1886`), so the remote path is a function.

2. **No env-var key fallback.** `APIKeyStore.resolve` accepts one and logs a warning (`APIKeyStore.swift:104`). Here, `_script_env` copies `os.environ` wholesale into every script child (`sidecar.py:4890`), so a key in the sidecar's environment reaches shipped examples and anything pasted from the internet. Keychain or nothing. A script wanting `OPENAI_API_KEY` can read `os.environ` itself.

3. **Weaker key storage, stated honestly.** `safeStorage` encrypts blobs with no per-item ACL and degrades to a basic-text backend on Linux without a keyring. It is not `kSecUseDataProtectionKeychain`. The unsigned-build caveat at `APIKeyStore.swift:14` applies here too, and more broadly.

4. **No bundled provider or model JSON.** `CloudProviders.json` ships with an empty list, and `CloudRecommendedModels` is ~130 lines of three-layer loader plus a hardcoded list that goes stale on the next model rename. Live list endpoints plus free text replace both (section 5).

5. **SDKs, not hand-rolled HTTP.** `openai>=1.50` and `anthropic>=0.40` are already declared (`python-sidecar/pyproject.toml:16`) and already bundled, currently imported nowhere. Their transitive churn is a known cost, already paid once: it is what moved the bundled env to `httpx2` and broke the client library's `httpx` import (`cyllama_desktop.py:56`).

## 8. Security properties and known gaps

What the credential store protects: `<userData>/credentials.json` once it is away from the machine -- a backup, a synced folder, a stolen disk copy, an accidental commit. What it does not protect: anything running as the user, which can ask the OS to decrypt exactly as the app does. That is what this class of storage does everywhere; a browser's saved passwords sit behind the same mechanism.

### 8.1 Linux `basic_text` (fixed)

`safeStorage.isEncryptionAvailable()` returns true on Linux whenever a key is available, and a desktop with no recognised keyring still has one: Chromium's `basic_text` backend derives it from a password compiled into Chromium, which is public. So the original check accepted keys on such a box, stored them under a known key, and the Providers tab said nothing. `credentialsAvailable()` now also rejects `getSelectedStorageBackend() === "basic_text"`, and the tab explains why it cannot save. Untestable from macOS or from the Playwright suite; needs a Linux box with the keyring stopped, or `--password-store=basic`.

### 8.2 Any bearer holder can spend a key without reading one (accepted)

`/chat` serves a provider request to anything holding the sidecar's bearer token. Nothing can read a key back -- `GET /providers/credentials` is booleans and no other endpoint returns key bytes -- but a token holder can use one, which costs money and can carry data out in a prompt.

Every workspace script is such a holder: `_script_env` passes `CYLLAMA_SIDECAR_TOKEN` (`sidecar.py:5059`). So "a script never sees a key" is true and weaker than it sounds. On Linux `/proc/<pid>/environ` gives the token to any same-user process; macOS restricts reading another process's environment.

Accepted rather than fixed. Scripts are already documented as unrestricted code running with the user's full privileges (`README.md`, `docs/dev/scripting.md`), so a gate here would be theatre against a malicious script while blocking the legitimate case R1 is about. What is genuinely missing is not a gate but a record: the usage rows due in R1 are what make a script's provider spend visible. Revisit then. If a gate is ever wanted, the shape is a second token the renderer holds and `_script_env` does not, required on provider-bearing requests.

### 8.3 A compat endpoint's URL is not bound to its credential (open)

`provider_endpoints` lives unencrypted in `settings.json`, and the credential is keyed by the endpoint's normalized *name*, not its URL. Anyone who can write that file can repoint an endpoint at a host of their own and the sidecar will send that key there on the next request. It takes an attacker who can already write the user's files, so it ranks below 8.2, but binding the credential to name plus URL would close it -- at the cost of losing the key whenever the user edits a URL, which is the reason it is not done yet.

### 8.4 Smaller notes

- Keys cross loopback in plaintext JSON on the credential push. Capturing loopback traffic needs root; nothing logs request bodies, and uvicorn runs at `log_level="warning"`.

- `credentials.json` is written mode `0600` via the tmp-file-plus-rename path, which preserves the mode. Ignored on Windows, where the DPAPI blob is scoped to the user account anyway.

- Keys live in the sidecar's memory for the session. A debugger attached as the same user reads them; see the first paragraph.

- Error bodies from a provider are scrubbed of the key and its first 8 characters, then truncated to 400 chars, before reaching the chat pane or a log.

## 9. Open questions

1. Does a chat switch backends mid-conversation, or does the backend belong to the chat? Switching is easy to allow and hard to explain in a transcript. R0 allows it and records nothing: the active backend is app-level (`localStorage`), a chat still remembers only `modelPath`, and reopening a chat does not switch back to the provider it was written with. Per-message attribution arrives with the usage rows in R1.

2. Do `compat` endpoints get more than one configured slot in R0? Settled: yes. They live in `settings.json` as `provider_endpoints`, so the Providers tab lists as many as the user adds and each keeps its own key and model cache. Cheaper than a single-slot UI, since `settings.json` already had a validated write path.

3. Where does a cost cap live -- per workspace, per job kind, or not at all until R5?
