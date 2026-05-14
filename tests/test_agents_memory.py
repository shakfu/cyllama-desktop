"""Phase E: semantic_memory entry in _build_agent_tools.

The spec ``{"semantic_memory": {"collection_id": ..., "namespace": ...}}``
exposes two tools to the agent: ``remember`` and ``recall``. The backing
store is a regular RAG collection; the namespace isolates memory entries
within it.
"""
from __future__ import annotations

import json
import time

import pytest


@pytest.fixture(autouse=True)
def _clear_memory_store(sidecar_app):
    """Class-level memory store on the stub leaks across tests by
    design (so a single test can write then read). Clear it before
    each test to keep cases isolated."""
    sidecar_app.cyllama.agents.SemanticMemory._store.clear()
    sidecar_app.cyllama.agents.SemanticMemory.instances.clear()


def _make_collection(client, auth, fake_model, name="memstore"):
    r = client.post("/rag/collections", json={
        "name": name, "embedding_model_path": fake_model,
    }, headers=auth)
    assert r.status_code in (200, 201)
    return r.json()["id"]


# ---------------------------------------------------------------------------
# _build_agent_tools validation
# ---------------------------------------------------------------------------


def test_semantic_memory_400_on_missing_collection_id(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {}},
    }, headers=auth)
    assert r.status_code == 400
    assert "collection_id" in r.json()["detail"]


def test_semantic_memory_404_on_unknown_collection(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {"collection_id": "missing-xxxxxxxx"}},
    }, headers=auth)
    assert r.status_code == 404


def test_semantic_memory_400_on_invalid_collection_id(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {"collection_id": "INVALID id"}},
    }, headers=auth)
    assert r.status_code == 400


def test_semantic_memory_400_on_blank_namespace(client, auth, fake_model):
    coll_id = _make_collection(client, auth, fake_model)
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {
            "collection_id": coll_id, "namespace": "  ",
        }},
    }, headers=auth)
    assert r.status_code == 400


def test_semantic_memory_501_when_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    coll_id = _make_collection(client, auth, fake_model)
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "agents.memory", False)
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {"collection_id": coll_id}},
    }, headers=auth)
    assert r.status_code == 501


# ---------------------------------------------------------------------------
# Tool wiring + agent integration
# ---------------------------------------------------------------------------


def test_semantic_memory_attaches_two_tools_to_agent(
    client, auth, fake_model, sidecar_app,
):
    coll_id = _make_collection(client, auth, fake_model)
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {"collection_id": coll_id}},
    }, headers=auth)
    assert r.status_code == 200
    # Drain so the agent finishes; the stub doesn't actually call the
    # tools but the tool catalog must be the right size.
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    names = sorted(t.name for t in inst.tools)
    assert "remember" in names
    assert "recall" in names
    # Stock cyllama tools (current_time / calculator / word_count) are
    # auto-injected on top of whatever the renderer requested.
    assert {"remember", "recall"}.issubset(set(names))


def test_semantic_memory_round_trip_via_tool_functions(
    client, auth, fake_model, sidecar_app,
):
    """Build the tool pair via the endpoint, then directly invoke the
    remember + recall callables to verify they round-trip through the
    SemanticMemory stub's in-memory store."""
    coll_id = _make_collection(client, auth, fake_model)
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {
            "collection_id": coll_id, "namespace": "test-ns",
        }},
    }, headers=auth)
    assert r.status_code == 200
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    remember = next(t for t in inst.tools if t.name == "remember")
    recall = next(t for t in inst.tools if t.name == "recall")

    out = remember.func("Paris is the capital of France")
    assert "remembered" in out.lower()

    hits = recall.func("Paris", k=3)
    assert "Paris is the capital of France" in hits


def test_semantic_memory_namespace_isolation(client, auth, fake_model, sidecar_app):
    """Two memory tools backed by the same collection but different
    namespaces don't see each other's entries."""
    coll_id = _make_collection(client, auth, fake_model)

    def _build_tools(ns):
        r = client.post("/jobs/agent/run", json={
            "model_path": fake_model, "task": "x",
            "tools": {"semantic_memory": {
                "collection_id": coll_id, "namespace": ns,
            }},
        }, headers=auth)
        assert r.status_code == 200
        _drain(client, auth, r.json()["job_id"])
        inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
        return (
            next(t for t in inst.tools if t.name == "remember"),
            next(t for t in inst.tools if t.name == "recall"),
        )

    rem_a, rec_a = _build_tools("alice")
    rem_b, rec_b = _build_tools("bob")
    rem_a.func("alice's secret")
    rem_b.func("bob's secret")

    a_hits = rec_a.func("secret")
    b_hits = rec_b.func("secret")
    assert "alice" in a_hits and "bob" not in a_hits
    assert "bob" in b_hits and "alice" not in b_hits


def test_semantic_memory_recall_no_matches_returns_placeholder(
    client, auth, fake_model, sidecar_app,
):
    coll_id = _make_collection(client, auth, fake_model)
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"semantic_memory": {"collection_id": coll_id}},
    }, headers=auth)
    assert r.status_code == 200
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    recall = next(t for t in inst.tools if t.name == "recall")
    # Empty store -> recall returns the (no matches) sentinel.
    assert recall.func("anything") == "(no matches)"


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
