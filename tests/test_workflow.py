"""Phase D: workflow discovery + execute + dry-run endpoints.

Workflows are workspace-scoped *.py files under WORKFLOWS_DIR. Each
module exports either ``flow: Workflow`` or ``make_flow()``. The
sidecar imports them on demand and caches the compiled form by mtime.
"""
from __future__ import annotations

import json
import textwrap
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workflows_dir(sidecar_app, tmp_path, monkeypatch):
    """Point the sidecar at a fresh temp workflows dir for each test.

    Also clears the per-(path, mtime) module cache so a re-import in
    the new dir doesn't pick up a stale entry.
    """
    d = tmp_path / "workflows"
    d.mkdir()
    monkeypatch.setattr(sidecar_app, "WORKFLOWS_DIR", d)
    sidecar_app._WORKFLOW_CACHE.clear()
    return d


def _write_workflow(dir_: Path, name: str, body: str) -> Path:
    p = dir_ / f"{name}.py"
    p.write_text(textwrap.dedent(body))
    return p


# Minimal workflow source: exports a ``flow`` attribute. The _FakeWorkflow
# stub in conftest exposes ``set_entry`` / ``set_exit`` / ``add_node``;
# astream() yields a scripted WORKFLOW_START -> NODE_START -> NODE_END ->
# ANSWER -> WORKFLOW_END sequence.
_MIN_FLOW = """
'''Minimal test workflow.'''
from cyllama.agents import Workflow

flow = Workflow()
flow.add_node("greet", lambda s: {"greet": "hello " + s.get("name", "world")})
flow.set_entry("greet")
flow.set_exit("greet")
flow.declare_inputs("name")
"""


_FACTORY_FLOW = """
'''Factory-form workflow.'''
from cyllama.agents import Workflow

def make_flow():
    f = Workflow()
    f.add_node("step", lambda s: {"step": 1})
    f.set_entry("step")
    return f
"""


_BROKEN_FLOW = "raise RuntimeError('boom at import time')\n"


_NO_FLOW = "# this module has neither `flow` nor `make_flow`\nx = 1\n"


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


# ---------------------------------------------------------------------------
# /workflows discovery
# ---------------------------------------------------------------------------


def test_workflows_list_empty_when_dir_empty(client, auth, workflows_dir):
    r = client.get("/workflows", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["workflows"] == []
    assert body["dir"] == str(workflows_dir)


def test_workflows_list_returns_summary_per_file(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    _write_workflow(workflows_dir, "factory", _FACTORY_FLOW)
    r = client.get("/workflows", headers=auth)
    assert r.status_code == 200
    body = r.json()
    ids = {w["id"] for w in body["workflows"]}
    assert ids == {"minimal", "factory"}

    minimal = next(w for w in body["workflows"] if w["id"] == "minimal")
    assert minimal["filename"] == "minimal.py"
    assert minimal["doc"] == "Minimal test workflow."
    assert minimal["entry"] == "greet"
    assert minimal["error"] is None
    assert minimal["inputs_required"] == ["name"]


def test_workflows_list_surfaces_broken_files(client, auth, workflows_dir):
    """A broken workflow file populates ``error`` but doesn't break
    discovery of sibling files."""
    _write_workflow(workflows_dir, "good", _MIN_FLOW)
    _write_workflow(workflows_dir, "broken", _BROKEN_FLOW)
    r = client.get("/workflows", headers=auth)
    body = r.json()
    by_id = {w["id"]: w for w in body["workflows"]}
    assert by_id["good"]["error"] is None
    assert by_id["broken"]["error"] is not None
    assert "boom" in by_id["broken"]["error"]


def test_workflows_list_surfaces_files_missing_flow_export(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "no_flow", _NO_FLOW)
    body = client.get("/workflows", headers=auth).json()
    item = body["workflows"][0]
    assert item["error"] is not None
    assert "flow" in item["error"] or "make_flow" in item["error"]


def test_workflows_list_skips_invalid_filenames(client, auth, workflows_dir):
    # Filenames that don't match the id regex (leading underscore,
    # leading digit, hidden file) are silently skipped.
    _write_workflow(workflows_dir, "__init__", _MIN_FLOW)
    (workflows_dir / "2024_thing.py").write_text(_MIN_FLOW)
    (workflows_dir / ".hidden.py").write_text(_MIN_FLOW)
    _write_workflow(workflows_dir, "good", _MIN_FLOW)
    body = client.get("/workflows", headers=auth).json()
    ids = [w["id"] for w in body["workflows"]]
    assert ids == ["good"]


def test_workflows_list_empty_when_feature_unavailable(
    client, auth, workflows_dir, sidecar_app, monkeypatch,
):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "workflow", False)
    body = client.get("/workflows", headers=auth).json()
    assert body["workflows"] == []


# ---------------------------------------------------------------------------
# /workflows/{id}/spec dry-run
# ---------------------------------------------------------------------------


def test_workflow_spec_returns_plan_for_known_id(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    r = client.get("/workflows/minimal/spec", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "minimal"
    assert body["entry"] == "greet"
    assert body["exits"] == ["greet"]
    assert body["inputs_required"] == ["name"]
    assert body["mermaid"] is not None
    assert "graph TD" in body["mermaid"]


def test_workflow_spec_400_on_invalid_id(client, auth, workflows_dir):
    r = client.get("/workflows/__init__/spec", headers=auth)
    assert r.status_code == 400


def test_workflow_spec_404_on_unknown_id(client, auth, workflows_dir):
    r = client.get("/workflows/missing/spec", headers=auth)
    assert r.status_code == 404


def test_workflow_spec_400_on_broken_workflow(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "broken", _BROKEN_FLOW)
    r = client.get("/workflows/broken/spec", headers=auth)
    assert r.status_code == 400
    assert "boom" in r.json()["detail"]


def test_workflow_spec_501_when_feature_unavailable(
    client, auth, workflows_dir, sidecar_app, monkeypatch,
):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "workflow", False)
    r = client.get("/workflows/minimal/spec", headers=auth)
    assert r.status_code == 501


# ---------------------------------------------------------------------------
# /jobs/workflow/run execute
# ---------------------------------------------------------------------------


def test_workflow_run_400_on_invalid_id(client, auth, workflows_dir):
    r = client.post("/jobs/workflow/run", json={"workflow_id": "__init__"}, headers=auth)
    assert r.status_code == 400


def test_workflow_run_404_on_unknown_id(client, auth, workflows_dir):
    r = client.post("/jobs/workflow/run", json={"workflow_id": "missing"}, headers=auth)
    assert r.status_code == 404


def test_workflow_run_400_on_non_dict_initial_state(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    r = client.post("/jobs/workflow/run", json={
        "workflow_id": "minimal",
        "initial_state": "not a dict",
    }, headers=auth)
    assert r.status_code == 400


def test_workflow_run_400_on_broken_workflow(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "broken", _BROKEN_FLOW)
    r = client.post("/jobs/workflow/run", json={"workflow_id": "broken"}, headers=auth)
    assert r.status_code == 400


def test_workflow_run_501_when_feature_unavailable(
    client, auth, workflows_dir, sidecar_app, monkeypatch,
):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    monkeypatch.setitem(sidecar_app._FEATURE_FLAGS, "workflow", False)
    r = client.post("/jobs/workflow/run", json={"workflow_id": "minimal"}, headers=auth)
    assert r.status_code == 501


def test_workflow_run_streams_workflow_events(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    r = client.post("/jobs/workflow/run", json={
        "workflow_id": "minimal",
        "initial_state": {"name": "Alice", "greet": "hello Alice"},
    }, headers=auth)
    assert r.status_code == 200
    events = _drain(client, auth, r.json()["job_id"])
    traces = [e for e in events if e.get("type") == "trace"]
    types = [t["event_type"] for t in traces]
    # Default fake script:
    #   WORKFLOW_START -> NODE_START -> NODE_END -> ANSWER -> WORKFLOW_END
    assert types[0] == "WORKFLOW_START"
    assert types[-1] == "WORKFLOW_END"
    assert "ANSWER" in types
    assert "NODE_START" in types
    assert "NODE_END" in types


def test_workflow_run_result_carries_state_and_answer(client, auth, workflows_dir):
    _write_workflow(workflows_dir, "minimal", _MIN_FLOW)
    r = client.post("/jobs/workflow/run", json={
        "workflow_id": "minimal",
        "initial_state": {"name": "Alice", "greet": "hello Alice"},
    }, headers=auth)
    events = _drain(client, auth, r.json()["job_id"])
    res = [e for e in events if e.get("type") == "result"][0]["result"]
    assert res["success"] is True
    assert res["error"] is None
    # Initial state forwarded to WORKFLOW_END.
    assert res["state"]["name"] == "Alice"


def test_workflow_factory_form_loads(client, auth, workflows_dir):
    """``make_flow()`` form is honored when ``flow`` attribute is absent."""
    _write_workflow(workflows_dir, "factory", _FACTORY_FLOW)
    r = client.get("/workflows/factory/spec", headers=auth)
    assert r.status_code == 200
    assert r.json()["entry"] == "step"


# ---------------------------------------------------------------------------
# D.5 example workflows: smoke test that the shipped example file loads
# under the real-cyllama runtime. Uses an absolute path to the
# resources/ dir so the test is robust against pytest cwd. Skipped under
# the conftest stub because the stub's _FakeWorkflow doesn't implement
# the Layer C ``@flow.node`` decorator the example relies on.
# ---------------------------------------------------------------------------


def test_resources_example_workflows_directory_exists():
    """The seeded example dir must ship with the repo so first-launch
    seeding (src/main/index.js seedExampleWorkflows) has something to
    copy. A regression here means packaged builds ship with an empty
    Workflows pane on first launch."""
    repo = Path(__file__).resolve().parent.parent
    examples = repo / "resources" / "example-workflows"
    assert examples.is_dir(), f"missing examples dir: {examples}"
    py_files = sorted(examples.glob("*.py"))
    assert py_files, "no example workflow files shipped"
    # word_count.py is the canonical first example; new examples land
    # alongside it.
    names = {p.name for p in py_files}
    assert "word_count.py" in names
