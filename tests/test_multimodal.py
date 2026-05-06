"""Multimodal: /chat/upload + /chat with images routed through ImageAnalyzer."""
from __future__ import annotations

import json
from pathlib import Path


_TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
    b"\x00\x01\x01\x01\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_info_features_includes_multimodal(client, auth):
    body = client.get("/info", headers=auth).json()
    assert body["features"].get("multimodal") is True
    assert "uploads_dir" in body["sidecar"]


# --- /chat/upload -----------------------------------------------------------


def test_upload_400_on_missing_file(client, auth):
    r = client.post("/chat/upload", headers=auth, data={})
    assert r.status_code == 400


def test_upload_400_on_unsupported_extension(client, auth):
    r = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("note.txt", b"hi", "text/plain")},
    )
    assert r.status_code == 400


def test_upload_writes_into_uploads_dir(client, auth, sidecar_app):
    r = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("photo.png", _TINY_PNG, "image/png")},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "photo.png"
    assert body["size"] == len(_TINY_PNG)
    assert body["url"].startswith("/chat/upload/")
    assert body["path"].endswith(".png")

    # The on-disk file lives under UPLOADS_DIR; the URL points at
    # exactly that filename.
    p = Path(body["path"])
    assert p.is_file()
    assert p.parent.resolve() == Path(sidecar_app.UPLOADS_DIR).resolve()


def test_upload_serve_roundtrip(client, auth):
    r = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("photo.png", _TINY_PNG, "image/png")},
    )
    assert r.status_code == 200
    url = r.json()["url"]
    served = client.get(url, headers=auth)
    assert served.status_code == 200
    assert served.content == _TINY_PNG


def test_upload_serve_404_when_missing(client, auth):
    r = client.get("/chat/upload/nope.png", headers=auth)
    assert r.status_code == 404


def test_upload_serve_400_on_invalid_name(client, auth):
    r = client.get("/chat/upload/foo:bar.png", headers=auth)
    assert r.status_code == 400


def test_upload_requires_auth(client):
    r = client.post(
        "/chat/upload",
        files={"file": ("photo.png", _TINY_PNG, "image/png")},
    )
    assert r.status_code == 401


def test_upload_size_cap(client, auth, sidecar_app):
    # Drop the cap to a tiny value to exercise the limit without
    # actually streaming 16 MiB through the test transport.
    sidecar_app._UPLOAD_MAX_BYTES = 4
    r = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("photo.png", _TINY_PNG, "image/png")},
    )
    assert r.status_code == 400


# --- /chat with images + mmproj --------------------------------------------


def test_chat_routes_to_image_analyzer_when_image_attached(
    client, auth, fake_model, sidecar_app, tmp_path,
):
    # Upload first so the path resolves under UPLOADS_DIR.
    up = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("photo.png", _TINY_PNG, "image/png")},
    ).json()
    image_path = up["path"]

    # The mmproj path just needs to exist; the stub ImageAnalyzer
    # ignores the file contents.
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF\x00")

    body = {
        "model_path": fake_model,
        "mmproj_path": str(mmproj),
        "messages": [
            {"role": "user", "content": "what is in this image?",
             "images": [{"path": image_path, "url": up["url"], "name": up["name"]}]},
        ],
    }
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())

    # ImageAnalyzer.answer_question ran (one instance, one call).
    instances = sidecar_app.cyllama.llama.mtmd.ImageAnalyzer.instances
    assert len(instances) == 1
    assert instances[0].mmproj_path == str(mmproj)
    assert instances[0].calls == [("what is in this image?", image_path)]

    # The single SSE chunk carries the analyzer's deterministic answer.
    assert "VISION ANSWER" in text
    assert "data: [DONE]" in text


def test_chat_string_image_path_also_works(
    client, auth, fake_model, sidecar_app, tmp_path,
):
    """Renderer may send images as plain strings (path) rather than dicts."""
    up = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("photo.png", _TINY_PNG, "image/png")},
    ).json()
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF\x00")

    body = {
        "model_path": fake_model,
        "mmproj_path": str(mmproj),
        "messages": [
            {"role": "user", "content": "describe", "images": [up["path"]]},
        ],
    }
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200
        "".join(r.iter_text())
    assert sidecar_app.cyllama.llama.mtmd.ImageAnalyzer.instances[-1].calls[0][1] == up["path"]


def test_chat_without_mmproj_falls_back_to_llm_chat(
    client, auth, fake_model, sidecar_app,
):
    """If no mmproj is configured, an attached image is silently
    ignored and the request flows through llm.chat() -- same as a
    plain chat. The renderer's UI gates on the mmproj being pinned
    so this is a safety net for hand-crafted requests."""
    up = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("photo.png", _TINY_PNG, "image/png")},
    ).json()
    body = {
        "model_path": fake_model,
        "messages": [
            {"role": "user", "content": "what's this?",
             "images": [up["path"]]},
        ],
    }
    with client.stream("POST", "/chat", json=body, headers=auth) as r:
        assert r.status_code == 200
        text = "".join(r.iter_text())
    # ImageAnalyzer was never built.
    assert sidecar_app.cyllama.llama.mtmd.ImageAnalyzer.instances == []
    # FakeLLM.chat yields ``hello world`` when streaming.
    assert "hello" in text


def test_chat_400_when_image_outside_uploads_dir(client, auth, fake_model, tmp_path):
    """An image path that resolves outside UPLOADS_DIR is refused
    (sandbox). Defence in depth: the renderer only sends paths it
    got back from /chat/upload, but a hand-crafted body shouldn't
    be able to coerce the analyzer into reading /etc/passwd."""
    rogue = tmp_path / "outside.png"
    rogue.write_bytes(_TINY_PNG)
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF\x00")
    body = {
        "model_path": fake_model,
        "mmproj_path": str(mmproj),
        "messages": [
            {"role": "user", "content": "x", "images": [str(rogue)]},
        ],
    }
    r = client.post("/chat", json=body, headers=auth)
    assert r.status_code == 400


def test_chat_400_when_image_missing(client, auth, fake_model, tmp_path):
    mmproj = tmp_path / "mmproj.gguf"
    mmproj.write_bytes(b"GGUF\x00")
    body = {
        "model_path": fake_model,
        "mmproj_path": str(mmproj),
        "messages": [
            {"role": "user", "content": "x",
             "images": [str(tmp_path / "nope.png")]},
        ],
    }
    r = client.post("/chat", json=body, headers=auth)
    assert r.status_code == 400


def test_chat_400_when_mmproj_missing(client, auth, fake_model, tmp_path):
    up = client.post(
        "/chat/upload", headers=auth,
        files={"file": ("p.png", _TINY_PNG, "image/png")},
    ).json()
    body = {
        "model_path": fake_model,
        "mmproj_path": str(tmp_path / "nope.gguf"),
        "messages": [
            {"role": "user", "content": "x", "images": [up["path"]]},
        ],
    }
    r = client.post("/chat", json=body, headers=auth)
    assert r.status_code == 400
