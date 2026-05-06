"""Phase 8: /server/{start,stop,status} + features.openai_server."""
from __future__ import annotations


def test_info_features_includes_openai_server(client, auth):
    body = client.get("/info", headers=auth).json()
    assert body["features"].get("openai_server") is True
    assert "embedded" in body["server_kinds"]
    assert "python" in body["server_kinds"]


def test_status_idle_by_default(client, auth):
    r = client.get("/server/status", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is False
    assert body["url"] is None


def test_start_400_on_missing_model(client, auth):
    r = client.post("/server/start", json={"kind": "embedded"}, headers=auth)
    assert r.status_code == 400


def test_start_400_on_missing_kind(client, auth, fake_model):
    r = client.post("/server/start", json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_start_400_on_unknown_kind(client, auth, fake_model):
    r = client.post("/server/start", json={
        "kind": "magical", "model_path": fake_model,
    }, headers=auth)
    assert r.status_code == 400


def test_start_embedded_happy_path(client, auth, fake_model, sidecar_app):
    r = client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model, "port": 8123,
    }, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["running"] is True
    assert body["kind"] == "embedded"
    assert body["host"] == "127.0.0.1"
    assert body["port"] == 8123
    assert body["url"] == "http://127.0.0.1:8123"

    inst = sidecar_app.cyllama.llama.server.embedded.EmbeddedServer.instances[-1]
    assert inst.started is True


def test_start_python_happy_path(client, auth, fake_model, sidecar_app):
    r = client.post("/server/start", json={
        "kind": "python", "model_path": fake_model, "port": 9000,
    }, headers=auth)
    assert r.status_code == 200
    assert r.json()["kind"] == "python"
    assert sidecar_app.cyllama.llama.server.python.PythonServer.instances[-1].started is True


def test_start_409_on_double_start(client, auth, fake_model):
    client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
    }, headers=auth)
    r = client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
    }, headers=auth)
    assert r.status_code == 409


def test_start_refuses_non_loopback_without_expose_lan(client, auth, fake_model):
    r = client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
        "host": "0.0.0.0",
    }, headers=auth)
    assert r.status_code == 400


def test_start_promotes_loopback_to_all_interfaces_when_expose_lan(client, auth, fake_model):
    r = client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
        "expose_lan": True,
    }, headers=auth)
    assert r.status_code == 200
    assert r.json()["host"] == "0.0.0.0"


def test_start_400_on_bad_port(client, auth, fake_model):
    r = client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
        "port": 70000,
    }, headers=auth)
    assert r.status_code == 400


def test_start_500_when_underlying_start_returns_false(client, auth, fake_model, sidecar_app):
    sidecar_app.cyllama.llama.server.embedded.EmbeddedServer.start_returns = False
    r = client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
    }, headers=auth)
    assert r.status_code == 500


def test_stop_when_idle(client, auth):
    r = client.post("/server/stop", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "wasRunning": False}


def test_start_then_stop_clears_state(client, auth, fake_model, sidecar_app):
    client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
    }, headers=auth)
    r = client.post("/server/stop", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "wasRunning": True}

    s = client.get("/server/status", headers=auth).json()
    assert s["running"] is False
    inst = sidecar_app.cyllama.llama.server.embedded.EmbeddedServer.instances[-1]
    assert inst.stopped is True


def test_501_when_feature_unavailable(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "openai_server", False)
    r = client.post("/server/start", json={
        "kind": "embedded", "model_path": fake_model,
    }, headers=auth)
    assert r.status_code == 501


def test_status_requires_auth(client):
    r = client.get("/server/status")
    assert r.status_code == 401
