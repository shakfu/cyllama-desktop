# SCRIPTING: Workspace Python Scripts as Sidecar Jobs

Status: implemented (Phases 0-3). Owner: @shakfu. Last updated: 2026-09-12. See Section 16 for where the build deviated from this spec.

Companion docs: `docs/dev/plan.md` (overall rollout), `docs/dev/agent_plan.md` (agent + workflow layer), `TODO.md`, `CHANGELOG.md`. This document is scoped to one question: should the app run user-authored Python scripts as jobs, and if so, how.

---

## 1. Goal

Let a user run a workspace-scoped `.py` file from the app, against the model the sidecar already has resident, with streaming output, working cancellation, and results collected as job artifacts.

Concrete acceptance criterion: a 50-cell parameter sweep runs from one script, loads the model once, streams progress, is cancellable mid-run, and leaves a downloadable `report.csv` in the job's artifact directory.

## 2. Non-goals

- A scheduler. No cron, no triggers, no unattended runs.

- A sandbox. Scripts run with the user's full privileges (Section 10.3).

- A REPL or terminal pane. See Section 6.3.

- A general task runner for non-Python work.

- Replacing workflows. Workflows stay the declarative DAG surface; scripts are the imperative escape valve.

- Scripting cyllama itself. `pip install cyllama` already covers that (Section 6.1).

## 3. Recommendation

Build it, but only after the trigger condition is met.

**Trigger:** you have written the same ad-hoc loop by hand three times. Until then the cost (about two days, plus a permanent second execution path) exceeds the benefit.

**Build Phase 0 now regardless.** The job-queue backpressure fix (Section 12, Phase 0) repairs a latent stall that affects workflow, batch, ingest, and quantize jobs today. It is worth doing on its own merits and is a hard prerequisite for scripts.

**Scope it narrowly if built:** subprocess only, workspace files only, one endpoint, no scheduler, no sandbox. The strongest argument is not any single use case. It is that a script runner converts a whole class of future features from product work into user work (Section 8.3).

## 4. Current state (2026-09-12)

### 4.1 What already exists

- **Job registry.** `class Job` (`sidecar.py:991`), `run_job` (`sidecar.py:1080`), `_emit` (`sidecar.py:1064`). Every long operation funnels through it.

- **Job HTTP surface.** List (`sidecar.py:1118`), get (`:1127`), cancel (`:1135`), SSE events (`:1143`), result (`:1177`).

- **Artifacts.** Files under `ARTIFACTS_DIR/<job_id>/` are served by `/artifacts/{job_id}/{name}` (`sidecar.py:1229`). No extra code needed for a script to publish output.

- **Renderer job client.** `JobHandle` and `startJob` (`src/renderer/src/lib/jobs.js:21`, `:118`) already do SSE framing, listener fan-out, cancel, and a `done` promise.

- **Arbitrary user Python, executed in-process.** Workflow files under `WORKFLOWS_DIR` (`sidecar.py:739`) are imported via `spec.loader.exec_module` (`sidecar.py:4202`). The trust boundary is documented at `sidecar.py:731-738`.

- **Resident model with a cache key that matters.** `_get_llm` (`sidecar.py:795`) caches one `LLM` per `(model_path, _hw_signature(params))`. `_hw_signature` (`sidecar.py:637`) covers load-time fields only. Sampling fields (temperature, top_p, penalties, mirostat) do **not** evict the cache.

- **Bearer auth.** One per-launch token, checked in `auth_mw` (`sidecar.py:775`). `/health` is the only exempt route.

### 4.2 The gap

Existing panes each cover one fixed shape:

| Pane | Shape | What it cannot do |
|-|-|-|
| Batch (`sidecar.py:4598`) | N prompts x 1 config | vary configs, score outputs, tabulate |
| Workflows (`sidecar.py:4341`) | agent DAG | non-agent compute, arbitrary control flow |
| RAG | ingest, query | custom chunking, re-embed, dedupe, export |
| Quantize, HF download, transcribe, txt2img | one operation each | compose them |

Anything that composes these, or varies a parameter across runs, is currently hand-driven through the UI or written outside the app against a second model copy.

### 4.3 What the sidecar does not own

Chat history is **not** sidecar state. It lives under `<userData>/workspaces/<id>/chats/*.json`, reachable only over the `chats:*` IPC in the main process (`src/main/index.js:498`, `:511`). Any use case phrased as "over my conversations" is out of reach unless Section 14, Q4 is resolved.

## 5. Use cases, and what each demands of the design

A use case qualifies only if it passes three tests:

1. The app cannot do it today.

2. A terminal with `pip install cyllama` cannot do it either, because it needs state the app owns.

3. It recurs.

### 5.1 Parameter sweeps (highest value)

Run 10 prompts x 5 sampling configs, score each output, emit a table.

This is the strongest case purely because of `_get_llm`'s cache key (Section 4.1): 50 generations run against **one** resident model load. The same script outside the app reloads per process, or must implement its own reuse. On a 30B on consumer hardware, model load is tens of seconds; the sweep is the difference between one load and fifty.

*Design demands:* `chat()` over HTTP; progress events; a CSV or JSONL artifact; cancellation that actually stops work mid-sweep.

### 5.2 Eval on model or cyllama bump

Re-run a stored prompt set against a new GGUF or a new system prompt, diff against recorded outputs, emit a pass/fail report.

*Design demands:* read access to the models dir; stable structured output (`result.json`); artifacts that survive the job registry's one-hour GC.

### 5.3 Bulk RAG maintenance

Ingest with filtering the pane does not offer; re-embed a collection after changing the embedding model; dedupe; export chunks.

This is the one case where going through the app is *required*, not merely convenient. The vector stores under `RAG_DIR` are written by ingest jobs. A script opening `SqliteVectorStore` directly is a second writer on the same file.

*Design demands:* `rag_query()` and ingest over HTTP; the client lib must make the safe route the obvious one; the docs must state the corruption case explicitly.

### 5.4 Model triage

A download lands. Run a fixed smoke set, record tokens/sec and context behavior, append a row to a scoreboard.

*Design demands:* models-dir listing; append-friendly artifact handling; tolerable startup latency (this runs often and briefly).

### 5.5 Rejected use cases

| Ask | Correct answer |
|-|-|
| "Run a one-off cyllama script" | terminal, `pip install cyllama` |
| "Chain agent steps" | workflows (`/jobs/workflow/run`) |
| "Try the low-level batch/sampler api" | terminal; touches no app state |
| "Anything that loads its own model" | terminal; the subprocess buys nothing |

If the majority of real scripts fall in this table, do not build B2.

## 6. Alternatives considered and rejected

### 6.1 Expose the bundled interpreter, document scripting

Show the path to `Resources/python/bin/python3` in Preferences, ship a venv recipe.

**Rejected as a scripting answer.** cyllama is on PyPI and the build pins a version (`scripts/build-python-env.sh:16`), so the normal answer is `pip install cyllama==<pinned>`, in the user's own environment, at the version they choose. Documenting the bundle's interpreter converts `extraResources` layout, the Python version, and the pinned dependency set into a compatibility promise, for no capability the user does not already have.

**Partially retained:** adding `python_bin` to `/info.sidecar` (`sidecar.py:888`) as a *diagnostic* row beside the existing paths is cheap and useful in bug reports. No doc, no recipe, no promise.

### 6.2 In-process script execution (B1)

Run the script the way workflows run, inside the sidecar.

**Rejected.** It offers the resident model object directly, but a segfault in cyllama's native layer kills the sidecar, taking every chat, job, and loaded model with it. Cancellation is also a lie: Python cannot interrupt a thread parked in llama.cpp, so `/jobs/{id}/cancel` would report success while work continues. If in-process execution is ever wanted, the honest version is a 20-line change to `_resolve_workflow` (`sidecar.py:4221`) letting a workflow module export `main()`, not a new subsystem.

### 6.3 Embedded Python terminal / REPL pane

**Rejected.** It carries every B1 failure mode plus an interactive front-end (xterm.js plus a pty, or a REPL with no history, completion, or multiline paste). A venv with `ipykernel` against the pinned cyllama gives a strictly better interactive experience for zero app code. The only thing it cannot reach is the resident model, which is exactly what B2's HTTP client provides.

### 6.4 Extend the Batch pane

Add a config matrix to the existing pane.

**Rejected as a general answer,** though it would serve Section 5.1 alone. It solves one shape and leaves 5.2 through 5.4 untouched, and each subsequent shape costs another pane. That is the growth pattern Section 8.3 exists to stop.

## 7. Design

### 7.1 Shape

```
renderer                sidecar (asyncio)              child process
--------                -----------------              -------------
POST /jobs/script/run -> spawn subprocess  ---------->  python -u script.py
                         pump stdout/stderr <---------  prints, progress lines
GET  /jobs/<id>/events                                  |
  <- log / progress / result                            | HTTP + bearer token
POST /jobs/<id>/cancel -> killpg(TERM, then KILL)       v
GET  /artifacts/<id>/<name> <- files in cwd        POST /chat (resident model)
```

The child shares no memory with the sidecar. Everything it needs from app state it requests over the loopback API.

### 7.2 Storage and discovery

`SCRIPTS_DIR`, same env-var-with-fallback pattern as `WORKFLOWS_DIR` (`sidecar.py:739-743`), defaulting to `<workspace>/scripts/`.

`GET /scripts` mirrors `_list_workflow_files` (`sidecar.py:4183`) and `_workflow_summary` (`sidecar.py:4258`): id-shaped filenames only, module docstring as the description. Read the docstring with `ast.parse`, not by importing. Listing a broken or hostile script must never execute it.

### 7.3 Run endpoint

`POST /jobs/script/run` with `{script_id, args: {...}, timeout_s?: int}` returning `{job_id}`. Register the kind as `script.run`, matching the workflow convention where the route path and the registered kind differ.

Producer sketch:

```python
async def producer(job: Job) -> None:
    cwd = ARTIFACTS_DIR / job.id
    cwd.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": f"{CLIENT_LIB_DIR}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "CYLLAMA_SIDECAR_URL": f"http://127.0.0.1:{PORT}",
        "CYLLAMA_SIDECAR_TOKEN": TOKEN,
        "CYLLAMA_JOB_ID": job.id,
        "CYLLAMA_MODELS_DIR": str(MODELS_DIR),
        "CYLLAMA_ARTIFACTS_DIR": str(cwd),
    }
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", str(path),
        cwd=cwd, env=env,
        stdin=PIPE, stdout=PIPE, stderr=PIPE,
        start_new_session=True,          # POSIX: own process group
    )
    proc.stdin.write(json.dumps(args).encode())
    proc.stdin.close()
    try:
        await asyncio.wait_for(
            asyncio.gather(
                _pump(job, proc.stdout, "stdout"),
                _pump(job, proc.stderr, "stderr"),
                proc.wait(),
            ),
            timeout=timeout_s,
        )
    finally:
        await _terminate(proc)          # no-op when already exited
    if proc.returncode != 0:
        raise RuntimeError(f"script exited {proc.returncode}")
    await _emit(job, {"type": "result", "result": _collect_result(cwd)})
```

`-u` plus `PYTHONUNBUFFERED=1` is mandatory. Without it CPython block-buffers a pipe and nothing streams until the process exits.

### 7.4 Input and output contract

- **Input:** one JSON object on stdin. Mirrors the workflow API's `initial_state`. Avoids argv quoting entirely.

- **Structured output:** the script writes `result.json` in its cwd. The producer reads it after a clean exit and emits it as the `result` event. Cap the read at 1 MB; larger output belongs in an artifact.

- **Files:** anything written to cwd is already downloadable via `/artifacts/{job_id}/{name}`. Zero new code.

- **Progress:** a sentinel-prefixed line on stdout, `\x1e{"progress": 0.4, "message": "..."}`, parsed by `_pump` and re-emitted as a normal progress event. One-way, no second route, no new auth surface.

### 7.5 Client library

`cyllama_desktop.py`, placed on the child's `PYTHONPATH`, built on `httpx` (already in the bundled env). About 120 lines.

```python
from cyllama_desktop import app

app.progress(0.1, "loading")
models = app.models()
text = app.chat("summarize this", model=models[0].path, temperature=0.2)
hits = app.rag_query("notes", "what did I decide about X")
app.artifact("report.csv").write_text(rows)
```

Scope rule: the library wraps **app state only** -- `chat`, `models`, `rag_query`, `rag_ingest`, `progress`, `artifact`. It must not wrap cyllama's inference API. A script wanting low-level control should `import cyllama` and get the real thing, not a lossy proxy (Section 10.3).

External providers (`docs/dev/providers.md` R1). `app.chat(..., provider=, model=)` runs against a provider the user configured instead of a local GGUF, and `app.providers()` / `app.provider_models()` say what is available:

```python
for name in app.providers():            # ["openai", "compat.lm-studio"]
    for m in app.provider_models(name):
        print(name, m["id"])

text = app.chat("name three primes", provider="openai", model="gpt-5.4")
```

`model=` is required there -- there is no local file to default to. The key stays in the sidecar, so the script never sees one, but it can spend one: each call books a usage row labelled `script:<job id>`, visible in Preferences -> Providers (`docs/dev/providers.md` S8.2).

A workflow node can import the same handle. It runs inside the sidecar rather than a child process, so `chat`, `models`, `rag_*` and `providers` behave identically while the job-scoped members are inert: `args` is empty, `progress()` does nothing, and `artifact()` resolves against the sidecar's working directory. Sync nodes are dispatched on `asyncio.to_thread`, so the blocking loopback call does not stall the event loop.

### 7.6 Renderer

Reuse, near-total:

- `startJob("script/run", {...})` works against `src/renderer/src/lib/jobs.js:118` unchanged.

- A new row in `AGENT_TYPES` (`agents-pane.js:31-38`), reusing the file-list, run-button, and trace-render code at `agents-pane.js:536-660`.

- One genuinely new widget: an append-only log view with a DOM node cap (a few hundred lines, then drop from the top).

## 8. Pros

**8.1 One model load per run.** The quantitative benefit, per Section 5.1. Nothing else in the design matters as much.

**8.2 Job plumbing for free.** Progress, cancel, log, artifacts, and a renderer client already exist. The marginal cost of a new long-running capability is one producer function.

**8.3 It stops the pane treadmill.** Every "run N things, collect results" feature currently costs a pane, an endpoint, and a renderer. Batch is one. Quantize is one. Sweeps, evals, and RAG maintenance would be three more. A script runner moves that class of work out of the product and into the user's workspace. This is the strongest long-term argument, and it is an argument about maintenance cost, not features.

**8.4 Crash containment relative to today.** Scripts run in a child process. Workflow files, which run in-process, do not. B2 is strictly less exposed than what already ships.

**8.5 Real cancellation.** A killable child makes `/jobs/{id}/cancel` honest for this job kind, which it cannot be for in-process native work.

## 9. Cons

**9.1 A second execution path.** Workflows and scripts would both be "user Python in the workspace", with different discovery, different execution models, and different failure modes. Expect drift, duplicated docs, and the recurring question of which one a user should reach for.

**9.2 Startup latency.** Each run pays interpreter startup plus `import cyllama`, roughly 1 to 3 seconds. Section 5.4 runs often and briefly, so this is a real tax there.

**9.3 The resident model is reachable only through `/chat`.** Anything the HTTP API does not expose is unavailable without a second model load. Every gap in the API becomes a script limitation.

**9.4 The bundle becomes a de facto API.** Scripts importing `numpy` or `cyllama` break when the bundled env is bumped. Smaller blast radius than Section 6.1 (the user's own scripts, failing visibly in a job log), but not zero.

**9.5 Attractive nuisance.** A script runner invites the scheduler request, the "run on file change" request, and the shared-script-library request. Section 2 exists to hold that line, and will be tested.

**9.6 Chats are unreachable.** Per Section 4.3, the most intuitive use cases ("re-run everything I asked last week") do not work.

## 10. Risks and mitigations

### 10.1 Job queue backpressure stall (highest severity)

`job.queue` is `maxsize=1024` and `_emit` **awaits** `put` (`sidecar.py:1006`, `:1073`). `/jobs/{id}/events` refuses a second subscriber and does not replay (`sidecar.py:1143-1160`).

Failure: the user navigates away from the pane, the only consumer detaches, the queue fills at 1024 events, and the producer blocks forever. The script stalls mid-run with no error and no timeout. Workflows emit tens of events and rarely hit this. A script's stdout emits thousands.

*Mitigation:* log and progress events use `put_nowait` inside `try/except asyncio.QueueFull`, incrementing a dropped counter that is reported in the final result. Terminal events (`result`, `error`, `done`) keep the blocking path. Add a per-job ring buffer plus `GET /jobs/{id}/log` so a re-attaching pane can catch up. This fix benefits every existing job kind and ships first (Phase 0).

### 10.2 Orphaned processes on cancel

`run_job` cancels an asyncio task (`sidecar.py:1089-1096`) and knows nothing about a child process. A script that spawns its own children leaves them running, holding GPU memory, while `/cancel` reports success.

*Mitigation:* `start_new_session=True` makes the child a process-group leader; cancel sends `SIGTERM` to the group, waits about 3 seconds, then `SIGKILL`. Test explicitly with a script that forks.

### 10.3 No sandbox, and none is possible in-language

The child can `import cyllama`, read any file the user can read, and reach the network. Nothing inside Python restricts a Python process to an API: RestrictedPython, `-I`, and audit hooks are all bypassable from the same interpreter. Real restriction needs OS facilities (`sandbox-exec`, bubblewrap/seccomp, job objects), which is three platform implementations to constrain code the user could equally run from a terminal.

*Mitigation:* none attempted. Document the posture explicitly, matching the existing workflow-file boundary note (`sidecar.py:731-738`). The client library is convenience, not containment, and its docstring must say so.

### 10.4 Token handed to user code

The child receives a token with full sidecar privileges. A script that prints it publishes loopback API access to anything that reads the job log.

*Mitigation (optional, about 20 lines):* mint a per-job token at spawn and drop it from the accepted set in `auth_mw` (`sidecar.py:775`) when the job ends. Low stakes on a single-user desktop, since the child already runs as the user. Defer unless logs are ever shared.

### 10.5 Concurrent RAG writers

A script writing directly to `RAG_DIR/<id>.sqlite` while an ingest job runs gives two writers on one file.

*Mitigation:* the client library exposes ingest and query over HTTP so the safe route is the easy one. Document the direct-access case as unsupported. Consider enabling WAL on the stores regardless.

### 10.6 Memory and VRAM exhaustion

`_get_llm` is deliberately single-slot to bound VRAM (`sidecar.py:786-789`). A script that loads its own model adds a second resident copy the sidecar does not know about. N concurrent scripts add N.

*Mitigation:* cap concurrent script jobs with a semaphore (start at 2). Document that `app.chat()` reuses the resident model and a direct load does not.

### 10.7 Windows process termination

`start_new_session` is POSIX-only, and Windows has no equivalent primitive: `TerminateProcess` kills one process and orphans its children, while console control events are cooperative notifications a process may ignore.

*Mitigation:* the child is assigned to a job object at spawn (`_win_create_job_for`). Descendants join their parent's job automatically, so `TerminateJobObject` kills the set at once. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` also makes the handle a dead-man switch: if the sidecar dies holding it, the tree goes too. Ctrl+Break to the process group is the graceful attempt first; it needs a console the sidecar usually lacks, so a failed delivery escalates immediately instead of waiting out the grace period. Job creation failing for any reason falls back to killing the direct child.

*Residual risk:* unverified. CI runs pytest on Linux only, and the cancel test's liveness probe is POSIX-only -- `os.kill(pid, 0)` terminates the target on Windows rather than probing it. A grandchild spawned between `CreateProcess` and the job assignment also escapes the job; starting the child suspended would close that window, but `subprocess` discards the thread handle and leaves no way to resume it.

### 10.8 Output that never yields a line

A script printing a progress bar with `\r` and no newline produces a pump that never emits, so the run looks hung.

*Mitigation:* read by chunk rather than by line; split on `\r` as well as `\n`; flush a partial line after a short idle interval; cap line length at 8 KB with `errors="replace"` decoding.

### 10.9 Artifact directory growth

Jobs are GC'd from the registry after one hour (`_JOB_DONE_TTL_SECONDS`, `sidecar.py:1027`), but `ARTIFACTS_DIR` persists by design -- the image gallery depends on it. Scripts writing large files accumulate silently.

*Mitigation:* report per-job artifact bytes in the result; add a workspace disk-usage row and a "clear script artifacts" action before this becomes a support question.

### 10.10 Scope creep

See 9.5.

*Mitigation:* Section 2 is normative. A scheduler request is a new design document, not an increment to this one.

## 11. Testing

Sidecar (pytest, matching the existing `tests/test_jobs.py` and `tests/test_workflow.py` shape):

1. Discovery lists id-shaped files only, and never imports them.

2. A clean script exits 0, and `result.json` becomes the `result` event.

3. A failing script surfaces exit code and the tail of stderr.

4. Cancel kills a script that forks a child; assert both are gone.

5. Timeout kills a sleeping script and marks the job failed.

6. A script printing 50,000 lines with no subscriber attached still completes, and reports a nonzero dropped-event count. This is the regression test for Section 10.1.

7. A script printing `\r`-only output emits log events.

8. Artifacts written to cwd are retrievable via `/artifacts/<id>/<name>`.

9. The concurrency cap rejects or queues the third simultaneous run.

Renderer (Playwright, in `tests/e2e/panes.spec.js`): the scripts row renders, a run streams log lines, and cancel returns the pane to idle.

## 12. Implementation plan

### Phase 0 -- Job event backpressure (ship regardless of B2)

Lossy `put_nowait` for log and progress events, dropped-event accounting, per-job ring buffer, `GET /jobs/{id}/log`. About 60 lines plus tests. Fixes a latent stall in workflow, batch, ingest, and quantize jobs. Hard prerequisite for everything below.

*Done when:* test 6 in Section 11 passes, and every existing job kind still streams unchanged.

### Phase 1 -- Sidecar execution

`SCRIPTS_DIR`, `GET /scripts`, `POST /jobs/script/run`, the producer, `_terminate` (POSIX first), timeout, concurrency cap. About 180 lines. Exercised by curl and pytest only; no UI.

*Done when:* tests 1 through 5, 7, and 9 pass on macOS and Linux.

### Phase 2 -- Client library

`cyllama_desktop.py` with `chat`, `models`, `progress`, `artifact`, `rag_query`, `rag_ingest`. About 120 lines. Two worked example scripts seeded into the workspace, following the `resources/example-workflows` precedent (`electron-builder.yml:26`): one parameter sweep (5.1), one eval (5.2).

*Done when:* the sweep example runs end to end and loads the model once. Verify by asserting a single load line in the sidecar log.

### Phase 3 -- Renderer

Scripts row in the Agents pane, log view with a node cap, artifact links, cancel wired to `JobHandle.cancel`. About 150 lines.

*Done when:* the Playwright checks in Section 11 pass.

### Phase 4 -- Optional, only on demand

- `/run <script>` slash command, likely cheaper and better placed than the pane. Reassess after Phase 3 lives in real use.

- A read-only sidecar route over the chats directory, unblocking Section 4.3.

- Per-job scoped tokens (10.4).

- Windows tree-kill (10.7).

### Estimate

Phases 0 through 3: about two days, plus tests. Phase 0 alone is a half day and stands on its own.

## 13. Definition of done

- A user drops `sweep.py` into `<workspace>/scripts/`, sees it listed, runs it, watches progress, cancels it mid-run, and confirms no child process survives.

- A completed run leaves `report.csv` downloadable from the pane.

- The sidecar log shows one model load for a 50-generation sweep.

- Navigating away mid-run and returning does not stall the script, and the log catches up from the ring buffer.

- `make test` passes, including every pre-existing test.

## 14. Open questions

- **Q1.** Workspace files only, or any path the user picks? Workspace files avoid a path-allowlist decision entirely. Default to workspace only.

- **Q2.** Pane or slash command? The pane reuses more code; `/run` puts output where the user already looks and costs far less. Phase 3 assumes the pane, but Q2 should be reconsidered before building it.

- **Q3.** Should scripts import cyllama directly? Yes, unrestricted and documented (10.3). The alternative is not enforceable without an OS sandbox.

- **Q4.** Do scripts need chat history (4.3)? If yes, it needs a sidecar route over the main process's chats directory, or an IPC bridge. Neither is in scope here.

- **Q5.** Do scripts and workflows eventually merge into one workspace-code surface? If the answer is likely yes, consider whether `SCRIPTS_DIR` should instead be a convention inside `WORKFLOWS_DIR` before two directories exist in users' workspaces.

- **Q6.** Is the Windows build in scope at all for this feature, or is it POSIX-only until someone asks?

## 15. Implementation notes

Three things landed differently from the plan above.

**15.1 Phase 0 became a deque, not a lossy queue.** The plan was `put_nowait` plus a dropped counter on the existing bounded `asyncio.Queue`. That is not sufficient: progressive events still *occupy* the queue, so a terminal event arriving after 1024 of them blocks behind events nobody is reading. Skipping the queue entirely when `subscribers == 0` fixes the stall but loses every event emitted between the POST and the client attaching its stream -- which is the order every existing pane uses, and it broke four existing test files. The queue is now a `deque` the producer can always append to, drained by the subscriber and woken by an `asyncio.Event`. Overflow evicts the oldest progressive event; terminal events are never evicted. `_emit` no longer awaits anything.

**15.2 Script jobs are the first that outlive their request.** Every existing job test drives a stub that finishes inside the POST handler, so a bare `TestClient` -- which starts a portal per request -- was enough. A script spawns a real child process, and its task was cancelled the moment the POST's event loop went away. `tests/conftest.py` gains a `live_client` fixture that enters the client as a context manager; `tests/test_scripts.py` overrides the module's `client` with it.

**15.3 Windows tree-kill uses a job object, and is unverified.** `_signal_child` signals the process group on POSIX. On Windows it terminates a kill-on-close job object created at spawn, falling back to the direct child when the job could not be created. Section 10.7 has the mechanism and what remains unproven; Q6 stays open, since nothing in CI exercises it.

## 16. References

- `sidecar.py:637` `_hw_signature` -- what evicts the model cache.

- `sidecar.py:731-738` -- existing user-code trust boundary.

- `sidecar.py:795` `_get_llm` -- single-slot model cache.

- `sidecar.py:991`, `:1064`, `:1080` -- Job, `_emit`, `run_job`.

- `sidecar.py:1143` -- SSE events, single-subscriber, no replay.

- `sidecar.py:1229` -- artifact serving.

- `sidecar.py:4183`, `:4258`, `:4341` -- workflow discovery and run.

- `sidecar.py:4598` -- batch job, the closest existing shape.

- `src/renderer/src/lib/jobs.js:21`, `:118` -- renderer job client.

- `src/renderer/src/features/agents-pane.js:31`, `:536-660` -- pane structures available for reuse.

- `src/main/index.js:498` -- chats directory, not sidecar state.

## 17. Next: examples as a catalog, not a seed

Implemented. Supersedes first-launch seeding for scripts and workflows; `seedExamples` and its `.seeded` marker are gone from the launch path.

**17.1 What is wrong now.** `seedExamples` (`src/main/index.js:200`) copies `resources/example-scripts/*.py` into `SCRIPTS_DIR` once, then writes a `.seeded` marker. The marker records one bit -- that seeding ran -- so it cannot distinguish "the user deleted `sweep.py`" from "`rag_audit2.py` did not exist when this install first launched". Respecting deletion is the stated goal (`index.js:198`) and is correct; the cost is that an install which first launched on 0.3.0 never receives an example added later. The mechanism was inherited from `example-workflows` (`agent_plan.md:206`) and was never argued for scripts. `WORKFLOWS_DIR` has the same marker and the same defect (`index.js:290`).

**17.2 Shape.** Stop writing to the user's directory on launch. Ship the examples read-only inside the app, list them in the pane as a second group the user cannot edit, and add a copy action that puts one into `SCRIPTS_DIR`, where it becomes an ordinary user file. The user decides what exists in their workspace and what runs.

**17.3 Sidecar.** Scripts first; 17.7 applies the same shape to workflows.

- `EXAMPLES_DIR` from `CYLLAMA_SIDECAR_EXAMPLE_SCRIPTS`, same env-var-with-fallback pattern as `SCRIPTS_DIR` (`sidecar.py:756`), except the fallback is "no catalog" rather than a created directory. The sidecar never writes here.

- `_list_script_files` and `_script_summary` (`sidecar.py:4605`, `:4624`) take a directory argument. Both are already path-driven; only the `SCRIPTS_DIR` reference at `:4612` and `:4615` is fixed.

- `GET /scripts` (`:4659`) gains `examples: [...]`, each summary carrying `in_workspace: bool` -- true when a file of that stem already exists in `SCRIPTS_DIR`. One round trip, one response shape.

- `POST /scripts/examples/{id}/copy` copies `EXAMPLES_DIR/{id}.py` to `SCRIPTS_DIR/{id}.py`. 409 when the target exists; never overwrite user code. Returns the new script summary so the pane can select it.

- `POST /jobs/script/run` (`:5046`) is unchanged and still resolves only under `SCRIPTS_DIR` (`:5059`). Examples are not runnable in place, so the trust-boundary statement at `sidecar.py:748-755` keeps one execution root and needs no second clause.

**17.4 Main process.** `resolveExamplesDir` (`index.js:190`) already resolves packaged vs dev; pass its result as `CYLLAMA_SIDECAR_EXAMPLE_SCRIPTS` beside `CYLLAMA_SIDECAR_SCRIPTS` (`:304`). Drop the `seedExamples("example-scripts", ...)` call (`:291`).

**17.5 Renderer.** Scripts merge the catalog into a single list rather than showing a second section: `scriptRows()` joins `state.scripts` and `state.scriptExamples` by id, and each row carries the name, the first docstring line, Install or Uninstall, and Run. A shipped script that is not installed has no Run; a user-authored file has no Uninstall. Install is `POST .../copy`, Uninstall is `DELETE /scripts/examples/{id}`, which refuses a name the build does not ship -- so neither the pane nor the API can delete a script the user wrote. An installed copy whose bytes differ from the shipped file returns 409, and the pane confirms before retrying with `force`.

The workflow pane merges the same way (`workflowRows`), with two differences. Its rows drop `entry`, which the catalog cannot report without importing and which the Plan section already shows; and its Run is inert until the selected workflow's `inputs_required` are filled, with the tooltip naming what is missing. Typing in those fields updates that one button (`syncWorkflowRunButton`) rather than re-rendering the form, which would move focus out of the field. No Cancel on a workflow row: it runs in-process, where cancel cannot interrupt a node mid-call (the reason scripts are a child process at all, Section 7).

**17.6 Existing installs.** 0.3.0 seeded five files and wrote `.seeded`. Leave both. Those copies are user files now and may have been edited. The catalog shows the same five with `in_workspace` true, so they read as already copied rather than as duplicates. The marker becomes inert; a cleanup pass that deletes files from a user's directory buys nothing.

**17.7 Workflows get the same treatment, in the same change.** Two answers to "where do examples come from" is worse than the extra work, and if Q5 ever merges the two directories, one catalog is one less thing to merge. `CYLLAMA_SIDECAR_EXAMPLE_WORKFLOWS`, an `examples` array on `GET /workflows` (`sidecar.py:4409`), `POST /workflows/examples/{id}/copy`, and an Examples section in the workflow pane (`agents-pane.js:573`).

One thing does not carry over. `_workflow_summary` (`sidecar.py:4381`) builds its summary by importing and compiling the module, which is fine for a file the user placed in their own workspace and wrong for a catalog: opening the pane would execute every shipped example in the sidecar process, before the user has chosen anything. The catalog needs the `ast.parse` path scripts already use (`:4624`, and the rule at Section 7). So a shared docstring-only summary serves both catalogs, and catalog rows carry no `entry`, `exits`, or `inputs_required` -- those appear once the file is copied into the workspace and the existing summary runs on it.

**17.8 Cost.** Two endpoints, two env vars, a shared ast-based summary, three functions gaining a parameter, two pane sections, two copy actions. The regression is that a first launch shows an empty script list. Mitigate by pointing the empty state at the Examples section directly below it, not by seeding.

**17.9 Rejected: manifest seeding.** Store the list of names ever seeded in `.seeded` and copy any shipped example absent from it. About 15 lines, no UI, keeps first-launch content, and fixes 17.1 exactly. Rejected because it keeps writing to the user's directory on the app's schedule and keeps what the app ships and what the user wrote in one namespace, which is what made the one-bit marker ambiguous. It is the right fallback if 17.5 does not fit the release.
