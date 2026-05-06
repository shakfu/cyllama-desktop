"""Phase 2: grammar endpoint, /info.features, advanced /chat params."""
from __future__ import annotations


def test_info_exposes_features(client, auth):
    r = client.get("/info", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "features" in body
    feats = body["features"]
    # Stub installs json_schema_to_grammar; the rest depend on
    # GenerationConfig accepting matching kwargs (it doesn't in the stub).
    assert feats.get("json_schema_to_grammar") is True
    assert feats.get("grammar") is False
    assert feats.get("speculative") is False
    assert feats.get("ngram") is False


def test_grammar_from_schema_object(client, auth):
    r = client.post("/grammar/from-schema", json={
        "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
    }, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "grammar" in body
    assert "root" in body["grammar"]


def test_grammar_from_schema_string(client, auth):
    # Renderer may pass the schema as a JSON-encoded string.
    r = client.post("/grammar/from-schema", json={
        "schema": '{"type":"object","properties":{"x":{"type":"string"}}}',
    }, headers=auth)
    assert r.status_code == 200
    assert "grammar" in r.json()


def test_grammar_from_schema_bad_json_string(client, auth):
    r = client.post("/grammar/from-schema", json={"schema": "{not json"}, headers=auth)
    assert r.status_code == 400


def test_grammar_from_schema_non_object(client, auth):
    r = client.post("/grammar/from-schema", json={"schema": 42}, headers=auth)
    assert r.status_code == 400


def test_grammar_from_schema_requires_auth(client):
    r = client.post("/grammar/from-schema", json={"schema": {}})
    assert r.status_code == 401


def test_grammar_from_schema_501_when_missing(client, auth, sidecar_app, monkeypatch):
    # When cyllama doesn't expose the helper, the endpoint returns 501
    # so the renderer can show a clear "not available" message.
    monkeypatch.setattr(sidecar_app, "_JSON_SCHEMA_TO_GRAMMAR", None)
    r = client.post("/grammar/from-schema", json={"schema": {}}, headers=auth)
    assert r.status_code == 501


def test_chat_accepts_grammar_param_without_500(client, auth, fake_model):
    # Unsupported in stub GenerationConfig -> dropped silently.
    body = {
        "model_path": fake_model,
        "messages": [{"role": "user", "content": "hi"}],
        "params": {"grammar": "root ::= \"a\""},
    }
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "data: [DONE]" in text


def test_chat_accepts_speculative_param_without_500(client, auth, fake_model):
    body = {
        "model_path": fake_model,
        "messages": [{"role": "user", "content": "hi"}],
        "params": {"speculative": {
            "draft_model_path": "/tmp/draft.gguf",
            "n_max": 16, "n_min": 0, "p_split": 0.1, "p_min": 0.75,
        }},
    }
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "data: [DONE]" in text


def test_chat_accepts_ngram_toggle_without_500(client, auth, fake_model):
    body = {
        "model_path": fake_model,
        "messages": [{"role": "user", "content": "hi"}],
        "params": {"ngram": True},
    }
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    assert "data: [DONE]" in text


def test_build_config_drops_grammar_when_unsupported(sidecar_app):
    # _GC_ACCEPTED in the stub doesn't include "grammar"; verify the
    # build_config helper silently drops it rather than raising.
    cfg = sidecar_app._build_config({"temperature": 0.5, "grammar": "root ::= 'x'"})
    assert cfg is not None
    assert not hasattr(cfg, "grammar") or getattr(cfg, "grammar", None) is None
