"""Tests for the Claude Code PreToolUse Trace Trails boundary hook."""
from __future__ import annotations

import json
import subprocess
from io import StringIO
from pathlib import Path


def _invoke(module_main, payload: dict, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    module_main()


def _read_events(transcript: Path) -> list[dict]:
    return [json.loads(line) for line in transcript.read_text().splitlines() if line.strip()]


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("print('before')\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_pre_tool_use_appends_boundary_state(tmp_path: Path, monkeypatch) -> None:
    from opentraces.capture.claude_code.hooks.on_pre_tool_use import main
    from opentraces.core.trails import write_worktree_tree

    _init_repo(tmp_path)
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")
    expected_tree = write_worktree_tree(tmp_path)
    payload = {
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "session_id": "sess",
        "tool_name": "Edit",
        "tool_use_id": "toolu_pre",
        "tool_input": {"file_path": str(tmp_path / "app.py")},
    }

    _invoke(main, payload, monkeypatch)

    events = _read_events(transcript)
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "opentraces_hook"
    assert ev["event"] == "PreToolUse"
    assert "timestamp" in ev
    assert ev["data"]["session_id"] == "sess"
    assert ev["data"]["tool"] == "Edit"
    assert ev["data"]["tool_use_id"] == "toolu_pre"
    assert ev["data"]["tool_input"]["file_path"] == str(tmp_path / "app.py")
    assert ev["data"]["trail"]["worktree_root"] == str(tmp_path)
    assert ev["data"]["trail"]["tree_id"] == expected_tree
    assert ev["data"]["trail"]["git_head"]["algo"] == "sha1"


def test_pre_tool_use_missing_transcript_exits_clean(monkeypatch) -> None:
    import pytest
    from opentraces.capture.claude_code.hooks.on_pre_tool_use import main

    monkeypatch.setattr("sys.stdin", StringIO(json.dumps({})))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
