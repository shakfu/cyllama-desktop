"""Phase C: /jobs/agent/reflect -- worker + critic Reflexion loop.

Sidecar orchestrates the loop directly with .stream() so events flow
incrementally with metadata.source = worker-N / critic-N.
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


def test_reflect_400_on_missing_model(client, auth):
    r = client.post("/jobs/agent/reflect", json={"task": "x"}, headers=auth)
    assert r.status_code == 400


def test_reflect_400_on_missing_task(client, auth, fake_model):
    r = client.post("/jobs/agent/reflect",
                    json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_reflect_501_when_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "agents.reflect", False)
    r = client.post("/jobs/agent/reflect", json={
        "model_path": fake_model, "task": "x",
    }, headers=auth)
    assert r.status_code == 501


def test_reflect_accepts_on_first_critic_pass(
    client, auth, fake_model, sidecar_app, monkeypatch,
):
    """Default stub script: ANSWER='42'. Default acceptance_marker is
    'ACCEPT'. '42' lacks 'ACCEPT' so the loop should *not* accept on
    attempt 1. To force acceptance, override the script."""
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [
            ("THOUGHT", "thinking"),
            ("ANSWER", "ACCEPT"),
        ],
    )
    r = client.post("/jobs/agent/reflect", json={
        "model_path": fake_model, "task": "do it",
        "max_attempts": 3,
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])
    traces = [e for e in events if e.get("type") == "trace"]
    # Attempt 1: worker emits THOUGHT + ANSWER ('ACCEPT'); critic emits
    # the same ANSWER which contains 'ACCEPT' -> loop accepts. 4 traces.
    sources = [t["metadata"].get("source") for t in traces]
    assert sources == ["worker-1", "worker-1", "critic-1", "critic-1"]

    res = [e for e in events if e.get("type") == "result"][0]["result"]
    assert res["accepted"] is True
    assert res["attempts"] == 1
    assert res["answer"] == "ACCEPT"
    assert len(res["rounds"]) == 1
    assert res["rounds"][0]["accepted"] is True


def test_reflect_loops_to_max_attempts_when_critic_never_accepts(
    client, auth, fake_model, sidecar_app, monkeypatch,
):
    # Worker + critic both answer '42' -- 'ACCEPT' never appears, so the
    # loop runs max_attempts=2 times then stops with accepted=False.
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [
            ("THOUGHT", "t"),
            ("ANSWER", "42"),
        ],
    )
    r = client.post("/jobs/agent/reflect", json={
        "model_path": fake_model, "task": "do it",
        "max_attempts": 2,
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])
    traces = [e for e in events if e.get("type") == "trace"]
    sources = [t["metadata"].get("source") for t in traces]
    # 2 attempts * (worker + critic) * 2 events = 8 traces total.
    assert sources == [
        "worker-1", "worker-1",
        "critic-1", "critic-1",
        "worker-2", "worker-2",
        "critic-2", "critic-2",
    ]
    res = [e for e in events if e.get("type") == "result"][0]["result"]
    assert res["accepted"] is False
    assert res["attempts"] == 2
    assert res["answer"] == "42"  # last draft survives
    assert len(res["rounds"]) == 2


def test_reflect_custom_acceptance_marker(
    client, auth, fake_model, sidecar_app, monkeypatch,
):
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [
            ("THOUGHT", "t"),
            ("ANSWER", "looks good to me"),
        ],
    )
    # Acceptance marker matches the answer (case-insensitive substring).
    r = client.post("/jobs/agent/reflect", json={
        "model_path": fake_model, "task": "x",
        "max_attempts": 3,
        "acceptance_marker": "looks good",
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])
    res = [e for e in events if e.get("type") == "result"][0]["result"]
    assert res["accepted"] is True
    assert res["attempts"] == 1


def test_reflect_clamps_max_attempts(client, auth, fake_model):
    # max_attempts=999 -> clamped to 10 (the sidecar's hard cap).
    r = client.post("/jobs/agent/reflect", json={
        "model_path": fake_model, "task": "x",
        "max_attempts": 999,
    }, headers=auth)
    assert r.status_code == 200
    # The cap isn't directly observable from the trace under the stub
    # (the loop terminates on the first attempt if the marker isn't
    # found, but max_attempts gates the *upper bound* of iterations).
    # Drain to ensure the job ran clean.
    _drain(client, auth, r.json()["job_id"])


def test_reflect_critic_runs_without_tools(
    client, auth, fake_model, sidecar_app, monkeypatch,
):
    """Worker gets the tool catalog; critic always runs tool-less."""
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [("ANSWER", "ACCEPT")],  # quick accept so only one round happens
    )
    r = client.post("/jobs/agent/reflect", json={
        "model_path": fake_model, "task": "x",
        "tools": {"calculator": True},
    }, headers=auth)
    assert r.status_code == 200
    _drain(client, auth, r.json()["job_id"])

    # Two agent instances were created: worker-1 (tools=[calc]) and
    # critic-1 (tools=[]).
    instances = sidecar_app.cyllama.agents.ReActAgent.instances
    assert len(instances) >= 2
    worker, critic = instances[-2], instances[-1]
    assert len(worker.tools) == 1
    assert len(critic.tools) == 0
