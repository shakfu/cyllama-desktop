"""Phase 7: /jobs/agent/run + tool catalog + features.agents."""
from __future__ import annotations

import json
import time
from pathlib import Path


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


def test_info_features_includes_agents(client, auth):
    body = client.get("/info", headers=auth).json()
    assert body["features"].get("agents") is True


def test_agent_400_on_missing_model(client, auth):
    r = client.post("/jobs/agent/run", json={"task": "do a thing"}, headers=auth)
    assert r.status_code == 400


def test_agent_400_on_missing_task(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={"model_path": fake_model}, headers=auth)
    assert r.status_code == 400


def test_agent_400_on_blank_task(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "   ",
    }, headers=auth)
    assert r.status_code == 400


def test_agent_501_when_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "agents", False)
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
    }, headers=auth)
    assert r.status_code == 501


def test_agent_streams_trace_then_result(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "what is 6 * 7?",
        "tools": {"calculator": True},
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    events = _drain(client, auth, job_id)

    traces = [ev for ev in events if ev.get("type") == "trace"]
    assert [t["event_type"] for t in traces] == ["THOUGHT", "ANSWER"]
    assert traces[1]["content"] == "42"

    results = [ev for ev in events if ev.get("type") == "result"]
    assert len(results) == 1
    res = results[0]["result"]
    assert res["answer"] == "42"
    assert len(res["events"]) == 2


def test_agent_clamps_max_iterations(client, auth, fake_model, sidecar_app):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"calculator": True},
        "max_iterations": 9999,
    }, headers=auth)
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _drain(client, auth, job_id)
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    assert inst.max_iterations == 50  # _build_agent_tools uses [1,50] cap


def test_agent_read_file_400_without_sandbox(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "read foo",
        "tools": {"read_file": {}},
    }, headers=auth)
    assert r.status_code == 400


def test_agent_read_file_tool_refuses_path_escape(sidecar_app, tmp_path):
    """The read_file tool must refuse paths that resolve outside the
    chosen sandbox directory. Tested by exercising the tool's func
    directly so we don't have to script the agent into calling it."""
    (tmp_path / "in.txt").write_text("inside", "utf-8")
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", "utf-8")

    tool = sidecar_app._make_read_file_tool(tmp_path)
    assert tool.func("in.txt") == "inside"
    assert tool.func("../outside.txt").startswith("error: refusing path outside sandbox")
    assert tool.func("missing.txt").startswith("error: not a file")


def test_agent_calculator_tool_rejects_unsafe_input(sidecar_app):
    tool = sidecar_app._make_calculator_tool()
    assert tool.func("2 + 3 * 4") == "14"
    # Names / function calls / attribute access are all rejected by the
    # AST-walking sandbox.
    assert tool.func("__import__('os').system('rm -rf /')").startswith("error:")
    assert tool.func("(1).__class__").startswith("error:")


def test_agent_rag_query_404_on_unknown_collection(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"rag_query": {"collection_id": "nope-12345678"}},
    }, headers=auth)
    assert r.status_code == 404


def test_agent_rag_query_400_on_invalid_collection_id(client, auth, fake_model):
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"rag_query": {"collection_id": "BAD-ID"}},  # uppercase rejected
    }, headers=auth)
    assert r.status_code == 400


def test_agent_stock_tools_default_on(client, auth, fake_model, sidecar_app):
    """Stock cyllama tools (calculator / current_time / word_count) are
    auto-injected by default -- the renderer doesn't have to opt in."""
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"web_fetch": True},
    }, headers=auth)
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    names = {t.name for t in inst.tools}
    assert {"calculator", "current_time", "word_count"}.issubset(names)


def test_agent_stock_tools_suppressed_when_off(client, auth, fake_model, sidecar_app):
    """``stock_tools: false`` drops the auto-injected group -- useful
    when a task-specific tool would tempt the model otherwise."""
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"stock_tools": False, "web_fetch": True},
    }, headers=auth)
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    names = {t.name for t in inst.tools}
    assert "calculator" not in names
    assert "current_time" not in names
    assert "word_count" not in names
    assert "web_fetch" in names


def test_agent_quarto_render_opt_in(client, auth, fake_model, sidecar_app, monkeypatch):
    """``quarto_render: true`` adds the stock cyllama @tool. Gated on
    both the tool resolving AND the ``quarto`` CLI being on PATH; we
    monkeypatch the CLI flag so the test doesn't depend on quarto
    being installed in the CI runner."""
    monkeypatch.setattr(sidecar_app, "_QUARTO_CLI", "/usr/local/bin/quarto")
    # Default off.
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"calculator": True},
    }, headers=auth)
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    assert "quarto_render" not in {t.name for t in inst.tools}

    # Opt-in: present.
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"quarto_render": True},
    }, headers=auth)
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    assert "quarto_render" in {t.name for t in inst.tools}


def test_agent_quarto_render_501_when_cli_missing(client, auth, fake_model, sidecar_app, monkeypatch):
    """Tool resolved, CLI absent → 501 with the install hint."""
    monkeypatch.setattr(sidecar_app, "_QUARTO_CLI", None)
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"quarto_render": True},
    }, headers=auth)
    assert r.status_code == 501
    assert "quarto" in r.json()["detail"].lower()


def test_agent_search_wikipedia_opt_in(client, auth, fake_model, sidecar_app):
    """``search_wikipedia: true`` adds the stock cyllama @tool. Not in
    the auto-injected set (network side effect)."""
    # Default off: stock tools include calculator/time/word_count only.
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"calculator": True},
    }, headers=auth)
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    assert "search_wikipedia" not in {t.name for t in inst.tools}

    # Opt-in: now present.
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {"search_wikipedia": True},
    }, headers=auth)
    _drain(client, auth, r.json()["job_id"])
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    assert "search_wikipedia" in {t.name for t in inst.tools}


def test_agent_passes_selected_tools_to_react_agent(client, auth, fake_model, sidecar_app, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    r = client.post("/jobs/agent/run", json={
        "model_path": fake_model, "task": "x",
        "tools": {
            "calculator": True,
            "read_file": {"sandbox_dir": str(sandbox)},
            "web_fetch": True,
        },
    }, headers=auth)
    job_id = r.json()["job_id"]
    _drain(client, auth, job_id)
    inst = sidecar_app.cyllama.agents.ReActAgent.instances[-1]
    names = sorted(t.name for t in inst.tools)
    # Auto-injected stock cyllama tools always sit alongside whatever
    # the renderer picked. The ``calculator`` entry in the spec is a
    # no-op because the stock @tool already provides it (dedup by name).
    assert names == [
        "calculator", "current_time", "read_file", "web_fetch", "word_count",
    ]
