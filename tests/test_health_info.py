"""Health, /info, and bearer-auth enforcement."""
from __future__ import annotations


def test_health_unauthenticated(client):
    # /health is the one endpoint that bypasses auth -- the Electron host
    # uses it to detect "sidecar is up" before it has the token wired up.
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_info_requires_auth(client):
    r = client.get("/info")
    assert r.status_code == 401


def test_info_shape(client, auth):
    r = client.get("/info", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "cyllama" in body and "backends" in body and "sidecar" in body
    assert body["cyllama"]["version"] == "0.0.0-test"
    assert body["backends"] == {
        "cuda": False, "metal": True, "rocm": False,
        "vulkan": False, "sycl": False, "opencl": False,
    }
    assert "artifacts_dir" in body["sidecar"]


def test_bad_token_rejected(client):
    r = client.get("/info", headers={"authorization": "Bearer wrong"})
    assert r.status_code == 401


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


def test_info_exposes_granular_agent_flags(client, auth):
    """Each Phase-7+ agent class is independently probed at module load
    so the renderer can hide individual slash commands when the bundle
    is missing a class. The conftest stub installs all of them."""
    r = client.get("/info", headers=auth)
    feats = r.json()["features"]
    # Base agents flag remains the gate for the ReAct surface.
    assert feats.get("agents") is True
    # Granular flags: each independently True when the corresponding
    # cyllama.agents.* symbol resolves.
    assert feats.get("agents.constrained") is True
    assert feats.get("agents.contract") is True
    assert feats.get("agents.plan") is True
    assert feats.get("agents.reflect") is True
    assert feats.get("workflow") is True
    assert feats.get("agents.memory") is True
    # rag_tool stub not installed (cyllama.agents.rag_as_tool isn't wired
    # into the conftest). Asserting key existence + value catches
    # accidental removal of the probe.
    assert feats.get("agents.rag_tool") is False
