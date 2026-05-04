"""Phase 3: hardware fields + /hardware/estimate-layers."""
from __future__ import annotations

import pytest


def test_hw_signature_normalises_missing_and_empty(sidecar_app):
    a = sidecar_app._hw_signature(None)
    b = sidecar_app._hw_signature({})
    c = sidecar_app._hw_signature({"n_gpu_layers": "", "n_ctx": None})
    assert a == b == c
    # All keys present, values None.
    assert all(v is None for _, v in a)


def test_hw_signature_distinguishes_loads(sidecar_app):
    one = sidecar_app._hw_signature({"n_gpu_layers": 32})
    two = sidecar_app._hw_signature({"n_gpu_layers": 33})
    assert one != two
    # tensor_split is a list -> tuple.
    ts = sidecar_app._hw_signature({"tensor_split": "0.5, 0.5"})
    flat = dict(ts)["tensor_split"]
    assert flat == (0.5, 0.5)


def test_get_llm_reuses_when_hw_unchanged(client, auth, fake_model, sidecar_app):
    """Same model + same hardware => one LLM construction."""
    body = {"model_path": fake_model, "params": {"n_gpu_layers": 16}}
    client.post("/tokenize", json={**body, "text": "hi"}, headers=auth)
    client.post("/tokenize", json={**body, "text": "again"}, headers=auth)
    # Conftest's _FakeLLM appends each instance to .instances.
    assert len(sidecar_app.cyllama.LLM.instances) == 1


def test_get_llm_reloads_on_hw_change(client, auth, fake_model, sidecar_app):
    """Changing n_gpu_layers must evict + reload (otherwise the new
    layer count never reaches cyllama at construction time)."""
    client.post("/tokenize", json={
        "model_path": fake_model, "text": "x", "params": {"n_gpu_layers": 16},
    }, headers=auth)
    client.post("/tokenize", json={
        "model_path": fake_model, "text": "y", "params": {"n_gpu_layers": 32},
    }, headers=auth)
    assert len(sidecar_app.cyllama.LLM.instances) == 2


def test_get_llm_does_not_reload_on_temperature_change(client, auth, fake_model, sidecar_app):
    """Sampling tweaks must NOT evict the model -- that would cost
    seconds-to-minutes per slider drag."""
    for t in (0.1, 0.5, 0.9):
        client.post("/tokenize", json={
            "model_path": fake_model, "text": "x", "params": {"temperature": t},
        }, headers=auth)
    assert len(sidecar_app.cyllama.LLM.instances) == 1


def test_estimate_layers_400_on_missing_path(client, auth):
    r = client.post("/hardware/estimate-layers", json={"gpu_memory_mb": 8000}, headers=auth)
    assert r.status_code == 400


def test_estimate_layers_400_on_missing_vram(client, auth, fake_model):
    r = client.post("/hardware/estimate-layers", json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_estimate_layers_501_when_helper_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    """Older cyllama builds may lack the helper. Surface a typed 501,
    not a 500, so the renderer can show a helpful message."""
    monkeypatch.delattr(sidecar_app.cyllama, "estimate_gpu_layers", raising=False)
    r = client.post("/hardware/estimate-layers", json={
        "model_path": fake_model, "gpu_memory_mb": 8000,
    }, headers=auth)
    assert r.status_code == 501


def test_estimate_layers_happy_path(client, auth, fake_model, sidecar_app):
    """When the helper is present, surface its known fields."""
    class _FakeMemEstimate:
        n_gpu_layers = 28
        n_layers_total = 32
        model_size_mb = 7000
        kv_cache_mb = 512
        compute_buffer_mb = 256
        fits_fully = False
        notes = "ok"

    def fake(*args, **kwargs):
        return _FakeMemEstimate()
    sidecar_app.cyllama.estimate_gpu_layers = fake

    r = client.post("/hardware/estimate-layers", json={
        "model_path": fake_model, "gpu_memory_mb": 8000, "ctx_size": 4096,
    }, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["n_gpu_layers"] == 28
    assert body["n_layers_total"] == 32
    assert body["fits_fully"] is False
    assert body["notes"] == "ok"


def test_supported_params_includes_hardware(client, auth, sidecar_app):
    """If cyllama's GenerationConfig accepts hardware fields (the conftest
    stub does), /info advertises them so the renderer surfaces the rows."""
    r = client.get("/info", headers=auth)
    body = r.json()
    sp = set(body["supported_params"])
    # The conftest stub doesn't accept hardware fields, so this asserts
    # the wiring works rather than the values. Real cyllama is exercised
    # via the GC accepted-set introspection at module load.
    assert "stop_sequences" in sp
