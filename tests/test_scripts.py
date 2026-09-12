"""Workspace scripts: discovery, execution, cancel, timeout, backpressure.

Scripts are *.py files under SCRIPTS_DIR run as child processes. These
tests spawn real interpreters, so the fixtures write self-contained
scripts that import nothing from cyllama.
"""
from __future__ import annotations

import json
import os
import textwrap
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(live_client):
    """Override the default client for this module.

    A script job spawns a child process and outlives the POST that
    started it, which the per-request TestClient portal cannot support.
    """
    return live_client


@pytest.fixture
def scripts_dir(sidecar_app, tmp_path, monkeypatch):
    d = tmp_path / "scripts"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(sidecar_app, "SCRIPTS_DIR", d)
    monkeypatch.setattr(sidecar_app, "EXAMPLE_SCRIPTS_DIR", None)
    sidecar_app._SCRIPT_RUNNING.clear()
    return d


@pytest.fixture
def examples_dir(sidecar_app, scripts_dir, tmp_path, monkeypatch):
    d = tmp_path / "example-scripts"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(sidecar_app, "EXAMPLE_SCRIPTS_DIR", d)
    return d


def write_script(scripts_dir: Path, name: str, body: str) -> Path:
    p = scripts_dir / name
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return p


def start(client, auth, script_id, **body):
    r = client.post(
        "/jobs/script/run",
        json={"script_id": script_id, **body},
        headers=auth,
    )
    return r


def run_ok(client, auth, script_id, **body):
    r = start(client, auth, script_id, **body)
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def drain(client, auth, job_id, timeout_s=20.0):
    """Read the SSE stream to completion and return the event list."""
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
                raise AssertionError(f"timeout draining {job_id}; got {events}")
    return events


def wait_terminal(client, auth, job_id, timeout_s=30.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}", headers=auth)
        assert r.status_code == 200
        body = r.json()
        if body["state"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never finished")


def logs_of(events, stream=None):
    return [
        e["message"] for e in events
        if e.get("type") == "log" and (stream is None or e.get("stream") == stream)
    ]


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def wait_dead(pid: int, timeout_s=10.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_scripts_list_empty_when_dir_empty(client, auth, scripts_dir):
    r = client.get("/scripts", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["scripts"] == []
    assert body["dir"] == str(scripts_dir)


def test_scripts_list_returns_docstring_summary(client, auth, scripts_dir):
    write_script(scripts_dir, "hello.py", '''
        """First line of the doc.

        More detail.
        """
        print("hi")
    ''')
    body = client.get("/scripts", headers=auth).json()
    assert len(body["scripts"]) == 1
    item = body["scripts"][0]
    assert item["id"] == "hello"
    assert item["filename"] == "hello.py"
    assert item["doc"].startswith("First line of the doc.")
    assert item["error"] is None
    assert item["bytes"] > 0


def test_scripts_list_skips_invalid_filenames(client, auth, scripts_dir):
    for name in ("__init__.py", "2024_thing.py", ".hidden.py", "notes.txt"):
        (scripts_dir / name).write_text("print('x')\n", encoding="utf-8")
    write_script(scripts_dir, "ok.py", "print('ok')\n")
    body = client.get("/scripts", headers=auth).json()
    assert [s["id"] for s in body["scripts"]] == ["ok"]


def test_scripts_list_surfaces_syntax_errors(client, auth, scripts_dir):
    write_script(scripts_dir, "broken.py", "def (:\n")
    item = client.get("/scripts", headers=auth).json()["scripts"][0]
    assert item["id"] == "broken"
    assert "SyntaxError" in item["error"]
    assert item["doc"] == ""


def test_scripts_list_does_not_execute_scripts(client, auth, scripts_dir, tmp_path):
    """Listing a directory must never run what is in it."""
    marker = tmp_path / "imported.marker"
    write_script(scripts_dir, "sideeffect.py", f'''
        """Writes a marker at import time."""
        from pathlib import Path
        Path({str(marker)!r}).write_text("executed")
    ''')
    body = client.get("/scripts", headers=auth).json()
    assert body["scripts"][0]["doc"] == "Writes a marker at import time."
    assert not marker.exists()


# ---------------------------------------------------------------------------
# Run: success, args, result, artifacts
# ---------------------------------------------------------------------------


def test_run_streams_stdout_and_succeeds(client, auth, scripts_dir):
    write_script(scripts_dir, "chatty.py", '''
        print("line one")
        print("line two")
    ''')
    job_id = run_ok(client, auth, "chatty")
    events = drain(client, auth, job_id)
    out = logs_of(events, "stdout")
    assert "line one" in out and "line two" in out
    result = next(e for e in events if e["type"] == "result")["result"]
    assert result["exit_code"] == 0
    assert result["script_id"] == "chatty"
    assert result["duration_s"] >= 0


def test_run_passes_args_on_stdin(client, auth, scripts_dir):
    write_script(scripts_dir, "echo_args.py", '''
        import json, sys
        args = json.loads(sys.stdin.read())
        print(json.dumps(args, sort_keys=True))
    ''')
    job_id = run_ok(client, auth, "echo_args", args={"b": 2, "a": 1})
    events = drain(client, auth, job_id)
    assert '{"a": 1, "b": 2}' in logs_of(events, "stdout")


def test_run_result_json_becomes_result_payload(client, auth, scripts_dir):
    write_script(scripts_dir, "producer.py", '''
        import json, pathlib
        pathlib.Path("result.json").write_text(json.dumps({"rows": 3}))
    ''')
    job_id = run_ok(client, auth, "producer")
    events = drain(client, auth, job_id)
    result = next(e for e in events if e["type"] == "result")["result"]
    assert result["result"] == {"rows": 3}
    assert result["result_error"] is None


def test_run_invalid_result_json_reported_not_fatal(client, auth, scripts_dir):
    write_script(scripts_dir, "badresult.py", '''
        import pathlib
        pathlib.Path("result.json").write_text("{not json")
    ''')
    job_id = run_ok(client, auth, "badresult")
    events = drain(client, auth, job_id)
    result = next(e for e in events if e["type"] == "result")["result"]
    assert result["result"] is None
    assert "JSONDecodeError" in result["result_error"] or "Expecting" in result["result_error"]


def test_artifacts_are_listed_and_downloadable(client, auth, scripts_dir):
    write_script(scripts_dir, "writer.py", '''
        import pathlib
        pathlib.Path("report.csv").write_text("a,b\\n1,2\\n")
    ''')
    job_id = run_ok(client, auth, "writer")
    events = drain(client, auth, job_id)
    result = next(e for e in events if e["type"] == "result")["result"]
    names = [a["name"] for a in result["artifacts"]]
    assert "report.csv" in names
    r = client.get(f"/artifacts/{job_id}/report.csv", headers=auth)
    assert r.status_code == 200
    assert r.text == "a,b\n1,2\n"


def test_progress_sentinel_becomes_progress_event(client, auth, scripts_dir):
    write_script(scripts_dir, "progressive.py", '''
        import json, sys
        for i in (1, 2):
            sys.stdout.write("\\x1e" + json.dumps({"progress": i / 2, "message": f"step {i}"}) + "\\n")
        sys.stdout.flush()
    ''')
    job_id = run_ok(client, auth, "progressive")
    events = drain(client, auth, job_id)
    progress = [e for e in events if e["type"] == "progress"]
    assert [p["value"] for p in progress] == [0.5, 1.0]
    assert progress[-1]["message"] == "step 2"
    # A progress line is not also logged as output.
    assert not any("\x1e" in m for m in logs_of(events))


def test_carriage_return_only_output_still_emits(client, auth, scripts_dir):
    """A progress bar that only writes \\r must not look hung."""
    write_script(scripts_dir, "bar.py", '''
        import sys
        for i in range(3):
            sys.stdout.write(f"{i}%\\r")
        sys.stdout.flush()
        sys.stdout.write("\\n")
    ''')
    job_id = run_ok(client, auth, "bar")
    events = drain(client, auth, job_id)
    assert "0%" in logs_of(events, "stdout")
    assert "2%" in logs_of(events, "stdout")


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_failing_script_surfaces_exit_code_and_stderr_tail(client, auth, scripts_dir):
    write_script(scripts_dir, "boom.py", '''
        import sys
        print("about to fail", file=sys.stderr)
        sys.exit(3)
    ''')
    job_id = run_ok(client, auth, "boom")
    events = drain(client, auth, job_id)
    error = next(e for e in events if e["type"] == "error")
    assert "exited 3" in error["message"]
    assert "about to fail" in error["message"]
    assert wait_terminal(client, auth, job_id)["state"] == "failed"


def test_run_404_on_unknown_script(client, auth, scripts_dir):
    assert start(client, auth, "nope").status_code == 404


def test_run_400_on_invalid_id(client, auth, scripts_dir):
    assert start(client, auth, "../etc/passwd").status_code == 400


def test_run_400_on_non_object_args(client, auth, scripts_dir):
    write_script(scripts_dir, "noop.py", "pass\n")
    assert start(client, auth, "noop", args=[1, 2]).status_code == 400


def test_run_400_on_oversized_args(client, auth, scripts_dir, sidecar_app):
    write_script(scripts_dir, "noop.py", "pass\n")
    blob = "x" * (sidecar_app._SCRIPT_MAX_ARGS_BYTES + 100)
    assert start(client, auth, "noop", args={"blob": blob}).status_code == 400


def test_timeout_kills_the_script(client, auth, scripts_dir):
    write_script(scripts_dir, "sleeper.py", '''
        import time
        time.sleep(60)
    ''')
    job_id = run_ok(client, auth, "sleeper", timeout_s=1)
    events = drain(client, auth, job_id)
    error = next(e for e in events if e["type"] == "error")
    assert "timed out" in error["message"]
    assert wait_terminal(client, auth, job_id)["state"] == "failed"


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.name == "nt",
    reason="liveness probe is POSIX-only: os.kill(pid, 0) terminates on Windows",
)
def test_cancel_kills_the_whole_process_tree(client, auth, scripts_dir):
    """Cancel must reach grandchildren, not just the script itself."""
    write_script(scripts_dir, "forker.py", '''
        import pathlib, subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        pathlib.Path("pids.txt").write_text(f"{pathlib.os.getpid()} {child.pid}")
        print("spawned", flush=True)
        time.sleep(120)
    ''')
    job_id = run_ok(client, auth, "forker")
    pids_file = Path(client.get("/info", headers=auth).json()["sidecar"]["artifacts_dir"]) / job_id / "pids.txt"

    deadline = time.time() + 20
    while time.time() < deadline and not pids_file.exists():
        time.sleep(0.05)
    assert pids_file.exists(), "script never started"
    parent_pid, child_pid = (int(x) for x in pids_file.read_text().split())

    r = client.post(f"/jobs/{job_id}/cancel", headers=auth)
    assert r.status_code == 200

    assert wait_dead(parent_pid), "script process survived cancel"
    assert wait_dead(child_pid), "grandchild survived cancel"
    assert wait_terminal(client, auth, job_id)["state"] == "cancelled"


@pytest.mark.skipif(os.name == "nt", reason="checks the non-Windows fallbacks")
def test_windows_job_helpers_are_inert_off_windows(sidecar_app):
    """The job-object path must never fire on POSIX, where killpg rules."""
    assert sidecar_app._win_create_job_for(os.getpid()) is None
    assert sidecar_app._win_terminate_job(None) is False
    assert sidecar_app._win_terminate_job(object()) is False
    sidecar_app._win_close_job(None)          # no raise
    sidecar_app._win_close_job(object())      # no raise


def test_spawn_kwargs_isolate_the_child(sidecar_app):
    kwargs = sidecar_app._script_spawn_kwargs()
    if os.name == "nt":
        assert kwargs["creationflags"] & sidecar_app._WIN_CREATE_NEW_PROCESS_GROUP
    else:
        assert kwargs == {"start_new_session": True}


# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------


def test_concurrency_cap_rejects_extra_runs(client, auth, scripts_dir, sidecar_app):
    write_script(scripts_dir, "sleeper.py", '''
        import time
        time.sleep(30)
    ''')
    started = []
    for _ in range(sidecar_app._SCRIPT_MAX_CONCURRENT):
        started.append(run_ok(client, auth, "sleeper", timeout_s=30))
    r = start(client, auth, "sleeper")
    assert r.status_code == 429
    for job_id in started:
        client.post(f"/jobs/{job_id}/cancel", headers=auth)
    for job_id in started:
        wait_terminal(client, auth, job_id)


# ---------------------------------------------------------------------------
# Backpressure (Phase 0 regression)
# ---------------------------------------------------------------------------


def test_chatty_script_completes_with_no_subscriber(client, auth, scripts_dir):
    """A flood of output with nobody listening must not wedge the job.

    The event queue is bounded and the SSE stream takes one subscriber
    and never replays. Before the lossy path, the producer blocked
    forever once the queue filled and the script stalled mid-run with no
    error and no timeout.
    """
    write_script(scripts_dir, "flood.py", '''
        for i in range(20000):
            print(f"line {i}")
    ''')
    job_id = run_ok(client, auth, "flood")
    body = wait_terminal(client, auth, job_id, timeout_s=60)
    assert body["state"] == "succeeded"
    assert body["dropped"] > 0


def test_job_log_replays_retained_events(client, auth, scripts_dir, sidecar_app):
    write_script(scripts_dir, "three.py", '''
        for i in range(3):
            print(f"line {i}")
    ''')
    job_id = run_ok(client, auth, "three")
    wait_terminal(client, auth, job_id)

    body = client.get(f"/jobs/{job_id}/log", headers=auth).json()
    messages = [e["message"] for e in body["events"] if e["type"] == "log"]
    assert "line 0" in messages and "line 2" in messages
    assert body["capacity"] == sidecar_app._JOB_LOG_RING
    seqs = [e["seq"] for e in body["events"]]
    assert seqs == sorted(seqs)

    # ``after`` returns only newer entries.
    tail = client.get(f"/jobs/{job_id}/log?after={seqs[-2]}", headers=auth).json()
    assert [e["seq"] for e in tail["events"]] == [seqs[-1]]


def test_job_log_ring_is_bounded(client, auth, scripts_dir, sidecar_app):
    write_script(scripts_dir, "flood.py", '''
        for i in range(3000):
            print(f"line {i}")
    ''')
    job_id = run_ok(client, auth, "flood")
    wait_terminal(client, auth, job_id, timeout_s=60)
    body = client.get(f"/jobs/{job_id}/log", headers=auth).json()
    assert body["retained"] <= sidecar_app._JOB_LOG_RING


# ---------------------------------------------------------------------------
# Client library wiring
# ---------------------------------------------------------------------------


def test_client_library_is_importable_from_a_script(client, auth, scripts_dir):
    """The child finds cyllama_desktop on PYTHONPATH and sees its job."""
    write_script(scripts_dir, "usesclient.py", '''
        import json
        from cyllama_desktop import app
        app.progress(0.5, "halfway")
        print(json.dumps({
            "job_id": app.job_id,
            "args": app.args,
            "artifacts_dir": str(app.artifacts_dir),
        }, sort_keys=True))
    ''')
    job_id = run_ok(client, auth, "usesclient", args={"k": "v"})
    events = drain(client, auth, job_id)
    line = next(m for m in logs_of(events, "stdout") if m.startswith("{"))
    payload = json.loads(line)
    assert payload["job_id"] == job_id
    assert payload["args"] == {"k": "v"}
    assert payload["artifacts_dir"].endswith(job_id)
    progress = [e for e in events if e["type"] == "progress"]
    assert progress and progress[0]["message"] == "halfway"


def test_client_library_errors_clearly_outside_the_app(client, auth, scripts_dir, monkeypatch):
    """Run by hand with no sidecar env, the failure must name the cause."""
    import importlib
    import sys as _sys

    lib_dir = Path(__file__).resolve().parent.parent / "python-sidecar"
    _sys.path.insert(0, str(lib_dir))
    try:
        for var in ("CYLLAMA_SIDECAR_URL", "CYLLAMA_SIDECAR_TOKEN"):
            monkeypatch.delenv(var, raising=False)
        _sys.modules.pop("cyllama_desktop", None)
        mod = importlib.import_module("cyllama_desktop")
        with pytest.raises(mod.NotRunningUnderDesktop) as exc:
            mod.app.models()
        assert "cyllama-desktop" in str(exc.value)
    finally:
        _sys.modules.pop("cyllama_desktop", None)
        _sys.path.remove(str(lib_dir))


# ---------------------------------------------------------------------------
# Packaging
# ---------------------------------------------------------------------------


def test_client_library_ships_next_to_the_sidecar():
    lib = Path(__file__).resolve().parent.parent / "python-sidecar" / "cyllama_desktop.py"
    assert lib.is_file(), "cyllama_desktop.py must sit next to sidecar.py"


def test_example_scripts_are_valid_and_documented():
    """Each example must parse, be discoverable, and explain itself.

    The pane shows the module docstring as the description and derives
    the id from the filename, so a missing docstring or a filename the
    discovery regex rejects makes an example useless on arrival.
    """
    import ast
    import re

    d = Path(__file__).resolve().parent.parent / "resources" / "example-scripts"
    files = sorted(d.glob("*.py"))
    assert len(files) >= 5, "the shipped examples should cover the documented use cases"
    for f in files:
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        assert re.match(r"^[A-Za-z][A-Za-z0-9_-]*$", f.stem), f"{f.name} would be skipped by discovery"
        doc = ast.get_docstring(tree)
        assert doc and len(doc.splitlines()) > 1, f"{f.name} needs a docstring with an args note"
        assert "from cyllama_desktop import app" in src, f"{f.name} should show the client library"


# ---------------------------------------------------------------------------
# Shipped examples: catalog + copy (docs/dev/scripting.md S17)
# ---------------------------------------------------------------------------


def test_examples_absent_when_no_examples_dir(client, auth, scripts_dir):
    assert client.get("/scripts", headers=auth).json()["examples"] == []


def test_examples_listed_with_docstring(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '''
        """Sampling grid."""
        print("sweep")
    ''')
    body = client.get("/scripts", headers=auth).json()
    assert body["scripts"] == []
    assert [e["id"] for e in body["examples"]] == ["sweep"]
    assert body["examples"][0]["doc"] == "Sampling grid."
    assert body["examples"][0]["in_workspace"] is False


def test_example_marked_in_workspace_when_name_taken(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    write_script(scripts_dir, "sweep.py", '"""Mine."""\n')
    body = client.get("/scripts", headers=auth).json()
    assert body["examples"][0]["in_workspace"] is True


def test_listing_examples_does_not_execute_them(client, auth, scripts_dir, examples_dir, tmp_path):
    """The catalog is summarised with ast.parse, never by importing."""
    marker = tmp_path / "example.marker"
    write_script(examples_dir, "sideeffect.py", f'''
        """Writes a marker at import time."""
        from pathlib import Path
        Path({str(marker)!r}).write_text("executed")
    ''')
    body = client.get("/scripts", headers=auth).json()
    assert body["examples"][0]["doc"] == "Writes a marker at import time."
    assert not marker.exists()


def test_copy_example_puts_it_in_the_workspace(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Sampling grid."""\nprint("sweep")\n')
    r = client.post("/scripts/examples/sweep/copy", headers=auth)
    assert r.status_code == 200
    assert r.json()["filename"] == "sweep.py"
    assert (scripts_dir / "sweep.py").read_text() == '"""Sampling grid."""\nprint("sweep")\n'
    body = client.get("/scripts", headers=auth).json()
    assert [s["id"] for s in body["scripts"]] == ["sweep"]
    assert body["examples"][0]["in_workspace"] is True


def test_copy_never_overwrites_user_code(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    write_script(scripts_dir, "sweep.py", '"""Mine, edited."""\n')
    r = client.post("/scripts/examples/sweep/copy", headers=auth)
    assert r.status_code == 409
    assert (scripts_dir / "sweep.py").read_text() == '"""Mine, edited."""\n'


def test_copy_404_on_unknown_example(client, auth, scripts_dir, examples_dir):
    assert client.post("/scripts/examples/nope/copy", headers=auth).status_code == 404


def test_copy_400_on_invalid_id(client, auth, scripts_dir, examples_dir):
    assert client.post("/scripts/examples/2bad/copy", headers=auth).status_code == 400


def test_copy_404_when_build_ships_no_examples(client, auth, scripts_dir):
    assert client.post("/scripts/examples/sweep/copy", headers=auth).status_code == 404


def test_catalog_flags_a_locally_edited_copy(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    write_script(scripts_dir, "sweep.py", '"""Shipped."""\n')
    body = client.get("/scripts", headers=auth).json()
    assert body["examples"][0]["workspace_differs"] is False
    write_script(scripts_dir, "sweep.py", '"""Shipped."""\nprint("mine")\n')
    body = client.get("/scripts", headers=auth).json()
    assert body["examples"][0]["workspace_differs"] is True


def test_uninstall_removes_an_unmodified_copy(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    client.post("/scripts/examples/sweep/copy", headers=auth)
    r = client.delete("/scripts/examples/sweep", headers=auth)
    assert r.status_code == 200
    assert r.json()["had_edits"] is False
    assert not (scripts_dir / "sweep.py").exists()
    # The example itself survives, and is offered again.
    body = client.get("/scripts", headers=auth).json()
    assert body["examples"][0]["in_workspace"] is False


def test_uninstall_409_on_a_modified_copy(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    write_script(scripts_dir, "sweep.py", '"""Shipped."""\nprint("my edit")\n')
    r = client.delete("/scripts/examples/sweep", headers=auth)
    assert r.status_code == 409
    assert (scripts_dir / "sweep.py").exists()


def test_uninstall_force_deletes_a_modified_copy(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    write_script(scripts_dir, "sweep.py", '"""Shipped."""\nprint("my edit")\n')
    r = client.delete("/scripts/examples/sweep?force=true", headers=auth)
    assert r.status_code == 200
    assert r.json()["had_edits"] is True
    assert not (scripts_dir / "sweep.py").exists()


def test_uninstall_cannot_delete_a_user_authored_script(client, auth, scripts_dir, examples_dir):
    """Only a name the build ships can be uninstalled.

    Otherwise the route would delete any file in the workspace, and a
    script the user wrote exists nowhere else.
    """
    write_script(scripts_dir, "mine.py", '"""Mine."""\n')
    r = client.delete("/scripts/examples/mine", headers=auth)
    assert r.status_code == 404
    assert (scripts_dir / "mine.py").exists()


def test_uninstall_404_when_not_installed(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    assert client.delete("/scripts/examples/sweep", headers=auth).status_code == 404


def test_summary_carries_the_absolute_path(client, auth, scripts_dir, examples_dir):
    """The pane shows the path before running, so discovery must send it."""
    write_script(scripts_dir, "mine.py", '"""Mine."""\n')
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    body = client.get("/scripts", headers=auth).json()
    assert body["scripts"][0]["path"] == str(scripts_dir / "mine.py")
    assert body["examples"][0]["path"] == str(examples_dir / "sweep.py")


# ---------------------------------------------------------------------------
# Source view: the pane shows the file before running it
# ---------------------------------------------------------------------------


def test_source_returns_the_file_verbatim(client, auth, scripts_dir):
    body = '"""Doc."""\nx = 1  # trailing\n\tindented = 2\n'
    (scripts_dir / "mine.py").write_text(body, encoding="utf-8")
    r = client.get("/scripts/mine/source", headers=auth)
    assert r.status_code == 200
    got = r.json()
    # Verbatim: the user is asked to judge this text, so no normalising.
    assert got["source"] == body
    assert got["path"] == str(scripts_dir / "mine.py")
    assert got["bytes"] == len(body.encode())


def test_source_reads_a_shipped_example_before_install(client, auth, scripts_dir, examples_dir):
    write_script(examples_dir, "sweep.py", '"""Shipped."""\n')
    got = client.get("/scripts/sweep/source?shipped=true", headers=auth).json()
    assert got["source"] == '"""Shipped."""\n'
    # Not installed, so the workspace read fails.
    assert client.get("/scripts/sweep/source", headers=auth).status_code == 404


def test_source_404_on_unknown_and_400_on_bad_id(client, auth, scripts_dir):
    assert client.get("/scripts/nope/source", headers=auth).status_code == 404
    assert client.get("/scripts/2bad/source", headers=auth).status_code == 400


def test_source_413_when_too_large(client, auth, scripts_dir, sidecar_app, monkeypatch):
    monkeypatch.setattr(sidecar_app, "_SCRIPT_MAX_SOURCE_BYTES", 10)
    (scripts_dir / "big.py").write_text("x = 1\n" * 100, encoding="utf-8")
    assert client.get("/scripts/big/source", headers=auth).status_code == 413


def test_source_does_not_execute_the_file(client, auth, scripts_dir, tmp_path):
    marker = tmp_path / "ran.marker"
    write_script(scripts_dir, "sideeffect.py", f"""
        \"\"\"Writes a marker at import time.\"\"\"
        from pathlib import Path
        Path({str(marker)!r}).write_text("executed")
    """)
    got = client.get("/scripts/sideeffect/source", headers=auth).json()
    assert "write_text" in got["source"]
    assert not marker.exists()


# ---------------------------------------------------------------------------
# Client library: external providers (docs/dev/providers.md R1).
#
# The wire format is already covered by tests/test_providers.py; what matters
# here is the body the client library builds, so a script naming a provider
# reaches the same endpoint the chat pane does.
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Stands in for the urllib response ``App._open`` returns."""

    def __init__(self, lines):
        self._lines = list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self):
        return b"".join(self._lines)


@pytest.fixture()
def client_lib(monkeypatch):
    """The client library with a sidecar env, plus the calls it makes.

    ``_open`` is stubbed: these tests are about the request the library
    builds, not about HTTP.
    """
    import importlib
    import sys as _sys

    lib_dir = Path(__file__).resolve().parent.parent / "python-sidecar"
    _sys.path.insert(0, str(lib_dir))
    monkeypatch.setenv("CYLLAMA_SIDECAR_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CYLLAMA_SIDECAR_TOKEN", "t")
    monkeypatch.delenv("CYLLAMA_JOB_ID", raising=False)
    _sys.modules.pop("cyllama_desktop", None)
    mod = importlib.import_module("cyllama_desktop")

    calls = []
    replies = {}

    def fake_open(method, path, body=None, params=None, timeout=None):
        calls.append({"method": method, "path": path, "body": body, "params": params})
        return _FakeResponse(replies.get(path, [b'data: [DONE]\n']))

    monkeypatch.setattr(mod.app, "_open", fake_open)
    try:
        yield mod, calls, replies
    finally:
        _sys.modules.pop("cyllama_desktop", None)
        _sys.path.remove(str(lib_dir))


def test_chat_with_a_named_provider_sends_a_provider_ref(client_lib):
    mod, calls, replies = client_lib
    replies["/chat"] = [b'data: {"text": "hi"}\n', b'data: [DONE]\n']
    out = mod.app.chat("q", provider="openai", model="gpt-5.4", temperature=0.5)
    assert out == "hi"
    body = calls[0]["body"]
    assert body["provider"] == {"kind": "openai"}
    assert body["model"] == "gpt-5.4"
    assert body["params"] == {"temperature": 0.5}
    # No local model is involved, so no path is sent and none is looked up.
    assert "model_path" not in body


def test_chat_with_a_compat_provider_passes_the_endpoint_through(client_lib):
    mod, calls, _ = client_lib
    ref = {"kind": "compat", "name": "LM Studio", "base_url": "http://localhost:1234/v1"}
    mod.app.chat("q", provider=ref, model="local-model")
    assert calls[0]["body"]["provider"] == ref


def test_chat_with_a_provider_requires_a_model(client_lib):
    mod, calls, _ = client_lib
    with pytest.raises(mod.SidecarError) as exc:
        mod.app.chat("q", provider="openai")
    assert "model=" in str(exc.value)
    # Failed before any request, so no /models/cached lookup either.
    assert calls == []


def test_provider_must_be_a_string_or_a_dict(client_lib):
    mod, _, _ = client_lib
    with pytest.raises(mod.SidecarError):
        mod.app.chat("q", provider=42, model="m")


def test_chat_without_a_provider_still_sends_model_path(client_lib):
    mod, calls, replies = client_lib
    replies["/chat"] = [b'data: {"text": "x"}\n', b'data: [DONE]\n']
    mod.app.chat("q", model="/models/a.gguf")
    body = calls[0]["body"]
    assert body["model_path"] == "/models/a.gguf"
    assert "provider" not in body and "model" not in body


def test_providers_lists_configured_accounts(client_lib):
    mod, calls, replies = client_lib
    replies["/providers/credentials"] = [b'{"configured": ["openai", "compat.lm"]}']
    assert mod.app.providers() == ["openai", "compat.lm"]
    assert calls[0]["path"] == "/providers/credentials"


def test_provider_models_forwards_the_endpoint_fields(client_lib):
    mod, calls, replies = client_lib
    replies["/providers/models"] = [b'{"models": [{"id": "gpt-5.4"}]}']
    ref = {"kind": "compat", "name": "LM", "base_url": "https://x.co/v1"}
    assert mod.app.provider_models(ref) == [{"id": "gpt-5.4"}]
    assert calls[0]["params"] == {
        "kind": "compat", "name": "LM", "base_url": "https://x.co/v1",
    }


def test_progress_is_inert_without_a_job(client_lib, capsys):
    """A workflow node imports the same handle inside the sidecar, where
    this stdout is the sidecar's log rather than a job's event stream."""
    mod, _, _ = client_lib
    mod.app.progress(0.5, "halfway")
    assert capsys.readouterr().out == ""


def test_progress_writes_the_sentinel_inside_a_job(client_lib, monkeypatch, capsys):
    mod, _, _ = client_lib
    monkeypatch.setattr(mod.app, "job_id", "job-1")
    mod.app.progress(0.5, "halfway")
    assert "halfway" in capsys.readouterr().out


def test_sidecar_exports_its_own_url_so_workflows_can_reach_it(sidecar_app):
    """Workflows run in the sidecar process; the client library finds the
    app through this variable, which only _script_env used to set."""
    import os
    assert os.environ["CYLLAMA_SIDECAR_URL"].endswith(f":{sidecar_app.PORT}")


def test_chat_with_a_provider_labels_the_usage_row(client_lib, monkeypatch):
    """Provider spend has to be traceable to the job that caused it."""
    mod, calls, _ = client_lib
    monkeypatch.setattr(mod.app, "job_id", "job-7")
    mod.app.chat("q", provider="openai", model="gpt-5.4")
    assert calls[0]["body"]["source"] == "script:job-7"


def test_chat_from_a_workflow_labels_itself_as_such(client_lib):
    """A workflow node imports the same handle but has no job id."""
    mod, calls, _ = client_lib
    mod.app.chat("q", provider="openai", model="gpt-5.4")
    assert calls[0]["body"]["source"] == "workflow"
