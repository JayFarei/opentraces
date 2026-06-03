"""Pi sidecars feed existing Trace Trails emission."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.capture.pi.parse import PiSessionParser
from opentraces.core.trails import emit_step_window_events_from_record, read_events


def _init_repo(path: Path) -> None:
    subprocess.check_call(["git", "init", "-q"], cwd=path)
    subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=path)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=path)
    (path / "app.py").write_text("print('old')\n")
    subprocess.check_call(["git", "add", "app.py"], cwd=path)
    subprocess.check_call(["git", "commit", "-q", "-m", "base"], cwd=path)
    subprocess.check_call(["git", "update-ref", "refs/heads/main", "HEAD"], cwd=path)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=path)


def _head(repo: Path) -> dict[str, str]:
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    return {"algo": "sha1", "hex": sha}


def _tree(repo: Path) -> dict[str, str]:
    sha = subprocess.check_output(["git", "write-tree"], cwd=repo, text=True).strip()
    return {"algo": "sha1", "hex": sha}


def test_pi_sidecar_metadata_emits_trace_trail_events(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pre_tree = _tree(repo)
    head = _head(repo)
    (repo / "app.py").write_text("print('new')\n")
    subprocess.check_call(["git", "add", "app.py"], cwd=repo)
    post_tree = _tree(repo)
    subprocess.check_call(["git", "reset", "-q"], cwd=repo)

    session_id = "pi-trail-session"
    transcript = tmp_path / "pi-session.jsonl"
    rows = [
        {"type": "session", "version": 3, "id": session_id, "timestamp": "2026-06-02T09:00:00Z", "cwd": str(repo), "pi_version": "1.0.0"},
        {"type": "message", "id": "u1", "parentId": None, "timestamp": "2026-06-02T09:00:01Z", "message": {"role": "user", "content": "Edit app.py"}},
        {"type": "message", "id": "a1", "parentId": "u1", "timestamp": "2026-06-02T09:00:02Z", "message": {"role": "assistant", "provider": "anthropic", "model": "claude-sonnet-4", "usage": {"input": 1, "output": 1}, "stopReason": "toolUse", "content": [{"type": "toolCall", "id": "call-edit", "name": "Edit", "arguments": {"file_path": str(repo / "app.py"), "old_string": "print('old')", "new_string": "print('new')"}}]}},
        {"type": "message", "id": "t1", "parentId": "a1", "timestamp": "2026-06-02T09:00:03Z", "message": {"role": "toolResult", "toolCallId": "call-edit", "toolName": "Edit", "content": [{"type": "text", "text": "ok"}], "isError": False}},
    ]
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    sidecar = repo / ".opentraces" / "pi" / "events" / f"{session_id}.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text("\n".join(json.dumps(row) for row in [
        {
            "schema_version": "opentraces.pi.sidecar.v1",
            "type": "opentraces_pi_event",
            "event": "tool_pre",
            "event_id": "pre",
            "sequence": 1,
            "session_id": session_id,
            "session_file": str(transcript),
            "cwd": str(repo),
            "tool_call_id": "call-edit",
            "timestamp": "2026-06-02T09:00:02Z",
            "final": False,
            "data": {"tool_name": "Edit", "input": {"file_path": str(repo / "app.py")}, "trail": {"worktree_root": str(repo), "tree_id": pre_tree, "git_head": head}},
        },
        {
            "schema_version": "opentraces.pi.sidecar.v1",
            "type": "opentraces_pi_event",
            "event": "tool_post",
            "event_id": "post",
            "sequence": 2,
            "session_id": session_id,
            "session_file": str(transcript),
            "cwd": str(repo),
            "tool_call_id": "call-edit",
            "timestamp": "2026-06-02T09:00:03Z",
            "final": False,
            "data": {"tool_name": "Edit", "input": {"file_path": str(repo / "app.py")}, "file_path": str(repo / "app.py"), "capture_status": "captured", "trail": {"worktree_root": str(repo), "tree_id": post_tree, "git_head": head}},
        },
        {
            "schema_version": "opentraces.pi.sidecar.v1",
            "type": "opentraces_pi_event",
            "event": "session_shutdown",
            "event_id": "stop",
            "sequence": 3,
            "session_id": session_id,
            "session_file": str(transcript),
            "cwd": str(repo),
            "timestamp": "2026-06-02T09:00:04Z",
            "final": True,
            "data": {"git": {"sha": head["hex"], "dirty": True, "changed_paths": ["app.py"]}, "trail": {"worktree_root": str(repo), "tree_id": post_tree, "git_head": head}},
        },
    ]) + "\n")

    record = PiSessionParser().parse_session(transcript)
    assert record is not None
    record.trace_id = "trace-pi-trail"
    record.generation_index = 1

    result = emit_step_window_events_from_record(repo, record)
    event_types = [event.event_type for event in read_events(repo, verify=False)]

    assert result.emitted_events
    assert "trace_step_window_opened" in event_types
    assert "trace_step_window_closed" in event_types
    assert "trace_patch_created" in event_types
    assert "trace_session_closed" in event_types
