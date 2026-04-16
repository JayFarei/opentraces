from __future__ import annotations

import json

from opentraces.clients.web.server import create_app
from opentraces.core.state import StateManager, TraceStatus


def _seed_trace(tmp_path):
    trace = {
        "schema_version": "0.3.0",
        "trace_id": "trace-web-001",
        "session_id": "sess-web-001",
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": "web tree"},
        "steps": [
            {
                "step_index": 1,
                "role": "user",
                "content": "start",
                "timestamp": "2026-04-16T10:00:00Z",
            },
            {
                "step_index": 2,
                "role": "agent",
                "content": "branch",
                "timestamp": "2026-04-16T10:01:00Z",
                "subagent_trajectory_ref": "subtrace-1",
            },
        ],
        "metrics": {},
    }
    (tmp_path / "trace-web-001.jsonl").write_text(json.dumps(trace) + "\n")
    return trace


def test_web_tree_endpoint_serializes_trace_tree(tmp_path):
    _seed_trace(tmp_path)
    state_path = tmp_path / "state.json"
    state = StateManager(state_path=state_path)
    state.set_trace_status("trace-web-001", TraceStatus.PARSED)
    state.set_step_label("trace-web-001", "s2", "decision")

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.get("/api/traces/trace-web-001/tree")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tree"][0]["id"] == "s1"
    assert payload["tree"][0]["children"][0]["label"] == "decision"
    assert payload["tree"][0]["children"][0]["children"][0]["kind"] == "subagent_ref"


def test_web_resume_endpoint_returns_copyable_command(tmp_path, monkeypatch):
    _seed_trace(tmp_path)
    source_session = tmp_path / "source.jsonl"
    source_session.write_text(
        "\n".join([
            json.dumps({"type": "user", "uuid": "line-001", "message": {"role": "user", "content": "start"}}),
            json.dumps({"type": "assistant", "uuid": "line-002", "message": {"role": "assistant", "content": [{"type": "text", "text": "branch"}]}}),
        ]) + "\n"
    )
    state_path = tmp_path / "state.json"
    state = StateManager(state_path=state_path)
    state.set_trace_status("trace-web-001", TraceStatus.PARSED)
    state.upsert_session(
        session_id="sess-web-001",
        source_path=str(source_session),
        observed_size=source_session.stat().st_size,
        observed_mtime=source_session.stat().st_mtime,
    )
    state.set_step_anchors(
        "trace-web-001",
        {
            1: {"entry_uuid": "line-001", "line_no": 0, "session_file_relpath": None},
            2: {"entry_uuid": "line-002", "line_no": 1, "session_file_relpath": None},
        },
    )

    fake_home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(fake_home))

    app = create_app(staging_dir=str(tmp_path), state_path=str(state_path))
    client = app.test_client()

    response = client.post("/api/traces/trace-web-001/resume", json={"at_step": "s2"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["argv"][0] == "claude"
    assert payload["argv"][1] == "--resume"
    assert payload["truncated_at_line"] == 2
