"""Regression tests for the web ``/api/push`` endpoint.

The endpoint previously owned trace publication and later delegated to the
old root ``opentraces push`` subprocess. In the v0.4 flow that root command is
removed; publication happens through reviewed datasets. These tests pin the
fail-closed contract so the web surface cannot silently revive legacy trace
push behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

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


def test_api_push_returns_400_when_nothing_committed(tmp_path):
    # Seed only a PARSED JSONL — no committed state. State has no
    # committed traces and no staged fallback, so we expect the 400 path.
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


def test_api_push_is_disabled_for_committed_traces(tmp_path):
    state_path = _seed_committed_trace(tmp_path)
    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/push", json={})
    assert response.status_code == 410
    payload = response.get_json()
    assert payload["status"] == "disabled"
    assert payload["stage"] == "dataset"
    assert payload["count"] == 0
    assert payload["trace_ids"] == []
    assert payload["next_command"] == "opentraces dataset publish <name>"
    assert "Legacy trace push is disabled" in payload["error"]

    entry = StateManager(state_path=state_path).get_trace(TRACE_ID)
    assert entry is not None
    assert entry.status == TraceStatus.COMMITTED.value


def test_api_push_no_longer_imports_or_shells_out():
    """Guard against reintroducing legacy trace upload paths.

    If a future refactor re-imports ``HFUploader`` or shells out to the
    removed root ``opentraces push`` command inside ``api_push``, the
    silent-fail pattern can creep back.
    """
    import inspect
    from opentraces.clients.web import server

    source = inspect.getsource(server)
    push_block = source.split("def api_push(")[1].split("def ")[0]
    assert "HFUploader" not in push_block
    assert "from opentraces_schema import TraceRecord" not in push_block
    assert "subprocess" not in source
    assert "opentraces push" not in push_block
    assert "upload module not available" not in source
