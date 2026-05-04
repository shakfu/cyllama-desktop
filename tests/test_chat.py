"""Chat / tokenize / unload behaviour against the FakeLLM stub."""
from __future__ import annotations


def test_chat_400s_on_missing_model_path(client, auth):
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]}, headers=auth)
    assert r.status_code == 400


def test_chat_400s_on_missing_messages_and_prompt(client, auth, fake_model):
    r = client.post("/chat", json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_chat_400s_on_invalid_role(client, auth, fake_model):
    r = client.post("/chat", json={
        "model_path": fake_model,
        "messages": [{"role": "alien", "content": "hi"}],
    }, headers=auth)
    assert r.status_code == 400


def test_chat_400s_on_missing_model_file(client, auth, tmp_path):
    bogus = str(tmp_path / "does_not_exist.gguf")
    r = client.post("/chat", json={
        "model_path": bogus,
        "messages": [{"role": "user", "content": "hi"}],
    }, headers=auth)
    assert r.status_code == 400


def test_chat_streams_sse(client, auth, fake_model):
    body = {
        "model_path": fake_model,
        "messages": [{"role": "user", "content": "hi"}],
    }
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    # SSE frames separated by blank lines; final marker is [DONE].
    assert "data: [DONE]" in text
    # FakeLLM yields "hello", " ", "world"
    assert '"text": "hello"' in text
    assert '"text": "world"' in text


def test_tokenize_counts(client, auth, fake_model):
    r = client.post("/tokenize", json={
        "model_path": fake_model,
        "text": "one two three",
    }, headers=auth)
    assert r.status_code == 200
    assert r.json() == {"count": 3}


def test_tokenize_empty_text(client, auth, fake_model):
    r = client.post("/tokenize", json={"model_path": fake_model, "text": ""}, headers=auth)
    assert r.json() == {"count": 0}


def test_unload_releases_slot(client, auth, fake_model, sidecar_app):
    # Touch tokenize to force-load the model.
    client.post("/tokenize", json={"model_path": fake_model, "text": "x"}, headers=auth)
    r = client.post("/unload", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["unloaded"] == fake_model

    # Calling unload again is a no-op.
    r2 = client.post("/unload", headers=auth)
    assert r2.json() == {"unloaded": None}


def test_unknown_params_silently_dropped(client, auth, fake_model, sidecar_app):
    """Whitelist guards against new fields slipping through."""
    cfg = sidecar_app._build_config({
        "temperature": 0.5,
        "top_p": 0.9,
        "not_a_real_field": "boom",
    })
    # The stub's GenerationConfig is a dict for inspection.
    assert "not_a_real_field" not in cfg
    assert cfg["temperature"] == 0.5
    assert cfg["top_p"] == 0.9


def test_extended_sampler_whitelist_filtered_by_gc_signature(sidecar_app):
    """Phase 2.1: _build_config must filter against GenerationConfig's
    actual signature so a slider for a field cyllama doesn't accept
    (presence_penalty, mirostat, ...) is silently dropped instead of
    crashing chat with a TypeError. The conftest stub's GC accepts **kw,
    so we override _GC_ACCEPTED here to simulate a stricter cyllama."""
    saved = sidecar_app._GC_ACCEPTED
    try:
        sidecar_app._GC_ACCEPTED = {"temperature", "top_p", "top_k", "stop_sequences"}
        cfg = sidecar_app._build_config({
            "temperature": 0.5,
            "top_p": 0.9,
            "presence_penalty": 0.3,   # not accepted -> dropped
            "mirostat": 2,             # not accepted -> dropped
        })
        assert "presence_penalty" not in cfg
        assert "mirostat" not in cfg
        assert cfg["temperature"] == 0.5
        assert cfg["top_p"] == 0.9
    finally:
        sidecar_app._GC_ACCEPTED = saved


def test_supported_params_in_info(client, auth, sidecar_app):
    """/info must publish the list the renderer uses to hide UI rows."""
    r = client.get("/info", headers=auth)
    body = r.json()
    assert "supported_params" in body
    assert isinstance(body["supported_params"], list)
    # The conftest stub's GenerationConfig accepts **kwargs (lambda
    # **kw: dict(kw)), so its signature has no named params and
    # _supported_gc_params returns empty -- _SUPPORTED_PARAMS is then
    # just ['stop_sequences']. We assert the shape, not contents.
    assert all(isinstance(s, str) for s in body["supported_params"])
