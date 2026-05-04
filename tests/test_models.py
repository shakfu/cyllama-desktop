"""Models workspace endpoints: cached, inspect, import, hf peek/download."""
from __future__ import annotations

from pathlib import Path

import pytest


def _mk_gguf(path: Path, size: int = 16) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"GGUF" + b"\0" * (size - 4))
    return path


def test_models_cached_empty(client, auth, sidecar_app):
    r = client.get("/models/cached", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["models"] == []
    assert body["models_dir"] == str(sidecar_app.MODELS_DIR)


def test_models_cached_lists_local(client, auth, sidecar_app):
    a = _mk_gguf(sidecar_app.MODELS_DIR / "a.gguf")
    b = _mk_gguf(sidecar_app.MODELS_DIR / "sub" / "b.gguf")
    r = client.get("/models/cached", headers=auth)
    body = r.json()
    paths = {m["path"] for m in body["models"]}
    assert str(a) in paths and str(b) in paths
    for m in body["models"]:
        assert m["source"] == "local"


def test_models_inspect_requires_existing_path(client, auth, tmp_path):
    r = client.post("/models/inspect", json={"path": str(tmp_path / "nope.gguf")}, headers=auth)
    assert r.status_code == 400


def test_models_inspect_returns_metadata(client, auth, sidecar_app):
    p = _mk_gguf(sidecar_app.MODELS_DIR / "x.gguf")
    r = client.post("/models/inspect", json={"path": str(p)}, headers=auth)
    body = r.json()
    assert body["path"] == str(p)
    assert body["metadata"]["general.architecture"] == "llama"


def test_models_import_copies_into_models_dir(client, auth, tmp_path, sidecar_app):
    src = _mk_gguf(tmp_path / "external" / "ext.gguf")
    r = client.post("/models/import", json={"path": str(src)}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] is True
    assert body["name"] == "ext.gguf"
    assert (sidecar_app.MODELS_DIR / "ext.gguf").is_file()


def test_models_import_no_op_when_already_inside(client, auth, sidecar_app):
    inside = _mk_gguf(sidecar_app.MODELS_DIR / "inside.gguf")
    r = client.post("/models/import", json={"path": str(inside)}, headers=auth)
    assert r.json()["imported"] is False


def test_models_import_rejects_non_gguf(client, auth, tmp_path):
    bad = tmp_path / "thing.bin"
    bad.write_bytes(b"\x00")
    r = client.post("/models/import", json={"path": str(bad)}, headers=auth)
    assert r.status_code == 400


def test_models_import_409_on_collision(client, auth, tmp_path, sidecar_app):
    _mk_gguf(sidecar_app.MODELS_DIR / "dup.gguf")
    src = _mk_gguf(tmp_path / "elsewhere" / "dup.gguf")
    r = client.post("/models/import", json={"path": str(src)}, headers=auth)
    assert r.status_code == 409


# --- HF target parsing -----------------------------------------------------


def test_hf_parse_url(sidecar_app):
    repo, rev, path = sidecar_app._parse_hf_target({
        "url": "https://huggingface.co/user/Model-X/resolve/main/sub/dir/m.gguf",
    })
    assert repo == "user/Model-X" and rev == "main" and path == "sub/dir/m.gguf"


def test_hf_parse_url_with_revision(sidecar_app):
    repo, rev, path = sidecar_app._parse_hf_target({
        "url": "https://huggingface.co/u/r/resolve/abc123/file.gguf",
    })
    assert rev == "abc123"


def test_hf_parse_repo_file(sidecar_app):
    repo, rev, path = sidecar_app._parse_hf_target({"repo": "u/r", "file": "m.gguf"})
    assert repo == "u/r" and rev == "main" and path == "m.gguf"


def test_hf_parse_shorthand(sidecar_app):
    repo, rev, path = sidecar_app._parse_hf_target({"target": "u/r:dir/m.gguf"})
    assert repo == "u/r" and path == "dir/m.gguf"


def test_hf_parse_invalid(sidecar_app):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        sidecar_app._parse_hf_target({})
    with pytest.raises(HTTPException):
        sidecar_app._parse_hf_target({"url": "https://example.com/foo"})
    with pytest.raises(HTTPException):
        sidecar_app._parse_hf_target({"repo": "no-slash", "file": "x"})


# --- HF peek + download (httpx mocked) -------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, body=b""):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body

    async def aiter_bytes(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None


class _FakeAsyncClient:
    """Mocks httpx.AsyncClient for both peek (head) and download (stream)."""

    def __init__(self, *, head=None, body=b"GGUF" + b"\0" * 1020, status=200):
        self._head = head or {"content-length": str(len(body))}
        self._body = body
        self._status = status

    def __init_kwargs__(self, **kw):
        return self

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return None

    async def head(self, url):
        return _FakeResponse(self._status, self._head)

    def stream(self, method, url):
        return _FakeResponse(self._status, self._head, self._body)


@pytest.fixture()
def mock_httpx(monkeypatch):
    """Replace httpx.AsyncClient with a fake; tests configure body via attr."""
    import httpx

    fake = {"client": _FakeAsyncClient()}

    class _Wrapper:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self_inner): return fake["client"]
        async def __aexit__(self_inner, *a): return None

    monkeypatch.setattr(httpx, "AsyncClient", _Wrapper)
    return fake


def test_hf_peek_returns_size(client, auth, mock_httpx, sidecar_app):
    mock_httpx["client"] = _FakeAsyncClient(head={"content-length": "12345"})
    r = client.post("/models/hf/peek", json={
        "url": "https://huggingface.co/u/r/resolve/main/m.gguf",
    }, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] == "u/r" and body["file"] == "m.gguf"
    assert body["size"] == 12345
    assert body["exists"] is True
    assert body["target_path"].endswith("u_r/m.gguf")


def test_hf_download_writes_to_models_dir(client, auth, mock_httpx, sidecar_app):
    payload = b"GGUF" + b"\0" * 4092  # 4 KiB
    mock_httpx["client"] = _FakeAsyncClient(
        body=payload,
        head={"content-length": str(len(payload))},
    )

    # Spawn the download job.
    r = client.post("/jobs/models.hf-download", json={
        "url": "https://huggingface.co/u/r/resolve/main/m.gguf",
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # Drain SSE.
    import json as _json
    events = []
    with client.stream("GET", f"/jobs/{job_id}/events", headers=auth) as resp:
        buf = ""
        for chunk in resp.iter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.splitlines():
                    if line.startswith("data: "):
                        events.append(_json.loads(line[6:]))
                if events and events[-1].get("type") == "done":
                    break
            if events and events[-1].get("type") == "done":
                break

    types = [e["type"] for e in events]
    assert "result" in types and types[-1] == "done"
    result = next(e for e in events if e["type"] == "result")["result"]
    assert result["name"] == "m.gguf"
    assert Path(result["path"]).is_file()
    assert Path(result["path"]).read_bytes() == payload


def test_hf_download_409_when_already_local(client, auth, mock_httpx, sidecar_app):
    target = sidecar_app.MODELS_DIR / "_hf" / "u_r" / "m.gguf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"GGUF\0\0\0\0")
    r = client.post("/jobs/models.hf-download", json={
        "url": "https://huggingface.co/u/r/resolve/main/m.gguf",
    }, headers=auth)
    assert r.status_code == 409
