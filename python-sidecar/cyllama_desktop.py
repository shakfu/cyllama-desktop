"""Client library for scripts run by cyllama-desktop.

A workspace script (``<workspace>/scripts/*.py``) runs in a child
process spawned by the sidecar. This module is on its ``PYTHONPATH``
and reaches the running app back over the loopback API::

    from cyllama_desktop import app

    for temp in (0.2, 0.7, 1.0):
        app.progress(message=f"temperature={temp}")
        print(app.chat("name three primes", temperature=temp))
    app.set_result({"done": True})

Scope: this wraps *app state* only -- the resident model, the model
list, RAG collections, progress reporting, artifacts. It deliberately
does not wrap cyllama's inference API. A script that wants low-level
control should ``import cyllama`` and get the real thing.

A workflow node can import the same handle. It runs inside the sidecar
rather than a child process, so the job-scoped members are inert there:
``args`` is empty, ``progress()`` does nothing, and ``artifact()`` /
``set_result()`` resolve against the process working directory rather
than a job. Everything else -- ``chat``, ``models``, ``rag_*``,
``providers`` -- behaves identically.

``app.chat(..., provider="openai", model="gpt-5.4")`` runs against an
external provider the user configured in Preferences. The key never
enters this process: the script names a provider and the sidecar holds
the credential. That also means a script can spend a key it cannot read
-- see ``docs/dev/providers.md`` S8.2.

The reason to prefer ``app.chat()`` over loading a model here is
memory: the sidecar keeps one LLM resident and reuses it across calls,
so a 50-generation sweep pays one model load. Loading a second copy in
this process is invisible to that cache and doubles resident memory.

The same applies to RAG: the vector stores are written by the sidecar's
ingest jobs, so reach them through ``app.rag_*`` rather than opening the
sqlite files directly, or you get two writers on one file.

See docs/dev/scripting.md for the full contract.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator, Optional, Union

__all__ = ["app", "App", "SidecarError", "NotRunningUnderDesktop"]


class SidecarError(RuntimeError):
    """The sidecar refused a request or failed mid-stream."""


class NotRunningUnderDesktop(RuntimeError):
    """The script was started outside the app, so there is no sidecar."""


# Stdlib only, deliberately. Scripts import this module, so a dependency
# here is a dependency every script inherits -- and the bundled env is
# not a stable API. This module was written against ``httpx`` and broke
# on the next cyllama bump, when openai and anthropic moved to
# ``httpx2`` and plain httpx stopped being installed at all. urllib
# cannot be uninstalled out from under a script.

# Generation has no useful upper bound, so streaming reads never time
# out. Everything else fails fast rather than hanging a job.
_TIMEOUT_S = 30.0

_PROGRESS_SENTINEL = "\x1e"


class App:
    """Handle on the running desktop app. Use the module-level ``app``."""

    def __init__(self) -> None:
        self._url_base = (os.environ.get("CYLLAMA_SIDECAR_URL") or "").rstrip("/")
        self._token = os.environ.get("CYLLAMA_SIDECAR_TOKEN") or ""
        self.job_id = os.environ.get("CYLLAMA_JOB_ID") or ""
        self.models_dir = Path(os.environ.get("CYLLAMA_MODELS_DIR") or ".")
        self.artifacts_dir = Path(os.environ.get("CYLLAMA_ARTIFACTS_DIR") or ".")
        self.scripts_dir = Path(os.environ.get("CYLLAMA_SCRIPTS_DIR") or ".")
        self._args: Optional[dict] = None

    # -- plumbing ----------------------------------------------------------

    def _url(self, path: str, params: Optional[dict] = None) -> str:
        if not self._url_base or not self._token:
            raise NotRunningUnderDesktop(
                "CYLLAMA_SIDECAR_URL / _TOKEN are unset. This script is meant "
                "to be run from cyllama-desktop; to script cyllama directly, "
                "install it with pip and import it."
            )
        url = self._url_base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return url

    def _open(self, method: str, path: str, body: Optional[dict] = None,
              params: Optional[dict] = None, timeout: Optional[float] = _TIMEOUT_S):
        """Send a request and return the open response. Caller closes it."""
        data = None
        headers = {"authorization": f"Bearer {self._token}"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["content-type"] = "application/json"
        req = urllib.request.Request(
            self._url(path, params), data=data, headers=headers, method=method,
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail).get("detail", detail)
            except ValueError:
                pass
            raise SidecarError(f"HTTP {exc.code}: {detail}") from None
        except urllib.error.URLError as exc:
            raise SidecarError(f"cannot reach the sidecar: {exc.reason}") from None

    def _get(self, path: str, **params) -> Any:
        with self._open("GET", path, params=params or None) as r:
            return json.loads(r.read().decode("utf-8"))

    def _post(self, path: str, body: dict) -> Any:
        with self._open("POST", path, body=body) as r:
            return json.loads(r.read().decode("utf-8"))

    # -- input / output ----------------------------------------------------

    @property
    def args(self) -> dict:
        """The JSON object passed on stdin when the job started.

        Empty when the script was started with no args, or run outside
        the app (where stdin is a terminal and reading it would hang).
        """
        if self._args is None:
            self._args = {}
            try:
                if not sys.stdin.isatty():
                    raw = sys.stdin.read()
                    if raw.strip():
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            self._args = parsed
            except (OSError, ValueError):
                self._args = {}
        return self._args

    def progress(self, value: Optional[float] = None, message: str = "") -> None:
        """Report progress to the app.

        ``value`` is a 0..1 fraction and drives the progress bar;
        ``message`` is a one-line status. Either may be omitted.

        Inert outside a job. A workflow node imports this same handle but
        runs inside the sidecar, where this stdout belongs to the sidecar's
        log rather than to a job -- writing the sentinel there would put
        control characters in the Console for no benefit.
        """
        if not self.job_id:
            return
        payload: dict[str, Any] = {}
        if value is not None:
            payload["progress"] = max(0.0, min(1.0, float(value)))
        if message:
            payload["message"] = str(message)
        sys.stdout.write(_PROGRESS_SENTINEL + json.dumps(payload) + "\n")
        sys.stdout.flush()

    def artifact(self, name: str) -> Path:
        """Path in the job's artifact directory, downloadable after the run.

        The directory is also the script's working directory, so a plain
        relative ``open(name, "w")`` lands in the same place.
        """
        return self.artifacts_dir / name

    def set_result(self, value: Any) -> None:
        """Write ``result.json``, which becomes the job's result payload.

        Must be JSON-serialisable and under 1 MB. Larger output belongs in
        an artifact.
        """
        (self.artifacts_dir / "result.json").write_text(
            json.dumps(value, indent=2), encoding="utf-8"
        )

    # -- models ------------------------------------------------------------

    def models(self, kinds: str = "") -> list[dict]:
        """Models the app knows about.

        ``kinds`` filters by classification, e.g. ``"chat"`` or
        ``"embedding"``. Each item carries ``path``, ``name``, ``kind``,
        and ``source``.
        """
        body = self._get("/models/cached", **({"kinds": kinds} if kinds else {}))
        return body.get("models", [])

    def default_model(self) -> str:
        """Path of the first chat model, for scripts that do not pick one."""
        items = self.models("chat")
        if not items:
            raise SidecarError(
                "no chat model available; import one in the Models pane "
                "or pass model= explicitly"
            )
        return items[0]["path"]

    # -- generation --------------------------------------------------------

    def chat(
        self,
        prompt: Union[str, list],
        *,
        model: str = "",
        system: str = "",
        provider: Union[str, dict, None] = None,
        **params,
    ) -> str:
        """Generate against the model the app already has resident.

        ``prompt`` is either a string or a list of ``{role, content}``
        messages. ``model`` defaults to :meth:`default_model`. Extra
        keyword arguments are sampling parameters (temperature, top_p,
        max_tokens, ...); the sidecar drops any the installed cyllama
        does not accept.

        Pass ``provider=`` to run against an external provider instead of
        a local GGUF -- see :meth:`providers`. ``model`` is then a provider
        model id and is required, since there is no local file to default
        to::

            app.chat("name three primes", provider="openai", model="gpt-5.4")

        The key stays in the sidecar; this process never sees it.
        """
        return "".join(self.chat_stream(
            prompt, model=model, system=system, provider=provider, **params,
        ))

    def chat_stream(
        self,
        prompt: Union[str, list],
        *,
        model: str = "",
        system: str = "",
        provider: Union[str, dict, None] = None,
        **params,
    ) -> Iterator[str]:
        """Same as :meth:`chat`, yielding text chunks as they arrive."""
        messages = self._messages(prompt, system)
        body: dict = {"messages": messages, "params": params or {}}
        if provider is None:
            body["model_path"] = model or self.default_model()
        else:
            ref = self._provider_ref(provider)
            if not model:
                raise SidecarError(
                    "model= is required with provider=; there is no local "
                    "file to fall back to. Use app.provider_models() to list "
                    "what the provider offers."
                )
            body["provider"] = ref
            body["model"] = model
            # Label the usage row so provider spend can be traced back to
            # the job that caused it. A hint, not a claim the sidecar can
            # verify -- docs/dev/providers.md S8.2.
            body["source"] = f"script:{self.job_id}" if self.job_id else "workflow"
        # No read timeout: a long generation is not a stalled one.
        with self._open("POST", "/chat", body=body, timeout=None) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if "error" in chunk:
                    raise SidecarError(str(chunk["error"]))
                text = chunk.get("text")
                if text:
                    yield text

    @staticmethod
    def _provider_ref(provider: Union[str, dict]) -> dict:
        """Normalise ``provider=`` into the wire shape.

        A string names one of the built-in kinds. A dict is a compat
        endpoint: ``{"kind": "compat", "name": ..., "base_url": ...}``,
        matching what the Providers tab stores. The sidecar validates it.
        """
        if isinstance(provider, str):
            return {"kind": provider}
        if isinstance(provider, dict):
            return dict(provider)
        raise SidecarError("provider= must be a string or a dict")

    @staticmethod
    def _messages(prompt: Union[str, list], system: str) -> list:
        if isinstance(prompt, str):
            messages = [{"role": "user", "content": prompt}]
        else:
            messages = [dict(m) for m in prompt]
        if system:
            messages = [{"role": "system", "content": system}] + messages
        return messages

    def tokenize(self, text: str, *, model: str = "") -> int:
        """True token count for ``text`` against a model."""
        body = self._post("/tokenize", {
            "model_path": model or self.default_model(),
            "text": text,
        })
        return int(body.get("count", 0))

    # -- external providers ------------------------------------------------

    def providers(self) -> list[str]:
        """Accounts that have a key configured, e.g. ``["openai"]``.

        A compat endpoint appears as ``compat.<normalized-name>``. This is
        the full extent of what a script can learn about credentials: no
        endpoint returns key bytes. A provider missing from this list will
        refuse a :meth:`chat` with 401.
        """
        return self._get("/providers/credentials").get("configured", [])

    def provider_models(self, provider: Union[str, dict]) -> list[dict]:
        """Chat models a provider offers. Each item carries ``id``.

        Served from the app's cache; pass a provider the user has not
        configured and this raises rather than returning an empty list.
        """
        ref = self._provider_ref(provider)
        return self._get(
            "/providers/models",
            kind=ref.get("kind", ""),
            name=ref.get("name", ""),
            base_url=ref.get("base_url", ""),
        ).get("models", [])

    # -- RAG ---------------------------------------------------------------

    def rag_collections(self) -> list[dict]:
        return self._get("/rag/collections").get("collections", [])

    def rag_retrieve(
        self,
        collection_id: str,
        query: str,
        *,
        top_k: int = 3,
        similarity_threshold: Optional[float] = None,
    ) -> list[dict]:
        """Retrieve chunks without generating. No LLM is loaded."""
        body: dict[str, Any] = {
            "collection_id": collection_id,
            "query": query,
            "top_k": top_k,
        }
        if similarity_threshold is not None:
            body["similarity_threshold"] = similarity_threshold
        return self._post("/rag/retrieve", body).get("sources", [])


app = App()
