"""Codex CLI hook sidecar tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.capture.codex_cli.parse import CodexCliParser


def _init_repo(path: Path) -> None:
    subprocess.check_call(["git", "init", "-q"], cwd=path)
    subprocess.check_call(["git", "config", "user.email", "test@example.invalid"], cwd=path)
    subprocess.check_call(["git", "config", "user.name", "Test User"], cwd=path)
    (path / "README.md").write_text("base\n")
    subprocess.check_call(["git", "add", "README.md"], cwd=path)
    subprocess.check_call(["git", "commit", "-q", "-m", "base"], cwd=path)
    subprocess.check_call(["git", "update-ref", "refs/heads/main", "HEAD"], cwd=path)
    subprocess.check_call(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=path)


def _write_session(path: Path, project: Path, session_id: str = "codex-hook-session") -> None:
    rows = [
        {
            "timestamp": "2026-05-21T09:00:00Z",
            "type": "session_meta",
            "payload": {
                "id": session_id,
                "timestamp": "2026-05-21T09:00:00Z",
                "cwd": str(project),
                "originator": "codex-tui",
                "cli_version": "0.132.0",
                "source": "tui",
                "model_provider": "openai",
            },
        },
        {
            "timestamp": "2026-05-21T09:00:01Z",
            "type": "turn_context",
            "payload": {
                "turn_id": "turn-1",
                "cwd": str(project),
                "model": "gpt-5.5",
                "approval_policy": "never",
            },
        },
        {
            "timestamp": "2026-05-21T09:00:02Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Modify README"}],
            },
        },
        {
            "timestamp": "2026-05-21T09:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "printf changed >> README.md"}),
                "call_id": "call-1",
            },
        },
        {
            "timestamp": "2026-05-21T09:00:04Z",
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "ok",
            },
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def _payload(project: Path, transcript: Path) -> dict:
    return {
        "session_id": "codex-hook-session",
        "turn_id": "turn-1",
        "transcript_path": str(transcript),
        "cwd": str(project),
        "hook_event_name": "PreToolUse",
        "model": "gpt-5.5",
        "permission_mode": "never",
        "tool_name": "exec_command",
        "tool_input": {"cmd": "printf changed >> README.md"},
        "tool_use_id": "call-1",
    }


def test_codex_tool_hooks_write_sidecar_and_parser_merges_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from opentraces.capture.codex_cli.hooks import on_post_tool_use, on_pre_tool_use

    project = tmp_path / "project"
    project.mkdir()
    _init_repo(project)
    transcript = tmp_path / "rollout-2026-05-21T09-00-00-hook.jsonl"
    _write_session(transcript, project)
    payload = _payload(project, transcript)

    monkeypatch.setattr("sys.stdin", _Stdin(payload))
    on_pre_tool_use.main()
    (project / "README.md").write_text("base\nchanged\n")
    payload["hook_event_name"] = "PostToolUse"
    payload["tool_response"] = {"stdout": "ok", "exit_code": 0}
    monkeypatch.setattr("sys.stdin", _Stdin(payload))
    on_post_tool_use.main()

    sidecar = project / ".opentraces" / "codex-cli" / "hooks" / "codex-hook-session.jsonl"
    rows = [json.loads(line) for line in sidecar.read_text().splitlines()]
    assert [row["event"] for row in rows] == ["PreToolUse", "PostToolUse"]

    record = CodexCliParser().parse_session(transcript)

    assert record is not None
    assert record.metadata["hook_pre_tool_use"]["call-1"]["tool"] == "exec_command"
    assert record.metadata["hook_post_tool_use"]["call-1"]["capture_status"] == "hook_only"
    assert record.metadata["hook_post_tool_use"]["call-1"]["trail"]["tree_id"]["algo"] == "sha1"


def test_codex_stop_hook_records_git_state_and_spawns_agent_ingest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from opentraces.capture.codex_cli.hooks import on_stop

    project = tmp_path / "project"
    project.mkdir()
    _init_repo(project)
    transcript = tmp_path / "rollout-2026-05-21T09-00-00-stop.jsonl"
    _write_session(transcript, project)
    (project / "README.md").write_text("base\nchanged\n")
    spawned: list[dict] = []

    monkeypatch.setattr(
        "opentraces.capture.codex_cli.hooks.on_stop.spawn_ingest",
        lambda payload: spawned.append(dict(payload)),
    )
    payload = _payload(project, transcript)
    payload["hook_event_name"] = "Stop"
    payload["stop_hook_active"] = False
    monkeypatch.setattr("sys.stdin", _Stdin(payload))

    on_stop.main()

    record = CodexCliParser().parse_session(transcript)
    assert record is not None
    stop = record.metadata["hook_stop"][0]
    assert stop["git"]["dirty"] is True
    assert stop["trail"]["tree_id"]["algo"] == "sha1"
    assert spawned[0]["session_id"] == "codex-hook-session"


class _Stdin:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self, *_args, **_kwargs) -> str:
        return json.dumps(self.payload)
