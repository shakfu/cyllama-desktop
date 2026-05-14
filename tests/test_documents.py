"""Composer document attachment (Multimodal #1).

The renderer drops a .pdf / .md / .txt into the chat composer, posts
the bytes to /documents/extract, and inlines the returned text as a
``[Document: foo.pdf]`` block before the user prompt.

Tests here cover the sidecar half: capability flag wiring, the per-
backend info shape, and the multipart endpoint's happy / error paths.
"""
from __future__ import annotations

import io


def test_info_advertises_pdf_backends(client, auth):
    body = client.get("/info", headers=auth).json()
    backends = body.get("pdf_backends") or []
    assert isinstance(backends, list)
    # The stub registers exactly one pypdf-style backend.
    names = [b["name"] for b in backends]
    assert "pypdf-stub" in names
    entry = next(b for b in backends if b["name"] == "pypdf-stub")
    assert entry["available"] is True
    assert "per_page" in entry["capabilities"]
    assert entry["install_hint"] == "pip install pypdf"


def test_info_features_documents_flags(client, auth):
    feats = client.get("/info", headers=auth).json()["features"]
    assert feats["documents.extract"] is True
    assert feats["documents.pdf"] is True


def test_extract_400_on_missing_file(client, auth):
    r = client.post("/documents/extract", headers=auth)
    assert r.status_code == 400


def test_extract_415_on_unsupported_extension(client, auth):
    files = {"file": ("evil.exe", io.BytesIO(b"MZ\x00\x00"), "application/octet-stream")}
    r = client.post("/documents/extract", headers=auth, files=files)
    assert r.status_code == 415
    assert ".exe" in r.json()["detail"]


def test_extract_413_on_oversized_upload(client, auth, sidecar_app, monkeypatch):
    # Tighten the cap so we don't have to ship a 32 MiB blob in the test.
    monkeypatch.setattr(sidecar_app, "_DOC_UPLOAD_MAX_BYTES", 1024)
    files = {"file": ("big.txt", io.BytesIO(b"x" * 4096), "text/plain")}
    r = client.post("/documents/extract", headers=auth, files=files)
    assert r.status_code == 413


def test_extract_markdown_happy_path(client, auth):
    body = b"# Heading\n\nSome text body."
    files = {"file": ("note.md", io.BytesIO(body), "text/markdown")}
    r = client.post("/documents/extract", headers=auth, files=files)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["filetype"] == "md"
    assert out["filename"] == "note.md"
    assert "Some text body" in out["text"]
    assert out["truncated"] is False
    # Non-PDF -> backend is empty.
    assert out["backend"] == ""
    assert out["pages"] == 1


def test_extract_pdf_records_backend(client, auth):
    """The stub's load_document stamps ``backend`` into metadata --
    same shape cyllama 0.2.17 produces (loaders.py:952)."""
    files = {"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4..."), "application/pdf")}
    r = client.post("/documents/extract", headers=auth, files=files)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["filetype"] == "pdf"
    assert out["filename"] == "paper.pdf"  # display name preserved
    assert out["backend"] == "pypdf-stub"
    # Stub returns a non-empty body; we assert presence not exact contents
    # (cyllama's real backends emit per-document text we don't reproduce).
    assert "pdf body of" in out["text"]


def test_extract_501_when_no_pdf_backend(client, auth, sidecar_app, monkeypatch):
    """Simulate the older-cyllama / no-PDF-lib install path. The
    renderer surfaces the install_hint to the user."""
    monkeypatch.setattr(sidecar_app, "_AVAILABLE_PDF_BACKENDS_FN", lambda *a, **k: [])
    files = {"file": ("paper.pdf", io.BytesIO(b"%PDF-1.4..."), "application/pdf")}
    r = client.post("/documents/extract", headers=auth, files=files)
    assert r.status_code == 501
    # Hint surfaced for the renderer to display.
    assert "pip install" in r.json()["detail"]


def test_extract_truncates_oversized_text(client, auth, sidecar_app, monkeypatch):
    """Cap on extracted text length prevents a giant doc from blowing
    the context window when inlined into a chat."""
    monkeypatch.setattr(sidecar_app, "_DOC_EXTRACT_MAX_CHARS", 100)
    body = b"a" * 1000
    files = {"file": ("big.txt", io.BytesIO(body), "text/plain")}
    r = client.post("/documents/extract", headers=auth, files=files)
    assert r.status_code == 200
    out = r.json()
    assert out["truncated"] is True
    assert out["char_count"] == 100
    assert len(out["text"]) == 100
