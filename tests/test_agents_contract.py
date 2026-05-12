"""Phase 7+: /jobs/agent/contract -- pre/post-condition contracts with policy."""
from __future__ import annotations

import json
import time


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


def test_contract_400_on_missing_model(client, auth):
    r = client.post("/jobs/agent/contract", json={"task": "x"}, headers=auth)
    assert r.status_code == 400


def test_contract_400_on_missing_task(client, auth, fake_model):
    r = client.post("/jobs/agent/contract",
                    json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_contract_400_on_unknown_preset(client, auth, fake_model):
    r = client.post("/jobs/agent/contract", json={
        "model_path": fake_model, "task": "x",
        "preset": "nonexistent",
    }, headers=auth)
    assert r.status_code == 400
    assert "preset" in r.json()["detail"]


def test_contract_400_on_unknown_policy(client, auth, fake_model):
    r = client.post("/jobs/agent/contract", json={
        "model_path": fake_model, "task": "x",
        "policy": "PERMISSIVE",
    }, headers=auth)
    assert r.status_code == 400
    assert "policy" in r.json()["detail"]


def test_contract_501_when_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "agents.contract", False)
    r = client.post("/jobs/agent/contract", json={
        "model_path": fake_model, "task": "x",
    }, headers=auth)
    assert r.status_code == 501


def test_contract_default_preset_runs(client, auth, fake_model):
    """Default preset 'none' + default policy 'OBSERVE' -> clean run."""
    r = client.post("/jobs/agent/contract", json={
        "model_path": fake_model, "task": "what is 6 * 7?",
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])

    traces = [ev for ev in events if ev.get("type") == "trace"]
    # Stub script: THOUGHT + ANSWER. 'none' preset has no rules.
    assert [t["event_type"] for t in traces] == ["THOUGHT", "ANSWER"]

    results = [ev for ev in events if ev.get("type") == "result"]
    res = results[0]["result"]
    assert res["contract"]["preset"] == "none"
    assert res["contract"]["policy"] == "OBSERVE"
    assert res["contract"]["violations"] == 0


def test_contract_answer_quality_preset_fires_violation(client, auth, fake_model):
    """'answer-quality' preset requires answer >= 10 chars; stub answers '42'
    (2 chars) -> a CONTRACT_VIOLATION event should land."""
    r = client.post("/jobs/agent/contract", json={
        "model_path": fake_model, "task": "x",
        "preset": "answer-quality",
        "policy": "OBSERVE",
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])

    traces = [ev for ev in events if ev.get("type") == "trace"]
    types = [t["event_type"] for t in traces]
    assert "CONTRACT_VIOLATION" in types, types

    results = [ev for ev in events if ev.get("type") == "result"]
    res = results[0]["result"]
    assert res["contract"]["violations"] >= 1


def test_contract_task_nonempty_preset_passes(client, auth, fake_model):
    """'task-nonempty' precondition holds for any non-blank task."""
    r = client.post("/jobs/agent/contract", json={
        "model_path": fake_model, "task": "do something",
        "preset": "task-nonempty",
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])
    types = [e["event_type"] for e in events if e.get("type") == "trace"]
    # No violation expected.
    assert "CONTRACT_VIOLATION" not in types


def test_contract_passes_policy_through(client, auth, fake_model, sidecar_app):
    r = client.post("/jobs/agent/contract", json={
        "model_path": fake_model, "task": "x",
        "preset": "none",
        "policy": "ENFORCE",
    }, headers=auth)
    assert r.status_code == 200
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ContractAgent.instances[-1]
    # _FakeContractPolicy.ENFORCE is the string "ENFORCE"
    assert inst.policy == "ENFORCE"


def test_info_contract_presets_endpoint(client, auth):
    r = client.get("/info/contract-presets", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert "presets" in body
    assert "none" in body["presets"]
    assert "answer-quality" in body["presets"]
    assert "ENFORCE" in body["policies"]


def test_info_contract_presets_empty_when_unavailable(
    client, auth, sidecar_app, monkeypatch,
):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "agents.contract", False)
    r = client.get("/info/contract-presets", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"presets": []}
