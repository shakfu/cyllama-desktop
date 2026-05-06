"""Phase 5: /jobs/transcribe + whisper feature flag."""
from __future__ import annotations

import json
import time


def _drain_events(client, auth, job_id, max_wait=2.0):
    """Subscribe to /jobs/<id>/events and return the parsed event list."""
    deadline = time.time() + max_wait
    events: list[dict] = []
    with client.stream("GET", f"/jobs/{job_id}/events", headers=auth) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if not line:
                continue
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            events.append(ev)
            if ev.get("type") == "done":
                return events
            if time.time() > deadline:
                raise AssertionError(f"timeout draining events; got {events!r}")
    return events


def test_info_features_includes_whisper(client, auth):
    body = client.get("/info", headers=auth).json()
    assert body["features"].get("whisper") is True


def test_transcribe_400_on_missing_audio_path(client, auth, fake_model):
    r = client.post("/jobs/transcribe", json={
        "model_path": fake_model,
    }, headers=auth)
    assert r.status_code == 400


def test_transcribe_400_on_missing_model_path(client, auth, fake_wav):
    r = client.post("/jobs/transcribe", json={
        "audio_path": fake_wav,
    }, headers=auth)
    assert r.status_code == 400


def test_transcribe_400_on_bogus_audio_path(client, auth, fake_model, tmp_path):
    bogus = str(tmp_path / "missing.wav")
    r = client.post("/jobs/transcribe", json={
        "audio_path": bogus, "model_path": fake_model,
    }, headers=auth)
    assert r.status_code == 400


def test_transcribe_501_when_whisper_missing(client, auth, fake_wav, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "whisper", False)
    r = client.post("/jobs/transcribe", json={
        "audio_path": fake_wav, "model_path": fake_model,
    }, headers=auth)
    assert r.status_code == 501


def test_transcribe_emits_segments_then_result(client, auth, fake_wav, fake_model):
    r = client.post("/jobs/transcribe", json={
        "audio_path": fake_wav, "model_path": fake_model, "language": "en",
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    events = _drain_events(client, auth, job_id)

    segments = [ev for ev in events if ev.get("type") == "segment"]
    assert len(segments) == 3
    assert segments[0]["text"] == "hello"   # leading space stripped server-side
    # Whisper t0/t1 are in 10ms units; sidecar converts to absolute ms.
    assert segments[0]["t0_ms"] == 0
    assert segments[0]["t1_ms"] == 1000
    assert segments[2]["t1_ms"] == 3200

    results = [ev for ev in events if ev.get("type") == "result"]
    assert len(results) == 1
    res = results[0]["result"]
    assert res["n_segments"] == 3
    assert res["language"] == "en"
    assert len(res["segments"]) == 3


def test_transcribe_rejects_non_wav_extension(client, auth, fake_model, tmp_path):
    mp3 = tmp_path / "audio.mp3"
    mp3.write_bytes(b"\x00")
    r = client.post("/jobs/transcribe", json={
        "audio_path": str(mp3), "model_path": fake_model,
    }, headers=auth)
    # Job is accepted (the suffix check runs in the producer), so this
    # surfaces as an error event in the stream rather than a 400 here.
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    events = _drain_events(client, auth, job_id)
    errors = [ev for ev in events if ev.get("type") == "error"]
    assert len(errors) == 1
    assert "WAV" in errors[0]["message"]


def test_transcribe_passes_options_to_full_params(client, auth, fake_wav, fake_model, sidecar_app):
    r = client.post("/jobs/transcribe", json={
        "audio_path": fake_wav, "model_path": fake_model,
        "language": "fr", "translate": True, "n_threads": 4,
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _drain_events(client, auth, job_id)

    instances = sidecar_app.cyllama.whisper.whisper_cpp.WhisperContext.instances
    assert len(instances) == 1
    _, params = instances[0].full_called_with
    assert params.language == "fr"
    assert params.translate is True
    assert params.n_threads == 4
    # Sidecar always silences the C library's stdout chatter.
    assert params.print_progress is False
    assert params.print_realtime is False
