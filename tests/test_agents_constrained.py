"""Phase 7+: /jobs/agent/constrained -- grammar-enforced tool calling.

Mirrors test_agents.py's coverage for the ReAct endpoint: feature gate,
validation 400s, 501 when missing, happy-path streaming + result,
optional kwargs forwarding (format / allow_reasoning).
"""
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


def test_constrained_400_on_missing_model(client, auth):
    r = client.post("/jobs/agent/constrained", json={"task": "x"}, headers=auth)
    assert r.status_code == 400


def test_constrained_400_on_missing_task(client, auth, fake_model):
    r = client.post("/jobs/agent/constrained",
                    json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_constrained_400_on_blank_task(client, auth, fake_model):
    r = client.post("/jobs/agent/constrained", json={
        "model_path": fake_model, "task": "   ",
    }, headers=auth)
    assert r.status_code == 400


def test_constrained_400_on_bad_format(client, auth, fake_model):
    r = client.post("/jobs/agent/constrained", json={
        "model_path": fake_model, "task": "x",
        "format": "yaml",  # not one of json|json_array|function_call
    }, headers=auth)
    assert r.status_code == 400


def test_constrained_501_when_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "agents.constrained", False)
    r = client.post("/jobs/agent/constrained", json={
        "model_path": fake_model, "task": "x",
    }, headers=auth)
    assert r.status_code == 501


def test_constrained_streams_trace_then_result(client, auth, fake_model):
    r = client.post("/jobs/agent/constrained", json={
        "model_path": fake_model, "task": "what is 6 * 7?",
    }, headers=auth)
    assert r.status_code == 200, f"unexpected: {r.status_code} body={r.text}"
    job_id = r.json()["job_id"]
    events = _drain(client, auth, job_id)

    traces = [ev for ev in events if ev.get("type") == "trace"]
    # The conftest stub for _FakeConstrainedAgent inherits the default
    # script (THOUGHT then ANSWER).
    assert [t["event_type"] for t in traces] == ["THOUGHT", "ANSWER"]
    assert traces[1]["content"] == "42"

    results = [ev for ev in events if ev.get("type") == "result"]
    assert len(results) == 1
    res = results[0]["result"]
    assert res["answer"] == "42"


def test_constrained_passes_format_and_reasoning_through(
    client, auth, fake_model, sidecar_app,
):
    r = client.post("/jobs/agent/constrained", json={
        "model_path": fake_model, "task": "x",
        "format": "function_call",
        "allow_reasoning": True,
        "max_iterations": 7,
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _drain(client, auth, job_id)
    inst = sidecar_app.cyllama.agents.ConstrainedAgent.instances[-1]
    # _FakeConstrainedAgent inherits __init__ via _FakeReActAgent which
    # captures these on the instance via **kwargs -- the round-trip is
    # the proof we passed them through.
    assert inst.max_iterations == 7


def test_constrained_clamps_max_iterations(client, auth, fake_model, sidecar_app):
    r = client.post("/jobs/agent/constrained", json={
        "model_path": fake_model, "task": "x",
        "max_iterations": 9999,
    }, headers=auth)
    assert r.status_code == 200
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ConstrainedAgent.instances[-1]
    assert inst.max_iterations == 50
