"""Tests for plan 041 R8: on_stop captures session-end `git diff HEAD` path list.

Integration-style: materialize a real tmp_path git repo, mutate it via
subprocess (simulating a Bash-side edit the agent couldn't Edit-tool
attribute), then invoke the hook and assert `changed_paths` lands in
the emitted transcript line.
"""

from __future__ import annotations

import json
import subprocess
from io import StringIO
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.check_call(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _invoke(module_main, payload: dict, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", StringIO(json.dumps(payload)))
    module_main()


def test_on_stop_captures_changed_paths(tmp_path, monkeypatch):
    from opentraces.installers.claude_code_hooks.on_stop import main

    # bootstrap a git repo with one committed file
    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "app.py").write_text("hello\n")
    (tmp_path / "other.py").write_text("world\n")
    _run(["git", "add", "-A"], tmp_path)
    _run(["git", "commit", "-qm", "init"], tmp_path)

    # mutate one file (simulates a Bash-side codemod/sed the agent ran)
    (tmp_path / "app.py").write_text("HELLO\n")

    transcript = tmp_path / "session.jsonl"
    transcript.write_text("")
    payload = {
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "session_id": "sess",
    }
    _invoke(main, payload, monkeypatch)

    lines = [json.loads(l) for l in transcript.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    git = lines[0]["data"]["git"]
    assert git.get("dirty") is True
    assert "changed_paths" in git
    assert "app.py" in git["changed_paths"]
    assert "other.py" not in git["changed_paths"]


def test_changed_paths_empty_on_clean_tree(tmp_path, monkeypatch):
    from opentraces.installers.claude_code_hooks.on_stop import main

    _run(["git", "init", "-q", "-b", "main"], tmp_path)
    _run(["git", "config", "user.email", "t@t.t"], tmp_path)
    _run(["git", "config", "user.name", "t"], tmp_path)
    (tmp_path / "f.py").write_text("x\n")
    _run(["git", "add", "-A"], tmp_path)
    _run(["git", "commit", "-qm", "init"], tmp_path)

    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")
    payload = {
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        "session_id": "sess",
    }
    _invoke(main, payload, monkeypatch)

    git = json.loads(transcript.read_text().splitlines()[0])["data"]["git"]
    # changed_paths is strictly `git diff HEAD` (tracked). The transcript
    # itself is untracked and shows up in `dirty` but not here — that's
    # the intent of R8: don't surface the session's own artifact.
    assert git["changed_paths"] == []
