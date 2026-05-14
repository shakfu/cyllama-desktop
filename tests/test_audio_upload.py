"""Voice prompts (composer mic): /audio/upload accepts WAV blobs.

The renderer captures audio via MediaRecorder, encodes to 16 kHz mono
WAV in-browser, posts it here, then fires /jobs/transcribe with the
returned path. This file covers the upload half; the encode + transcribe
path is exercised end-to-end at the Playwright layer.
"""
from __future__ import annotations

import io
from pathlib import Path


# Minimal RIFF WAVE header (44 bytes) + a sliver of silence. Enough
# bytes for the upload handler to accept; the extension matters more
# than the content for this test.
_TINY_WAV = (
    b"RIFF\x24\x00\x00\x00WAVE"
    b"fmt \x10\x00\x00\x00\x01\x00\x01\x00"
    b"\x80\x3e\x00\x00\x00\x7d\x00\x00"
    b"\x02\x00\x10\x00data\x00\x00\x00\x00"
)


def test_audio_upload_400_on_missing_file(client, auth):
    r = client.post("/audio/upload", headers=auth, data={})
    assert r.status_code == 400


def test_audio_upload_400_on_unsupported_extension(client, auth):
    r = client.post(
        "/audio/upload", headers=auth,
        files={"file": ("voice.mp3", _TINY_WAV, "audio/mpeg")},
    )
    assert r.status_code == 400
    assert ".mp3" in r.json()["detail"]


def test_audio_upload_writes_into_uploads_dir(client, auth, sidecar_app):
    r = client.post(
        "/audio/upload", headers=auth,
        files={"file": ("voice.wav", _TINY_WAV, "audio/wav")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "voice.wav"
    assert body["size"] == len(_TINY_WAV)
    assert body["url"].startswith("/chat/upload/")  # served via shared route
    assert body["path"].endswith(".wav")
    p = Path(body["path"])
    assert p.is_file()
    assert p.parent.resolve() == Path(sidecar_app.UPLOADS_DIR).resolve()


def test_audio_upload_413_on_oversized(client, auth, sidecar_app, monkeypatch):
    monkeypatch.setattr(sidecar_app, "_AUDIO_UPLOAD_MAX_BYTES", 16)
    r = client.post(
        "/audio/upload", headers=auth,
        files={"file": ("voice.wav", b"x" * 256, "audio/wav")},
    )
    assert r.status_code == 413


def test_audio_upload_accepts_wave_extension_too(client, auth):
    """``.wave`` is rare but valid -- whitelist mirrors /jobs/transcribe."""
    r = client.post(
        "/audio/upload", headers=auth,
        files={"file": ("recording.wave", _TINY_WAV, "audio/wav")},
    )
    assert r.status_code == 200
    assert r.json()["path"].endswith(".wave")
