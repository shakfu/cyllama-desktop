"""External provider support: identity, endpoint policy, param mapping,
request bodies, model-list caching, and the /chat remote branch.

The provider SDKs are never called. ``providers.client_for`` is the seam the
endpoint tests replace; the body builders are pure and tested directly, which
is where the per-kind mapping lives.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

# The main process holds a JS copy of these rules; tests/e2e/provider-identity.spec.js
# checks it against the same file.
_IDENTITY = json.loads(
    (Path(__file__).parent / "fixtures" / "provider_identity.json").read_text()
)


# --- test doubles -----------------------------------------------------------


class _StubClient:
    """Stands in for OpenAICompatClient / AnthropicClient."""

    def __init__(self, chunks=(), models=(), raises=None, usage=None):
        self.chunks = list(chunks)
        self.models = list(models)
        self.raises = raises
        # What the provider would report at the end of the stream.
        self.usage = dict(usage or {})
        self.calls = []

    def stream_chat(self, model, messages, params, usage=None):
        self.calls.append((model, messages, params))
        if self.raises is not None:
            raise self.raises
        yield from self.chunks
        if usage is not None:
            usage.update(self.usage)

    def list_models(self):
        if self.raises is not None:
            raise self.raises
        return list(self.models)


def _install_stub(prov, monkeypatch, stub):
    monkeypatch.setattr(prov, "client_for", lambda provider, key: stub)
    return stub


def _sse_text(client, body, auth):
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        return "".join(r.iter_text())


# --- identity ---------------------------------------------------------------


def test_named_kinds_use_their_own_account(prov):
    for kind in ("openai", "anthropic", "openrouter"):
        assert prov.Provider(kind=kind).account == kind


@pytest.mark.parametrize("name, suffix", _IDENTITY["account_suffix"])
def test_account_suffix_matches_the_shared_fixture(prov, name, suffix):
    assert prov._normalize_account_suffix(name) == suffix


@pytest.mark.parametrize("case", _IDENTITY["endpoints"], ids=lambda c: c["url"])
def test_endpoint_policy_matches_the_shared_fixture(prov, case):
    assert prov.endpoint_acceptable(case["url"]) is case["acceptable"]
    p = prov.Provider(kind="compat", name="x", base_url=case["url"])
    assert p.needs_key is case["needs_key"]


def test_compat_account_is_keyed_by_normalized_name(prov):
    p = prov.Provider(kind="compat", name="My LM Studio!", base_url="http://localhost:1234/v1")
    assert p.account == "compat.my-lm-studio"
    assert p.display_name == "My LM Studio!"


def test_named_kinds_carry_their_canonical_base_url(prov):
    assert prov.Provider(kind="openai").effective_base_url == prov.OPENAI_BASE_URL
    assert prov.Provider(kind="openrouter").effective_base_url == prov.OPENROUTER_BASE_URL


# --- endpoint policy --------------------------------------------------------


@pytest.mark.parametrize("url", [
    "https://api.example.com/v1",
    "http://localhost:1234/v1",
    "http://127.0.0.1:11434/v1",
])
def test_endpoint_policy_allows_https_and_loopback_http(prov, url):
    assert prov.endpoint_acceptable(url)


@pytest.mark.parametrize("url", [
    "http://api.example.com/v1",   # a key in clear to a remote host
    "ftp://example.com",
    "https://",
    "not a url",
    "",
])
def test_endpoint_policy_rejects_everything_else(prov, url):
    assert not prov.endpoint_acceptable(url)


# --- provider_from_dict -----------------------------------------------------


def test_provider_from_dict_rejects_unknown_kind(prov):
    with pytest.raises(prov.ProviderError):
        prov.provider_from_dict({"kind": "gemini"})


def test_provider_from_dict_rejects_non_object(prov):
    with pytest.raises(prov.ProviderError):
        prov.provider_from_dict("openai")


def test_provider_from_dict_ignores_name_for_named_kinds(prov):
    p = prov.provider_from_dict({"kind": "openai", "name": "ignored"})
    assert p == prov.Provider(kind="openai")


def test_provider_from_dict_requires_name_and_url_for_compat(prov):
    with pytest.raises(prov.ProviderError):
        prov.provider_from_dict({"kind": "compat", "base_url": "https://x.co/v1"})
    with pytest.raises(prov.ProviderError):
        prov.provider_from_dict({"kind": "compat", "name": "x"})


def test_provider_from_dict_rejects_name_that_normalizes_to_nothing(prov):
    with pytest.raises(prov.ProviderError):
        prov.provider_from_dict({
            "kind": "compat", "name": "!!!", "base_url": "https://x.co/v1",
        })


def test_provider_from_dict_rejects_plaintext_remote_endpoint(prov):
    with pytest.raises(prov.ProviderError):
        prov.provider_from_dict({
            "kind": "compat", "name": "x", "base_url": "http://evil.example/v1",
        })


def test_provider_from_dict_strips_trailing_slash(prov):
    p = prov.provider_from_dict({
        "kind": "compat", "name": "x", "base_url": "https://x.co/v1/",
    })
    assert p.base_url == "https://x.co/v1"


# --- credentials ------------------------------------------------------------


def test_scrub_key_removes_the_key_and_its_prefix(prov):
    key = "sk-abcdefghijklmnop"
    assert prov.scrub_key(f"bad key {key} here", key) == "bad key *** here"
    # A provider echoing a truncated form is caught too (first 8 chars).
    assert prov.scrub_key("bad key sk-abcdefg...", key) == "bad key ***fg..."


def test_scrub_key_leaves_short_keys_unprefixed(prov):
    # An 8-char prefix of a short key would match arbitrary text.
    assert prov.scrub_key("abcdefgh and more", "abcdefgh") == "*** and more"


def test_set_credentials_drops_blanks_and_reports_accounts(prov):
    configured = prov.set_credentials({"openai": "k1", "anthropic": "  ", "compat.x": "k2"})
    assert configured == ["compat.x", "openai"]
    assert prov.has_key(prov.Provider(kind="openai"))
    assert not prov.has_key(prov.Provider(kind="anthropic"))


def test_set_credentials_replaces_wholesale(prov):
    prov.set_credentials({"openai": "k1"})
    assert prov.set_credentials({"anthropic": "k2"}) == ["anthropic"]
    assert not prov.has_key(prov.Provider(kind="openai"))


def test_set_credentials_rejects_non_string_values(prov):
    with pytest.raises(prov.ProviderError):
        prov.set_credentials({"openai": 42})
    with pytest.raises(prov.ProviderError):
        prov.set_credentials("openai=k")


# --- parameter mapping ------------------------------------------------------


def test_build_params_drops_fields_the_kind_cannot_use(prov):
    raw = {"temperature": 0.5, "top_k": 40, "seed": 7, "frequency_penalty": 0.1}
    assert prov.build_params(raw, "openai") == {
        "temperature": 0.5, "seed": 7, "frequency_penalty": 0.1,
    }
    assert prov.build_params(raw, "anthropic") == {"temperature": 0.5, "top_k": 40}
    assert prov.build_params(raw, "openrouter") == raw


def test_build_params_drops_local_only_fields(prov):
    raw = {
        "mirostat": 2, "mirostat_tau": 5.0, "min_p": 0.05, "repeat_penalty": 1.1,
        "n_gpu_layers": 99, "n_ctx": 4096, "grammar": "root ::= x",
    }
    for kind in prov.KINDS:
        assert prov.build_params(raw, kind) == {}


def test_build_params_casts_and_skips_uncastable(prov):
    out = prov.build_params({"temperature": "0.5", "max_tokens": "256", "seed": "abc"}, "openai")
    assert out == {"temperature": 0.5, "max_tokens": 256}


def test_build_params_ignores_blanks_and_non_dicts(prov):
    assert prov.build_params({"temperature": "", "seed": None}, "openai") == {}
    assert prov.build_params(None, "openai") == {}


def test_build_params_parses_comma_separated_stop_sequences(prov):
    out = prov.build_params({"stop_sequences": "a, b ,, c"}, "openai")
    assert out == {"stop_sequences": ["a", "b", "c"]}
    out = prov.build_params({"stop_sequences": ["x", " "]}, "anthropic")
    assert out == {"stop_sequences": ["x"]}


# --- request bodies ---------------------------------------------------------

_MSGS = [{"role": "user", "content": "hi"}]


def test_openai_body_uses_max_completion_tokens(prov):
    # o-series and gpt-5 reject max_tokens.
    body = prov.openai_body("gpt-5.4", _MSGS, {"max_tokens": 99}, "openai")
    assert body["max_completion_tokens"] == 99
    assert "max_tokens" not in body


@pytest.mark.parametrize("kind", ["openrouter", "compat"])
def test_openrouter_and_compat_bodies_use_max_tokens(prov, kind):
    # Compat servers predating the rename only know max_tokens.
    body = prov.openai_body("m", _MSGS, {"max_tokens": 99}, kind)
    assert body["max_tokens"] == 99
    assert "max_completion_tokens" not in body


def test_openai_body_defaults_max_tokens(prov):
    body = prov.openai_body("gpt-5.4", _MSGS, {}, "openai")
    assert body["max_completion_tokens"] == prov.DEFAULT_MAX_TOKENS


def test_openai_body_trims_stop_sequences_to_four(prov):
    body = prov.openai_body(
        "m", _MSGS, {"stop_sequences": ["a", "b", "c", "d", "e"]}, "compat",
    )
    assert body["stop"] == ["a", "b", "c", "d"]


def test_openai_body_omits_unset_params(prov):
    body = prov.openai_body("m", _MSGS, {}, "openai")
    assert set(body) == {"model", "messages", "max_completion_tokens"}


def test_anthropic_body_hoists_the_system_prompt(prov):
    body = prov.anthropic_body("claude", [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ], {})
    assert body["system"] == "be terse"
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_body_joins_multiple_system_messages(prov):
    body = prov.anthropic_body("claude", [
        {"role": "system", "content": "one"},
        {"role": "system", "content": "two"},
        {"role": "user", "content": "hi"},
    ], {})
    assert body["system"] == "one\n\ntwo"


def test_anthropic_body_merges_consecutive_same_role_turns(prov):
    body = prov.anthropic_body("claude", [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"},
    ], {})
    assert body["messages"] == [
        {"role": "user", "content": "a\n\nb"},
        {"role": "assistant", "content": "c"},
    ]


def test_anthropic_body_drops_a_leading_assistant_turn(prov):
    body = prov.anthropic_body("claude", [
        {"role": "assistant", "content": "orphan"},
        {"role": "user", "content": "hi"},
    ], {})
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_body_requires_a_user_message(prov):
    with pytest.raises(prov.ProviderError):
        prov.anthropic_body("claude", [{"role": "system", "content": "s"}], {})


def test_anthropic_body_always_sets_max_tokens(prov):
    # The API rejects a request without it.
    assert prov.anthropic_body("claude", _MSGS, {})["max_tokens"] == prov.DEFAULT_MAX_TOKENS


# --- model lists ------------------------------------------------------------


def test_filter_chat_models_drops_non_chat_openai_ids(prov):
    models = [{"id": x} for x in (
        "gpt-5.4", "text-embedding-3-large", "whisper-1", "tts-1",
        "dall-e-3", "omni-moderation-latest", "gpt-4o-realtime-preview",
    )]
    assert prov.filter_chat_models("openai", models) == [{"id": "gpt-5.4"}]


def test_filter_chat_models_leaves_other_kinds_alone(prov):
    # A compat server's list is whatever the user runs; OpenAI's naming
    # rules would hide legitimate models.
    models = [{"id": "whisper-tuned-chat"}]
    for kind in ("anthropic", "openrouter", "compat"):
        assert prov.filter_chat_models(kind, models) == models


def test_model_cache_round_trip(prov, tmp_path):
    p = prov.Provider(kind="openai")
    payload = prov.write_cache(tmp_path, p, [{"id": "gpt-5.4"}])
    assert prov.cache_path(tmp_path, p).name == "models.openai.json"
    assert prov.read_cache(tmp_path, p)["models"] == [{"id": "gpt-5.4"}]
    assert not prov.is_stale(payload)


def test_model_cache_reports_staleness_past_the_ttl(prov):
    assert prov.is_stale({"models": [], "fetched_at": time.time() - prov.MODEL_CACHE_TTL_S - 1})
    assert prov.is_stale({"models": []})


def test_read_cache_tolerates_garbage(prov, tmp_path):
    p = prov.Provider(kind="openai")
    prov.cache_path(tmp_path, p).parent.mkdir(parents=True, exist_ok=True)
    prov.cache_path(tmp_path, p).write_text("{not json", "utf-8")
    assert prov.read_cache(tmp_path, p) is None


def test_list_models_fetches_sorts_and_caches(prov, tmp_path, monkeypatch):
    prov.set_credentials({"openai": "k"})
    _install_stub(prov, monkeypatch, _StubClient(models=[{"id": "b"}, {"id": "a"}]))
    out = prov.list_models(prov.Provider(kind="openai"), tmp_path)
    assert [m["id"] for m in out["models"]] == ["a", "b"]
    assert out["cached"] is False
    # Second call is served from disk without touching the client.
    _install_stub(prov, monkeypatch, _StubClient(raises=RuntimeError("no network")))
    again = prov.list_models(prov.Provider(kind="openai"), tmp_path)
    assert again["cached"] is True
    assert [m["id"] for m in again["models"]] == ["a", "b"]


def test_list_models_serves_the_cache_without_a_key(prov, tmp_path):
    p = prov.Provider(kind="openai")
    prov.write_cache(tmp_path, p, [{"id": "a"}])
    out = prov.list_models(p, tmp_path, refresh=True)
    assert out["cached"] is True


def test_list_models_401s_with_no_key_and_no_cache(prov, tmp_path):
    with pytest.raises(prov.ProviderError) as exc:
        prov.list_models(prov.Provider(kind="openai"), tmp_path)
    assert exc.value.status == 401


def test_list_models_serves_stale_cache_when_the_refresh_fails(prov, tmp_path, monkeypatch):
    p = prov.Provider(kind="openai")
    prov.write_cache(tmp_path, p, [{"id": "a"}])
    prov.set_credentials({"openai": "k"})
    _install_stub(prov, monkeypatch, _StubClient(raises=RuntimeError("boom")))
    out = prov.list_models(p, tmp_path, refresh=True)
    assert out["cached"] is True and out["stale"] is True
    assert [m["id"] for m in out["models"]] == ["a"]


def test_list_models_raises_when_refresh_fails_with_no_cache(prov, tmp_path, monkeypatch):
    prov.set_credentials({"openai": "k"})
    _install_stub(prov, monkeypatch, _StubClient(raises=RuntimeError("boom")))
    with pytest.raises(prov.ProviderError) as exc:
        prov.list_models(prov.Provider(kind="openai"), tmp_path)
    assert exc.value.status == 502


# --- /info ------------------------------------------------------------------


def test_info_reports_remote_kinds_sdks_and_params(client, auth, prov):
    remote = client.get("/info", headers=auth).json()["remote"]
    assert remote["kinds"] == list(prov.KINDS)
    assert set(remote["sdks"]) == {"openai", "anthropic"}
    # Per-kind row gating for the Parameters pane.
    assert "top_k" not in remote["supported_params"]["openai"]
    assert "top_k" in remote["supported_params"]["anthropic"]
    assert "mirostat" not in remote["supported_params"]["openrouter"]


def test_info_does_not_report_which_accounts_have_keys(client, auth):
    # Runtime state; lives on /providers/credentials.
    assert "configured" not in client.get("/info", headers=auth).json()["remote"]


# --- /providers/credentials -------------------------------------------------


def test_credentials_round_trip_returns_accounts_not_keys(client, auth):
    r = client.post("/providers/credentials", headers=auth, json={
        "credentials": {"openai": "sk-secret", "compat.lm": "local-key"},
    })
    assert r.status_code == 200
    assert r.json() == {"configured": ["compat.lm", "openai"]}
    body = client.get("/providers/credentials", headers=auth).text
    assert "sk-secret" not in body and "local-key" not in body
    assert json.loads(body) == {"configured": ["compat.lm", "openai"]}


def test_credentials_post_replaces_the_set(client, auth):
    client.post("/providers/credentials", headers=auth,
                json={"credentials": {"openai": "k"}})
    client.post("/providers/credentials", headers=auth,
                json={"credentials": {"anthropic": "k"}})
    assert client.get("/providers/credentials", headers=auth).json() == {
        "configured": ["anthropic"],
    }


def test_credentials_400s_on_a_bad_shape(client, auth):
    r = client.post("/providers/credentials", headers=auth, json={"credentials": [1]})
    assert r.status_code == 400


@pytest.mark.parametrize("method,path", [
    ("get", "/providers/credentials"),
    ("post", "/providers/credentials"),
    ("get", "/providers/models?kind=openai"),
])
def test_provider_endpoints_require_the_bearer(client, method, path):
    assert getattr(client, method)(path).status_code == 401


# --- /providers/models ------------------------------------------------------


def test_providers_models_endpoint_serves_the_cache(client, auth, prov, sidecar_app, monkeypatch):
    prov.write_cache(sidecar_app.PROVIDERS_DIR, prov.Provider(kind="anthropic"),
                     [{"id": "claude-opus-4-7"}])
    r = client.get("/providers/models?kind=anthropic", headers=auth)
    assert r.status_code == 200
    assert r.json()["models"] == [{"id": "claude-opus-4-7"}]
    assert r.json()["cached"] is True


def test_providers_models_endpoint_401s_without_a_key(client, auth):
    assert client.get("/providers/models?kind=openai", headers=auth).status_code == 401


def test_providers_models_endpoint_400s_on_a_bad_compat_url(client, auth):
    r = client.get(
        "/providers/models?kind=compat&name=x&base_url=http://evil.example/v1",
        headers=auth,
    )
    assert r.status_code == 400


def test_providers_models_endpoint_caches_per_compat_name(client, auth, prov, sidecar_app):
    # Two compat endpoints must not share a list.
    a = prov.provider_from_dict({"kind": "compat", "name": "A", "base_url": "https://a.co/v1"})
    b = prov.provider_from_dict({"kind": "compat", "name": "B", "base_url": "https://b.co/v1"})
    prov.write_cache(sidecar_app.PROVIDERS_DIR, a, [{"id": "from-a"}])
    prov.write_cache(sidecar_app.PROVIDERS_DIR, b, [{"id": "from-b"}])
    r = client.get("/providers/models?kind=compat&name=A&base_url=https://a.co/v1", headers=auth)
    assert r.json()["models"] == [{"id": "from-a"}]


# --- /chat, remote branch ---------------------------------------------------


def test_chat_remote_401s_without_a_key(client, auth):
    r = client.post("/chat", headers=auth, json={
        "provider": {"kind": "openai"},
        "model": "gpt-5.4",
        "messages": _MSGS,
    })
    assert r.status_code == 401


def test_chat_remote_400s_on_an_unknown_kind(client, auth):
    r = client.post("/chat", headers=auth, json={
        "provider": {"kind": "gemini"}, "model": "x", "messages": _MSGS,
    })
    assert r.status_code == 400


def test_chat_remote_400s_without_a_model(client, auth, prov):
    prov.set_credentials({"openai": "k"})
    r = client.post("/chat", headers=auth, json={
        "provider": {"kind": "openai"}, "messages": _MSGS,
    })
    assert r.status_code == 400


def test_chat_remote_400s_on_a_plaintext_compat_endpoint(client, auth):
    r = client.post("/chat", headers=auth, json={
        "provider": {"kind": "compat", "name": "x", "base_url": "http://evil.example/v1"},
        "model": "m",
        "messages": _MSGS,
    })
    assert r.status_code == 400


def test_chat_remote_streams_the_same_sse_frames_as_local(client, auth, prov, monkeypatch):
    prov.set_credentials({"anthropic": "k"})
    _install_stub(prov, monkeypatch, _StubClient(chunks=["hel", "lo"]))
    text = _sse_text(client, {
        "provider": {"kind": "anthropic"},
        "model": "claude-opus-4-7",
        "messages": _MSGS,
    }, auth)
    assert '"text": "hel"' in text and '"text": "lo"' in text
    assert "data: [DONE]" in text


def test_chat_remote_forwards_only_the_params_the_kind_accepts(client, auth, prov, monkeypatch):
    prov.set_credentials({"openai": "k"})
    stub = _install_stub(prov, monkeypatch, _StubClient(chunks=["x"]))
    _sse_text(client, {
        "provider": {"kind": "openai"},
        "model": "gpt-5.4",
        "messages": _MSGS,
        "params": {"temperature": 0.3, "top_k": 40, "mirostat": 2},
    }, auth)
    assert stub.calls[0][2] == {"temperature": 0.3}


def test_chat_remote_reports_an_upstream_failure_as_an_error_frame(client, auth, prov, monkeypatch):
    prov.set_credentials({"openai": "k"})
    _install_stub(prov, monkeypatch, _StubClient(raises=RuntimeError("rate limited")))
    text = _sse_text(client, {
        "provider": {"kind": "openai"}, "model": "gpt-5.4", "messages": _MSGS,
    }, auth)
    assert '"error"' in text and "rate limited" in text


def test_chat_remote_scrubs_the_key_from_an_upstream_error(client, auth, prov, monkeypatch):
    key = "sk-abcdefghijklmnop"
    prov.set_credentials({"openai": key})
    _install_stub(prov, monkeypatch, _StubClient(raises=RuntimeError(f"bad key {key}")))
    text = _sse_text(client, {
        "provider": {"kind": "openai"}, "model": "gpt-5.4", "messages": _MSGS,
    }, auth)
    assert key not in text
    assert "***" in text


def test_chat_remote_ignores_model_path(client, auth, prov, monkeypatch, fake_model):
    # A body carrying both is unambiguous: the provider wins.
    prov.set_credentials({"openai": "k"})
    stub = _install_stub(prov, monkeypatch, _StubClient(chunks=["remote"]))
    text = _sse_text(client, {
        "provider": {"kind": "openai"},
        "model": "gpt-5.4",
        "model_path": fake_model,
        "messages": _MSGS,
    }, auth)
    assert '"text": "remote"' in text
    assert stub.calls


def test_chat_still_400s_without_a_model_path_or_provider(client, auth):
    r = client.post("/chat", headers=auth, json={"messages": _MSGS})
    assert r.status_code == 400
    assert "provider" in r.json()["detail"]


# --- usage accounting -------------------------------------------------------


def test_record_usage_skips_an_empty_report(prov, tmp_path):
    db = tmp_path / "usage.db"
    p = prov.Provider(kind="openai")
    assert prov.record_usage(db, p, "gpt-5.4", {}) is False
    assert prov.record_usage(db, p, "gpt-5.4", {"prompt_tokens": 0}) is False
    assert prov.usage_totals(db) == []


def test_record_usage_groups_by_account_and_model(prov, tmp_path):
    db = tmp_path / "usage.db"
    oa = prov.Provider(kind="openai")
    an = prov.Provider(kind="anthropic")
    prov.record_usage(db, oa, "gpt-5.4", {"prompt_tokens": 10, "completion_tokens": 5})
    prov.record_usage(db, oa, "gpt-5.4", {"prompt_tokens": 7, "completion_tokens": 3})
    prov.record_usage(db, an, "claude", {"prompt_tokens": 1, "completion_tokens": 1})
    totals = {(t["account"], t["model"]): t for t in prov.usage_totals(db)}
    assert totals[("openai", "gpt-5.4")]["calls"] == 2
    assert totals[("openai", "gpt-5.4")]["prompt_tokens"] == 17
    assert totals[("openai", "gpt-5.4")]["completion_tokens"] == 8
    assert totals[("anthropic", "claude")]["calls"] == 1


def test_usage_totals_honour_the_since_floor(prov, tmp_path):
    db = tmp_path / "usage.db"
    prov.record_usage(db, prov.Provider(kind="openai"), "m",
                      {"prompt_tokens": 1, "completion_tokens": 1})
    assert prov.usage_totals(db, since=time.time() + 60) == []
    assert len(prov.usage_totals(db, since=0)) == 1


def test_usage_totals_on_a_missing_db_is_empty(prov, tmp_path):
    assert prov.usage_totals(tmp_path / "nope.db") == []


def test_record_usage_never_raises(prov, tmp_path):
    """A generation that already succeeded must not fail on accounting."""
    # A directory where the db file should be: sqlite cannot open it.
    bad = tmp_path / "blocked.db"
    bad.mkdir()
    assert prov.record_usage(bad, prov.Provider(kind="openai"), "m",
                             {"prompt_tokens": 1}) is False


def test_clear_usage_reports_what_it_removed(prov, tmp_path):
    db = tmp_path / "usage.db"
    prov.record_usage(db, prov.Provider(kind="openai"), "m",
                      {"prompt_tokens": 1, "completion_tokens": 1})
    assert prov.clear_usage(db) == 1
    assert prov.usage_totals(db) == []


def test_chat_remote_records_usage_and_sends_a_usage_frame(
    client, auth, prov, sidecar_app, monkeypatch,
):
    prov.set_credentials({"openai": "k"})
    _install_stub(prov, monkeypatch, _StubClient(
        chunks=["hi"], usage={"prompt_tokens": 12, "completion_tokens": 4},
    ))
    text = _sse_text(client, {
        "provider": {"kind": "openai"},
        "model": "gpt-5.4",
        "messages": _MSGS,
        "source": "script:job-1",
    }, auth)
    assert '"prompt_tokens": 12' in text
    # The usage frame precedes [DONE] so a reader that stops there sees it.
    assert text.index('"usage"') < text.index("[DONE]")

    totals = prov.usage_totals(sidecar_app.USAGE_DB)
    assert len(totals) == 1
    assert totals[0]["account"] == "openai"
    assert totals[0]["model"] == "gpt-5.4"
    assert totals[0]["completion_tokens"] == 4


def test_chat_remote_records_nothing_when_the_provider_reports_nothing(
    client, auth, prov, sidecar_app, monkeypatch,
):
    """A compat endpoint that does not report usage must not book zeros."""
    prov.set_credentials({"compat.lm": "k"})
    _install_stub(prov, monkeypatch, _StubClient(chunks=["hi"]))
    text = _sse_text(client, {
        "provider": {"kind": "compat", "name": "lm", "base_url": "https://x.co/v1"},
        "model": "m",
        "messages": _MSGS,
    }, auth)
    assert '"usage"' not in text
    assert prov.usage_totals(sidecar_app.USAGE_DB) == []


def test_usage_endpoint_returns_totals_and_clears(client, auth, prov, sidecar_app):
    prov.record_usage(sidecar_app.USAGE_DB, prov.Provider(kind="openai"), "m",
                      {"prompt_tokens": 3, "completion_tokens": 2}, "chat")
    r = client.get("/providers/usage", headers=auth)
    assert r.status_code == 200
    assert r.json()["totals"][0]["prompt_tokens"] == 3
    assert client.delete("/providers/usage", headers=auth).json() == {"deleted": 1}
    assert client.get("/providers/usage", headers=auth).json()["totals"] == []


def test_usage_endpoint_requires_the_bearer(client):
    assert client.get("/providers/usage").status_code == 401


# --- keyless loopback endpoints ---------------------------------------------


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1",
    "http://127.0.0.1:1234/v1",
])
def test_loopback_compat_endpoints_need_no_key(prov, url):
    """Ollama and LM Studio authenticate nothing; demanding a key there
    means asking the user to invent one."""
    p = prov.Provider(kind="compat", name="local", base_url=url)
    assert p.needs_key is False
    assert prov.usable(p) is True
    assert prov.has_key(p) is False


def test_remote_compat_endpoints_still_need_a_key(prov):
    p = prov.Provider(kind="compat", name="groq", base_url="https://api.groq.com/openai/v1")
    assert p.needs_key is True
    assert prov.usable(p) is False


@pytest.mark.parametrize("kind", ["openai", "anthropic", "openrouter"])
def test_named_kinds_always_need_a_key(prov, kind):
    p = prov.Provider(kind=kind)
    assert p.needs_key is True
    assert prov.usable(p) is False


def test_keyless_endpoint_gets_a_placeholder_credential(prov):
    """The OpenAI SDK refuses to construct a client with a falsy api_key."""
    p = prov.Provider(kind="compat", name="local", base_url="http://localhost:11434/v1")
    assert prov._key_for(p) == prov.KEYLESS_PLACEHOLDER
    assert prov.KEYLESS_PLACEHOLDER


def test_a_configured_token_wins_over_the_placeholder(prov):
    """A local server started with its own token still gets that token."""
    p = prov.Provider(kind="compat", name="local", base_url="http://localhost:1234/v1")
    prov.set_credentials({p.account: "real-token"})
    assert prov._key_for(p) == "real-token"


def test_chat_to_a_keyless_loopback_endpoint_is_not_401(client, auth, prov, monkeypatch):
    _install_stub(prov, monkeypatch, _StubClient(chunks=["local"]))
    text = _sse_text(client, {
        "provider": {"kind": "compat", "name": "Ollama",
                     "base_url": "http://localhost:11434/v1"},
        "model": "llama4",
        "messages": _MSGS,
    }, auth)
    assert '"text": "local"' in text


def test_models_for_a_keyless_loopback_endpoint_is_not_401(client, auth, prov, monkeypatch):
    _install_stub(prov, monkeypatch, _StubClient(models=[{"id": "llama4"}]))
    r = client.get(
        "/providers/models?kind=compat&name=Ollama&base_url=http://localhost:11434/v1",
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["models"] == [{"id": "llama4"}]
