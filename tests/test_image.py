"""Phase 6: /jobs/image/txt2img + image feature flag."""
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


def test_info_features_includes_image(client, auth):
    body = client.get("/info", headers=auth).json()
    assert body["features"].get("image") is True


def test_image_400_on_missing_model(client, auth):
    r = client.post("/jobs/image/txt2img", json={"prompt": "a cat"}, headers=auth)
    assert r.status_code == 400


def test_image_400_on_missing_prompt(client, auth, fake_model):
    r = client.post("/jobs/image/txt2img", json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_image_400_on_blank_prompt(client, auth, fake_model):
    r = client.post("/jobs/image/txt2img", json={
        "model_path": fake_model, "prompt": "   ",
    }, headers=auth)
    assert r.status_code == 400


def test_image_501_when_sd_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "image", False)
    r = client.post("/jobs/image/txt2img", json={
        "model_path": fake_model, "prompt": "a cat",
    }, headers=auth)
    assert r.status_code == 501


def test_image_emits_result_with_artifact(client, auth, fake_model, sidecar_app):
    r = client.post("/jobs/image/txt2img", json={
        "model_path": fake_model, "prompt": "a cat in a hat",
        "width": 256, "height": 256, "sample_steps": 4, "seed": 42,
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    events = _drain(client, auth, job_id)
    results = [ev for ev in events if ev.get("type") == "result"]
    assert len(results) == 1
    res = results[0]["result"]
    assert res["artifact_name"] == "output.png"
    assert res["artifact_url"] == f"/jobs/{job_id}/artifact/output.png"
    assert res["width"] == 256 and res["height"] == 256
    assert res["seed"] == 42

    # File written to <ARTIFACTS_DIR>/<job_id>/output.png.
    artifact_dir = Path(sidecar_app.ARTIFACTS_DIR) / job_id
    out = artifact_dir / "output.png"
    assert out.is_file()
    assert out.stat().st_size > 0


def test_image_artifact_endpoint_serves_png(client, auth, fake_model):
    r = client.post("/jobs/image/txt2img", json={
        "model_path": fake_model, "prompt": "x",
    }, headers=auth)
    job_id = r.json()["job_id"]
    _drain(client, auth, job_id)
    art = client.get(f"/jobs/{job_id}/artifact/output.png", headers=auth)
    assert art.status_code == 200
    assert art.content.startswith(b"\x89PNG")


def test_artifacts_image_list_empty(client, auth):
    r = client.get("/artifacts/image", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_artifacts_image_list_after_runs(client, auth, fake_model):
    # Drive two txt2img runs and assert both surface in the listing,
    # most-recent first.
    for _ in range(2):
        r = client.post("/jobs/image/txt2img", json={
            "model_path": fake_model, "prompt": "x",
        }, headers=auth)
        _drain(client, auth, r.json()["job_id"])
    r = client.get("/artifacts/image", headers=auth)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    # Each item carries the job_id, an url, and a positive size.
    for it in items:
        assert it["name"] == "output.png"
        assert it["url"] == f"/artifacts/{it['job_id']}/output.png"
        assert it["size"] > 0
    # mtime sort: first item is the most-recent.
    assert items[0]["mtime"] >= items[1]["mtime"]


def test_artifacts_serve_returns_png(client, auth, fake_model):
    r = client.post("/jobs/image/txt2img", json={
        "model_path": fake_model, "prompt": "x",
    }, headers=auth)
    job_id = r.json()["job_id"]
    _drain(client, auth, job_id)
    art = client.get(f"/artifacts/{job_id}/output.png", headers=auth)
    assert art.status_code == 200
    assert art.content.startswith(b"\x89PNG")


def test_artifacts_serve_404_when_missing(client, auth):
    r = client.get("/artifacts/nonexistent/output.png", headers=auth)
    assert r.status_code == 404


def test_artifacts_serve_400_on_bad_name(client, auth):
    # Refuses anything that isn't [A-Za-z0-9._-]+ so a hand-crafted
    # path with traversal segments can't slip through.
    r = client.get("/artifacts/abc/../etc-passwd", headers=auth)
    # FastAPI normalizes path traversal in the URL itself, so this
    # may end up as a 404 from the router. The defence-in-depth is
    # the route handler's own _ARTIFACT_NAME_RE.match -- which is
    # exercised by the bad-name test below.
    assert r.status_code in (400, 404)


def test_artifacts_serve_400_on_invalid_chars(client, auth):
    r = client.get("/artifacts/abc/output%2epng", headers=auth)
    # %2e decodes to '.' which is fine; we want to test something the
    # regex actually rejects. Hit the regex via a colon (not in
    # [A-Za-z0-9._-]).
    r2 = client.get("/artifacts/abc/foo:bar", headers=auth)
    assert r2.status_code == 400


def test_artifacts_image_list_requires_auth(client):
    r = client.get("/artifacts/image")
    assert r.status_code == 401


def test_image_clamps_extreme_dimensions(client, auth, fake_model, sidecar_app):
    r = client.post("/jobs/image/txt2img", json={
        "model_path": fake_model, "prompt": "x",
        "width": 99999, "height": 0, "sample_steps": 9999,
    }, headers=auth)
    job_id = r.json()["job_id"]
    events = _drain(client, auth, job_id)
    res = next(ev["result"] for ev in events if ev.get("type") == "result")
    assert res["width"] == sidecar_app._IMG_MAX_DIM
    assert res["height"] == sidecar_app._IMG_MIN_DIM
    assert res["sample_steps"] == sidecar_app._IMG_MAX_STEPS
