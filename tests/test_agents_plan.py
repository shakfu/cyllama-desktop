"""Phase 7+: /jobs/agent/plan -- plan-and-execute streaming.

The sidecar bypasses cyllama's ``plan_and_execute`` helper and
orchestrates planner + executor with ``.stream()`` so events flow in
real time. Each event carries ``metadata.source`` = ``"planner"`` for
the plan-emission phase and ``"step-N"`` for each executor pass.
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


def test_plan_400_on_missing_model(client, auth):
    r = client.post("/jobs/agent/plan", json={"task": "x"}, headers=auth)
    assert r.status_code == 400


def test_plan_400_on_missing_task(client, auth, fake_model):
    r = client.post("/jobs/agent/plan",
                    json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_plan_501_when_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "agents.plan", False)
    r = client.post("/jobs/agent/plan", json={
        "model_path": fake_model, "task": "x",
    }, headers=auth)
    assert r.status_code == 501


def test_plan_streams_planner_then_executor_steps(
    client, auth, fake_model, sidecar_app, monkeypatch,
):
    """Override the ReActAgent stub script so the planner emits a
    two-step plan; the sidecar should then run the executor twice and
    tag each phase's events with the right ``source``."""
    # Replace the default THOUGHT/ANSWER script with one that yields a
    # plan answer = two newline-separated steps. The executor inherits
    # the same script, so each step run yields the same THOUGHT/ANSWER
    # pair (with the step text as the task).
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [
            ("THOUGHT", "let me think"),
            ("ANSWER", "step one\nstep two"),
        ],
    )

    r = client.post("/jobs/agent/plan", json={
        "model_path": fake_model, "task": "build the thing",
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])

    traces = [ev for ev in events if ev.get("type") == "trace"]
    # Three phases (planner, step-1, step-2) * 2 events each = 6 traces.
    assert len(traces) == 6
    sources = [t["metadata"].get("source") for t in traces]
    assert sources[:2] == ["planner", "planner"]
    assert sources[2:4] == ["step-1", "step-1"]
    assert sources[4:6] == ["step-2", "step-2"]

    results = [ev for ev in events if ev.get("type") == "result"]
    res = results[0]["result"]
    assert res["plan"] == ["step one", "step two"]
    assert len(res["steps"]) == 2
    assert res["steps"][0]["index"] == 1
    assert res["steps"][0]["plan"] == "step one"
    # Aggregated answer mentions both steps.
    assert "step one" in res["answer"]
    assert "step two" in res["answer"]


def test_plan_empty_plan_returns_planner_only(client, auth, fake_model, sidecar_app, monkeypatch):
    """Planner emits a blank answer -> no executor runs; result has an
    empty plan + empty steps."""
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [
            ("THOUGHT", "thinking"),
            ("ANSWER", "   "),  # blank-after-strip -> empty plan
        ],
    )
    r = client.post("/jobs/agent/plan", json={
        "model_path": fake_model, "task": "x",
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])

    traces = [ev for ev in events if ev.get("type") == "trace"]
    # Only planner events.
    assert all(t["metadata"].get("source") == "planner" for t in traces)

    res = [e for e in events if e.get("type") == "result"][0]["result"]
    assert res["plan"] == []
    assert res["steps"] == []


def test_plan_respects_max_steps(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [
            ("THOUGHT", "thinking"),
            ("ANSWER", "a\nb\nc\nd"),  # 4-step plan
        ],
    )
    r = client.post("/jobs/agent/plan", json={
        "model_path": fake_model, "task": "x",
        "max_steps": 2,
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])
    res = [e for e in events if e.get("type") == "result"][0]["result"]
    # plan_steps is capped to max_steps=2.
    assert len(res["steps"]) == 2


def test_plan_parser_strips_bullets_and_numbering(client, auth, fake_model, sidecar_app, monkeypatch):
    """Planner LLMs often emit bullets/numbering despite the prompt;
    the sidecar's parser strips them so the steps are clean."""
    monkeypatch.setattr(
        sidecar_app.cyllama.agents.ReActAgent,
        "_script",
        [
            ("THOUGHT", "t"),
            ("ANSWER", "1. first\n- second\n  * third\n4) fourth"),
        ],
    )
    r = client.post("/jobs/agent/plan", json={
        "model_path": fake_model, "task": "x",
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])
    res = [e for e in events if e.get("type") == "result"][0]["result"]
    assert res["plan"] == ["first", "second", "third", "fourth"]
