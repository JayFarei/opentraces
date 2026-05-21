"""Codex hook sidecars feed existing Trace Trails emission."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.capture.codex_cli.parse import CodexCliParser
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
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()
    return {"algo": "sha1", "hex": sha}


def _tree(repo: Path) -> dict[str, str]:
    sha = subprocess.check_output(
        ["git", "write-tree"],
        cwd=repo,
        text=True,
    ).strip()
    return {"algo": "sha1", "hex": sha}


def test_codex_sidecar_metadata_emits_trace_trail_events(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    pre_tree = _tree(repo)
    head = _head(repo)
    (repo / "app.py").write_text("print('new')\n")
    subprocess.check_call(["git", "add", "app.py"], cwd=repo)
    post_tree = _tree(repo)
    subprocess.check_call(["git", "reset", "-q"], cwd=repo)

    transcript = tmp_path / "rollout-2026-05-21T09-00-00-trail.jsonl"
    session_id = "codex-trail-session"
    transcript.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "timestamp": "2026-05-21T09:00:00Z",
                    "type": "session_meta",
                    "payload": {
                        "id": session_id,
                        "timestamp": "2026-05-21T09:00:00Z",
                        "cwd": str(repo),
                        "cli_version": "0.132.0",
                        "source": "tui",
                        "model_provider": "openai",
                    },
                },
                {
                    "timestamp": "2026-05-21T09:00:01Z",
                    "type": "turn_context",
                    "payload": {"turn_id": "turn-1", "cwd": str(repo), "model": "gpt-5.5"},
                },
                {
                    "timestamp": "2026-05-21T09:00:02Z",
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Edit app.py"}],
                    },
                },
                {
                    "timestamp": "2026-05-21T09:00:03Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "Edit",
                        "arguments": json.dumps({
                            "file_path": str(repo / "app.py"),
                            "old_string": "print('old')",
                            "new_string": "print('new')",
                        }),
                        "call_id": "call-edit",
                    },
                },
                {
                    "timestamp": "2026-05-21T09:00:04Z",
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "call-edit",
                        "output": "ok",
                    },
                },
            ]
        )
        + "\n"
    )

    sidecar = repo / ".opentraces" / "codex-cli" / "hooks" / f"{session_id}.jsonl"
    sidecar.parent.mkdir(parents=True)
    sidecar.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "type": "opentraces_hook",
                    "event": "PreToolUse",
                    "timestamp": "2026-05-21T09:00:03Z",
                    "data": {
                        "session_id": session_id,
                        "tool_use_id": "call-edit",
                        "tool": "Edit",
                        "tool_input": {"file_path": str(repo / "app.py")},
                        "trail": {
                            "worktree_root": str(repo),
                            "tree_id": pre_tree,
                            "git_head": head,
                        },
                    },
                },
                {
                    "type": "opentraces_hook",
                    "event": "PostToolUse",
                    "timestamp": "2026-05-21T09:00:04Z",
                    "data": {
                        "session_id": session_id,
                        "tool_use_id": "call-edit",
                        "tool": "Edit",
                        "tool_input": {"file_path": str(repo / "app.py")},
                        "tool_response": {"status": "ok"},
                        "capture_status": "captured",
                        "limitations": [],
                        "trail": {
                            "worktree_root": str(repo),
                            "tree_id": post_tree,
                            "git_head": head,
                        },
                    },
                },
                {
                    "type": "opentraces_hook",
                    "event": "Stop",
                    "timestamp": "2026-05-21T09:00:05Z",
                    "data": {
                        "session_id": session_id,
                        "permission_mode": "never",
                        "stop_hook_active": False,
                        "git": {"sha": head["hex"], "dirty": True, "changed_paths": ["app.py"]},
                        "trail": {
                            "worktree_root": str(repo),
                            "tree_id": post_tree,
                            "git_head": head,
                        },
                    },
                },
            ]
        )
        + "\n"
    )

    record = CodexCliParser().parse_session(transcript)
    assert record is not None
    record.trace_id = "trace-codex-trail"
    record.generation_index = 1

    result = emit_step_window_events_from_record(repo, record)
    events = read_events(repo, verify=False)
    event_types = [event.event_type for event in events]

    assert result.emitted_events
    assert "trace_step_window_opened" in event_types
    assert "trace_step_window_closed" in event_types
    assert "trace_patch_created" in event_types
    assert "trace_session_closed" in event_types
