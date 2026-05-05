"""RAG collections + ingest job (Phase 4 slice 1)."""
from __future__ import annotations

import json
import time
from pathlib import Path


def _read_events(client, auth, job_id, timeout_s=2.0):
    """Drain the SSE stream for a job and return the parsed event list."""
    events = []
    deadline = time.time() + timeout_s
    with client.stream("GET", f"/jobs/{job_id}/events", headers=auth) as r:
        assert r.status_code == 200
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))
                if events and events[-1].get("type") == "done":
                    return events
            if time.time() > deadline:
                raise AssertionError(f"timeout draining events; got {events}")
    return events


def _create_collection(client, auth, embedding_model, name="docs"):
    r = client.post("/rag/collections", json={
        "name": name,
        "embedding_model_path": embedding_model,
    }, headers=auth)
    assert r.status_code == 200, r.text
    return r.json()


# --- collections registry ---------------------------------------------------


def test_rag_collections_starts_empty(client, auth):
    r = client.get("/rag/collections", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["collections"] == []
    assert body["rag_dir"]


def test_rag_collections_create_then_list(client, auth, fake_model):
    rec = _create_collection(client, auth, fake_model, name="My Docs")
    assert rec["name"] == "My Docs"
    assert rec["embedding_model_path"] == fake_model
    assert rec["doc_count"] == 0
    assert rec["chunk_count"] == 0
    assert rec["id"].startswith("my-docs-") and len(rec["id"]) > len("my-docs-")
    assert rec["sqlite_path"].endswith(f"{rec['id']}.sqlite")

    r = client.get("/rag/collections", headers=auth)
    assert [c["id"] for c in r.json()["collections"]] == [rec["id"]]


def test_rag_collections_create_requires_name(client, auth, fake_model):
    r = client.post("/rag/collections", json={"embedding_model_path": fake_model}, headers=auth)
    assert r.status_code == 400
    assert "name" in r.json()["detail"].lower()


def test_rag_collections_create_requires_existing_embedding_model(client, auth):
    r = client.post("/rag/collections", json={
        "name": "x",
        "embedding_model_path": "/nope/missing.gguf",
    }, headers=auth)
    assert r.status_code == 400


def test_rag_collections_persisted_to_disk(client, auth, fake_model, sidecar_app):
    rec = _create_collection(client, auth, fake_model)
    manifest = Path(sidecar_app.RAG_DIR) / "collections.json"
    assert manifest.exists()
    payload = json.loads(manifest.read_text())
    assert payload["version"] == 1
    assert any(c["id"] == rec["id"] for c in payload["collections"])


def test_rag_collections_delete_removes_entry_and_sqlite(client, auth, fake_model, sidecar_app, tmp_path):
    rec = _create_collection(client, auth, fake_model)
    # Simulate an ingest having written a sqlite file.
    sqlite = Path(rec["sqlite_path"])
    sqlite.parent.mkdir(parents=True, exist_ok=True)
    sqlite.write_bytes(b"SQLite-stub")

    r = client.delete(f"/rag/collections/{rec['id']}", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # Manifest no longer lists it.
    listing = client.get("/rag/collections", headers=auth).json()["collections"]
    assert all(c["id"] != rec["id"] for c in listing)
    # Sqlite file gone.
    assert not sqlite.exists()


def test_rag_collections_delete_404_when_missing(client, auth):
    r = client.delete("/rag/collections/does-not-exist", headers=auth)
    assert r.status_code == 404


def test_rag_collections_delete_400_on_invalid_id(client, auth):
    r = client.delete("/rag/collections/Bad..Id", headers=auth)
    assert r.status_code == 400


# --- ingest job -------------------------------------------------------------


def test_rag_ingest_404_for_unknown_collection(client, auth, tmp_path):
    f = tmp_path / "doc.txt"
    f.write_text("hello world")
    r = client.post("/jobs/rag.ingest", json={
        "collection_id": "ghost",
        "paths": [str(f)],
    }, headers=auth)
    # Invalid id pattern matches first
    assert r.status_code == 404 or r.status_code == 400


def test_rag_ingest_400_on_empty_paths(client, auth, fake_model):
    rec = _create_collection(client, auth, fake_model)
    r = client.post("/jobs/rag.ingest", json={
        "collection_id": rec["id"],
        "paths": [],
    }, headers=auth)
    assert r.status_code == 400


def test_rag_ingest_streams_progress_and_updates_manifest(client, auth, fake_model, tmp_path, sidecar_app):
    rec = _create_collection(client, auth, fake_model)

    # Build a small directory of text docs.
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    # Each file produces multiple chunks because chunk_size=2 (words per chunk).
    (docs_dir / "a.txt").write_text("alpha beta gamma delta epsilon zeta")
    (docs_dir / "b.txt").write_text("one two three four")

    r = client.post("/jobs/rag.ingest", json={
        "collection_id": rec["id"],
        "paths": [str(docs_dir)],
        "chunk_size": 2,
        "chunk_overlap": 0,
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    events = _read_events(client, auth, job_id)
    types = [e["type"] for e in events]
    assert types.count("progress") >= 1
    assert "result" in types
    assert types[-1] == "done"

    result = next(e for e in events if e["type"] == "result")["result"]
    assert result["collection_id"] == rec["id"]
    assert result["documents"] == 2
    assert result["chunks"] >= 2  # 6/2=3 + 4/2=2 = 5 chunks given fake splitter

    # Manifest got updated counts.
    listing = client.get("/rag/collections", headers=auth).json()["collections"]
    after = next(c for c in listing if c["id"] == rec["id"])
    assert after["doc_count"] == 2
    assert after["chunk_count"] == result["chunks"]
    assert after["updated_at"] >= rec["updated_at"]

    # Embedder + store were instantiated and closed.
    from conftest import _FakeEmbedder, _FakeSqliteVectorStore
    assert len(_FakeEmbedder.instances) == 1
    assert _FakeEmbedder.instances[0].closed is True
    assert len(_FakeSqliteVectorStore.instances) == 1
    assert len(_FakeSqliteVectorStore.instances[0].rows) == result["chunks"]


def test_rag_ingest_skips_missing_paths(client, auth, fake_model, tmp_path):
    rec = _create_collection(client, auth, fake_model)
    real = tmp_path / "real.txt"
    real.write_text("alpha beta gamma")

    r = client.post("/jobs/rag.ingest", json={
        "collection_id": rec["id"],
        "paths": [str(real), str(tmp_path / "missing.txt")],
        "chunk_size": 2,
    }, headers=auth)
    assert r.status_code == 200
    events = _read_events(client, auth, r.json()["job_id"])
    log_msgs = [e.get("message", "") for e in events if e.get("type") == "log"]
    assert any("skip" in m.lower() and "missing.txt" in m for m in log_msgs)
    result = next(e for e in events if e["type"] == "result")["result"]
    assert result["documents"] == 1


def test_rag_ingest_fails_when_no_documents_loaded(client, auth, fake_model, tmp_path):
    rec = _create_collection(client, auth, fake_model)
    r = client.post("/jobs/rag.ingest", json={
        "collection_id": rec["id"],
        "paths": [str(tmp_path / "nope.txt")],
    }, headers=auth)
    assert r.status_code == 200
    events = _read_events(client, auth, r.json()["job_id"])
    types = [e["type"] for e in events]
    assert "error" in types
    assert types[-1] == "done"


# --- /rag/query (SSE) -------------------------------------------------------


def _drain_sse(client, auth, path, body, timeout_s=2.0):
    """Drain an SSE response. Returns (events, done_seen)."""
    events = []
    done = False
    deadline = time.time() + timeout_s
    with client.stream("POST", path, json=body, headers=auth) as r:
        if r.status_code != 200:
            return r, events, done
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                frame, buf = buf.split("\n\n", 1)
                for line in frame.splitlines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        done = True
                        return r, events, done
                    events.append(json.loads(payload))
            if time.time() > deadline:
                raise AssertionError(f"timeout draining SSE; got {events}")
    return r, events, done


def test_rag_query_400_on_invalid_collection_id(client, auth, fake_model):
    r = client.post("/rag/query", json={
        "collection_id": "Bad..ID",
        "generation_model_path": fake_model,
        "question": "hi",
    }, headers=auth)
    assert r.status_code == 400


def test_rag_query_404_on_unknown_collection(client, auth, fake_model):
    r = client.post("/rag/query", json={
        "collection_id": "nosuch",
        "generation_model_path": fake_model,
        "question": "hi",
    }, headers=auth)
    assert r.status_code == 404


def test_rag_query_400_on_missing_question(client, auth, fake_model):
    rec = _create_collection(client, auth, fake_model)
    r = client.post("/rag/query", json={
        "collection_id": rec["id"],
        "generation_model_path": fake_model,
        "question": "  ",
    }, headers=auth)
    assert r.status_code == 400


def test_rag_query_400_on_missing_generation_model(client, auth, fake_model):
    rec = _create_collection(client, auth, fake_model)
    r = client.post("/rag/query", json={
        "collection_id": rec["id"],
        "generation_model_path": "/nope/missing.gguf",
        "question": "hi",
    }, headers=auth)
    assert r.status_code == 400


def test_rag_query_streams_sources_then_tokens(client, auth, fake_model, sidecar_app, tmp_path):
    rec = _create_collection(client, auth, fake_model)
    # Drive the fake RAG with deterministic outputs.
    from conftest import _FakeRAG, _FakeSearchResult
    _FakeRAG._chunks = ["The ", "answer ", "is ", "42."]
    _FakeRAG._sources = [
        _FakeSearchResult(id="doc-1", text="42 is the answer", score=0.91, metadata={"source": "/x"}),
        _FakeSearchResult(id="doc-2", text="towel", score=0.40, metadata={}),
    ]
    # Generation model just needs to exist on disk.
    gen_model = tmp_path / "gen.gguf"
    gen_model.write_bytes(b"GGUF\x00")

    r, events, done = _drain_sse(client, auth, "/rag/query", {
        "collection_id": rec["id"],
        "generation_model_path": str(gen_model),
        "question": "what is the answer?",
        "top_k": 2,
    })
    assert done is True
    # First event must be sources.
    assert "sources" in events[0]
    assert [s["id"] for s in events[0]["sources"]] == ["doc-1", "doc-2"]
    assert events[0]["sources"][0]["score"] == 0.91
    # Remaining events are token frames.
    tokens = [e["text"] for e in events[1:] if "text" in e]
    assert "".join(tokens) == "The answer is 42."

    # Cache populated, single instance.
    assert len(_FakeRAG.instances) == 1
    inst = _FakeRAG.instances[0]
    assert inst.embedding_model == rec["embedding_model_path"]
    assert inst.generation_model == str(gen_model)
    assert inst.db_path == rec["sqlite_path"]


def test_rag_query_reuses_cached_instance(client, auth, fake_model, tmp_path):
    rec = _create_collection(client, auth, fake_model)
    from conftest import _FakeRAG
    _FakeRAG._chunks = ["x"]
    gen_model = tmp_path / "gen.gguf"
    gen_model.write_bytes(b"GGUF\x00")

    body = {
        "collection_id": rec["id"],
        "generation_model_path": str(gen_model),
        "question": "q1",
    }
    _drain_sse(client, auth, "/rag/query", body)
    _drain_sse(client, auth, "/rag/query", {**body, "question": "q2"})

    assert len(_FakeRAG.instances) == 1  # second call reused the cached RAG
    assert _FakeRAG.closed_count == 0


def test_rag_query_evicts_on_generation_model_change(client, auth, fake_model, tmp_path):
    rec = _create_collection(client, auth, fake_model)
    from conftest import _FakeRAG
    _FakeRAG._chunks = ["x"]
    g1 = tmp_path / "g1.gguf"; g1.write_bytes(b"GGUF\x00")
    g2 = tmp_path / "g2.gguf"; g2.write_bytes(b"GGUF\x00")

    _drain_sse(client, auth, "/rag/query", {
        "collection_id": rec["id"],
        "generation_model_path": str(g1),
        "question": "q",
    })
    _drain_sse(client, auth, "/rag/query", {
        "collection_id": rec["id"],
        "generation_model_path": str(g2),
        "question": "q",
    })

    assert len(_FakeRAG.instances) == 2
    assert _FakeRAG.instances[0].closed is True  # prior instance closed on eviction


# --- /rag/retrieve ----------------------------------------------------------


def test_rag_retrieve_400_on_invalid_collection_id(client, auth):
    r = client.post("/rag/retrieve", json={
        "collection_id": "Bad..ID", "query": "x",
    }, headers=auth)
    assert r.status_code == 400


def test_rag_retrieve_404_on_unknown_collection(client, auth):
    r = client.post("/rag/retrieve", json={
        "collection_id": "ghost", "query": "x",
    }, headers=auth)
    assert r.status_code == 404


def test_rag_retrieve_400_on_missing_query(client, auth, fake_model):
    rec = _create_collection(client, auth, fake_model)
    r = client.post("/rag/retrieve", json={
        "collection_id": rec["id"], "query": "  ",
    }, headers=auth)
    assert r.status_code == 400


def test_rag_retrieve_returns_sources_after_ingest(client, auth, fake_model, tmp_path):
    rec = _create_collection(client, auth, fake_model)

    # Ingest something so the store has rows.
    docs = tmp_path / "docs"; docs.mkdir()
    (docs / "a.txt").write_text("alpha beta gamma")
    (docs / "b.txt").write_text("one two three")
    job_resp = client.post("/jobs/rag.ingest", json={
        "collection_id": rec["id"], "paths": [str(docs)], "chunk_size": 2,
    }, headers=auth)
    job_id = job_resp.json()["job_id"]
    _read_events(client, auth, job_id)

    r = client.post("/rag/retrieve", json={
        "collection_id": rec["id"], "query": "alpha", "top_k": 5,
    }, headers=auth)
    assert r.status_code == 200
    sources = r.json()["sources"]
    assert len(sources) > 0
    for s in sources:
        assert {"id", "text", "score", "metadata"} <= set(s.keys())


def test_rag_retrieve_caches_embedder_per_collection(client, auth, fake_model):
    rec = _create_collection(client, auth, fake_model)
    # Warm a tiny store with one row so search has something to return.
    from conftest import _FakeEmbedder
    _FakeEmbedder.instances.clear()

    body = {"collection_id": rec["id"], "query": "anything"}
    client.post("/rag/retrieve", json=body, headers=auth)
    client.post("/rag/retrieve", json=body, headers=auth)
    # Second call reuses the cached embedder.
    assert len(_FakeEmbedder.instances) == 1


# --- /info ------------------------------------------------------------------


def test_info_reports_rag_dir(client, auth):
    r = client.get("/info", headers=auth)
    assert r.status_code == 200
    assert "rag_dir" in r.json()["sidecar"]
