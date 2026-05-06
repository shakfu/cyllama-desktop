"""FastAPI sidecar exposing cyllama over localhost HTTP/SSE.

Started by the Electron main process with these env vars:
  CYLLAMA_SIDECAR_PORT          port to bind on 127.0.0.1
  CYLLAMA_SIDECAR_TOKEN         bearer token clients must send
  CYLLAMA_SIDECAR_PARENT_PID    parent pid; sidecar exits if parent dies
"""

from __future__ import annotations

import asyncio
import importlib
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
    # Phase 3 hardware / load-time fields. Same _GC_ACCEPTED filter
    # gates them; they only land in the config if the installed cyllama
    # actually accepts the field. Per LLM(model_path, config=...), these
    # take effect at *construction* time, so changing them must evict
    # the cached LLM and reload (see _LOAD_KEYS / _hw_signature).
    "n_gpu_layers":      int,
    "main_gpu":          int,
    "split_mode":        int,
    "n_ctx":             int,
    "n_batch":           int,
    # Phase 2 grammar / GBNF. Passed as a raw GBNF string. Forward-
    # looking: cyllama 0.2.15's GenerationConfig doesn't accept it, so
    # the _GC_ACCEPTED gate drops it on the floor for now and the UI
    # hides the row. When cyllama exposes the field this becomes live
    # without further changes.
    "grammar":           str,
}

# Fields whose value affects model construction (and therefore VRAM
# layout) rather than per-token sampling. tensor_split is special-cased
# as a list[float] -- not in _ALLOWED_PARAMS because its caster differs.
_LOAD_KEYS: tuple[str, ...] = (
    "n_gpu_layers", "main_gpu", "split_mode",
    "n_ctx", "n_batch", "tensor_split",
)


def _coerce_tensor_split(v) -> list[float] | None:
    """Accept list[number] or comma-separated string. Empty / invalid -> None."""
    if isinstance(v, str):
        items = [s.strip() for s in v.split(",") if s.strip()]
    elif isinstance(v, (list, tuple)):
        items = list(v)
    else:
        return None
    out: list[float] = []
    for x in items:
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            return None
    return out or None


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


def _resolve_attr(paths: tuple[tuple[str, str], ...]):
    """Walk ``(module, attr)`` pairs, return the first attribute that imports.

    Used for the Phase 2 capability probes -- ``Speculative``, ``NgramCache``,
    ``json_schema_to_grammar`` live at different paths across cyllama
    versions, and the renderer needs to know which are present so it can
    hide rows whose backing API isn't available.
    """
    for mod_name, attr in paths:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        obj = getattr(mod, attr, None)
        if obj is not None:
            return obj
    return None


_JSON_SCHEMA_TO_GRAMMAR = _resolve_attr((
    ("cyllama.utils.json_schema_to_grammar", "json_schema_to_grammar"),
    ("cyllama.utils", "json_schema_to_grammar"),
    ("cyllama", "json_schema_to_grammar"),
))
_SPECULATIVE_CLS = _resolve_attr((
    ("cyllama.llama.llama_cpp", "Speculative"),
    ("cyllama", "Speculative"),
))
_SPECULATIVE_PARAMS_CLS = _resolve_attr((
    ("cyllama.llama.llama_cpp", "SpeculativeParams"),
    ("cyllama", "SpeculativeParams"),
))
_NGRAM_CACHE_CLS = _resolve_attr((
    ("cyllama.llama.llama_cpp", "NgramCache"),
    ("cyllama", "NgramCache"),
))

# Phase 5 -- Whisper transcription. Both the C-level context class and
# the WAV-loader helper from cyllama's CLI module are needed; if either
# is missing the /info.features.whisper flag stays False and the
# Transcribe pane hides itself.
_WHISPER_CTX_CLS = _resolve_attr((
    ("cyllama.whisper.whisper_cpp", "WhisperContext"),
))
_WHISPER_CTX_PARAMS_CLS = _resolve_attr((
    ("cyllama.whisper.whisper_cpp", "WhisperContextParams"),
))
_WHISPER_FULL_PARAMS_CLS = _resolve_attr((
    ("cyllama.whisper.whisper_cpp", "WhisperFullParams"),
))
_WHISPER_LOAD_WAV = _resolve_attr((
    ("cyllama.whisper.cli", "load_wav_file"),
))
_WHISPER_RESAMPLE = _resolve_attr((
    ("cyllama.whisper.cli", "resample_audio"),
))

# Phase 6 -- Stable Diffusion text-to-image. The convenience
# ``text_to_image`` does the SDContext lifecycle internally so a single
# call hides the multi-stage setup. ``SDImage.save_png`` writes the
# result to disk; the job records the path on ``job.artifact_path``
# and the renderer fetches it via the existing artifact endpoint.
_SD_TEXT_TO_IMAGE = _resolve_attr((
    ("cyllama.sd", "text_to_image"),
))
_SD_IMAGE_CLS = _resolve_attr((
    ("cyllama.sd", "SDImage"),
))

# Phase 7 -- Agents. ``ReActAgent`` is the workhorse; ``Tool`` defines
# the schema cyllama hands to the LLM as a function-calling preamble;
# ``AgentEvent`` / ``EventType`` are the streamed trace payload.
_AGENT_REACT_CLS = _resolve_attr((
    ("cyllama.agents", "ReActAgent"),
))
_AGENT_TOOL_CLS = _resolve_attr((
    ("cyllama.agents", "Tool"),
))
_AGENT_EVENT_CLS = _resolve_attr((
    ("cyllama.agents", "AgentEvent"),
    ("cyllama.agents.types", "AgentEvent"),
))
_AGENT_EVENT_TYPE = _resolve_attr((
    ("cyllama.agents", "EventType"),
    ("cyllama.agents.types", "EventType"),
))

# Phase 8 -- OpenAI-compatible server. Both flavours (embedded C++
# server vs. pure-Python http.server-based one) are surfaced; the
# renderer picks. ``ServerConfig`` is shared between them.
_EMBEDDED_SERVER_CLS = _resolve_attr((
    ("cyllama.llama.server.embedded", "EmbeddedServer"),
))
_PYTHON_SERVER_CLS = _resolve_attr((
    ("cyllama.llama.server.python", "PythonServer"),
))
_SERVER_CONFIG_CLS = _resolve_attr((
    ("cyllama.llama.server.embedded", "ServerConfig"),
    ("cyllama.llama.server.python", "ServerConfig"),
))

# Phase 9 -- batch + quantize tooling.
_BATCH_GENERATE = _resolve_attr((
    ("cyllama", "batch_generate"),
))
_MODEL_QUANTIZE = _resolve_attr((
    ("cyllama.llama.llama_cpp", "model_quantize"),
    ("cyllama", "model_quantize"),
))
_QUANTIZE_PARAMS_CLS = _resolve_attr((
    ("cyllama.llama.llama_cpp", "LlamaModelQuantizeParams"),
))

# Multimodal (LLAVA / MTMD). ``ImageAnalyzer`` is the high-level wrapper
# that takes an mmproj path + a LlamaModel and exposes
# ``answer_question(question, image)``. We use the single-shot
# answer_question path rather than ``VisionLanguageChat`` because the
# desktop's chat history is owned by the renderer; cyllama-side
# conversation state would diverge from what the user sees.
_MTMD_IMAGE_ANALYZER = _resolve_attr((
    ("cyllama.llama.mtmd", "ImageAnalyzer"),
))


# Capability flags surfaced via /info. ``grammar`` reflects whether
# GenerationConfig actually accepts a ``grammar`` field AND whether the
# json-schema helper is present -- the UI uses the former to decide
# whether grammar-constrained chat is functional, and the latter to
# enable "From JSON Schema" generation. ``speculative`` / ``ngram``
# are end-to-end: both the helper class and the GC field have to exist
# for the chat path to actually use them. Until cyllama threads these
# through ``LLM.chat()``, the flags are False even when the classes
# exist, which is correct -- the UI hides rows that wouldn't take effect.
def _probe_devices() -> list[dict]:
    """Best-effort enumeration of ggml backend devices.

    Returns ``[{name, description, type}, ...]``. Used by Phase 3 to
    decide whether to show multi-GPU controls (split_mode, tensor_split,
    main_gpu) -- a single-GPU machine doesn't need them. Failure to
    probe returns ``[]`` rather than raising; the renderer treats empty
    as "unknown" and falls back to always-show.
    """
    try:
        mod = importlib.import_module("cyllama.llama.llama_cpp")
    except Exception:
        return []
    init = getattr(mod, "llama_backend_init", None)
    load_all = getattr(mod, "ggml_backend_load_all", None)
    dev_info = getattr(mod, "ggml_backend_dev_info", None)
    if dev_info is None:
        return []
    try:
        if callable(init): init()
        if callable(load_all): load_all()
        raw = dev_info()
    except Exception:
        return []
    out: list[dict] = []
    for d in (raw or []):
        if isinstance(d, dict):
            out.append({
                "name": str(d.get("name", "")),
                "description": str(d.get("description", "")),
                "type": str(d.get("type", "")),
            })
    return out


_DEVICES: list[dict] = _probe_devices()


_FEATURE_FLAGS: dict[str, bool] = {
    "grammar": ("grammar" in _GC_ACCEPTED),
    "json_schema_to_grammar": _JSON_SCHEMA_TO_GRAMMAR is not None,
    "speculative": (
        _SPECULATIVE_CLS is not None
        and _SPECULATIVE_PARAMS_CLS is not None
        and "speculative" in _GC_ACCEPTED
    ),
    "ngram": (
        _NGRAM_CACHE_CLS is not None
        and "ngram" in _GC_ACCEPTED
    ),
    # Whisper requires both the context class and the WAV loader -- the
    # context alone can't ingest a file from disk without a way to
    # produce 16 kHz mono float32 samples.
    "whisper": (
        _WHISPER_CTX_CLS is not None
        and _WHISPER_FULL_PARAMS_CLS is not None
        and _WHISPER_LOAD_WAV is not None
    ),
    "image": (
        _SD_TEXT_TO_IMAGE is not None
        and _SD_IMAGE_CLS is not None
    ),
    "agents": (
        _AGENT_REACT_CLS is not None
        and _AGENT_TOOL_CLS is not None
    ),
    # Either flavour is enough to enable the pane -- the renderer's
    # kind picker filters per-flavour availability via the granular
    # ``server_kinds`` field below.
    "openai_server": (
        _SERVER_CONFIG_CLS is not None
        and (_EMBEDDED_SERVER_CLS is not None or _PYTHON_SERVER_CLS is not None)
    ),
    "batch": _BATCH_GENERATE is not None,
    # The params class isn't strictly required (model_quantize accepts
    # ``None``) but its absence is a strong signal that the C bindings
    # didn't link the quantize symbols. Gate on both for clarity.
    "quantize": _MODEL_QUANTIZE is not None and _QUANTIZE_PARAMS_CLS is not None,
    "multimodal": _MTMD_IMAGE_ANALYZER is not None,
}

# Granular per-kind availability so the renderer can hide the radio
# option for a flavour the build doesn't include.
_SERVER_KINDS_AVAILABLE: list[str] = [
    k for k, present in (
        ("embedded", _EMBEDDED_SERVER_CLS is not None),
        ("python", _PYTHON_SERVER_CLS is not None),
    ) if present
]
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

    ts = _coerce_tensor_split(params.get("tensor_split"))
    if ts is not None and "tensor_split" in _GC_ACCEPTED:
        kwargs["tensor_split"] = ts

    # Phase 2 advanced fields. Each only flows into ``GenerationConfig``
    # when the installed cyllama actually accepts the matching kwarg --
    # otherwise the wire field is silently dropped, which keeps the UI
    # forward-compatible without crashing on older builds.
    spec = _coerce_speculative(params.get("speculative"))
    if spec is not None and "speculative" in _GC_ACCEPTED:
        kwargs["speculative"] = spec

    ngram = params.get("ngram")
    if ngram is not None and "ngram" in _GC_ACCEPTED:
        # Accept either a bool toggle or a dict of helper kwargs. The
        # caster is intentionally lax: cyllama hasn't pinned the shape
        # of this field yet, so we pass through whatever the renderer
        # sent rather than guessing.
        kwargs["ngram"] = bool(ngram) if isinstance(ngram, bool) else ngram

    return GenerationConfig(**kwargs) if kwargs else None


def _coerce_speculative(v):
    """Convert a renderer ``speculative`` dict into a ``SpeculativeParams``.

    Body shape (renderer): ``{draft_model_path?, n_max?, n_min?,
    p_split?, p_min?}``. Returns the constructed params object, or a
    dict (passed through unchanged) if the helper class isn't present in
    this cyllama. Returns ``None`` if the input is empty / unusable.
    """
    if not v or not isinstance(v, dict):
        return None
    # Drop the draft-model field before constructing -- it isn't part of
    # the params class. The chat path would consume it separately when
    # cyllama wires speculative through ``LLM.chat()``.
    fields = {k: v[k] for k in ("n_max", "n_min", "p_split", "p_min") if k in v}
    if _SPECULATIVE_PARAMS_CLS is None:
        return fields or None
    try:
        kwargs = {}
        if "n_max" in fields: kwargs["n_max"] = int(fields["n_max"])
        if "n_min" in fields: kwargs["n_min"] = int(fields["n_min"])
        if "p_split" in fields: kwargs["p_split"] = float(fields["p_split"])
        if "p_min" in fields: kwargs["p_min"] = float(fields["p_min"])
        return _SPECULATIVE_PARAMS_CLS(**kwargs) if kwargs else _SPECULATIVE_PARAMS_CLS()
    except (TypeError, ValueError):
        return None


def _hw_signature(params: dict | None) -> tuple:
    """Stable, hashable digest of the hardware/load fields in ``params``.

    Used as part of the LLM cache key so a cached LLM is evicted only
    when a load-time field changes -- temperature tweaks shouldn't
    reload a multi-GB model. Unset fields are normalised to ``None`` so
    "missing" and "explicitly default" hash the same.
    """
    if not params:
        return tuple((k, None) for k in _LOAD_KEYS)
    out = []
    for k in _LOAD_KEYS:
        v = params.get(k)
        if v is None or v == "":
            out.append((k, None))
            continue
        if k == "tensor_split":
            ts = _coerce_tensor_split(v)
            out.append((k, tuple(ts) if ts else None))
            continue
        try:
            out.append((k, int(v)))
        except (TypeError, ValueError):
            out.append((k, None))
    return tuple(out)


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

# Additional read-only model search roots. The user pins these in the
# General -> Preferences pane (or via env for direct smoke tests). The
# desktop-managed MODELS_DIR stays primary -- drag-drop import + HF
# download write there -- and these are scanned alongside it as
# read-only sources, items tagged ``source: "external"``.
def _resolve_models_extra(raw: str) -> tuple[Path, ...]:
    if not raw:
        return ()
    out: list[Path] = []
    for part in raw.split(os.pathsep):
        s = part.strip()
        if not s:
            continue
        try:
            p = Path(s).resolve()
        except OSError:
            continue
        if p == MODELS_DIR:
            # Don't scan the primary as an extra too -- would double
            # the dedup work and surface every file twice in the cache.
            continue
        out.append(p)
    return tuple(out)


MODELS_EXTRA: tuple[Path, ...] = _resolve_models_extra(
    os.environ.get("CYLLAMA_SIDECAR_MODELS_EXTRA", "")
)

# Per-workspace RAG state. Each collection gets a sqlite vector store at
# RAG_DIR/<id>.sqlite; the registry of collections (id, name, embedding
# model, counts) lives in RAG_DIR/collections.json.
RAG_DIR = Path(
    os.environ.get("CYLLAMA_SIDECAR_RAG")
    or (Path.home() / ".cache" / "cyllama-desktop" / "rag")
).resolve()
RAG_DIR.mkdir(parents=True, exist_ok=True)

# Per-workspace image upload root. Multimodal chat attachments land here
# (one file per upload, named with a uuid prefix to keep collisions
# trivial). Files survive restarts so chat replay can serve them.
UPLOADS_DIR = Path(
    os.environ.get("CYLLAMA_SIDECAR_UPLOADS")
    or (Path.home() / ".cache" / "cyllama-desktop" / "uploads")
).resolve()
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

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


# Cache one LLM per (model_path, hardware signature). cyllama.LLM holds
# GPU resources, so we keep this single-slot to avoid VRAM blowup;
# switching models OR changing a load-time field evicts and reloads.
_llm_lock = threading.Lock()
_llm: Optional[LLM] = None
_llm_path: Optional[str] = None
_llm_hw_sig: tuple = ()


def _get_llm(model_path: str, params: dict | None = None) -> LLM:
    global _llm, _llm_path, _llm_hw_sig
    hw_sig = _hw_signature(params)
    with _llm_lock:
        if _llm is not None and _llm_path == model_path and _llm_hw_sig == hw_sig:
            return _llm
        if _llm is not None:
            try:
                _llm.close()
            except Exception:
                pass
            _llm = None
        if not os.path.isfile(model_path):
            raise HTTPException(status_code=400, detail=f"model not found: {model_path}")
        # Build a load-only config: only fields cyllama actually takes,
        # filtered through the same _GC_ACCEPTED gate as /chat. Sampling
        # fields are present in the config too but harmless at load time
        # (cyllama uses them as defaults overridable per chat call).
        load_cfg = _build_config(params) if params else None
        if load_cfg is not None:
            _llm = LLM(model_path, config=load_cfg)
        else:
            _llm = LLM(model_path)
        _llm_path = model_path
        _llm_hw_sig = hw_sig
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
        # Extra read-only scan roots the user added in Preferences.
        # Empty when only the primary models dir is configured.
        "models_extra": [str(p) for p in MODELS_EXTRA],
        "rag_dir": str(RAG_DIR),
        "uploads_dir": str(UPLOADS_DIR),
    },
    # Subset of _ALLOWED_PARAMS that ``GenerationConfig`` in the
    # installed cyllama actually accepts. Renderer hides UI rows whose
    # key is not in this list. Probed once at module load.
    "supported_params": _SUPPORTED_PARAMS,
    # Phase 2 capability flags. Renderer hides UI rows whose backing
    # cyllama API isn't present in this build. ``grammar`` /
    # ``speculative`` / ``ngram`` reflect end-to-end usability; the
    # ``json_schema_to_grammar`` flag is independent (the helper can
    # produce GBNF text even if chat can't apply it yet).
    "features": dict(_FEATURE_FLAGS),
    # Phase 3: ggml backend devices. Renderer counts GPU-typed entries
    # to decide whether to show multi-GPU controls. Empty list means
    # the probe failed or this cyllama doesn't expose the helpers --
    # in that case the renderer leaves the controls visible (safer
    # than hiding them on a real multi-GPU rig with an old probe).
    "devices": list(_DEVICES),
    # Phase 8: which OpenAI-server flavours this cyllama build offers.
    "server_kinds": list(_SERVER_KINDS_AVAILABLE),
}


@app.post("/grammar/from-schema")
async def grammar_from_schema(req: Request):
    """Convert a JSON schema to a GBNF grammar string.

    Body: ``{"schema": <object|string>, "force_gbnf"?: bool}``. ``schema``
    is either a parsed object or a JSON-encoded string. Returns
    ``{grammar: "..."}``. 501 if the helper is missing in this cyllama.
    """
    if _JSON_SCHEMA_TO_GRAMMAR is None:
        raise HTTPException(501, "json_schema_to_grammar not available in this cyllama")
    body = await req.json()
    schema = body.get("schema")
    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except ValueError as exc:
            raise HTTPException(400, f"schema is not valid JSON: {exc}")
    if not isinstance(schema, dict):
        raise HTTPException(400, "schema must be a JSON object")
    force_gbnf = bool(body.get("force_gbnf", False))
    try:
        grammar = _JSON_SCHEMA_TO_GRAMMAR(schema, force_gbnf=force_gbnf)
    except TypeError:
        # Older signatures may not accept the kwarg.
        try:
            grammar = _JSON_SCHEMA_TO_GRAMMAR(schema)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"schema rejected: {exc}")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"schema rejected: {exc}")
    return {"grammar": grammar}


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


@app.get("/artifacts/image")
def artifacts_image_list():
    """List past image txt2img artifacts on disk.

    Each image job writes ``<ARTIFACTS_DIR>/<job_id>/output.png`` (see
    the txt2img producer). We enumerate those rather than reading the
    job registry because finished jobs get GC'd after an hour, while
    artifacts persist on disk and the user expects the gallery to
    survive sidecar restarts.

    Returns ``{items: [{job_id, name, size, mtime, url}]}`` sorted by
    most-recent first. ``url`` is the ``/artifacts/...`` path for
    serving the file (does not require the job registry).
    """
    items: list[dict] = []
    if ARTIFACTS_DIR.is_dir():
        for sub in ARTIFACTS_DIR.iterdir():
            if not sub.is_dir():
                continue
            png = sub / "output.png"
            if not png.is_file():
                continue
            try:
                st = png.stat()
            except OSError:
                continue
            items.append({
                "job_id": sub.name,
                "name": png.name,
                "size": st.st_size,
                "mtime": st.st_mtime,
                "url": f"/artifacts/{sub.name}/{png.name}",
            })
    items.sort(key=lambda r: r["mtime"], reverse=True)
    return {"items": items}


@app.get("/artifacts/{job_id}/{name}")
def artifacts_serve(job_id: str, name: str):
    """Serve a job artifact file directly, bypassing the job registry.

    The /jobs/<id>/artifact/<name> endpoint requires the job to still
    be in the registry (so it 404s on GC'd jobs). This route serves
    purely off the filesystem so the gallery can show artifacts from
    long-finished jobs. Path traversal is locked down via the same
    name regex + ``relative_to`` check.
    """
    if not _ARTIFACT_NAME_RE.match(job_id):
        raise HTTPException(400, "invalid job id")
    if not _ARTIFACT_NAME_RE.match(name):
        raise HTTPException(400, "invalid artifact name")
    base = (ARTIFACTS_DIR / job_id).resolve()
    target = (base / name).resolve()
    try:
        target.relative_to(ARTIFACTS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "path escape")
    if not target.is_file():
        raise HTTPException(404, "artifact not found")
    return FileResponse(str(target))


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
    global _llm, _llm_path, _llm_hw_sig
    with _llm_lock:
        path = _llm_path
        if _llm is not None:
            try:
                _llm.close()
            except Exception:
                pass
            _llm = None
            _llm_path = None
            _llm_hw_sig = ()
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

    # Tokenize doesn't care about hardware, but pass params so the cache
    # match logic doesn't evict an LLM the user is mid-conversation with.
    llm = _get_llm(model_path, body.get("params"))
    # vocab.tokenize is the cyllama primitive used internally by
    # _generate_stream. add_special=False / parse_special=False keeps
    # the count comparable to "raw content tokens" rather than a
    # template-formatted prompt.
    tokens = llm.vocab.tokenize(text, add_special=False, parse_special=False)
    return {"count": len(tokens)}


_VALID_ROLES = {"system", "user", "assistant"}

# ---------------------------------------------------------------------------
# Multimodal: image upload, serve, and the /chat routing branch that uses
# ``cyllama.llama.mtmd.ImageAnalyzer`` instead of ``LLM.chat`` when an
# image is attached and an mmproj path is configured.
# ---------------------------------------------------------------------------


# Single-slot ImageAnalyzer cache keyed on ``(mmproj_path, model_path)``.
# Keeps the projector + model bound across consecutive turns so a quick
# back-and-forth about the same image isn't re-loading the projector
# each time. Different chat models or different projectors evict.
_MTMD_INSTANCE: dict = {"key": None, "analyzer": None}


def _get_image_analyzer(mmproj_path: str, llm) -> object:
    if _MTMD_IMAGE_ANALYZER is None:
        raise HTTPException(501, "multimodal not available in this cyllama build")
    if not os.path.isfile(mmproj_path):
        raise HTTPException(400, f"mmproj_path not found: {mmproj_path}")
    key = (mmproj_path, getattr(llm, "model_path", None))
    if _MTMD_INSTANCE.get("key") == key and _MTMD_INSTANCE.get("analyzer") is not None:
        return _MTMD_INSTANCE["analyzer"]
    # ``ImageAnalyzer(mmproj_path, llama_model)`` -- pass ``llm.model``
    # which is the underlying ``LlamaModel`` (cyllama LLM exposes it as
    # a public attribute).
    inner_model = getattr(llm, "model", None)
    if inner_model is None:
        raise HTTPException(500, "LLM has no .model attribute (cyllama API change?)")
    try:
        analyzer = _MTMD_IMAGE_ANALYZER(mmproj_path, inner_model)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"failed to build ImageAnalyzer: {exc}")
    _MTMD_INSTANCE["key"] = key
    _MTMD_INSTANCE["analyzer"] = analyzer
    return analyzer


_UPLOAD_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_UPLOAD_ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_UPLOAD_MAX_BYTES = 16 * 1024 * 1024  # 16 MiB cap; LLAVA inputs rarely need more


@app.post("/chat/upload")
async def chat_upload(req: Request):
    """Accept a multipart image upload and stash it under UPLOADS_DIR.

    Body: multipart/form-data with a single ``file`` field. The
    response is ``{id, name, size, path, url}`` where ``url`` is the
    auth-fetchable serving path the renderer hands to ``<img src=>``
    via a blob (CSP forbids loopback HTTP for img-src).
    """
    form = await req.form()
    upload = form.get("file")
    if upload is None:
        raise HTTPException(400, "file field required")

    # FastAPI's UploadFile API. ``filename`` may be ``None`` for raw
    # blobs; we fall back to a generic name in that case.
    raw_name = (getattr(upload, "filename", "") or "image.bin").strip()
    suffix = Path(raw_name).suffix.lower()
    if suffix not in _UPLOAD_ALLOWED_EXTS:
        raise HTTPException(400, f"unsupported image type: {suffix or '(none)'}")

    # Drain the upload while enforcing a size cap. Streaming so a
    # huge upload doesn't balloon process memory before we reject.
    body = bytearray()
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        body.extend(chunk)
        if len(body) > _UPLOAD_MAX_BYTES:
            raise HTTPException(400, f"upload exceeds {_UPLOAD_MAX_BYTES} bytes")

    upload_id = uuid.uuid4().hex
    dest = (UPLOADS_DIR / f"{upload_id}{suffix}").resolve()
    # Defence in depth: even though the name is built from server-side
    # uuid + a whitelisted suffix, double-check the resolved path
    # stays inside UPLOADS_DIR.
    try:
        dest.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "path escape")
    with open(dest, "wb") as f:
        f.write(bytes(body))

    return {
        "id": upload_id,
        "name": raw_name,
        "size": len(body),
        "path": str(dest),
        "url": f"/chat/upload/{dest.name}",
    }


@app.get("/chat/upload/{name}")
def chat_upload_serve(name: str):
    """Serve a previously uploaded image. Sandboxed to UPLOADS_DIR."""
    if not _UPLOAD_NAME_RE.match(name):
        raise HTTPException(400, "invalid upload name")
    target = (UPLOADS_DIR / name).resolve()
    try:
        target.relative_to(UPLOADS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "path escape")
    if not target.is_file():
        raise HTTPException(404, "upload not found")
    return FileResponse(str(target))


def _latest_user_image_path(messages: list[dict]) -> Optional[str]:
    """Return the first image path on the most recent user message.

    Multi-image is deferred -- ImageAnalyzer.answer_question takes a
    single image. The renderer is allowed to attach more than one for
    persistence reasons (a chat may mention several images in passing)
    but only the first goes into the multimodal call.
    """
    for m in reversed(messages):
        if m.get("role") != "user":
            continue
        imgs = m.get("images") or []
        if isinstance(imgs, list) and imgs:
            first = imgs[0]
            if isinstance(first, str):
                return first
            if isinstance(first, dict):
                p = first.get("path")
                if isinstance(p, str):
                    return p
        return None
    return None


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
            entry: dict = {"role": role, "content": content}
            # Preserve attached image references on user messages so
            # the multimodal branch in /chat can route through
            # ImageAnalyzer. Validation is deliberately lenient here
            # -- the chat path checks the resolved paths before use.
            if role == "user":
                imgs = m.get("images")
                if isinstance(imgs, list) and imgs:
                    entry["images"] = imgs
            out.append(entry)
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
    raw_params = body.get("params")
    config = _build_config(raw_params)

    llm = _get_llm(model_path, raw_params)
    loop = asyncio.get_running_loop()

    # Multimodal route: if the latest user message has an image and the
    # caller provided an mmproj path, hand off to ImageAnalyzer. This
    # bypasses LLM.chat() entirely -- ImageAnalyzer.answer_question is
    # a single-shot path that returns a string, so we yield the result
    # as one SSE chunk rather than streaming tokens. (Streaming the
    # multimodal answer would need the lower-level VisionLanguageChat
    # generator, which is more invasive; deferring until a user asks.)
    image_path = _latest_user_image_path(messages)
    mmproj_path = (body.get("mmproj_path") or "").strip()
    if image_path and mmproj_path:
        if not os.path.isfile(image_path):
            raise HTTPException(400, f"image not found: {image_path}")
        # Sandbox: only paths under UPLOADS_DIR are accepted, so a
        # malicious renderer can't ask the analyzer to ingest /etc/passwd
        # via this route. Comparing against the resolved upload root
        # so symlinks-out-of-uploads are caught too.
        try:
            Path(image_path).resolve().relative_to(UPLOADS_DIR.resolve())
        except ValueError:
            raise HTTPException(400, "image must live under UPLOADS_DIR")
        analyzer = _get_image_analyzer(mmproj_path, llm)
        # The user's textual question. Trailing whitespace stripped so
        # the prompt the analyzer sees matches what the user typed.
        question = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        ).strip() or "Describe this image."

        async def mm_stream():
            try:
                # answer_question is synchronous + CPU/GPU-bound. Run on
                # the executor so the loop keeps servicing SSE consumers.
                # (TestClient note: this matches the transcribe pattern,
                # which works because the run_in_executor result feeds a
                # single emit -- no inter-await race window.)
                answer = await loop.run_in_executor(
                    None, analyzer.answer_question, question, image_path,
                )
            except Exception as exc:  # noqa: BLE001
                yield "data: " + json.dumps({"error": str(exc)}) + "\n\n"
                yield "data: [DONE]\n\n"
                return
            yield "data: " + json.dumps({"text": str(answer)}) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(mm_stream(), media_type="text/event-stream")

    # Always go through llm.chat() now -- it handles single-turn and
    # multi-turn uniformly via the model's chat template (cyllama's
    # Jinja path covers Gemma's "system role not supported" trap and
    # similar GGUF template quirks). Strip the multimodal ``images``
    # field before handing off; cyllama's chat templater hasn't
    # historically been forgiving of unexpected dict keys.
    text_only_messages = [
        {"role": m["role"], "content": m["content"]} for m in messages
    ]

    def _stream():
        return llm.chat(text_only_messages, stream=True, config=config)

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
# Shared helpers used by /hardware/* and /models/*.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Hardware: VRAM-aware GPU layer estimation.
# ---------------------------------------------------------------------------


@app.post("/hardware/estimate-layers")
async def hardware_estimate_layers(req: Request):
    """Wrap ``cyllama.estimate_gpu_layers`` so the renderer can suggest
    a sensible ``n_gpu_layers`` for a given (model, VRAM) pair without
    shelling out.

    Body: ``{model_path, gpu_memory_mb, ctx_size?, batch_size?,
              n_parallel?, kv_cache_type?, use_mmap?}``.
    Returns the helper's MemoryEstimate as a JSON-friendly dict, plus a
    flat ``n_gpu_layers`` for one-line consumption. Returns 501 with a
    structured envelope if the helper is missing in the installed
    cyllama (older releases).
    """
    body = await req.json()
    model_path = body.get("model_path")
    if not model_path or not os.path.isfile(model_path):
        raise HTTPException(400, "model_path required and must exist")
    gpu_memory_mb = body.get("gpu_memory_mb")
    if gpu_memory_mb is None:
        raise HTTPException(400, "gpu_memory_mb required")
    helper = getattr(cyllama, "estimate_gpu_layers", None)
    if helper is None:
        raise HTTPException(501, "cyllama.estimate_gpu_layers not available in this build")
    # Accept int or list[int]; helper handles both.
    if isinstance(gpu_memory_mb, list):
        gpu_memory_mb = [int(x) for x in gpu_memory_mb]
    else:
        gpu_memory_mb = int(gpu_memory_mb)

    kwargs = {"gpu_memory_mb": gpu_memory_mb}
    for key in ("ctx_size", "batch_size", "n_parallel", "kv_cache_type", "use_mmap"):
        if key in body and body[key] not in (None, ""):
            kwargs[key] = body[key]

    try:
        est = helper(model_path, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"estimate_gpu_layers failed: {exc}")

    # cyllama.memory.MemoryEstimate is a dataclass-ish object; coerce
    # known fields then fall back to vars() for anything custom.
    out: dict[str, Any] = {}
    for attr in (
        "n_gpu_layers", "n_layers_total", "model_size_mb",
        "kv_cache_mb", "compute_buffer_mb", "fits_fully", "notes",
    ):
        if hasattr(est, attr):
            out[attr] = _jsonify(getattr(est, attr))
    if not out:
        try:
            out = _jsonify(vars(est))
        except TypeError:
            out = {"raw": str(est)}
    return out


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


_MODEL_EXTS: tuple[str, ...] = (".gguf", ".bin")


def _scan_gguf(root: Path, source: str) -> list[dict]:
    """Scan a directory for cyllama-compatible model files.

    Returns ``[{path, name, size, source, dir, kind}]`` items. Each
    file is classified by ``_classify_model`` so the renderer's
    pickers can filter to the kinds they actually support (a chat
    picker shouldn't surface an mmproj projector, etc.).

    .gguf is the dominant format; .bin is the conventional whisper
    suffix and rarely anything else, so we widen the scan to it for
    the Transcribe pane to reach unconverted whisper models. Other
    extensions (.safetensors) aren't enumerated yet -- cyllama-side
    SD models in the desktop are .gguf in 0.2.x.
    """
    out: list[dict] = []
    if not root.exists():
        return out
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() not in _MODEL_EXTS:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            # Resolve symlinks so the same file accessible via two
            # roots (primary + an extra symlinked into it) dedups on
            # absolute path. Without this the user sees the same
            # model listed twice with conflicting ``source`` tags.
            try:
                resolved = p.resolve()
            except OSError:
                resolved = p
            kind = _classify_model_cached(p, st.st_size, st.st_mtime)
            out.append({
                "path": str(resolved),
                "name": p.name,
                "size": st.st_size,
                "source": source,
                "dir": str(resolved.parent),
                "kind": kind,
            })
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------
# Model classification.
#
# The renderer surfaces ~12 model pickers (chat, mmproj, whisper, SD,
# embedding, etc.) and used to dump every .gguf/.bin into each one.
# Capability gating works in two passes:
#
#   1. cheap signals (filename, extension) -- catches the conventional
#      names without paying for GGUF inspection;
#   2. ``GGUFContext`` metadata -- definitive when (1) is ambiguous;
#      cached per (path, mtime) so a directory full of files isn't
#      re-inspected on every dropdown open.
#
# Returns one of:
#   "chat"      -- text generation (default for unrecognised LLM archs)
#   "mmproj"    -- vision projector for LLAVA-style multimodal
#   "embedding" -- embedding model (BERT-family + similar)
#   "whisper"   -- whisper.cpp model
#   "sd"        -- stable-diffusion (rare in .gguf; mostly safetensors)
#   "unknown"   -- classification failed (e.g. unreadable GGUF)
# ---------------------------------------------------------------------------


# Embedding-family architectures that GGUFs commonly advertise. Anything
# in this set (case-insensitive substring) gets classified as embedding;
# everything else under ``general.architecture`` falls through to chat.
_EMBEDDING_ARCH_HINTS: tuple[str, ...] = (
    "bert", "nomic", "jina", "xlm-roberta", "gte", "e5", "mxbai",
    "stella", "snowflake-arctic", "arctic-embed",
)

# GGUF metadata key prefixes that signal an embedding-shaped model
# even when ``general.architecture`` is unhelpful.
_EMBEDDING_META_HINTS: tuple[str, ...] = (
    ".pooling_type",  # e.g. ``bert.pooling_type``
)

# Per-path classification cache: ``path -> (mtime, kind)``. Bypassed
# (and refreshed) when the file's mtime changes, so a quantize that
# rewrites the destination is reclassified next scan.
_KIND_CACHE: dict[str, tuple[float, str]] = {}


def _classify_model_cached(path: Path, size: int, mtime: float) -> str:
    spath = str(path)
    cached = _KIND_CACHE.get(spath)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    kind = _classify_model(path, size)
    _KIND_CACHE[spath] = (mtime, kind)
    return kind


def _classify_model(path: Path, size: int) -> str:  # noqa: ARG001
    name = path.name.lower()
    ext = path.suffix.lower()

    # Cheap filename signals first -- mmproj projectors are conventionally
    # named ``mmproj-*.gguf`` and shipping that pattern is so widespread
    # we treat it as authoritative without inspecting the file.
    if "mmproj" in name:
        return "mmproj"

    # Whisper models in the wild are nearly always ``.bin`` (the
    # whisper.cpp convention). cyllama supports them at this extension.
    # If a user converted to ``.gguf`` it'll be re-checked via metadata.
    if ext == ".bin":
        return "whisper"

    if ext == ".gguf":
        # Inspect via GGUF metadata. ``_resolve_gguf_context`` already
        # caches the class lookup; the open itself is fast (memory-maps
        # the header). Failure -> "unknown" so the picker still surfaces
        # the file behind a "Show all" / unknown bucket and the user
        # can opt to use it anyway.
        GGUFContext = _resolve_gguf_context()
        if GGUFContext is None:
            return "chat"  # presume chat in the absence of better data
        try:
            ctx = None
            for ctor in (
                lambda: GGUFContext.from_file(str(path)),
                lambda: GGUFContext(str(path)),
            ):
                try:
                    ctx = ctor()
                    break
                except (TypeError, AttributeError):
                    continue
            if ctx is None:
                return "unknown"
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
            if not isinstance(meta, dict):
                return "unknown"
            arch = str(meta.get("general.architecture") or "").lower()

            # Architecture-led classification. The order matters: the
            # mmproj architecture is sometimes ``clip`` standalone, but
            # the filename heuristic above usually catches it first.
            if "clip" in arch and "llama" not in arch:
                return "mmproj"
            if "whisper" in arch:
                return "whisper"
            if "stable-diffusion" in arch or arch in ("sdxl", "sd3", "flux"):
                return "sd"
            if any(h in arch for h in _EMBEDDING_ARCH_HINTS):
                return "embedding"

            # Secondary embedding signal: a pooling_type key under the
            # arch namespace (BERT-style models often advertise this).
            if any(any(s in str(k) for s in _EMBEDDING_META_HINTS) for k in meta):
                return "embedding"

            # Default: any GGUF with a recognisable architecture and no
            # embedding/multimodal markers is assumed to be a chat model.
            return "chat" if arch else "unknown"
        except Exception:  # noqa: BLE001
            return "unknown"

    return "unknown"


@app.get("/models/cached")
def models_cached(kinds: Optional[str] = None):
    """List cyllama-compatible model files we know about.

    Items from MODELS_DIR are flagged ``source: "local"``; items from the
    HF cache(s) are ``source: "hf"``. Same path appearing in multiple
    sources is deduped on absolute path with local taking precedence.
    Each item carries a ``kind`` field (see ``_classify_model``).

    Optional ``kinds`` query param: a comma-separated subset of
    ``{"chat","mmproj","embedding","whisper","sd","unknown"}``. When
    set, the response only includes items whose kind is in the set.
    ``unknown`` always passes the filter regardless of whether it
    was explicitly listed -- a misclassified model shouldn't lock
    the user out of a picker. Pass ``kinds=all`` (or omit) to return
    everything as-is.
    """
    seen: dict[str, dict] = {}
    # Precedence: local > external > hf. ``setdefault`` skips a
    # later source when the same absolute path already showed up
    # under an earlier (more authoritative) source.
    for item in _scan_gguf(MODELS_DIR, "local"):
        seen[item["path"]] = item
    for extra in MODELS_EXTRA:
        for item in _scan_gguf(extra, "external"):
            seen.setdefault(item["path"], item)
    for cache in _HF_CACHE_DIRS:
        for item in _scan_gguf(cache, "hf"):
            seen.setdefault(item["path"], item)
    # Sort: local first, then external, then hf; alphabetic within.
    _SOURCE_ORDER = {"local": 0, "external": 1, "hf": 2}
    items = sorted(
        seen.values(),
        key=lambda m: (_SOURCE_ORDER.get(m["source"], 9), m["name"].lower()),
    )

    if kinds and kinds != "all":
        wanted = {k.strip().lower() for k in kinds.split(",") if k.strip()}
        items = [m for m in items if m.get("kind") == "unknown" or m.get("kind") in wanted]
    return {"models": items, "models_dir": str(MODELS_DIR)}


_GGUF_CONTEXT = None
_GGUF_CONTEXT_RESOLVED = False


def _resolve_gguf_context():
    """Find ``GGUFContext`` across cyllama versions.

    0.2.15 doesn't re-export it at the top-level ``cyllama`` namespace; it
    lives at ``cyllama.llama.llama_cpp``. Older / newer versions may
    expose it elsewhere. Resolve once and cache (including the negative
    case) so we don't repeatedly walk the import graph.
    """
    global _GGUF_CONTEXT, _GGUF_CONTEXT_RESOLVED
    if _GGUF_CONTEXT_RESOLVED:
        return _GGUF_CONTEXT
    candidates = (
        ("cyllama", "GGUFContext"),
        ("cyllama.llama.llama_cpp", "GGUFContext"),
        ("cyllama.llama", "GGUFContext"),
    )
    for mod_name, attr in candidates:
        try:
            mod = importlib.import_module(mod_name)
        except Exception:
            continue
        cls = getattr(mod, attr, None)
        if cls is not None:
            _GGUF_CONTEXT = cls
            break
    _GGUF_CONTEXT_RESOLVED = True
    return _GGUF_CONTEXT


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

    GGUFContext = _resolve_gguf_context()
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


@app.post("/models/import")
async def models_import(req: Request):
    """Copy a GGUF file from an arbitrary local path into MODELS_DIR.

    Used by drag-drop in the Models tab. We do a server-side copy
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


# ---------------------------------------------------------------------------
# RAG (Phase 4 slice 1): collections registry + ingest job. Query and the
# Documents pane land in subsequent slices.
#
# Per-collection state lives at RAG_DIR/<id>.sqlite (cyllama
# SqliteVectorStore). RAG_DIR/collections.json carries id, display name,
# embedding model path, doc/chunk counts. Atomic writes via tmp+rename.
#
# The cyllama RAG class needs both an embedding model and a generation
# model. Ingest only needs embeddings, so we use Embedder + SqliteVector-
# Store directly (skipping RAG) to avoid loading an LLM during ingest.
# Query (next slice) will reach for the full RAG class.
# ---------------------------------------------------------------------------
_RAG_MANIFEST = RAG_DIR / "collections.json"
_RAG_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _rag_manifest_load() -> dict:
    if not _RAG_MANIFEST.exists():
        return {"version": 1, "collections": []}
    try:
        return json.loads(_RAG_MANIFEST.read_text("utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "collections": []}


def _rag_manifest_save(m: dict) -> None:
    tmp = _RAG_MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2), "utf-8")
    tmp.replace(_RAG_MANIFEST)


def _rag_collection_get(coll_id: str) -> Optional[dict]:
    for c in _rag_manifest_load().get("collections", []):
        if c.get("id") == coll_id:
            return c
    return None


@app.get("/rag/collections")
async def rag_collections_list():
    m = _rag_manifest_load()
    return {"collections": m.get("collections", []), "rag_dir": str(RAG_DIR)}


@app.post("/rag/collections")
async def rag_collections_create(req: Request):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name required")
    embedding_model_path = body.get("embedding_model_path") or ""
    if not embedding_model_path or not os.path.isfile(embedding_model_path):
        raise HTTPException(400, "embedding_model_path required and must exist")

    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "coll"
    coll_id = f"{base}-{uuid.uuid4().hex[:8]}"
    sqlite_path = RAG_DIR / f"{coll_id}.sqlite"

    now = int(time.time())
    record = {
        "id": coll_id,
        "name": name,
        "embedding_model_path": embedding_model_path,
        "sqlite_path": str(sqlite_path),
        "created_at": now,
        "updated_at": now,
        "doc_count": 0,
        "chunk_count": 0,
    }
    m = _rag_manifest_load()
    m.setdefault("collections", []).append(record)
    _rag_manifest_save(m)
    return record


@app.delete("/rag/collections/{coll_id}")
async def rag_collections_delete(coll_id: str):
    if not _RAG_ID_RE.match(coll_id):
        raise HTTPException(400, "invalid collection id")
    m = _rag_manifest_load()
    before = len(m.get("collections", []))
    m["collections"] = [c for c in m.get("collections", []) if c.get("id") != coll_id]
    if len(m["collections"]) == before:
        raise HTTPException(404, "collection not found")
    _rag_manifest_save(m)
    sqlite = RAG_DIR / f"{coll_id}.sqlite"
    try:
        sqlite.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


# Single-slot cache for an opened RAG. Keyed by
# ``(collection_id, generation_model_path)``; the embedding model is fixed
# per collection so it doesn't enter the key. Different (collection, gen
# model) combos evict the prior instance because cyllama RAG holds two
# GGUF models in memory.
_RAG_INSTANCE: dict = {"key": None, "rag": None}


def _close_rag_instance() -> None:
    rag = _RAG_INSTANCE.get("rag")
    if rag is None:
        return
    for closer in ("close",):
        try:
            getattr(rag, closer)()
        except Exception:  # noqa: BLE001
            pass
    _RAG_INSTANCE["rag"] = None
    _RAG_INSTANCE["key"] = None


def _get_rag(collection: dict, generation_model_path: str):
    """Open (or reuse) a cyllama RAG bound to this collection's sqlite."""
    rag_mod = importlib.import_module("cyllama.rag")
    RAG = rag_mod.RAG
    key = (collection["id"], generation_model_path)
    if _RAG_INSTANCE.get("key") == key and _RAG_INSTANCE.get("rag") is not None:
        return _RAG_INSTANCE["rag"]
    _close_rag_instance()
    rag = RAG(
        embedding_model=collection["embedding_model_path"],
        generation_model=generation_model_path,
        db_path=collection["sqlite_path"],
    )
    _RAG_INSTANCE["key"] = key
    _RAG_INSTANCE["rag"] = rag
    return rag


def _build_rag_config(body: dict):
    """Translate query body fields into a ``cyllama.rag.RAGConfig``.

    Only the subset we expose to the renderer; ``RAGConfig`` accepts more
    but the rest are left at cyllama defaults.
    """
    rag_mod = importlib.import_module("cyllama.rag")
    RAGConfig = rag_mod.RAGConfig
    kwargs = {}
    for src_key, dst_key, caster in (
        ("top_k", "top_k", int),
        ("similarity_threshold", "similarity_threshold", float),
        ("max_tokens", "max_tokens", int),
        ("temperature", "temperature", float),
        ("system_prompt", "system_prompt", str),
    ):
        v = body.get(src_key)
        if v is None:
            continue
        try:
            kwargs[dst_key] = caster(v)
        except (TypeError, ValueError):
            continue
    return RAGConfig(**kwargs) if kwargs else None


def _serialize_sources(sources) -> list[dict]:
    out = []
    for s in sources or []:
        out.append({
            "id": getattr(s, "id", None),
            "text": getattr(s, "text", ""),
            "score": float(getattr(s, "score", 0.0) or 0.0),
            "metadata": _jsonify(getattr(s, "metadata", {}) or {}),
        })
    return out


@app.post("/rag/query")
async def rag_query(req: Request):
    """Stream a RAG answer over SSE.

    Body: ``{collection_id, generation_model_path, question, top_k?,
    similarity_threshold?, max_tokens?, temperature?, system_prompt?}``.

    Wire shape mirrors ``/chat``: each token chunk is
    ``data: {"text": "..."}\\n\\n``, prefixed by exactly one
    ``data: {"sources": [...]}\\n\\n`` event. Errors emit
    ``data: {"error": "..."}\\n\\n``. Stream ends with
    ``data: [DONE]\\n\\n``.
    """
    body = await req.json()
    coll_id = body.get("collection_id") or ""
    gen_path = body.get("generation_model_path") or ""
    question = (body.get("question") or "").strip()

    if not _RAG_ID_RE.match(coll_id):
        raise HTTPException(400, "invalid collection id")
    coll = _rag_collection_get(coll_id)
    if coll is None:
        raise HTTPException(404, "collection not found")
    if not gen_path or not os.path.isfile(gen_path):
        raise HTTPException(400, "generation_model_path required and must exist")
    if not question:
        raise HTTPException(400, "question required")

    rag = _get_rag(coll, gen_path)
    config = _build_rag_config(body)
    loop = asyncio.get_running_loop()

    async def event_stream():
        # Sources first (one round-trip's worth of retrieval). Note: cyllama
        # ``RAG.stream`` does its own retrieval internally, so this is
        # effectively a duplicate fetch -- kept for v1 simplicity, optimise
        # later if cyllama exposes a "use these sources" path.
        try:
            sources = rag.retrieve(question, config) if config else rag.retrieve(question)
            yield "data: " + json.dumps({"sources": _serialize_sources(sources)}) + "\n\n"
        except Exception as exc:  # noqa: BLE001
            yield "data: " + json.dumps({"error": f"retrieve failed: {exc}"}) + "\n\n"
            yield "data: [DONE]\n\n"
            return

        # Then stream tokens via a producer thread, same shape as /chat.
        q: asyncio.Queue = asyncio.Queue()
        DONE = object()

        def producer():
            try:
                gen = rag.stream(question, config) if config else rag.stream(question)
                for chunk in gen:
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
                yield "data: " + json.dumps({"text": str(item)}) + "\n\n"
        except asyncio.CancelledError:
            # Client disconnected. Best-effort cancel of the underlying LLM
            # if RAG exposes one; otherwise the producer thread runs to
            # completion against a queue nobody's reading.
            inner_llm = getattr(rag, "llm", None) or getattr(rag, "_llm", None)
            if inner_llm is not None:
                try:
                    inner_llm.cancel()
                except Exception:  # noqa: BLE001
                    pass
            raise

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# Retrieve-only cache: Embedder + SqliteVectorStore for a collection.
# Distinct from ``_RAG_INSTANCE`` because retrieve-only never loads a
# generation model (cheap path used by the chat-side context injection).
_RETRIEVE_INSTANCE: dict = {"key": None, "embedder": None, "store": None}


def _close_retrieve_instance() -> None:
    for k in ("embedder", "store"):
        obj = _RETRIEVE_INSTANCE.get(k)
        if obj is None:
            continue
        try:
            obj.close()
        except Exception:  # noqa: BLE001
            pass
        _RETRIEVE_INSTANCE[k] = None
    _RETRIEVE_INSTANCE["key"] = None


def _get_retrieve(collection: dict):
    rag_mod = importlib.import_module("cyllama.rag")
    Embedder = rag_mod.Embedder
    SqliteVectorStore = rag_mod.SqliteVectorStore
    coll_id = collection["id"]
    if _RETRIEVE_INSTANCE.get("key") == coll_id and _RETRIEVE_INSTANCE.get("embedder") is not None:
        return _RETRIEVE_INSTANCE["embedder"], _RETRIEVE_INSTANCE["store"]
    _close_retrieve_instance()
    embedder = Embedder(model_path=collection["embedding_model_path"])
    store = SqliteVectorStore(dimension=embedder.dimension, db_path=collection["sqlite_path"])
    _RETRIEVE_INSTANCE["key"] = coll_id
    _RETRIEVE_INSTANCE["embedder"] = embedder
    _RETRIEVE_INSTANCE["store"] = store
    return embedder, store


@app.post("/rag/retrieve")
async def rag_retrieve(req: Request):
    """Retrieve-only RAG endpoint. No LLM involved.

    Body: ``{collection_id, query, top_k?, similarity_threshold?}``.
    Returns ``{sources: [{id, text, score, metadata}, ...]}``.

    The chat send path uses this to attach context to a user turn without
    pulling a second generation model into memory.
    """
    body = await req.json()
    coll_id = body.get("collection_id") or ""
    q = (body.get("query") or "").strip()
    top_k = max(1, int(body.get("top_k") or 3))
    threshold = body.get("similarity_threshold")
    threshold = float(threshold) if threshold is not None else None

    if not _RAG_ID_RE.match(coll_id):
        raise HTTPException(400, "invalid collection id")
    coll = _rag_collection_get(coll_id)
    if coll is None:
        raise HTTPException(404, "collection not found")
    if not q:
        raise HTTPException(400, "query required")

    embedder, store = _get_retrieve(coll)
    vecs = embedder.embed_batch([q])
    if not vecs:
        return {"sources": []}
    results = store.search(vecs[0], k=top_k, threshold=threshold)
    return {"sources": _serialize_sources(results)}


register_job_kind("rag.ingest")


@app.post("/jobs/rag.ingest")
async def jobs_rag_ingest(req: Request):
    """Ingest documents into a RAG collection.

    Body: ``{collection_id, paths: [str], glob?: str, chunk_size?: int,
    chunk_overlap?: int}``. Each path may be a file or a directory; dirs
    are scanned with ``load_directory(glob=...)``.
    """
    body = await req.json()
    coll_id = body.get("collection_id") or ""
    paths = body.get("paths") or []
    glob = body.get("glob") or "**/*"
    chunk_size = int(body.get("chunk_size") or 512)
    chunk_overlap = int(body.get("chunk_overlap") or 50)

    if not _RAG_ID_RE.match(coll_id):
        raise HTTPException(400, "invalid collection id")
    coll = _rag_collection_get(coll_id)
    if coll is None:
        raise HTTPException(404, "collection not found")
    if not isinstance(paths, list) or not paths:
        raise HTTPException(400, "paths required (non-empty list)")
    if not all(isinstance(p, str) and p for p in paths):
        raise HTTPException(400, "paths must be non-empty strings")

    embedding_model_path = coll["embedding_model_path"]
    sqlite_path = coll["sqlite_path"]

    async def producer(job: Job) -> None:
        rag_mod = importlib.import_module("cyllama.rag")
        Embedder = rag_mod.Embedder
        SqliteVectorStore = rag_mod.SqliteVectorStore
        TextSplitter = rag_mod.TextSplitter
        load_directory = rag_mod.load_directory
        load_document = rag_mod.load_document

        # Note: cyllama ops below are synchronous and CPU/IO-bound. We keep
        # them on the event loop because (a) ingest is a coarse-grained job
        # already isolated by the /jobs machinery, (b) FastAPI TestClient's
        # portal model interacts poorly with ``asyncio.to_thread`` for
        # fire-and-forget background tasks, and (c) per-batch ``await``
        # points below give the loop room to service SSE consumers.
        await _emit(job, {"type": "log", "message": "loading documents..."})
        docs = []
        for p in paths:
            pth = Path(p)
            if not pth.exists():
                await _emit(job, {"type": "log", "message": f"skip (missing): {p}"})
                continue
            if pth.is_dir():
                docs.extend(load_directory(str(pth), glob=glob))
            else:
                docs.extend(load_document(str(pth)))
        if not docs:
            raise RuntimeError("no documents loaded")
        await _emit(job, {"type": "log", "message": f"{len(docs)} documents loaded"})

        splitter = TextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        chunks = splitter.split_documents(docs)
        if not chunks:
            raise RuntimeError("no chunks produced")
        await _emit(job, {"type": "log", "message": f"{len(chunks)} chunks"})

        embedder = Embedder(model_path=embedding_model_path)
        store = None
        try:
            store = SqliteVectorStore(dimension=embedder.dimension, db_path=sqlite_path)
            BATCH = 32
            n_added = 0
            total = len(chunks)
            for i in range(0, total, BATCH):
                batch = chunks[i:i + BATCH]
                texts = [c.text for c in batch]
                metas = []
                for c in batch:
                    md = dict(getattr(c, "metadata", {}) or {})
                    md["chunk_index"] = getattr(c, "chunk_index", 0)
                    if getattr(c, "source_id", None) is not None:
                        md["source_id"] = c.source_id
                    metas.append(md)
                vecs = embedder.embed_batch(texts)
                store.add(vecs, texts, metas)
                n_added += len(batch)
                await _emit(job, {
                    "type": "progress",
                    "value": n_added / total,
                    "added": n_added,
                    "total": total,
                })
        finally:
            for closeable in (embedder, store):
                if closeable is None:
                    continue
                try:
                    closeable.close()
                except Exception:  # noqa: BLE001
                    pass

        m2 = _rag_manifest_load()
        for c in m2.get("collections", []):
            if c.get("id") == coll_id:
                c["doc_count"] = int(c.get("doc_count", 0)) + len(docs)
                c["chunk_count"] = int(c.get("chunk_count", 0)) + total
                c["updated_at"] = int(time.time())
                break
        _rag_manifest_save(m2)

        await _emit(job, {
            "type": "result",
            "result": {
                "collection_id": coll_id,
                "documents": len(docs),
                "chunks": total,
            },
        })

    job = await run_job("rag.ingest", producer)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Transcribe: WAV -> Whisper segments. Long-running, so funnels through the
# /jobs machinery; per-segment events arrive on /jobs/<id>/events as
# ``{type:"segment", index, t0_ms, t1_ms, text}`` interleaved with the
# usual progress / log frames. Final ``result`` carries the full segment
# list so a late subscriber can reconstruct without replaying the stream.
# ---------------------------------------------------------------------------


register_job_kind("transcribe")


@app.post("/jobs/transcribe")
async def jobs_transcribe(req: Request):
    if not _FEATURE_FLAGS.get("whisper"):
        raise HTTPException(501, "whisper not available in this cyllama build")
    body = await req.json()
    audio_path = body.get("audio_path") or ""
    model_path = body.get("model_path") or ""
    if not audio_path or not os.path.isfile(audio_path):
        raise HTTPException(400, "audio_path required and must exist")
    if not model_path or not os.path.isfile(model_path):
        raise HTTPException(400, "model_path required and must exist")
    # Optional fields. Empty / None means "let whisper auto-detect".
    language = (body.get("language") or "").strip() or None
    translate = bool(body.get("translate", False))
    n_threads = body.get("n_threads")
    try:
        n_threads = int(n_threads) if n_threads not in (None, "") else None
    except (TypeError, ValueError):
        n_threads = None

    async def producer(job: Job) -> None:
        loop = asyncio.get_running_loop()

        # 1. Load + resample audio. The cyllama CLI helper is wave-only;
        # non-WAV inputs are surfaced with a typed error so the renderer
        # can suggest re-encoding rather than crashing.
        suffix = Path(audio_path).suffix.lower()
        if suffix not in (".wav", ".wave"):
            raise RuntimeError(
                f"only WAV input supported in this build; got '{suffix}'. "
                f"Re-encode with ffmpeg: ffmpeg -i in -ar 16000 -ac 1 out.wav"
            )
        await _emit(job, {"type": "log", "message": "loading audio..."})
        samples, sr = _WHISPER_LOAD_WAV(audio_path)
        if _WHISPER_RESAMPLE is not None and sr != 16000:
            await _emit(job, {
                "type": "log",
                "message": f"resampling {sr} -> 16000 Hz",
            })
            samples = _WHISPER_RESAMPLE(samples, sr, 16000)

        # 2. Build whisper params + context. ``no_timestamps`` stays
        # False so per-segment t0/t1 are populated; ``print_*`` flags
        # are off so the C library doesn't spam stdout (the Electron
        # host pipes the sidecar's stdout into the Console panel, and
        # noisy output drowns useful log lines).
        ctx_params = _WHISPER_CTX_PARAMS_CLS() if _WHISPER_CTX_PARAMS_CLS else None
        full_params = _WHISPER_FULL_PARAMS_CLS()
        for attr, val in (
            ("print_progress", False),
            ("print_realtime", False),
            ("print_timestamps", False),
            ("print_special", False),
            ("translate", translate),
            ("no_timestamps", False),
        ):
            if hasattr(full_params, attr):
                try:
                    setattr(full_params, attr, val)
                except (AttributeError, TypeError):
                    pass
        if language and hasattr(full_params, "language"):
            try:
                full_params.language = language
            except (AttributeError, TypeError):
                pass
        if n_threads and hasattr(full_params, "n_threads"):
            try:
                full_params.n_threads = n_threads
            except (AttributeError, TypeError):
                pass

        # 3. Run transcription on a worker thread so the event loop stays
        # responsive for SSE consumers. Segments are extracted after
        # full() returns -- whisper.cpp's progress callback fires from
        # the C side, but cross-thread SSE emission from there gets
        # complicated; one bulk emit at the end is enough for typical
        # short-clip use, with periodic progress logged from this side.
        await _emit(job, {"type": "log", "message": "transcribing..."})
        ctx_args = (model_path,) if ctx_params is None else (model_path, ctx_params)
        ctx = _WHISPER_CTX_CLS(*ctx_args)
        try:
            await loop.run_in_executor(None, ctx.full, samples, full_params)

            # 4. Emit per-segment events + accumulate into a final result.
            n = ctx.full_n_segments()
            segments: list[dict] = []
            for i in range(n):
                t0 = ctx.full_get_segment_t0(i)  # 10 ms units
                t1 = ctx.full_get_segment_t1(i)
                text = ctx.full_get_segment_text(i)
                seg = {
                    "index": i,
                    # Convert whisper's 10 ms units to absolute milliseconds
                    # so the renderer doesn't have to know the unit.
                    "t0_ms": int(t0) * 10,
                    "t1_ms": int(t1) * 10,
                    "text": (text or "").strip(),
                }
                segments.append(seg)
                await _emit(job, {"type": "segment", **seg})
                if n:
                    await _emit(job, {
                        "type": "progress",
                        "value": (i + 1) / n,
                    })
            detected_lang = None
            try:
                lid = ctx.full_lang_id()
                if hasattr(ctx, "lang_str"):
                    detected_lang = ctx.lang_str(lid)
            except Exception:  # noqa: BLE001
                pass
            await _emit(job, {
                "type": "result",
                "result": {
                    "segments": segments,
                    "n_segments": len(segments),
                    "language": detected_lang,
                },
            })
        finally:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001
                pass

    job = await run_job("transcribe", producer)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Image: stable-diffusion text-to-image. Long-running so it goes through
# the /jobs machinery; on success the rendered PNG lives at
# ``<ARTIFACTS_DIR>/<job_id>/output.png`` and the job's artifact_path
# points at the parent so the existing /jobs/<id>/artifact/<name>
# endpoint serves it.
# ---------------------------------------------------------------------------


register_job_kind("image.txt2img")


# Bounds clamp client-supplied dimensions before they reach cyllama.
# The C library will accept arbitrary sizes but a 16k x 16k request will
# silently OOM the host long before any error surfaces; clamp at a
# generous-but-finite ceiling here.
_IMG_MIN_DIM = 64
_IMG_MAX_DIM = 4096
_IMG_MIN_STEPS = 1
_IMG_MAX_STEPS = 200


def _clamp_int(v, lo, hi, default):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


@app.post("/jobs/image/txt2img")
async def jobs_image_txt2img(req: Request):
    if not _FEATURE_FLAGS.get("image"):
        raise HTTPException(501, "stable-diffusion not available in this cyllama build")
    body = await req.json()
    model_path = (body.get("model_path") or "").strip()
    prompt = body.get("prompt") or ""
    if not model_path or not os.path.isfile(model_path):
        raise HTTPException(400, "model_path required and must exist")
    if not isinstance(prompt, str) or not prompt.strip():
        raise HTTPException(400, "prompt required")

    negative = body.get("negative_prompt") or ""
    width  = _clamp_int(body.get("width"),  _IMG_MIN_DIM, _IMG_MAX_DIM, 512)
    height = _clamp_int(body.get("height"), _IMG_MIN_DIM, _IMG_MAX_DIM, 512)
    steps  = _clamp_int(body.get("sample_steps"), _IMG_MIN_STEPS, _IMG_MAX_STEPS, 20)
    try:
        cfg_scale = float(body.get("cfg_scale", 7.0))
    except (TypeError, ValueError):
        cfg_scale = 7.0
    try:
        seed = int(body.get("seed", -1))
    except (TypeError, ValueError):
        seed = -1

    async def producer(job: Job) -> None:
        # Per-job artifact dir; matches the layout expected by
        # /jobs/<id>/artifact/<name>.
        out_dir = (ARTIFACTS_DIR / job.id).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "output.png"

        await _emit(job, {"type": "log", "message": "loading model + generating..."})

        # cyllama's text_to_image is synchronous and CPU/GPU-bound, but
        # we keep it on the event loop here for the same reason RAG
        # ingest does: FastAPI TestClient's portal model cancels the
        # producer task at request boundaries, so an ``await
        # loop.run_in_executor(...)`` mid-job races the boundary and
        # the result event never lands. Production runs are gated by
        # the /jobs machinery so the synchronous call doesn't block
        # other endpoints (it'd block the SSE consumer for the same
        # job, but that's the job we're driving).
        try:
            image = _SD_TEXT_TO_IMAGE(
                model_path=model_path,
                prompt=prompt,
                negative_prompt=negative,
                width=width,
                height=height,
                seed=seed,
                sample_steps=steps,
                cfg_scale=cfg_scale,
            )
        except Exception as exc:
            raise RuntimeError(f"text_to_image failed: {exc}") from exc

        if image is None or not getattr(image, "is_valid", lambda: True)():
            raise RuntimeError("generator returned an invalid image")

        image.save_png(str(out_path))

        # Record both the directory (for /jobs/<id>/artifact/<name>) and
        # the file name in the result so the renderer doesn't need to
        # guess. ``artifact_url`` is a relative path; the renderer
        # composes the full URL with the sidecar's host+port+token.
        job.artifact_path = out_dir
        await _emit(job, {
            "type": "result",
            "result": {
                "artifact_name": out_path.name,
                "artifact_url": f"/jobs/{job.id}/artifact/{out_path.name}",
                "width": width,
                "height": height,
                "seed": seed,
                "sample_steps": steps,
                "cfg_scale": cfg_scale,
            },
        })

    job = await run_job("image.txt2img", producer)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Agents (Phase 7): /jobs/agent/run wraps ``cyllama.agents.ReActAgent``.
#
# The renderer picks which built-in tools to enable per run. The catalog is
# server-side because:
#   - tool callables run inside the sidecar's process (CPU + filesystem +
#     network) and we don't want them defined in the renderer where the
#     code path crosses an IPC boundary;
#   - sandbox / network gating live next to the implementations so review
#     stays in one place.
#
# Tools shipped:
#   calculator  -- safe arithmetic via ``ast.literal_eval`` of a small
#                  expression dialect. Always available.
#   read_file   -- read a UTF-8 text file under a user-chosen sandbox dir.
#                  Refuses path-escape; refuses files > 1 MiB.
#   web_fetch   -- HTTP GET. Off by default; the renderer must explicitly
#                  enable. Loopback-only safety net does NOT apply here --
#                  this is by design (web fetch is the point), but we cap
#                  response size and disallow non-http(s) schemes.
#   rag_query   -- retrieve top-K chunks from a chosen RAG collection.
# ---------------------------------------------------------------------------


register_job_kind("agent.run")


# Maximum bytes to read from the filesystem / pull from a URL. Larger reads
# blow up the LLM's context window before they help; cap conservatively.
_AGENT_MAX_FILE_BYTES = 1024 * 1024
_AGENT_MAX_FETCH_BYTES = 1024 * 1024


def _make_calculator_tool():
    import ast
    import operator

    _OPS = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
    }

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_eval(node.operand))
        raise ValueError("expression rejected by calculator sandbox")

    def calculator(expression: str) -> str:
        """Evaluate a simple arithmetic expression and return the result."""
        try:
            tree = ast.parse(str(expression), mode="eval")
            return str(_eval(tree.body))
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    return _AGENT_TOOL_CLS(
        name="calculator",
        description="Evaluate an arithmetic expression like '2 + 2 * 3'. Returns a number.",
        func=calculator,
        parameters={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
    )


def _make_read_file_tool(sandbox_root: Path):
    sandbox_root = sandbox_root.resolve()
    if not sandbox_root.is_dir():
        raise HTTPException(400, f"sandbox_dir not a directory: {sandbox_root}")

    def read_file(path: str) -> str:
        """Read a UTF-8 text file under the agent's sandbox dir."""
        # Reject anything that resolves outside the sandbox -- this is the
        # security boundary, do NOT loosen without a redesign. Symlinks
        # are followed by resolve() so a symlink-out-of-sandbox is also
        # caught here.
        try:
            target = (sandbox_root / path).resolve()
        except Exception:  # noqa: BLE001
            return "error: invalid path"
        try:
            target.relative_to(sandbox_root)
        except ValueError:
            return "error: refusing path outside sandbox"
        if not target.is_file():
            return f"error: not a file: {path}"
        try:
            size = target.stat().st_size
        except OSError as exc:
            return f"error: {exc}"
        if size > _AGENT_MAX_FILE_BYTES:
            return f"error: file too large ({size} bytes; cap {_AGENT_MAX_FILE_BYTES})"
        try:
            return target.read_text("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    return _AGENT_TOOL_CLS(
        name="read_file",
        description=f"Read a UTF-8 text file under {sandbox_root}. Path is relative to that dir.",
        func=read_file,
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )


def _make_web_fetch_tool():
    import httpx

    def web_fetch(url: str) -> str:
        """HTTP GET a URL and return the body (truncated)."""
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            return "error: only http(s) URLs"
        try:
            with httpx.Client(follow_redirects=True, timeout=20.0) as cl:
                # We let the server stream so we can stop at the byte cap
                # without buffering the full body.
                with cl.stream("GET", url) as r:
                    if r.status_code >= 400:
                        return f"error: HTTP {r.status_code}"
                    chunks: list[bytes] = []
                    received = 0
                    for c in r.iter_bytes(chunk_size=64 * 1024):
                        received += len(c)
                        if received > _AGENT_MAX_FETCH_BYTES:
                            chunks.append(c[: _AGENT_MAX_FETCH_BYTES - (received - len(c))])
                            break
                        chunks.append(c)
                    return b"".join(chunks).decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    return _AGENT_TOOL_CLS(
        name="web_fetch",
        description="HTTP GET a URL and return the response body (text, truncated to 1 MiB).",
        func=web_fetch,
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    )


def _make_rag_query_tool(collection: dict, top_k: int = 3):
    embedder, store = _get_retrieve(collection)

    def rag_query(query: str, k: int = top_k) -> str:
        """Retrieve top-k chunks from the configured RAG collection."""
        try:
            kk = max(1, min(20, int(k)))
        except (TypeError, ValueError):
            kk = top_k
        vecs = embedder.embed_batch([str(query)])
        if not vecs:
            return "error: failed to embed query"
        results = store.search(vecs[0], k=kk)
        srcs = _serialize_sources(results)
        if not srcs:
            return "(no matches)"
        return "\n\n".join(f"[{i+1}] {s['text']}" for i, s in enumerate(srcs))

    return _AGENT_TOOL_CLS(
        name="rag_query",
        description=f"Retrieve up to {top_k} relevant chunks from collection '{collection.get('name')}'.",
        func=rag_query,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "k": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["query"],
        },
    )


def _build_agent_tools(spec: dict) -> list:
    """Translate the renderer's ``tools`` spec into a list of ``Tool`` objects.

    Spec shape: ``{"calculator": true, "read_file": {"sandbox_dir": "..."},
    "web_fetch": true, "rag_query": {"collection_id": "...", "top_k": 3}}``.
    Unknown keys are ignored. Empty tool list is allowed -- the agent then
    runs as a plain reasoning loop without tool calls.
    """
    out: list = []
    if spec.get("calculator"):
        out.append(_make_calculator_tool())
    if "read_file" in spec:
        # Treat key-present as intent to enable -- a misconfigured tool
        # (no sandbox_dir) surfaces here as 400 rather than letting the
        # agent run without it. Renderer should drop the key entirely
        # when the user didn't pick a folder.
        rf = spec["read_file"] if isinstance(spec["read_file"], dict) else {}
        sandbox = rf.get("sandbox_dir") or ""
        if not sandbox:
            raise HTTPException(400, "read_file requires sandbox_dir")
        out.append(_make_read_file_tool(Path(sandbox)))
    if spec.get("web_fetch"):
        out.append(_make_web_fetch_tool())
    if "rag_query" in spec:
        rq = spec["rag_query"] if isinstance(spec["rag_query"], dict) else {}
        coll_id = rq.get("collection_id") or ""
        if not coll_id or not _RAG_ID_RE.match(coll_id):
            raise HTTPException(400, "rag_query requires a valid collection_id")
        coll = _rag_collection_get(coll_id)
        if coll is None:
            raise HTTPException(404, f"rag_query collection not found: {coll_id}")
        top_k = int(rq.get("top_k") or 3)
        out.append(_make_rag_query_tool(coll, top_k=top_k))
    return out


def _agent_event_type_name(ev) -> str:
    """Coerce an EventType enum (or anything string-y) to a plain name."""
    t = getattr(ev, "type", None)
    if t is None:
        return "UNKNOWN"
    n = getattr(t, "name", None)
    if n is not None:
        return str(n)
    return str(t)


@app.post("/jobs/agent/run")
async def jobs_agent_run(req: Request):
    if not _FEATURE_FLAGS.get("agents"):
        raise HTTPException(501, "agents not available in this cyllama build")
    body = await req.json()
    model_path = (body.get("model_path") or "").strip()
    task = body.get("task") or ""
    if not model_path or not os.path.isfile(model_path):
        raise HTTPException(400, "model_path required and must exist")
    if not isinstance(task, str) or not task.strip():
        raise HTTPException(400, "task required")
    system_prompt = body.get("system_prompt") or None
    try:
        max_iterations = max(1, min(50, int(body.get("max_iterations") or 10)))
    except (TypeError, ValueError):
        max_iterations = 10

    # Tool catalog wiring runs *before* the job is spawned so a misconfigured
    # tool (e.g. missing sandbox_dir) surfaces as a 400 rather than as an
    # error event mid-trace. The renderer can't fix this from the trace.
    tools = _build_agent_tools(body.get("tools") or {})

    # Cache the LLM the same way /chat does -- avoids reloading the model
    # for back-to-back agent runs against the same target.
    llm = _get_llm(model_path, body.get("params"))

    async def producer(job: Job) -> None:
        agent = _AGENT_REACT_CLS(
            llm=llm,
            tools=tools,
            system_prompt=system_prompt,
            max_iterations=max_iterations,
            verbose=False,
        )

        # cyllama's ``stream`` is synchronous and does its own LLM calls
        # under the hood. Same TestClient-portal constraint as /image:
        # offloading to an executor races the request boundary in tests.
        # The job is already coarse-grained, and per-event awaits below
        # give the loop room to service SSE consumers.
        events: list[dict] = []
        final_answer: Optional[str] = None
        try:
            it = iter(agent.stream(task))
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"agent.stream failed: {exc}") from exc

        for ev in it:
            etype = _agent_event_type_name(ev)
            content = getattr(ev, "content", "")
            metadata = getattr(ev, "metadata", {}) or {}
            ev_dict = {
                "event_type": etype,
                "content": str(content) if content is not None else "",
                "metadata": _jsonify(metadata),
            }
            events.append(ev_dict)
            if etype == "ANSWER":
                final_answer = ev_dict["content"]
            await _emit(job, {"type": "trace", **ev_dict})

        await _emit(job, {
            "type": "result",
            "result": {
                "events": events,
                "answer": final_answer,
                "iterations": sum(1 for e in events if e["event_type"] == "ACTION"),
            },
        })

    job = await run_job("agent.run", producer)
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# Phase 8 -- OpenAI-compatible server pane.
#
# A single slot holds the running server instance. Switching kinds or
# models requires an explicit /server/stop first; we do NOT auto-evict
# the way /chat does, because the user has consumers attached to the
# URL who would notice the model swap.
#
# Loopback-only by default. Listening on 0.0.0.0 requires
# ``expose_lan: true`` in the body AND server-side auditing of the
# decision (loopback check on the resolved host). PLAN.md S.10
# anticipates this; the path stays narrow on purpose.
# ---------------------------------------------------------------------------


_SERVER_LOCK = threading.Lock()
_SERVER_STATE: dict = {
    "running": False,
    "kind": None,
    "instance": None,
    "model_path": None,
    "host": None,
    "port": None,
}


def _server_status_payload() -> dict:
    """Strip the live instance handle out of state for JSON return."""
    s = dict(_SERVER_STATE)
    inst = s.pop("instance", None)
    s["url"] = (
        f"http://{s['host']}:{s['port']}" if s.get("running") and s.get("host") and s.get("port")
        else None
    )
    s["instance"] = None  # never expose the Python object reference
    del s["instance"]
    return s


@app.get("/server/status")
def server_status():
    return _server_status_payload()


@app.post("/server/start")
async def server_start(req: Request):
    if not _FEATURE_FLAGS.get("openai_server"):
        raise HTTPException(501, "openai-compatible server not available in this cyllama build")

    body = await req.json()
    kind = (body.get("kind") or "").strip().lower()
    if kind not in _SERVER_KINDS_AVAILABLE:
        raise HTTPException(400, f"kind must be one of {_SERVER_KINDS_AVAILABLE!r}")
    model_path = (body.get("model_path") or "").strip()
    if not model_path or not os.path.isfile(model_path):
        raise HTTPException(400, "model_path required and must exist")

    # Loopback gate: 0.0.0.0 / :: / external IP needs explicit opt-in.
    # The renderer's checkbox already prompts for confirmation; this
    # is the second gate (defence in depth -- a bypassed UI must still
    # be a deliberate act on the wire).
    expose_lan = bool(body.get("expose_lan", False))
    host = (body.get("host") or "").strip() or "127.0.0.1"
    if host not in ("127.0.0.1", "localhost", "::1") and not expose_lan:
        raise HTTPException(400, "non-loopback host requires expose_lan=true")
    if expose_lan and host in ("127.0.0.1", "localhost", "::1"):
        # User asked to expose but didn't change the host; promote to
        # all-interfaces so the toggle isn't silently a no-op.
        host = "0.0.0.0"

    try:
        port = int(body.get("port") or 8080)
    except (TypeError, ValueError):
        port = 8080
    if not (1 <= port <= 65535):
        raise HTTPException(400, "port out of range")

    # Optional load-time fields. Same shape as ServerConfig's
    # constructor; we pass through when the renderer sets them and
    # rely on the dataclass defaults otherwise.
    extra: dict = {}
    for k in ("n_ctx", "n_batch", "n_threads", "n_gpu_layers", "n_parallel", "model_alias"):
        v = body.get(k)
        if v in (None, ""):
            continue
        try:
            extra[k] = int(v) if k != "model_alias" else str(v)
        except (TypeError, ValueError):
            continue

    with _SERVER_LOCK:
        if _SERVER_STATE.get("running"):
            raise HTTPException(409, "server already running; stop it first")

        cls = _EMBEDDED_SERVER_CLS if kind == "embedded" else _PYTHON_SERVER_CLS
        if cls is None:
            raise HTTPException(501, f"server kind not available: {kind}")

        try:
            cfg = _SERVER_CONFIG_CLS(model_path=model_path, host=host, port=port, **extra)
            inst = cls(cfg)
            ok = inst.start()
            # ``start()`` returns bool on EmbeddedServer; PythonServer
            # variants either return None or True. Treat any non-False
            # return as "started" but raise if it explicitly returned
            # False so the renderer sees a 500 with detail.
            if ok is False:
                raise RuntimeError("server.start() returned False")
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"failed to start {kind} server: {exc}")

        _SERVER_STATE.update({
            "running": True,
            "kind": kind,
            "instance": inst,
            "model_path": model_path,
            "host": host,
            "port": port,
        })

    return _server_status_payload()


@app.post("/server/stop")
def server_stop():
    with _SERVER_LOCK:
        inst = _SERVER_STATE.get("instance")
        if not _SERVER_STATE.get("running") or inst is None:
            return {"ok": True, "wasRunning": False}
        try:
            inst.stop()
        except Exception as exc:  # noqa: BLE001
            # Best-effort -- a stop that throws still clears the slot
            # so the renderer can retry. The exception detail flows back.
            _SERVER_STATE.update({
                "running": False, "kind": None, "instance": None,
                "model_path": None, "host": None, "port": None,
            })
            raise HTTPException(500, f"server.stop() raised: {exc}")
        _SERVER_STATE.update({
            "running": False, "kind": None, "instance": None,
            "model_path": None, "host": None, "port": None,
        })
    return {"ok": True, "wasRunning": True}


# ---------------------------------------------------------------------------
# Phase 9 -- batch generation + quantize tooling.
#
# Both go through /jobs because they're long-running and we want one
# progress/cancel pattern. The batch job is incremental (one event per
# completed prompt); quantize is a single C call with no progress hook
# in cyllama 0.2.x, so we emit start + done.
# ---------------------------------------------------------------------------


register_job_kind("batch")
register_job_kind("models.quantize")


_BATCH_MAX_PROMPTS = 1024  # ceiling so a runaway paste doesn't blow up


def _coerce_prompts(raw) -> list[str]:
    """Accept list[str] or a single newline-delimited string."""
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        return [ln for ln in raw.splitlines() if ln.strip()]
    return []


@app.post("/jobs/batch")
async def jobs_batch(req: Request):
    if not _FEATURE_FLAGS.get("batch"):
        raise HTTPException(501, "batch_generate not available in this cyllama build")
    body = await req.json()
    model_path = (body.get("model_path") or "").strip()
    if not model_path or not os.path.isfile(model_path):
        raise HTTPException(400, "model_path required and must exist")
    prompts = _coerce_prompts(body.get("prompts"))
    prompts = [p.strip() for p in prompts if p and p.strip()]
    if not prompts:
        raise HTTPException(400, "prompts required (non-empty list or newline string)")
    if len(prompts) > _BATCH_MAX_PROMPTS:
        raise HTTPException(400, f"too many prompts (cap {_BATCH_MAX_PROMPTS})")

    config = _build_config(body.get("params"))
    try:
        batch_size = int(body.get("batch_size") or 512)
        n_seq_max = int(body.get("n_seq_max") or 8)
    except (TypeError, ValueError):
        batch_size, n_seq_max = 512, 8
    batch_size = max(1, min(8192, batch_size))
    n_seq_max = max(1, min(64, n_seq_max))

    async def producer(job: Job) -> None:
        out_dir = (ARTIFACTS_DIR / job.id).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "outputs.jsonl"

        await _emit(job, {
            "type": "log",
            "message": f"running batch_generate on {len(prompts)} prompts...",
        })

        # cyllama's batch_generate is a single synchronous call that
        # returns the full list. There is no progress callback in 0.2.x,
        # so per-prompt SSE events get emitted *after* the call returns
        # rather than during -- the renderer's progress bar fills in
        # one big jump. The artifact JSONL is written incrementally as
        # we iterate the result, which keeps memory bounded for large
        # batches even though the wall-clock progress is coarse.
        try:
            kwargs = {"batch_size": batch_size, "n_seq_max": n_seq_max}
            if config is not None:
                kwargs["config"] = config
            responses = _BATCH_GENERATE(prompts, model_path, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"batch_generate failed: {exc}") from exc

        results: list[dict] = []
        with open(out_path, "w", encoding="utf-8") as f:
            for i, resp in enumerate(responses):
                # ``Response`` objects expose ``.text`` plus a ``.stats``
                # dataclass; coerce defensively in case the shape moves
                # (it has across versions).
                text = getattr(resp, "text", None)
                if text is None:
                    text = str(resp)
                row = {
                    "index": i,
                    "prompt": prompts[i] if i < len(prompts) else None,
                    "response": text,
                }
                stats = getattr(resp, "stats", None)
                if stats is not None:
                    row["stats"] = _jsonify(getattr(stats, "__dict__", {}) or {})
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                results.append(row)
                await _emit(job, {
                    "type": "result_row",
                    "index": i,
                    "prompt": row["prompt"],
                    "response": row["response"],
                })
                # Heart-beat progress so the UI bar moves even though
                # the underlying call already finished.
                await _emit(job, {
                    "type": "progress",
                    "value": (i + 1) / len(responses) if responses else 1.0,
                })

        job.artifact_path = out_dir
        await _emit(job, {
            "type": "result",
            "result": {
                "n": len(results),
                "artifact_name": out_path.name,
                "artifact_url": f"/jobs/{job.id}/artifact/{out_path.name}",
            },
        })

    job = await run_job("batch", producer)
    return {"job_id": job.id}


# Friendly map of common llama.cpp ftype integer codes. The sidecar
# accepts either an int or a label so the renderer dropdown can be
# label-driven without a magic-number lookup table.
_QUANTIZE_FTYPES: dict[str, int] = {
    "F32":     0,
    "F16":     1,
    "Q4_0":    2,
    "Q4_1":    3,
    "Q8_0":    7,
    "Q5_0":    8,
    "Q5_1":    9,
    "Q2_K":   10,
    "Q3_K_S": 11,
    "Q3_K_M": 12,
    "Q3_K_L": 13,
    "Q4_K_S": 14,
    "Q4_K_M": 15,
    "Q5_K_S": 16,
    "Q5_K_M": 17,
    "Q6_K":   18,
}


@app.post("/jobs/models/quantize")
async def jobs_models_quantize(req: Request):
    if not _FEATURE_FLAGS.get("quantize"):
        raise HTTPException(501, "model_quantize not available in this cyllama build")
    body = await req.json()
    src = (body.get("src_path") or "").strip()
    if not src or not os.path.isfile(src):
        raise HTTPException(400, "src_path required and must exist")

    # Destination resolution: the renderer sends a bare filename; we
    # always write into MODELS_DIR so the result shows up in the
    # cached-models listing. Refusing absolute / traversing names is
    # defence-in-depth -- the renderer constrains the input field but
    # a hand-crafted POST shouldn't be able to escape MODELS_DIR.
    dst_name = (body.get("dst_name") or "").strip()
    if not dst_name or "/" in dst_name or "\\" in dst_name or dst_name.startswith("."):
        raise HTTPException(400, "dst_name required and must be a plain filename")
    if not dst_name.endswith(".gguf"):
        dst_name += ".gguf"
    dst = (MODELS_DIR / dst_name).resolve()
    try:
        dst.relative_to(MODELS_DIR.resolve())
    except ValueError:
        raise HTTPException(400, "dst_name escapes MODELS_DIR")
    if dst.exists():
        raise HTTPException(409, f"already exists: {dst.name}")

    ftype_in = body.get("ftype")
    if isinstance(ftype_in, str):
        if ftype_in not in _QUANTIZE_FTYPES:
            raise HTTPException(400, f"unknown ftype label: {ftype_in!r}")
        ftype = _QUANTIZE_FTYPES[ftype_in]
    elif isinstance(ftype_in, int):
        ftype = ftype_in
    else:
        raise HTTPException(400, "ftype required (label or integer)")

    nthread = body.get("nthread")
    try:
        nthread = int(nthread) if nthread not in (None, "") else 0
    except (TypeError, ValueError):
        nthread = 0
    allow_requantize = bool(body.get("allow_requantize", False))
    only_copy = bool(body.get("only_copy", False))

    async def producer(job: Job) -> None:
        params = _QUANTIZE_PARAMS_CLS()
        # Each setattr is wrapped because the params class is a Cython
        # type and attribute names can drift across cyllama versions.
        for attr, val in (
            ("ftype", ftype),
            ("nthread", nthread),
            ("allow_requantize", allow_requantize),
            ("only_copy", only_copy),
        ):
            try: setattr(params, attr, val)
            except (AttributeError, TypeError): pass

        await _emit(job, {
            "type": "log",
            "message": f"quantizing {Path(src).name} -> {dst.name} (ftype={ftype})...",
        })
        try:
            _MODEL_QUANTIZE(src, str(dst), params)
        except Exception as exc:  # noqa: BLE001
            # Best-effort cleanup of a half-written destination so the
            # next attempt isn't blocked by the 409 above.
            try:
                if dst.exists(): dst.unlink()
            except OSError:
                pass
            raise RuntimeError(f"model_quantize failed: {exc}") from exc

        await _emit(job, {
            "type": "result",
            "result": {
                "path": str(dst),
                "name": dst.name,
                "size": dst.stat().st_size if dst.exists() else 0,
                "ftype": ftype,
            },
        })

    job = await run_job("models.quantize", producer)
    return {"job_id": job.id}


@app.get("/quantize/ftypes")
def quantize_ftypes():
    """Return the label->int ftype map so the renderer's dropdown can
    drive its options off a single source of truth."""
    return {"ftypes": dict(_QUANTIZE_FTYPES)}


def _shutdown_server_silently():
    """Best-effort stop of any running OpenAI-compat server.

    Called on signal / atexit so the C-side thread doesn't keep the
    port bound after the sidecar dies. Idempotent; never raises.
    """
    try:
        inst = _SERVER_STATE.get("instance")
        if inst is not None:
            try: inst.stop()
            except Exception: pass  # noqa: BLE001
    except Exception:  # noqa: BLE001
        pass


def _handle_signal(signum, frame):  # noqa: ARG001
    _shutdown_server_silently()
    sys.exit(0)


import atexit as _atexit  # noqa: E402
_atexit.register(_shutdown_server_silently)


for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, _handle_signal)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
