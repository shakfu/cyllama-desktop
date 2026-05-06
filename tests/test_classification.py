"""Model classification: kind heuristics + /models/cached?kinds= filter."""
from __future__ import annotations

from pathlib import Path

import pytest


def _mk_gguf(path: Path, size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\0" * (size - 4))
    return path


def _mk_bin(path: Path, size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"BIN " + b"\0" * (size - 4))
    return path


# --- _classify_model unit tests (no HTTP) ----------------------------------


def test_classify_mmproj_by_filename(sidecar_app):
    # Filename is authoritative for mmproj-* without inspecting metadata.
    assert sidecar_app._classify_model(Path("mmproj-llama-vision.gguf"), 0) == "mmproj"
    assert sidecar_app._classify_model(Path("MMPROJ-X.GGUF"), 0) == "mmproj"


def test_classify_whisper_by_extension(sidecar_app):
    # .bin is the conventional whisper.cpp suffix; treat as whisper without
    # opening the file (we don't have a whisper magic check in 0.2.x).
    assert sidecar_app._classify_model(Path("ggml-base.en.bin"), 0) == "whisper"


def test_classify_chat_for_llama_arch(sidecar_app, tmp_path):
    """Default fake GGUFContext metadata advertises ``architecture=llama``."""
    p = _mk_gguf(tmp_path / "qwen3-4b.gguf")
    assert sidecar_app._classify_model(p, p.stat().st_size) == "chat"


def test_classify_embedding_for_bert_arch(sidecar_app, tmp_path, monkeypatch):
    # Override the fake GGUFContext metadata to advertise a bert-family
    # arch; classification should bucket as embedding.
    monkeypatch.setattr(
        sidecar_app.cyllama.GGUFContext,
        "_metadata",
        {"general.architecture": "bert", "general.name": "all-MiniLM"},
    )
    p = _mk_gguf(tmp_path / "minilm.gguf")
    assert sidecar_app._classify_model(p, p.stat().st_size) == "embedding"


def test_classify_embedding_for_pooling_type_meta(sidecar_app, tmp_path, monkeypatch):
    # Architectures we don't enumerate explicitly (e.g. ``custom-emb``)
    # still classify as embedding when a pooling_type metadata key is
    # present -- the secondary heuristic from _classify_model.
    monkeypatch.setattr(
        sidecar_app.cyllama.GGUFContext,
        "_metadata",
        {"general.architecture": "custom-emb", "custom-emb.pooling_type": "mean"},
    )
    p = _mk_gguf(tmp_path / "custom.gguf")
    assert sidecar_app._classify_model(p, p.stat().st_size) == "embedding"


def test_classify_unknown_when_metadata_missing(sidecar_app, tmp_path, monkeypatch):
    # If the GGUFContext returns a non-dict (or empty arch), classification
    # falls back to "unknown" rather than misclassifying as chat.
    monkeypatch.setattr(
        sidecar_app.cyllama.GGUFContext,
        "_metadata",
        {"general.name": "weird"},  # no architecture key
    )
    p = _mk_gguf(tmp_path / "weird.gguf")
    assert sidecar_app._classify_model(p, p.stat().st_size) == "unknown"


def test_classify_cache_invalidates_on_mtime_change(sidecar_app, tmp_path, monkeypatch):
    """Re-writing the file should re-run classification rather than
    returning the stale cached kind."""
    monkeypatch.setattr(
        sidecar_app.cyllama.GGUFContext,
        "_metadata",
        {"general.architecture": "llama"},
    )
    p = _mk_gguf(tmp_path / "x.gguf")
    st = p.stat()
    assert sidecar_app._classify_model_cached(p, st.st_size, st.st_mtime) == "chat"
    # Switch the metadata and bump mtime: cache should refresh.
    monkeypatch.setattr(
        sidecar_app.cyllama.GGUFContext,
        "_metadata",
        {"general.architecture": "bert"},
    )
    p.write_bytes(b"GGUF" + b"\0" * 32)
    st2 = p.stat()
    assert st2.st_mtime != st.st_mtime, "mtime must change for cache test to be meaningful"
    assert sidecar_app._classify_model_cached(p, st2.st_size, st2.st_mtime) == "embedding"


# --- /models/cached?kinds= integration tests -------------------------------


def test_cached_returns_kind_per_item(client, auth, sidecar_app):
    _mk_gguf(sidecar_app.MODELS_DIR / "chat.gguf")
    _mk_gguf(sidecar_app.MODELS_DIR / "mmproj-vision.gguf")
    _mk_bin(sidecar_app.MODELS_DIR / "ggml-base.en.bin")
    body = client.get("/models/cached", headers=auth).json()
    by_name = {m["name"]: m for m in body["models"]}
    assert by_name["chat.gguf"]["kind"] == "chat"
    assert by_name["mmproj-vision.gguf"]["kind"] == "mmproj"
    assert by_name["ggml-base.en.bin"]["kind"] == "whisper"


def test_cached_filters_by_kinds(client, auth, sidecar_app):
    _mk_gguf(sidecar_app.MODELS_DIR / "chat.gguf")
    _mk_gguf(sidecar_app.MODELS_DIR / "mmproj-vision.gguf")
    _mk_bin(sidecar_app.MODELS_DIR / "whisper.bin")
    r = client.get("/models/cached?kinds=chat", headers=auth)
    names = {m["name"] for m in r.json()["models"]}
    assert names == {"chat.gguf"}


def test_cached_filters_accept_multiple_kinds(client, auth, sidecar_app):
    _mk_gguf(sidecar_app.MODELS_DIR / "chat.gguf")
    _mk_gguf(sidecar_app.MODELS_DIR / "mmproj-vision.gguf")
    _mk_bin(sidecar_app.MODELS_DIR / "whisper.bin")
    r = client.get("/models/cached?kinds=chat,whisper", headers=auth)
    names = {m["name"] for m in r.json()["models"]}
    assert names == {"chat.gguf", "whisper.bin"}


def test_cached_unknown_always_passes_filter(client, auth, sidecar_app, monkeypatch):
    """Misclassified models are surfaced under ``unknown`` and pass any
    kinds filter unconditionally so the user isn't locked out."""
    monkeypatch.setattr(
        sidecar_app.cyllama.GGUFContext,
        "_metadata",
        {"general.name": "no-arch"},  # forces "unknown"
    )
    _mk_gguf(sidecar_app.MODELS_DIR / "weird.gguf")
    r = client.get("/models/cached?kinds=chat", headers=auth)
    names = {m["name"] for m in r.json()["models"]}
    assert "weird.gguf" in names


def test_cached_kinds_all_returns_everything(client, auth, sidecar_app):
    _mk_gguf(sidecar_app.MODELS_DIR / "chat.gguf")
    _mk_bin(sidecar_app.MODELS_DIR / "whisper.bin")
    r = client.get("/models/cached?kinds=all", headers=auth)
    names = {m["name"] for m in r.json()["models"]}
    assert names == {"chat.gguf", "whisper.bin"}


def test_scan_picks_up_bin_files_alongside_gguf(client, auth, sidecar_app):
    """Pre-classification the scan was .gguf-only; whisper .bin files
    were invisible. The Transcribe pane wouldn't list them."""
    _mk_bin(sidecar_app.MODELS_DIR / "ggml-tiny.en.bin")
    body = client.get("/models/cached", headers=auth).json()
    names = {m["name"] for m in body["models"]}
    assert "ggml-tiny.en.bin" in names


# --- Extra (read-only) model search roots ---------------------------------


def test_extra_root_models_appear_with_external_source(client, auth, sidecar_app, tmp_path):
    """Files under MODELS_EXTRA show up in the catalog tagged as
    external -- not local (which would imply they're managed by the
    desktop's primary cache)."""
    extra = tmp_path / "library"
    extra.mkdir()
    _mk_gguf(extra / "remote-chat.gguf")
    sidecar_app.MODELS_EXTRA = (extra,)
    body = client.get("/models/cached", headers=auth).json()
    by_name = {m["name"]: m for m in body["models"]}
    assert "remote-chat.gguf" in by_name
    assert by_name["remote-chat.gguf"]["source"] == "external"


def test_local_takes_precedence_over_external_on_path_collision(
    client, auth, sidecar_app, tmp_path,
):
    """If the same absolute path resolves under both roots (rare, but
    possible via symlinks) the local entry wins so the user sees the
    file once with the more authoritative ``source`` tag."""
    primary_file = sidecar_app.MODELS_DIR / "shared.gguf"
    _mk_gguf(primary_file)
    # Symlink the same file into the extra root.
    extra = tmp_path / "extra"
    extra.mkdir()
    (extra / "shared.gguf").symlink_to(primary_file)
    sidecar_app.MODELS_EXTRA = (extra,)
    body = client.get("/models/cached", headers=auth).json()
    # Resolve happens in _scan_gguf so both rows hash to the same path.
    rows = [m for m in body["models"] if m["name"] == "shared.gguf"]
    assert len(rows) == 1
    assert rows[0]["source"] == "local"


def test_info_reports_models_extra(client, auth, sidecar_app, tmp_path):
    extra = tmp_path / "lib"
    extra.mkdir()
    sidecar_app.MODELS_EXTRA = (extra,)
    # /info is built once at module load; rebuild the relevant slice
    # so the test assertion sees the override.
    sidecar_app._INFO_CACHE["sidecar"]["models_extra"] = [str(extra)]
    body = client.get("/info", headers=auth).json()
    assert body["sidecar"]["models_extra"] == [str(extra)]


def test_resolve_models_extra_drops_blanks_and_dedups(sidecar_app, tmp_path):
    """The pathsep-split parser is forgiving -- empty entries from a
    leading/trailing delimiter, repeated paths, and the primary
    MODELS_DIR itself are all stripped."""
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    import os
    raw = os.pathsep.join([
        "", str(a), "  ", str(b),
        str(sidecar_app.MODELS_DIR),  # excluded -- this is the primary
    ])
    out = sidecar_app._resolve_models_extra(raw)
    assert a.resolve() in out
    assert b.resolve() in out
    assert sidecar_app.MODELS_DIR not in out
