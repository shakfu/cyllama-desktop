"""FastAPI sidecar exposing cyllama over localhost HTTP/SSE.

Started by the Electron main process with these env vars:
  CYLLAMA_SIDECAR_PORT          port to bind on 127.0.0.1
  CYLLAMA_SIDECAR_TOKEN         bearer token clients must send
  CYLLAMA_SIDECAR_PARENT_PID    parent pid; sidecar exits if parent dies
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import threading
import time
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from cyllama import LLM, GenerationConfig

# Whitelist of GenerationConfig fields the renderer is allowed to set.
# Restricting this is defense-in-depth: we never pass arbitrary kwargs
# from a JSON body straight into a model-loading API. New fields go here
# explicitly when the UI grows new controls.
_ALLOWED_PARAMS = {
    "temperature":    float,
    "top_p":          float,
    "top_k":          int,
    "min_p":          float,
    "repeat_penalty": float,
    "max_tokens":     int,
    "seed":           int,
}


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
    if stops is not None:
        kwargs["stop_sequences"] = stops

    return GenerationConfig(**kwargs) if kwargs else None


PORT = int(os.environ["CYLLAMA_SIDECAR_PORT"])
TOKEN = os.environ["CYLLAMA_SIDECAR_TOKEN"]
PARENT_PID = int(os.environ.get("CYLLAMA_SIDECAR_PARENT_PID", "0"))


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
        raise HTTPException(status_code=401, detail="unauthorized")
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


def _handle_signal(signum, frame):  # noqa: ARG001
    sys.exit(0)


for _sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(_sig, _handle_signal)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
