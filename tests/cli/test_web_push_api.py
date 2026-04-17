"""Regression tests for the web ``/api/push`` endpoint.

The endpoint previously had two silent fail-open branches — missing
``HF_TOKEN`` and ``ImportError`` on the upload module — that marked
traces as ``UPLOADED`` in local state while reporting a ✓ to the user
without anything reaching HuggingFace. These tests pin down the
post-fix behavior: the endpoint delegates to the CLI subprocess, and
local trace status only transitions when the subprocess actually
transitioned them out of COMMITTED.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.clients.web.server import create_app
from opentraces.core.state import StateManager, TraceStatus


TRACE_ID = "trace-push-001"


def _seed_committed_trace(tmp_path: Path) -> Path:
    trace = {
        "schema_version": "0.3.0",
        "trace_id": TRACE_ID,
        "session_id": "sess-push-001",
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": "push me"},
        "steps": [
            {"step_index": 1, "role": "user", "content": "hi",
             "timestamp": "2026-04-16T10:00:00Z"},
        ],
        "metrics": {},
    }
    (tmp_path / f"{TRACE_ID}.jsonl").write_text(json.dumps(trace) + "\n")
    state_path = tmp_path / "state.json"
    state = StateManager(state_path=state_path)
    state.set_trace_status(TRACE_ID, TraceStatus.COMMITTED, session_id="sess-push-001")
    return state_path


def _fake_script_next_to_python(monkeypatch, tmp_path: Path) -> Path:
    """Install a dummy ``opentraces`` executable next to sys.executable.

    ``_run_cli_push`` resolves the CLI as
    ``Path(sys.executable).parent / "opentraces"``, so we point
    ``sys.executable`` at a tmp shim directory and drop an empty file
    there. The subprocess itself is still mocked; we just need
    ``script.exists()`` to answer truthfully without tampering with
    the real venv layout.
    """
    shim_dir = tmp_path / "venv_bin"
    shim_dir.mkdir()
    fake_python = shim_dir / "python"
    fake_python.write_text("#!/bin/sh\nexec false\n")
    fake_python.chmod(0o755)
    fake_script = shim_dir / "opentraces"
    fake_script.write_text("#!/bin/sh\nexec false\n")
    fake_script.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(fake_python))
    return fake_script


def test_api_push_returns_400_when_nothing_committed(tmp_path):
    # Seed only sample JSONL — no committed state. ``_is_sample_data``
    # returns False because the JSONL file on disk matches the parsed
    # trace list, but the state has no committed traces and no staged
    # fallback, so we expect the 400 path.
    trace = {
        "schema_version": "0.3.0",
        "trace_id": TRACE_ID,
        "session_id": "sess-push-001",
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": "push me"},
        "steps": [],
        "metrics": {},
    }
    (tmp_path / f"{TRACE_ID}.jsonl").write_text(json.dumps(trace) + "\n")
    state_path = tmp_path / "state.json"
    state = StateManager(state_path=state_path)
    state.set_trace_status(TRACE_ID, TraceStatus.PARSED, session_id="sess-push-001")

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/push", json={})
    assert response.status_code == 400
    assert response.get_json()["error"] == "No staged sessions to push"


def test_api_push_delegates_to_subprocess_and_reports_real_transitions(
    tmp_path, monkeypatch
):
    state_path = _seed_committed_trace(tmp_path)
    _fake_script_next_to_python(monkeypatch, tmp_path)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        # Pretend the CLI push moved the trace COMMITTED → UPLOADED by
        # rewriting the same state file the web app reads.
        StateManager(state_path=state_path).set_trace_status(
            TRACE_ID, TraceStatus.UPLOADED,
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="uploaded 1 trace", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/push", json={})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "pushed"
    assert payload["count"] == 1
    assert payload["trace_ids"] == [TRACE_ID]
    # The handler shelled out to ``opentraces push -y`` — no in-process
    # HFUploader path exists anymore.
    assert any(c[-2:] == ["push", "-y"] for c in calls), calls


def test_api_push_with_llm_review_runs_review_then_push(tmp_path, monkeypatch):
    state_path = _seed_committed_trace(tmp_path)
    _fake_script_next_to_python(monkeypatch, tmp_path)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if "llm-review" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        StateManager(state_path=state_path).set_trace_status(
            TRACE_ID, TraceStatus.UPLOADED,
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/push", json={"llm_review": True})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "pushed"
    assert payload["count"] == 1
    # Review ran first, then the gated push. Order matters.
    assert len(calls) == 2
    assert calls[0][-3:] == ["llm-review", "--scope", "staged"]
    assert calls[1][-3:] == ["push", "--llm-review", "-y"]


def test_api_push_subprocess_failure_does_not_mark_uploaded(tmp_path, monkeypatch):
    """The critical regression: a failing push must NOT silently flip local state.

    Under the old code, an ImportError or missing-token branch would
    mark the trace as UPLOADED while returning ✓ to the UI. After the
    fix, the trace must remain COMMITTED when the subprocess does not
    transition it, and the response must carry a failed status.
    """
    state_path = _seed_committed_trace(tmp_path)
    _fake_script_next_to_python(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd, 2, stdout="trufflehog not on PATH", stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/push", json={})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "failed"
    assert payload["count"] == 0
    assert payload["trace_ids"] == []
    assert payload["error"]
    # The UI contract: ``log`` is a string with the subprocess output
    # for the modal to display.
    assert "trufflehog not on PATH" in payload["log"]

    # Most importantly: the trace is still COMMITTED on disk.
    entry = StateManager(state_path=state_path).get_trace(TRACE_ID)
    assert entry is not None
    assert entry.status == TraceStatus.COMMITTED.value


def test_api_push_missing_opentraces_script_surfaces_error(tmp_path, monkeypatch):
    state_path = _seed_committed_trace(tmp_path)
    # Point sys.executable at a directory without an ``opentraces`` shim.
    empty_bin = tmp_path / "empty_bin"
    empty_bin.mkdir()
    fake_python = empty_bin / "python"
    fake_python.write_text("#!/bin/sh\nexec false\n")
    fake_python.chmod(0o755)
    monkeypatch.setattr(sys, "executable", str(fake_python))

    def should_not_run(*args, **kwargs):
        raise AssertionError("subprocess.run must not be called")
    monkeypatch.setattr(subprocess, "run", should_not_run)

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/push", json={})
    assert response.status_code == 500
    payload = response.get_json()
    assert payload["status"] == "failed"
    assert "could not find 'opentraces'" in payload["error"]

    # Trace status must not have been touched.
    entry = StateManager(state_path=state_path).get_trace(TRACE_ID)
    assert entry is not None
    assert entry.status == TraceStatus.COMMITTED.value


def test_api_push_llm_review_failure_aborts_without_running_push(tmp_path, monkeypatch):
    state_path = _seed_committed_trace(tmp_path)
    _fake_script_next_to_python(monkeypatch, tmp_path)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        # llm-review fails; push must not be reached.
        return subprocess.CompletedProcess(cmd, 3, stdout="anthropic key missing", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/push", json={"llm_review": True})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "failed"
    assert payload["stage"] == "llm-review"
    assert payload["count"] == 0
    # Only the review step ran.
    assert len(calls) == 1
    assert calls[0][-3:] == ["llm-review", "--scope", "staged"]

    # Still COMMITTED, not UPLOADED.
    entry = StateManager(state_path=state_path).get_trace(TRACE_ID)
    assert entry is not None
    assert entry.status == TraceStatus.COMMITTED.value


def test_api_push_no_longer_imports_hfuploader_inline(tmp_path):
    """Guard against reintroducing the in-process upload fail-open.

    If a future refactor re-imports ``HFUploader`` inside ``api_push``,
    the silent-fail pattern can creep back. We assert the module body
    no longer references it in the push handler.
    """
    import inspect
    from opentraces.clients.web import server

    source = inspect.getsource(server)
    # Only the CLI subprocess path should own the upload. The handler
    # itself must not import HFUploader / TraceRecord inline.
    push_block = source.split("def api_push(")[1].split("def ")[0]
    assert "HFUploader" not in push_block
    assert "from opentraces_schema import TraceRecord" not in push_block
    assert "upload module not available" not in source
