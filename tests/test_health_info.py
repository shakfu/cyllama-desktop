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
