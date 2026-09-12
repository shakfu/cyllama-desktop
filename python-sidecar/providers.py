"""External chat providers: OpenAI, Anthropic, OpenRouter, OpenAI-compatible.

The sidecar owns every outbound provider call. Keys are pushed in by the main
process at unlock (``POST /providers/credentials``) and held in memory here --
they are never written to disk by this process, never returned to the
renderer, and deliberately never read from the environment (``_script_env``
copies ``os.environ`` wholesale into every script child, so a key there would
reach user scripts).

A token holder can still *use* a key it cannot read, scripts included. That is
accepted, not overlooked: see ``docs/dev/providers.md`` S8.2.

Four kinds. Only ``anthropic`` differs on the wire; ``openai``, ``openrouter``
and ``compat`` share one client and differ by base URL. The named kinds exist
so the UI can offer a one-click entry with its own credential slot and model
list, not because the protocol differs.

Design ported from ``infer-app``'s ``InferCore/Cloud/``. See
``docs/dev/providers.md`` section 7 for the divergences and why.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

__all__ = [
    "KINDS",
    "Provider",
    "ProviderError",
    "PARAM_SUPPORT",
    "provider_from_dict",
    "endpoint_acceptable",
    "scrub_key",
    "set_credentials",
    "configured_accounts",
    "has_key",
    "stream_chat",
    "list_models",
    "sdk_status",
    "record_usage",
    "usage_totals",
    "clear_usage",
]

KINDS = ("openai", "anthropic", "openrouter", "compat")

# Canonical routes. ``compat`` has none -- the user supplies it.
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Anthropic requires max_tokens; used when the caller sends no params.
DEFAULT_MAX_TOKENS = 1024

# Cached model lists older than this are refreshed on next use. The picker
# is still served from cache while that happens.
MODEL_CACHE_TTL_S = 24 * 60 * 60

# Retries, set in one place rather than per call site. Both SDKs already
# retry 429 and 5xx with exponential backoff and honour ``Retry-After``, so
# this is a policy knob, not an implementation: 2 means up to 3 attempts.
# A fan-out inventing its own retry at each call site is how a provider
# account gets suspended.
#
# Timeouts stay at the SDK defaults deliberately. A long generation is not a
# stalled one, and a number picked here would be a guess that truncates
# somebody's reply.
MAX_RETRIES = 2


class ProviderError(RuntimeError):
    """A provider rejected a request, or the request could not be built.

    ``status`` carries the HTTP status the sidecar should return -- 400 for a
    malformed request, 502 for an upstream failure, 501 for a missing SDK.
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Provider identity
# ---------------------------------------------------------------------------


def _normalize_account_suffix(s: str) -> str:
    """Lowercase and collapse anything outside [a-z0-9._-] to a single dash.

    Keeps the credential key printable and stable across restarts. Two compat
    endpoints whose names normalize alike share a slot, so the UI validates
    uniqueness before saving.
    """
    out = re.sub(r"[^a-z0-9._-]+", "-", s.lower())
    return out.strip("-")


@dataclass(frozen=True)
class Provider:
    kind: str
    name: str = ""
    base_url: str = ""

    @property
    def account(self) -> str:
        """Credential + model-cache key. Compat endpoints are keyed by name
        so two of them keep separate keys and separate model lists."""
        if self.kind == "compat":
            return "compat." + _normalize_account_suffix(self.name)
        return self.kind

    @property
    def display_name(self) -> str:
        return {
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "openrouter": "OpenRouter",
        }.get(self.kind, self.name)

    @property
    def effective_base_url(self) -> str:
        if self.kind == "openai":
            return OPENAI_BASE_URL
        if self.kind == "openrouter":
            return OPENROUTER_BASE_URL
        return self.base_url


def endpoint_acceptable(url: str) -> bool:
    """https anywhere, http only for loopback.

    Local runtimes (Ollama, LM Studio, llama.cpp's own server) are served over
    plain http on localhost, so blanket-requiring TLS would exclude them. Any
    other http URL is refused: sending a key in clear to a remote host is not
    a tradeoff worth offering.
    """
    try:
        u = urlparse(url)
    except ValueError:
        return False
    if u.scheme == "https":
        return bool(u.hostname)
    if u.scheme == "http":
        return (u.hostname or "").lower() in ("localhost", "127.0.0.1", "::1")
    return False


def provider_from_dict(raw: object) -> Provider:
    """Parse the ``provider`` field of a request body. Raises ProviderError."""
    if not isinstance(raw, dict):
        raise ProviderError("provider must be an object")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise ProviderError(f"provider.kind must be one of {list(KINDS)}")
    if kind != "compat":
        return Provider(kind=kind)

    name = str(raw.get("name") or "").strip()
    base_url = str(raw.get("base_url") or "").strip()
    if not name:
        raise ProviderError("provider.name required for a compat endpoint")
    if not _normalize_account_suffix(name):
        raise ProviderError("provider.name must contain a letter or digit")
    if not base_url:
        raise ProviderError("provider.base_url required for a compat endpoint")
    if not endpoint_acceptable(base_url):
        raise ProviderError(
            "provider.base_url must be https, or http on localhost"
        )
    return Provider(kind=kind, name=name, base_url=base_url.rstrip("/"))


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

_cred_lock = threading.Lock()
_credentials: dict[str, str] = {}


def set_credentials(raw: object) -> list[str]:
    """Replace the whole credential set. Returns the configured accounts.

    Wholesale replacement rather than per-account upsert: the main process
    owns the truth (``<userData>/credentials.json``, encrypted with Electron's
    ``safeStorage``) and pushes the full set whenever it changes, so there is
    no delete endpoint to keep in sync.
    """
    if not isinstance(raw, dict):
        raise ProviderError("credentials must be an object")
    clean: dict[str, str] = {}
    for account, key in raw.items():
        if not isinstance(account, str) or not isinstance(key, str):
            raise ProviderError("credentials must map account -> key string")
        if key.strip():
            clean[account] = key.strip()
    with _cred_lock:
        _credentials.clear()
        _credentials.update(clean)
        return sorted(_credentials)


def configured_accounts() -> list[str]:
    with _cred_lock:
        return sorted(_credentials)


def has_key(provider: Provider) -> bool:
    with _cred_lock:
        return provider.account in _credentials


def _key_for(provider: Provider) -> str:
    with _cred_lock:
        key = _credentials.get(provider.account)
    if not key:
        raise ProviderError(
            f"no API key configured for {provider.display_name}", status=401
        )
    return key


def scrub_key(body: str, key: str) -> str:
    """Replace the key, and its first 8 chars, with ``***``.

    Guards against an upstream that mirrors request data back in an error.
    Neither OpenAI nor Anthropic does today; the check costs nothing on the
    error path and drops the dependency on that staying true. The 8-char
    partial only applies to keys of 12+ chars, so a short test key cannot
    turn arbitrary text into asterisks.
    """
    if not key:
        return body
    out = body.replace(key, "***")
    if len(key) >= 12:
        out = out.replace(key[:8], "***")
    return out


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

# Which of the renderer's sampler fields each kind accepts. The Parameters
# pane hides every row outside the active kind's set, reusing the mechanism
# already driving ``/info.supported_params`` for local models.
#
# Excluded everywhere because no provider has an equivalent: min_p,
# mirostat*, repeat_penalty, and the load-time fields (n_gpu_layers,
# main_gpu, split_mode, n_ctx, n_batch, grammar).
#
# Provider-native fields with no local equivalent -- reasoning_effort,
# verbosity, thinking budget, service tier, prompt caching -- are deliberately
# absent: nothing in the UI produces them yet. They are their own slice.
PARAM_SUPPORT: dict[str, frozenset[str]] = {
    "openai": frozenset({
        "temperature", "top_p", "max_tokens", "seed",
        "presence_penalty", "frequency_penalty", "stop_sequences",
    }),
    "openrouter": frozenset({
        "temperature", "top_p", "top_k", "max_tokens", "seed",
        "presence_penalty", "frequency_penalty", "stop_sequences",
    }),
    "anthropic": frozenset({
        "temperature", "top_p", "top_k", "max_tokens", "stop_sequences",
    }),
    "compat": frozenset({
        "temperature", "top_p", "top_k", "max_tokens", "seed",
        "presence_penalty", "frequency_penalty", "stop_sequences",
    }),
}

_CASTERS = {
    "temperature": float,
    "top_p": float,
    "top_k": int,
    "max_tokens": int,
    "seed": int,
    "presence_penalty": float,
    "frequency_penalty": float,
}


def _coerce_stop_sequences(v: object) -> list[str]:
    """Same shape the local path accepts: list[str], or comma-separated."""
    if isinstance(v, str):
        items = [s.strip() for s in v.split(",")]
    elif isinstance(v, (list, tuple)):
        items = [str(x).strip() for x in v]
    else:
        return []
    return [s for s in items if s]


def build_params(raw: Optional[dict], kind: str) -> dict:
    """Filter + cast the renderer's params to what this kind accepts.

    Unsupported and uncastable fields are dropped rather than rejected: the
    renderer sends one params blob for whichever backend is active, and a
    400 on a leftover ``mirostat`` would make switching backends fail for a
    field the user cannot see.
    """
    supported = PARAM_SUPPORT.get(kind, frozenset())
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for key, value in raw.items():
        if key not in supported or value is None or value == "":
            continue
        if key == "stop_sequences":
            seqs = _coerce_stop_sequences(value)
            if seqs:
                out["stop_sequences"] = seqs
            continue
        caster = _CASTERS.get(key)
        if caster is None:
            continue
        try:
            out[key] = caster(value)
        except (TypeError, ValueError):
            continue
    return out


def openai_body(model: str, messages: list[dict], params: dict, kind: str) -> dict:
    """Request kwargs for the OpenAI-shaped ``/chat/completions``."""
    body: dict = {
        "model": model,
        "messages": [
            {"role": m["role"], "content": m["content"]} for m in messages
        ],
    }
    max_tokens = params.get("max_tokens", DEFAULT_MAX_TOKENS)
    if kind == "openai":
        # o-series and gpt-5 reject ``max_tokens`` outright.
        body["max_completion_tokens"] = max_tokens
    else:
        # OpenRouter normalizes ``max_tokens``, and compat servers predating
        # the rename (llama.cpp's own, LM Studio, Ollama) only know that one.
        body["max_tokens"] = max_tokens
    for key in ("temperature", "top_p", "top_k", "seed",
                "presence_penalty", "frequency_penalty"):
        if key in params:
            body[key] = params[key]
    if params.get("stop_sequences"):
        # OpenAI accepts at most 4; trim rather than 400 on an eager UI.
        body["stop"] = params["stop_sequences"][:4]
    return body


def anthropic_body(model: str, messages: list[dict], params: dict) -> dict:
    """Request kwargs for Anthropic's ``/v1/messages``.

    Two shape differences from the OpenAI format: the system prompt is a
    top-level field rather than a message, and the turn list must start with
    a user message. Consecutive same-role turns are joined -- the chat pane
    produces strictly alternating history, but ``/chat`` also serves scripts
    and workflows, and a provider-side 400 on message ordering is not
    something the caller can diagnose from the error text.
    """
    system_parts: list[str] = []
    turns: list[dict] = []
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":
            if content.strip():
                system_parts.append(content)
            continue
        if not turns and role != "user":
            continue
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] += "\n\n" + content
            continue
        turns.append({"role": role, "content": content})
    if not turns:
        raise ProviderError("anthropic requires at least one user message")

    body: dict = {
        "model": model,
        "messages": turns,
        "max_tokens": params.get("max_tokens", DEFAULT_MAX_TOKENS),
    }
    if system_parts:
        body["system"] = "\n\n".join(system_parts)
    for key in ("temperature", "top_p", "top_k"):
        if key in params:
            body[key] = params[key]
    if params.get("stop_sequences"):
        body["stop_sequences"] = params["stop_sequences"]
    return body


# ---------------------------------------------------------------------------
# Clients
#
# The SDKs are declared deps (pyproject.toml) and bundled, but the imports are
# guarded so a pruned env degrades to "provider section hidden" rather than
# taking the sidecar down at import. ``client_for`` is the seam tests replace;
# the body builders above are pure and tested directly, which is where the
# per-kind mapping bugs live.
# ---------------------------------------------------------------------------


def _import_openai():
    try:
        from openai import OpenAI
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"openai SDK unavailable: {exc}", status=501)
    return OpenAI


def _import_anthropic():
    try:
        from anthropic import Anthropic
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(f"anthropic SDK unavailable: {exc}", status=501)
    return Anthropic


def sdk_status() -> dict[str, bool]:
    """Which provider SDKs this build can actually use."""
    out = {}
    for name in ("openai", "anthropic"):
        try:
            __import__(name)
            out[name] = True
        except Exception:  # noqa: BLE001
            out[name] = False
    return out


class OpenAICompatClient:
    """Serves ``openai``, ``openrouter`` and ``compat`` -- one wire format."""

    def __init__(self, provider: Provider, api_key: str) -> None:
        self.provider = provider
        self.api_key = api_key
        OpenAI = _import_openai()
        self._client = OpenAI(
            api_key=api_key,
            base_url=provider.effective_base_url,
            max_retries=MAX_RETRIES,
        )

    def stream_chat(
        self,
        model: str,
        messages: list[dict],
        params: dict,
        usage: Optional[dict] = None,
    ) -> Iterator[str]:
        body = openai_body(model, messages, params, self.provider.kind)
        # ``stream_options`` asks for a final usage-only chunk. Sent to the
        # two endpoints known to accept it; a compat server is whatever the
        # user is running, and one that validates unknown fields strictly
        # would 400 on it. Usage from a compat endpoint is therefore absent
        # rather than wrong.
        if self.provider.kind in ("openai", "openrouter"):
            body["stream_options"] = {"include_usage": True}
        stream = self._client.chat.completions.create(stream=True, **body)
        for chunk in stream:
            _absorb_openai_usage(chunk, usage)
            # A usage-only or keepalive chunk carries no choices.
            choices = getattr(chunk, "choices", None)
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            text = getattr(delta, "content", None) if delta else None
            if text:
                yield text

    def list_models(self) -> list[dict]:
        page = self._client.models.list()
        out = []
        for m in page:
            entry = {"id": getattr(m, "id", "")}
            if not entry["id"]:
                continue
            # OpenRouter returns context length and pricing on the same
            # objects; OpenAI returns neither. Carried through when present.
            ctx = getattr(m, "context_length", None)
            if isinstance(ctx, int):
                entry["context_length"] = ctx
            out.append(entry)
        return out


class AnthropicClient:
    def __init__(self, provider: Provider, api_key: str) -> None:
        self.provider = provider
        self.api_key = api_key
        Anthropic = _import_anthropic()
        self._client = Anthropic(api_key=api_key, max_retries=MAX_RETRIES)

    def stream_chat(
        self,
        model: str,
        messages: list[dict],
        params: dict,
        usage: Optional[dict] = None,
    ) -> Iterator[str]:
        body = anthropic_body(model, messages, params)
        with self._client.messages.stream(**body) as stream:
            for text in stream.text_stream:
                if text:
                    yield text
            # Only available once the stream is drained, so a cancelled
            # generation records nothing. Accepted: the alternative is
            # counting a partial reply the user never received.
            if usage is not None:
                try:
                    final = stream.get_final_message()
                except Exception:  # noqa: BLE001
                    return
                u = getattr(final, "usage", None)
                if u is not None:
                    usage["prompt_tokens"] = int(getattr(u, "input_tokens", 0) or 0)
                    usage["completion_tokens"] = int(getattr(u, "output_tokens", 0) or 0)

    def list_models(self) -> list[dict]:
        page = self._client.models.list()
        out = []
        for m in page:
            mid = getattr(m, "id", "")
            if not mid:
                continue
            entry = {"id": mid}
            label = getattr(m, "display_name", None)
            if isinstance(label, str) and label:
                entry["display_name"] = label
            out.append(entry)
        return out


def _absorb_openai_usage(chunk: object, usage: Optional[dict]) -> None:
    """Copy a chunk's usage block into ``usage`` when it carries one.

    Only the final chunk does, and only when ``stream_options`` was sent.
    """
    if usage is None:
        return
    u = getattr(chunk, "usage", None)
    if u is None:
        return
    usage["prompt_tokens"] = int(getattr(u, "prompt_tokens", 0) or 0)
    usage["completion_tokens"] = int(getattr(u, "completion_tokens", 0) or 0)


def client_for(provider: Provider, api_key: str):
    if provider.kind == "anthropic":
        return AnthropicClient(provider, api_key)
    return OpenAICompatClient(provider, api_key)


def _wrap_upstream(exc: Exception, api_key: str) -> ProviderError:
    """Turn an SDK exception into a ProviderError with the key scrubbed.

    Body text is capped at 400 chars so a provider returning a wall of JSON
    does not push the chat pane's error line off screen.
    """
    status = getattr(exc, "status_code", None)
    text = scrub_key(str(exc), api_key)
    if len(text) > 400:
        text = text[:400] + "..."
    prefix = f"HTTP {status}: " if isinstance(status, int) else ""
    return ProviderError(prefix + text, status=502)


def stream_chat(
    provider: Provider,
    model: str,
    messages: list[dict],
    raw_params: Optional[dict] = None,
    usage: Optional[dict] = None,
) -> Iterator[str]:
    """Yield text deltas from the provider. Raises ProviderError.

    Stateless, unlike the local path: the renderer owns chat history and posts
    the full message list every turn, so there is no transcript to manage
    here (``docs/dev/providers.md`` section 7.1).

    ``usage`` is a dict the client fills with ``prompt_tokens`` /
    ``completion_tokens`` once the provider reports them, which is at the end
    of the stream. It stays empty when the generation is cancelled, and when
    a compat endpoint does not report usage at all.
    """
    if not model:
        raise ProviderError("model required")
    key = _key_for(provider)
    params = build_params(raw_params, provider.kind)
    client = client_for(provider, key)
    try:
        yield from client.stream_chat(model, messages, params, usage)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _wrap_upstream(exc, key) from exc


# ---------------------------------------------------------------------------
# Model lists
# ---------------------------------------------------------------------------

# OpenAI returns embeddings, speech, image, moderation and legacy completion
# models in the same list as chat models, with no capability field to filter
# on. This is a deny-list of id substrings rather than an allow-list so a
# newly released chat model shows up on its own; the cost of a stale entry is
# one unusable row, not a missing model.
_NON_CHAT_MARKERS = (
    "text-embedding", "text-moderation", "omni-moderation",
    "whisper", "tts-", "-tts", "dall-e", "gpt-image", "-image-",
    "-audio", "-realtime", "-transcribe", "davinci", "babbage",
    "-search-", "codex-mini",
)


def filter_chat_models(kind: str, models: list[dict]) -> list[dict]:
    """Drop ids that cannot serve a chat completion.

    Only applied to ``openai``. Anthropic's list is chat models only,
    OpenRouter's is chat models only, and a compat server's list is whatever
    the user is running -- filtering those by OpenAI's naming would hide
    legitimate models.
    """
    if kind != "openai":
        return models
    return [
        m for m in models
        if not any(marker in m["id"].lower() for marker in _NON_CHAT_MARKERS)
    ]


def cache_path(providers_dir: Path, provider: Provider) -> Path:
    return providers_dir / f"models.{provider.account}.json"


def read_cache(providers_dir: Path, provider: Provider) -> Optional[dict]:
    path = cache_path(providers_dir, provider)
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return None
    return payload


def write_cache(providers_dir: Path, provider: Provider, models: list[dict]) -> dict:
    payload = {"models": models, "fetched_at": time.time()}
    path = cache_path(providers_dir, provider)
    tmp = path.with_suffix(".json.tmp")
    try:
        providers_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload), "utf-8")
        tmp.replace(path)
    except OSError:
        # A model list is a convenience; failing to cache it must not fail
        # the request that fetched it.
        pass
    return payload


def is_stale(payload: dict) -> bool:
    fetched = payload.get("fetched_at")
    if not isinstance(fetched, (int, float)):
        return True
    return (time.time() - fetched) > MODEL_CACHE_TTL_S


def list_models(
    provider: Provider,
    providers_dir: Path,
    *,
    refresh: bool = False,
) -> dict:
    """Return ``{models, fetched_at, cached, stale}`` for the picker.

    Cache-first: a fetch happens only when asked for, when nothing is cached,
    or when the cache has aged past the TTL. The picker is never blocked on a
    network call while a usable list exists on disk.
    """
    cached = read_cache(providers_dir, provider)
    if cached and not refresh and not is_stale(cached):
        return {**cached, "cached": True, "stale": False}

    if not has_key(provider):
        if cached:
            return {**cached, "cached": True, "stale": is_stale(cached)}
        raise ProviderError(
            f"no API key configured for {provider.display_name}", status=401
        )

    key = _key_for(provider)
    try:
        fetched = filter_chat_models(provider.kind, client_for(provider, key).list_models())
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        if cached:
            # Serve the stale list rather than emptying the picker because
            # the network blipped.
            return {**cached, "cached": True, "stale": True}
        raise _wrap_upstream(exc, key) from exc

    fetched.sort(key=lambda m: m["id"])
    payload = write_cache(providers_dir, provider, fetched)
    return {**payload, "cached": False, "stale": False}


# ---------------------------------------------------------------------------
# Usage accounting
#
# Every provider call is billable, which nothing else in this app is. Without
# a record, a script looping over a provider is a silent bill -- and a script
# can spend a key it cannot read (docs/dev/providers.md S8.2), so the record
# is what makes that visible.
#
# Tokens, not money. A price table goes stale, varies by tier and by cached
# input, and would have to be shipped and maintained; tokens are what the
# provider actually reports. OpenRouter returns per-model pricing in its
# model list, so an estimate is possible there later.
#
# ``source`` is a hint from the caller ("chat", "script:<job>", ...), not a
# proof: every local caller holds the same bearer token, so a script could
# claim to be the chat pane. The account totals stay right either way, which
# is the number that matters.
# ---------------------------------------------------------------------------

_usage_lock = threading.Lock()

_USAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  ts                REAL NOT NULL,
  account           TEXT NOT NULL,
  model             TEXT NOT NULL,
  source            TEXT NOT NULL DEFAULT '',
  prompt_tokens     INTEGER NOT NULL DEFAULT 0,
  completion_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
"""


def _usage_conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_USAGE_SCHEMA)
    return conn


def record_usage(
    db_path: Path,
    provider: Provider,
    model: str,
    usage: dict,
    source: str = "",
) -> bool:
    """Append one row. Returns False when there was nothing to record.

    Never raises: a generation that already succeeded must not fail because
    the accounting table could not be written.
    """
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    if not prompt and not completion:
        return False
    try:
        with _usage_lock:
            conn = _usage_conn(db_path)
            try:
                conn.execute(
                    "INSERT INTO usage (ts, account, model, source, "
                    "prompt_tokens, completion_tokens) VALUES (?, ?, ?, ?, ?, ?)",
                    (time.time(), provider.account, model,
                     str(source or "")[:120], prompt, completion),
                )
                conn.commit()
            finally:
                conn.close()
    except Exception:  # noqa: BLE001
        return False
    return True


def usage_totals(db_path: Path, since: float = 0.0) -> list[dict]:
    """Per account and model: call count and token totals, newest first."""
    if not db_path.exists():
        return []
    try:
        with _usage_lock:
            conn = _usage_conn(db_path)
            try:
                rows = conn.execute(
                    "SELECT account, model, COUNT(*), SUM(prompt_tokens), "
                    "SUM(completion_tokens), MAX(ts) FROM usage "
                    "WHERE ts >= ? GROUP BY account, model ORDER BY MAX(ts) DESC",
                    (float(since or 0.0),),
                ).fetchall()
            finally:
                conn.close()
    except Exception:  # noqa: BLE001
        return []
    return [
        {
            "account": r[0],
            "model": r[1],
            "calls": int(r[2] or 0),
            "prompt_tokens": int(r[3] or 0),
            "completion_tokens": int(r[4] or 0),
            "last_used": float(r[5] or 0.0),
        }
        for r in rows
    ]


def clear_usage(db_path: Path) -> int:
    """Delete every row. Returns how many were removed."""
    if not db_path.exists():
        return 0
    with _usage_lock:
        conn = _usage_conn(db_path)
        try:
            n = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
            conn.execute("DELETE FROM usage")
            conn.commit()
        finally:
            conn.close()
    return int(n or 0)
