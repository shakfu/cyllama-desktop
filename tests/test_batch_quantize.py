"""Phase 9: /jobs/batch + /jobs/models/quantize."""
from __future__ import annotations

import json
import time
from pathlib import Path


def _drain(client, auth, job_id, max_wait=2.0):
    deadline = time.time() + max_wait
    events: list[dict] = []
    with client.stream("GET", f"/jobs/{job_id}/events", headers=auth) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            events.append(ev)
            if ev.get("type") == "done":
                return events
            if time.time() > deadline:
                raise AssertionError(f"timeout; got {events!r}")
    return events


# --- /info / feature flags --------------------------------------------------


def test_info_features_includes_batch_and_quantize(client, auth):
    body = client.get("/info", headers=auth).json()
    assert body["features"].get("batch") is True
    assert body["features"].get("quantize") is True


def test_quantize_ftypes_endpoint(client, auth):
    r = client.get("/quantize/ftypes", headers=auth)
    assert r.status_code == 200
    ftypes = r.json()["ftypes"]
    assert ftypes["Q4_K_M"] == 15
    assert ftypes["Q8_0"] == 7


# --- /jobs/batch ------------------------------------------------------------


def test_batch_400_on_missing_model(client, auth):
    r = client.post("/jobs/batch", json={"prompts": ["a"]}, headers=auth)
    assert r.status_code == 400


def test_batch_400_on_missing_prompts(client, auth, fake_model):
    r = client.post("/jobs/batch", json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_batch_400_on_empty_prompts(client, auth, fake_model):
    r = client.post("/jobs/batch", json={
        "model_path": fake_model, "prompts": ["  ", ""],
    }, headers=auth)
    assert r.status_code == 400


def test_batch_400_when_too_many(client, auth, fake_model, sidecar_app):
    too_many = ["q"] * (sidecar_app._BATCH_MAX_PROMPTS + 1)
    r = client.post("/jobs/batch", json={
        "model_path": fake_model, "prompts": too_many,
    }, headers=auth)
    assert r.status_code == 400


def test_batch_501_when_unavailable(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "batch", False)
    r = client.post("/jobs/batch", json={
        "model_path": fake_model, "prompts": ["x"],
    }, headers=auth)
    assert r.status_code == 501


def test_batch_streams_per_prompt_then_result(client, auth, fake_model, sidecar_app):
    prompts = ["one", "two", "three"]
    r = client.post("/jobs/batch", json={
        "model_path": fake_model, "prompts": prompts,
    }, headers=auth)
    job_id = r.json()["job_id"]
    events = _drain(client, auth, job_id)

    rows = [ev for ev in events if ev.get("type") == "result_row"]
    assert [r["prompt"] for r in rows] == prompts
    assert [r["response"] for r in rows] == [f"ECHO: {p}" for p in prompts]

    final = [ev for ev in events if ev.get("type") == "result"]
    assert len(final) == 1
    res = final[0]["result"]
    assert res["n"] == 3
    assert res["artifact_name"] == "outputs.jsonl"
    assert res["artifact_url"] == f"/jobs/{job_id}/artifact/outputs.jsonl"

    # Artifact written to disk under the job's artifact dir.
    out = Path(sidecar_app.ARTIFACTS_DIR) / job_id / "outputs.jsonl"
    assert out.is_file()
    body = out.read_text("utf-8").strip().split("\n")
    assert len(body) == 3
    assert json.loads(body[0])["response"] == "ECHO: one"


def test_batch_accepts_newline_string_prompts(client, auth, fake_model):
    r = client.post("/jobs/batch", json={
        "model_path": fake_model,
        "prompts": "alpha\nbeta\n\ngamma\n",
    }, headers=auth)
    job_id = r.json()["job_id"]
    events = _drain(client, auth, job_id)
    rows = [ev["prompt"] for ev in events if ev.get("type") == "result_row"]
    assert rows == ["alpha", "beta", "gamma"]


# --- /jobs/models/quantize --------------------------------------------------


def test_quantize_400_on_missing_src(client, auth):
    r = client.post("/jobs/models/quantize", json={
        "dst_name": "out.gguf", "ftype": "Q4_K_M",
    }, headers=auth)
    assert r.status_code == 400


def test_quantize_400_on_missing_dst(client, auth, fake_model):
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "ftype": "Q4_K_M",
    }, headers=auth)
    assert r.status_code == 400


def test_quantize_400_on_path_separator_in_dst(client, auth, fake_model):
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "dst_name": "../escape.gguf", "ftype": "Q4_K_M",
    }, headers=auth)
    assert r.status_code == 400


def test_quantize_400_on_unknown_ftype_label(client, auth, fake_model):
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "dst_name": "out.gguf", "ftype": "MAGICAL_Q42",
    }, headers=auth)
    assert r.status_code == 400


def test_quantize_400_when_ftype_missing(client, auth, fake_model):
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "dst_name": "out.gguf",
    }, headers=auth)
    assert r.status_code == 400


def test_quantize_501_when_unavailable(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "quantize", False)
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "dst_name": "out.gguf", "ftype": "Q4_K_M",
    }, headers=auth)
    assert r.status_code == 501


def test_quantize_writes_into_models_dir(client, auth, fake_model, sidecar_app):
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "dst_name": "myquant", "ftype": "Q4_K_M",
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    events = _drain(client, auth, job_id)
    final = [ev for ev in events if ev.get("type") == "result"]
    assert len(final) == 1
    res = final[0]["result"]
    assert res["name"] == "myquant.gguf"
    assert res["ftype"] == 15  # Q4_K_M

    out = Path(sidecar_app.MODELS_DIR) / "myquant.gguf"
    assert out.is_file()
    assert out.stat().st_size > 0


def test_quantize_409_on_collision(client, auth, fake_model, sidecar_app):
    # First quantize succeeds.
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "dst_name": "dup", "ftype": "Q4_K_M",
    }, headers=auth)
    _drain(client, auth, r.json()["job_id"])
    # Second one should refuse rather than silently overwrite.
    r = client.post("/jobs/models/quantize", json={
        "src_path": fake_model, "dst_name": "dup", "ftype": "Q4_K_M",
    }, headers=auth)
    assert r.status_code == 409
