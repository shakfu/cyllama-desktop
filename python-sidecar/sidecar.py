"""FastAPI sidecar exposing cyllama over localhost HTTP/SSE.

Started by the Electron main process with these env vars:
  CYLLAMA_SIDECAR_PORT          port to bind on 127.0.0.1
  CYLLAMA_SIDECAR_TOKEN         bearer token clients must send
  CYLLAMA_SIDECAR_PARENT_PID    parent pid; sidecar exits if parent dies
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import signal
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import cyllama
from cyllama import LLM, GenerationConfig

# Whitelist of GenerationConfig fields the renderer is allowed to set.
# Restricting this is defense-in-depth: we never pass arbitrary kwargs
# from a JSON body straight into a model-loading API. New fields go here
# explicitly when the UI grows new controls.
_ALLOWED_PARAMS = {
    "temperature":       float,
    "top_p":             float,
    "top_k":             int,
    "min_p":             float,
    "repeat_penalty":    float,
    "presence_penalty":  float,
    "frequency_penalty": float,
    # Mirostat: 0 = off, 1 = v1, 2 = v2. tau / eta are floats, used only
    # when mirostat != 0. We pass them through unconditionally and let
    # cyllama ignore them when off rather than gating server-side.
    "mirostat":          int,
    "mirostat_tau":      float,
    "mirostat_eta":      float,
    "max_tokens":        int,
    "seed":              int,
}


def _supported_gc_params() -> set[str]:
    """Names ``GenerationConfig.__init__`` actually accepts in this cyllama.

    Introspecting the signature is the source of truth -- the README's
    inventory has historically been forward-looking and a hard-coded
    whitelist crashes on TypeError when cyllama is older than the docs.
    Intersected with ``_ALLOWED_PARAMS`` at the call site so adding new
    desired keys here is the *only* place a renderer change is needed
    when cyllama exposes a new field.
    """
    try:
        sig = inspect.signature(GenerationConfig.__init__)
    except (TypeError, ValueError):
        return set(_ALLOWED_PARAMS)
    params = set(sig.parameters.keys())
    params.discard("self")
    return params


_GC_ACCEPTED: set[str] = _supported_gc_params()
_SUPPORTED_PARAMS: list[str] = sorted(set(_ALLOWED_PARAMS) & _GC_ACCEPTED) + ["stop_sequences"]
# stop_sequences is whitelisted via _coerce_stop_sequences rather than
# _ALLOWED_PARAMS, so it's appended explicitly. Renderer uses this list
# to decide which UI rows to show.


def _coerce_stop_sequences(v) -> list[str] | None:
    """Accept either a list[str] (preferred from the UI) or a comma-
    separated string (forgiving for ad-hoc curl callers). Trim, drop
    empties, return None if nothing usable remains.
    """
    if isinstance(v, str):
        items = [s.strip() for s in v.split(",")]
    elif isinstance(v, (list, tuple)):
        items = [str(x).strip() for x in v]
    else:
        return None
    items = [s for s in items if s]
    return items or None


def _build_config(params: dict | None) -> GenerationConfig | None:
    """Coerce a params dict from the renderer into a GenerationConfig.

    Returns None if nothing valid was supplied so the caller falls back
    to cyllama's defaults rather than overriding them with empty values.
    """
    if not params:
        return None
    kwargs = {}
    for key, caster in _ALLOWED_PARAMS.items():
        if key not in params:
            continue
        # Drop fields the installed cyllama doesn't accept -- the renderer
        # may surface forward-looking sliders (presence/frequency penalty,
        # mirostat, ...) and we never want a TypeError to surface as a
        # cryptic 500 mid-chat. /info advertises the supported set so the
        # renderer can hide the rows entirely; this is defence in depth.
        if key not in _GC_ACCEPTED:
            continue
        v = params[key]
        if v is None or v == "":
            continue
        try:
            kwargs[key] = caster(v)
        except (TypeError, ValueError):
            # Skip silently rather than 400ing -- the UI clamps inputs
            # client-side; this guards against stale localStorage values.
            continue

    stops = _coerce_stop_sequences(params.get("stop_sequences"))
    if stops is not None and "stop_sequences" in _GC_ACCEPTED:
        kwargs["stop_sequences"] = stops

    return GenerationConfig(**kwargs) if kwargs else None


PORT = int(os.environ["CYLLAMA_SIDECAR_PORT"])
TOKEN = os.environ["CYLLAMA_SIDECAR_TOKEN"]
PARENT_PID = int(os.environ.get("CYLLAMA_SIDECAR_PARENT_PID", "0"))

# Artifact dir is created by the main process under userData/artifacts and
# passed in. Falls back to a temp-style path so direct curl smoke tests still
# work without the Electron host.
ARTIFACTS_DIR = Path(
    os.environ.get("CYLLAMA_SIDECAR_ARTIFACTS")
    or (Path.home() / ".cache" / "cyllama-desktop" / "artifacts")
).resolve()
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

MODELS_DIR = Path(
    os.environ.get("CYLLAMA_SIDECAR_MODELS")
    or (Path.home() / ".cache" / "cyllama-desktop" / "models")
).resolve()
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# HF cache locations to enumerate read-only as a secondary listing. Both
# the llama.cpp-flavoured cache and the standard HuggingFace hub cache
# are scanned; missing dirs are silently skipped.
_HF_CACHE_DIRS: tuple[Path, ...] = tuple(
    p.resolve() for p in (
        Path.home() / ".cache" / "llama.cpp",
        Path.home() / ".cache" / "huggingface" / "hub",
    )
)


def _watch_parent() -> None:
    """Exit if the Electron parent process disappears."""
    if not PARENT_PID:
        return
    while True:
        try:
            os.kill(PARENT_PID, 0)
        except OSError:
            os._exit(0)
        time.sleep(2)


threading.Thread(target=_watch_parent, daemon=True).start()


app = FastAPI()


@app.middleware("http")
async def auth_mw(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        # Return directly rather than raising: Starlette's BaseHTTPMiddleware
        # does not catch HTTPException for us, so a raise here surfaces as
        # an unhandled 500 in some clients (TestClient included).
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


# Cache one LLM per model_path. cyllama.LLM holds GPU resources, so we
# keep this single-slot to avoid VRAM blowup; switching models evicts.
_llm_lock = threading.Lock()
_llm: Optional[LLM] = None
_llm_path: Optional[str] = None


def _get_llm(model_path: str) -> LLM:
    global _llm, _llm_path
    with _llm_lock:
        if _llm is not None and _llm_path == model_path:
            return _llm
        if _llm is not None:
            try:
                _llm.close()
            except Exception:
                pass
            _llm = None
        if not os.path.isfile(model_path):
            raise HTTPException(status_code=400, detail=f"model not found: {model_path}")
        _llm = LLM(model_path)
        _llm_path = model_path
        return _llm


@app.get("/health")
def health():
    return {"ok": True}


def _backend_flags() -> dict[str, bool]:
    """Best-effort introspection of which GPU backends cyllama has linked.

    cyllama's backend module name and attribute set has shifted across
    versions; rather than hard-coding one shape, probe for several known
    forms and report what we find. Missing attribute => False. Static for
    the lifetime of the process so the result is computed once and
    cached in ``_INFO_CACHE`` below.
    """
    flags: dict[str, bool] = {}
    candidates = ("cuda", "metal", "rocm", "vulkan", "sycl", "opencl", "blas")
    backend = getattr(cyllama, "_backend", None) or getattr(cyllama, "backend", None)
    if backend is not None:
        for name in candidates:
            v = getattr(backend, name, None)
            if v is None:
                continue
            try:
                flags[name] = bool(v() if callable(v) else v)
            except Exception:  # noqa: BLE001
                flags[name] = False
    return flags


# Build the /info payload once at module load. cyllama version + backend
# flags don't change during a session, so probing them per request is
# wasteful. Sidecar paths (artifacts/models) also fixed for the run.
_INFO_CACHE: dict = {
    "cyllama": {
        "version": getattr(cyllama, "__version__", "unknown"),
    },
    "backends": _backend_flags(),
    "sidecar": {
        "artifacts_dir": str(ARTIFACTS_DIR),
        "models_dir": str(MODELS_DIR),
    },
    # Subset of _ALLOWED_PARAMS that ``GenerationConfig`` in the
    # installed cyllama actually accepts. Renderer hides UI rows whose
    # key is not in this list. Probed once at module load.
    "supported_params": _SUPPORTED_PARAMS,
}


@app.get("/info")
def info():
    """Return cyllama / sidecar build info for the General tab.

    Served from a module-level cache built at import. Stable shape:
    ``{cyllama: {version}, backends: {name: bool}, sidecar: {...}}``.
    Adding fields is fine; renaming requires a bump.
    """
    return _INFO_CACHE


# ---------------------------------------------------------------------------
# Jobs: a single registry every long-running endpoint funnels through so the
# renderer has one progress / cancel / result pattern to wire up. Each job
# is an asyncio.Task plus a bounded event queue. Events are JSON-serialisable
# dicts streamed over SSE; the producer pushes ``{"type": "...", ...}``.
#
# Shape conventions:
#   {"type": "progress", "value": 0..1, "message": "..."}     # any frequency
#   {"type": "log",      "message": "..."}                    # any frequency
#   {"type": "result",   "result": <json>}                    # at most once
#   {"type": "error",    "message": "..."}                    # terminal
#   {"type": "done"}                                          # terminal
# Producers should yield "done" exactly once at the end (success or after
# error). The sidecar appends "done" automatically if a job finishes
# normally without one, so handler code only needs to push "result" / "error".
# ---------------------------------------------------------------------------


_JOB_KINDS: set[str] = set()  # populated as endpoints register handlers


def register_job_kind(name: str) -> None:
    _JOB_KINDS.add(name)


class Job:
    __slots__ = (
        "id", "kind", "state", "created_at", "updated_at", "task",
        "queue", "subscribers", "result", "error", "artifact_path",
    )

    def __init__(self, kind: str) -> None:
        self.id = uuid.uuid4().hex
        self.kind = kind
        self.state = "pending"  # pending | running | succeeded | failed | cancelled
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.task: Optional[asyncio.Task] = None
        # Bounded so a runaway producer can't grow memory unboundedly. Events
        # past the cap block the producer until subscribers drain.
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1024)
        self.subscribers: int = 0
        self.result: Any = None
        self.error: Optional[str] = None
        self.artifact_path: Optional[Path] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "hasResult": self.result is not None,
            "hasArtifact": self.artifact_path is not None,
            "error": self.error,
        }


_JOBS: dict[str, Job] = {}
_JOBS_LOCK = threading.Lock()
_JOB_DONE_TTL_SECONDS = 60 * 60  # gc finished jobs after an hour


def _gc_jobs() -> None:
    """Drop terminal jobs older than the TTL. Cheap, runs on each list call."""
    now = time.time()
    cutoff = now - _JOB_DONE_TTL_SECONDS
    with _JOBS_LOCK:
        stale = [
            j for j in _JOBS.values()
            if j.state in ("succeeded", "failed", "cancelled")
            and j.updated_at < cutoff
        ]
        for j in stale:
            _JOBS.pop(j.id, None)


def _new_job(kind: str) -> Job:
    if kind not in _JOB_KINDS:
        # Defensive: every long-running endpoint must register its kind. If
        # we accept arbitrary strings the renderer can't reason about a
        # finite enum.
        raise HTTPException(400, f"unknown job kind: {kind}")
    job = Job(kind)
    with _JOBS_LOCK:
        _JOBS[job.id] = job
    return job


def _get_job(job_id: str) -> Job:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"job not found: {job_id}")
    return job


async def _emit(job: Job, event: dict) -> None:
    """Push an event onto the job's queue from inside the producer coroutine.

    Use this instead of ``queue.put`` directly so we get one place to bump
    ``updated_at`` and stamp the job state on terminal events.
    """
    job.updated_at = time.time()
    t = event.get("type")
    if t == "result":
        job.result = event.get("result")
    elif t == "error":
        job.state = "failed"
        job.error = str(event.get("message") or "")
    await job.queue.put(event)


async def run_job(
    kind: str,
    coro_factory,
) -> Job:
    """Spawn a job. ``coro_factory`` takes the Job and returns a coroutine.

    The coroutine is responsible for emitting ``result`` / ``error`` events
    via ``_emit``; ``done`` is appended automatically. Cancellation is
    surfaced as a synthetic ``cancelled`` event followed by ``done``.
    """
    job = _new_job(kind)

    async def runner() -> None:
        job.state = "running"
        try:
            await coro_factory(job)
        except asyncio.CancelledError:
            job.state = "cancelled"
            try:
                await job.queue.put({"type": "cancelled"})
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception as exc:  # noqa: BLE001
            await _emit(job, {"type": "error", "message": str(exc)})
        else:
            if job.state == "running":
                job.state = "succeeded"
        finally:
            try:
                await job.queue.put({"type": "done"})
            except Exception:  # noqa: BLE001
                pass

    job.task = asyncio.create_task(runner())
    return job


@app.get("/jobs")
def jobs_list():
    _gc_jobs()
    with _JOBS_LOCK:
        items = [j.to_dict() for j in _JOBS.values()]
    items.sort(key=lambda j: j["createdAt"], reverse=True)
    return {"jobs": items, "kinds": sorted(_JOB_KINDS)}


@app.get("/jobs/{job_id}")
def jobs_get(job_id: str):
    job = _get_job(job_id)
    out = job.to_dict()
    out["result"] = job.result
    return out


@app.post("/jobs/{job_id}/cancel")
def jobs_cancel(job_id: str):
    job = _get_job(job_id)
    if job.task and not job.task.done():
        job.task.cancel()
    return {"ok": True, "state": job.state}


@app.get("/jobs/{job_id}/events")
async def jobs_events(job_id: str):
    """Stream events for a job over SSE.

    Multiple subscribers per job are not supported (single-window app); the
    second subscriber would race the first on queue consumption. Reject if
    one is already attached.
    """
    job = _get_job(job_id)
    if job.subscribers > 0:
        raise HTTPException(409, "events stream already has a subscriber")

    async def stream():
        job.subscribers += 1
        try:
            while True:
                ev = await job.queue.get()
                yield "data: " + json.dumps(ev) + "\n\n"
                if ev.get("type") == "done":
                    return
        except asyncio.CancelledError:
            # Client disconnected; do NOT cancel the job -- cancel is
            # explicit via /cancel. The producer keeps running and any
            # late events are dropped when the queue fills.
            raise
        finally:
            job.subscribers = max(0, job.subscribers - 1)

    return StreamingResponse(stream(), media_type="text/event-stream")


_ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


@app.get("/jobs/{job_id}/result")
def jobs_result(job_id: str):
    """Return the JSON result of a finished job.

    For binary artifacts, use ``/jobs/{id}/artifact`` which streams the file
    rather than wrapping it in JSON.
    """
    job = _get_job(job_id)
    if job.state == "running" or job.state == "pending":
        raise HTTPException(425, "job not finished")
    if job.state == "failed":
        return JSONResponse({"error": job.error}, status_code=500)
    return {"result": job.result}


@app.get("/jobs/{job_id}/artifact/{name}")
def jobs_artifact(job_id: str, name: str):
    job = _get_job(job_id)
    if not _ARTIFACT_NAME_RE.match(name):
        raise HTTPException(400, "invalid artifact name")
    if job.artifact_path is None:
        raise HTTPException(404, "no artifact")
    # Resolve under the registered artifact dir to refuse traversal even
    # though the name regex already disallows '/'.
    base = (ARTIFACTS_DIR / job.id).resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base) + os.sep) and target != base:
        raise HTTPException(400, "path escape")
    if not target.is_file():
        raise HTTPException(404, "artifact missing on disk")
    return FileResponse(str(target))


@app.post("/unload")
def unload():
    """Release the cached ``LLM`` instance, freeing GPU memory.

    No-op if nothing is loaded. Returns the path of whatever was
    unloaded (or ``None``) so the caller can confirm.
    """
    global _llm, _llm_path
    with _llm_lock:
        path = _llm_path
        if _llm is not None:
            try:
                _llm.close()
            except Exception:
                pass
            _llm = None
            _llm_path = None
        return {"unloaded": path}


@app.post("/tokenize")
async def tokenize(req: Request):
    """Return the true token count for ``text`` against the given model.

    Body: ``{"model_path": "...", "text": "..."}``. Loads the model on
    demand using the same single-slot cache as ``/chat`` so a tokenize
    call doesn't evict an LLM the user is mid-conversation with.
    """
    body = await req.json()
    model_path = body.get("model_path")
    text = body.get("text", "")
    if not model_path:
        raise HTTPException(400, "model_path required")
    if not isinstance(text, str):
        raise HTTPException(400, "text must be a string")
    if not text:
        return {"count": 0}

    llm = _get_llm(model_path)
    # vocab.tokenize is the cyllama primitive used internally by
    # _generate_stream. add_special=False / parse_special=False keeps
    # the count comparable to "raw content tokens" rather than a
    # template-formatted prompt.
    tokens = llm.vocab.tokenize(text, add_special=False, parse_special=False)
    return {"count": len(tokens)}


_VALID_ROLES = {"system", "user", "assistant"}


def _normalize_messages(body: dict) -> list[dict]:
    """Build the message list to send to llm.chat().

    Accepts two body shapes for forward/backward compatibility:

    1. New shape (preferred): ``{"messages": [{role, content}, ...]}``.
       Used by the renderer once multi-turn context exists.
    2. Legacy shape: ``{"prompt": "...", "system_prompt": "..."}``.
       Synthesized into a one-shot system + user pair. Useful for
       ad-hoc curl callers; the desktop renderer no longer emits this.
    """
    raw = body.get("messages")
    if raw is not None:
        if not isinstance(raw, list) or not raw:
            raise HTTPException(400, "messages must be a non-empty list")
        out: list[dict] = []
        for i, m in enumerate(raw):
            if not isinstance(m, dict):
                raise HTTPException(400, f"messages[{i}] must be an object")
            role = m.get("role")
            content = m.get("content")
            if role not in _VALID_ROLES:
                raise HTTPException(400, f"messages[{i}].role must be one of {sorted(_VALID_ROLES)}")
            if not isinstance(content, str):
                raise HTTPException(400, f"messages[{i}].content must be a string")
            out.append({"role": role, "content": content})
        return out

    # Legacy fallback.
    prompt = body.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        raise HTTPException(400, "messages or prompt required")
    sys = (body.get("system_prompt") or "").strip()
    msgs = []
    if sys:
        msgs.append({"role": "system", "content": sys})
    msgs.append({"role": "user", "content": prompt})
    return msgs


@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    model_path = body.get("model_path")
    if not model_path:
        raise HTTPException(status_code=400, detail="model_path required")

    messages = _normalize_messages(body)
    config = _build_config(body.get("params"))

    llm = _get_llm(model_path)
    loop = asyncio.get_running_loop()

    # Always go through llm.chat() now -- it handles single-turn and
    # multi-turn uniformly via the model's chat template (cyllama's
    # Jinja path covers Gemma's "system role not supported" trap and
    # similar GGUF template quirks).
    def _stream():
        return llm.chat(messages, stream=True, config=config)

    async def event_stream():
        # cyllama streams synchronously; bridge to async via a producer
        # thread that puts chunks onto an asyncio queue.
        q: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def producer():
            try:
                for chunk in _stream():
                    loop.call_soon_threadsafe(q.put_nowait, chunk)
            except Exception as e:  # noqa: BLE001
                loop.call_soon_threadsafe(q.put_nowait, {"__error__": str(e)})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, DONE)

        threading.Thread(target=producer, daemon=True).start()

        try:
            while True:
                item = await q.get()
                if item is DONE:
                    yield "data: [DONE]\n\n"
                    return
                if isinstance(item, dict) and "__error__" in item:
                    yield "data: " + json.dumps({"error": item["__error__"]}) + "\n\n"
                    return
                # JSON-encode each chunk so embedded newlines / quotes /
                # unicode round-trip through SSE without ad-hoc escaping.
                yield "data: " + json.dumps({"text": str(item)}) + "\n\n"
        except asyncio.CancelledError:
            # Client disconnected (renderer aborted the fetch). Tell cyllama
            # to stop generating: the Python-side flag short-circuits the
            # token loop, and the C-level abort callback aborts any
            # in-flight llama_decode (matters for long prompt prefill).
            try:
                llm.cancel()
            except Exception:
                pass
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Models: discovery, inspection, import, HuggingFace download.
#
# Source-of-truth precedence is per PLAN.md Section 12 Q1: the desktop-
# managed MODELS_DIR is primary; HF caches are enumerated read-only as a
# secondary listing so the user sees pre-existing GGUFs without having to
# move them. Drag-drop import copies into MODELS_DIR; HF downloads write
# under MODELS_DIR/_hf/<repo_slug>/ (kept under MODELS_DIR so the user
# can wipe everything by clearing one directory).
# ---------------------------------------------------------------------------


def _slug_repo(repo: str) -> str:
    """``user/Model-Name`` -> ``user__Model-Name`` (filesystem-safe)."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", repo).strip("_")


def _scan_gguf(root: Path, source: str) -> list[dict]:
    out: list[dict] = []
    if not root.exists():
        return out
    try:
        for p in root.rglob("*.gguf"):
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            out.append({
                "path": str(p),
                "name": p.name,
                "size": size,
                "source": source,
                "dir": str(p.parent),
            })
    except OSError:
        pass
    return out


@app.get("/models/cached")
def models_cached():
    """List GGUF files we know about.

    Items from MODELS_DIR are flagged ``source: "local"``; items from the
    HF cache(s) are ``source: "hf"``. Same path appearing in multiple
    sources is deduped on absolute path with local taking precedence.
    """
    seen: dict[str, dict] = {}
    for item in _scan_gguf(MODELS_DIR, "local"):
        seen[item["path"]] = item
    for cache in _HF_CACHE_DIRS:
        for item in _scan_gguf(cache, "hf"):
            seen.setdefault(item["path"], item)
    items = sorted(seen.values(), key=lambda m: (m["source"] != "local", m["name"].lower()))
    return {"models": items, "models_dir": str(MODELS_DIR)}


@app.post("/models/inspect")
async def models_inspect(req: Request):
    """Read GGUF metadata via cyllama.GGUFContext.

    The exact attribute name has shifted across cyllama versions; probe
    the obvious shapes and return whatever surfaces. Errors map to a
    structured response so the renderer can show "couldn't read" rather
    than crashing the workspace.
    """
    body = await req.json()
    model_path = body.get("path")
    if not model_path or not os.path.isfile(model_path):
        raise HTTPException(400, "path required and must exist")

    GGUFContext = getattr(cyllama, "GGUFContext", None)
    if GGUFContext is None:
        return {"path": model_path, "metadata": None, "error": "GGUFContext not available"}

    try:
        ctx = None
        # Try a few constructor / classmethod shapes.
        for ctor in (
            lambda: GGUFContext.from_file(model_path),
            lambda: GGUFContext(model_path),
        ):
            try:
                ctx = ctor()
                break
            except (TypeError, AttributeError):
                continue
        if ctx is None:
            return {"path": model_path, "metadata": None, "error": "no GGUFContext ctor matched"}

        meta = None
        for getter_name in ("get_all_metadata", "metadata", "all_metadata"):
            getter = getattr(ctx, getter_name, None)
            if getter is None:
                continue
            try:
                meta = getter() if callable(getter) else getter
                break
            except Exception:  # noqa: BLE001
                continue
        if meta is None:
            return {"path": model_path, "metadata": None, "error": "no metadata getter matched"}
        # GGUF metadata values can include numpy types; coerce to JSON-safe.
        return {"path": model_path, "metadata": _jsonify(meta)}
    except Exception as exc:  # noqa: BLE001
        return {"path": model_path, "metadata": None, "error": str(exc)}


def _jsonify(v):
    """Best-effort JSON coercion. Drops anything we can't represent."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, dict):
        return {str(k): _jsonify(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonify(x) for x in v]
    # Numpy scalars expose .item(); fall back to str.
    if hasattr(v, "item"):
        try:
            return _jsonify(v.item())
        except Exception:  # noqa: BLE001
            return str(v)
    return str(v)


@app.post("/models/import")
async def models_import(req: Request):
    """Copy a GGUF file from an arbitrary local path into MODELS_DIR.

    Used by drag-drop in the Models workspace. We do a server-side copy
    rather than upload because the renderer only has the path; reading
    multi-GB files into JS just to POST them back would be wasteful.
    Same-file (already inside MODELS_DIR) is a no-op.
    """
    body = await req.json()
    src = body.get("path")
    if not isinstance(src, str) or not src:
        raise HTTPException(400, "path required")
    src_path = Path(src).resolve()
    if not src_path.is_file():
        raise HTTPException(400, f"not a file: {src}")
    if src_path.suffix.lower() != ".gguf":
        raise HTTPException(400, "only .gguf files supported")

    # No-op if already inside MODELS_DIR.
    try:
        src_path.relative_to(MODELS_DIR)
        return {"path": str(src_path), "imported": False}
    except ValueError:
        pass

    dst = MODELS_DIR / src_path.name
    if dst.exists():
        # Don't silently clobber an existing local model.
        raise HTTPException(409, f"already exists: {dst.name}")

    # Copy via shutil for cross-filesystem safety; reflinks where the
    # OS supports it (APFS clonefile on macOS).
    import shutil
    shutil.copy2(str(src_path), str(dst))
    return {
        "path": str(dst),
        "name": dst.name,
        "size": dst.stat().st_size,
        "imported": True,
    }


# --- HuggingFace peek + download ------------------------------------------

_HF_RESOLVE_RE = re.compile(
    r"^https?://(?:huggingface\.co|hf\.co)/"
    r"(?P<repo>[^/]+/[^/]+)/resolve/(?P<rev>[^/]+)/(?P<path>.+?)(?:\?.*)?$"
)


def _parse_hf_target(body: dict) -> tuple[str, str, str]:
    """Accept either a full HF resolve URL or a ``repo:file`` shorthand.

    Returns ``(repo, revision, file_path)``. Default revision is "main".
    """
    url = (body.get("url") or "").strip()
    if url:
        m = _HF_RESOLVE_RE.match(url)
        if not m:
            raise HTTPException(400, "url must be a https://huggingface.co/<repo>/resolve/<rev>/<path>")
        return m.group("repo"), m.group("rev"), m.group("path")

    repo = (body.get("repo") or "").strip()
    file_path = (body.get("file") or "").strip()
    if repo and file_path:
        rev = (body.get("revision") or "main").strip() or "main"
        if "/" not in repo or repo.count("/") != 1:
            raise HTTPException(400, "repo must be 'user/name'")
        return repo, rev, file_path

    # Shorthand: "<repo>:<file>"
    short = (body.get("target") or "").strip()
    if short and ":" in short:
        repo, _, file_path = short.partition(":")
        if "/" not in repo or repo.count("/") != 1:
            raise HTTPException(400, "shorthand must be 'user/repo:path/to/file.gguf'")
        return repo.strip(), "main", file_path.strip()

    raise HTTPException(400, "provide 'url', or {'repo','file'}, or 'target' shorthand")


@app.post("/models/hf/peek")
async def models_hf_peek(req: Request):
    """HEAD the HF resolve URL to get size + content-type without downloading.

    Returns ``{repo, revision, file, size, exists, target_path}`` where
    ``target_path`` is where a download would land in MODELS_DIR.
    """
    body = await req.json()
    repo, rev, file_path = _parse_hf_target(body)
    target = MODELS_DIR / "_hf" / _slug_repo(repo) / file_path

    import httpx
    url = f"https://huggingface.co/{repo}/resolve/{rev}/{file_path}"
    size: Optional[int] = None
    exists_remote = False
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as cl:
            r = await cl.head(url)
            exists_remote = r.status_code == 200
            cl_header = r.headers.get("content-length")
            if cl_header and cl_header.isdigit():
                size = int(cl_header)
    except Exception as exc:  # noqa: BLE001
        return {
            "repo": repo, "revision": rev, "file": file_path,
            "size": None, "exists": False, "target_path": str(target),
            "error": str(exc),
        }

    return {
        "repo": repo, "revision": rev, "file": file_path,
        "size": size, "exists": exists_remote,
        "target_path": str(target),
        "already_local": target.exists(),
    }


register_job_kind("models.hf-download")


@app.post("/jobs/models.hf-download")
async def jobs_hf_download(req: Request):
    """Spawn an HF download job. Streams progress via /jobs/<id>/events."""
    body = await req.json()
    repo, rev, file_path = _parse_hf_target(body)
    target = (MODELS_DIR / "_hf" / _slug_repo(repo) / file_path).resolve()
    # Defence in depth: refuse target paths that escape MODELS_DIR even
    # though _slug_repo and the regex constrain inputs.
    if not str(target).startswith(str(MODELS_DIR) + os.sep):
        raise HTTPException(400, "target escapes MODELS_DIR")
    if target.exists():
        raise HTTPException(409, f"already downloaded: {target.name}")
    target.parent.mkdir(parents=True, exist_ok=True)

    url = f"https://huggingface.co/{repo}/resolve/{rev}/{file_path}"

    async def producer(job: Job) -> None:
        import httpx
        tmp = target.with_suffix(target.suffix + ".part")
        downloaded = 0
        total: Optional[int] = None
        last_pct = -1
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=None) as cl:
                async with cl.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"HTTP {r.status_code}")
                    cl_header = r.headers.get("content-length")
                    if cl_header and cl_header.isdigit():
                        total = int(cl_header)
                    with open(tmp, "wb") as f:
                        async for chunk in r.aiter_bytes(chunk_size=1024 * 256):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total:
                                pct = int(downloaded * 100 / total)
                                # Only emit on percent change so we don't
                                # spam the queue on fast connections.
                                if pct != last_pct:
                                    last_pct = pct
                                    await _emit(job, {
                                        "type": "progress",
                                        "value": downloaded / total,
                                        "downloaded": downloaded,
                                        "total": total,
                                    })
                            else:
                                await _emit(job, {
                                    "type": "progress",
                                    "value": None,
                                    "downloaded": downloaded,
                                })
            tmp.rename(target)
            job.artifact_path = target
        except asyncio.CancelledError:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
        except Exception:
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

        await _emit(job, {
            "type": "result",
            "result": {
                "path": str(target),
                "name": target.name,
                "size": target.stat().st_size,
                "repo": repo,
                "file": file_path,
            },
        })

    job = await run_job("models.hf-download", producer)
    return {"job_id": job.id}


# Demo job: lets the renderer exercise the /jobs SSE wiring before real
# producers (HF download, RAG ingest, image gen, etc.) land. Useful in
# tests too. Kept tiny on purpose -- not user-facing.
register_job_kind("demo")


@app.post("/jobs/demo")
async def jobs_demo(req: Request):
    """Spawn a fake job that emits a few progress events then a result.

    Body (optional): ``{"steps": int, "delay_s": float, "fail": bool}``.
    """
    try:
        body = await req.json()
    except Exception:  # noqa: BLE001
        body = {}
    steps = max(1, int(body.get("steps", 3)))
    delay = max(0.0, float(body.get("delay_s", 0.05)))
    fail = bool(body.get("fail", False))

    async def producer(job: Job) -> None:
        for i in range(steps):
            await _emit(job, {
                "type": "progress",
                "value": (i + 1) / steps,
                "message": f"step {i + 1}/{steps}",
            })
            await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("demo failure")
        await _emit(job, {"type": "result", "result": {"steps": steps}})

    job = await run_job("demo", producer)
    return {"job_id": job.id}


def _handle_signal(signum, frame):  # noqa: ARG001
    sys.exit(0)


for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, _handle_signal)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
