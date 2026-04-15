"""Doctor post-commit-hook panel (plan 047 U4)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from opentraces.core.doctor import _post_commit_hook_status


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=tmp_path, check=True)
    return tmp_path


def test_missing_when_no_hook_installed(tmp_path):
    _init_repo(tmp_path)
    status = _post_commit_hook_status(tmp_path)
    assert status["state"] == "missing"
    assert status["installed"] is False
    assert status["last_run"] is None


def test_installed_but_not_chained(tmp_path):
    _init_repo(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "opentraces-post-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    status = _post_commit_hook_status(tmp_path)
    assert status["state"] == "installed_not_chained"
    assert status["installed"] is True
    assert status["chained_in_post_commit"] is False


def test_installed_and_chained_with_log(tmp_path):
    _init_repo(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "opentraces-post-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    chained = tmp_path / ".git" / "hooks" / "post-commit"
    chained.write_text(
        '#!/bin/sh\n"$(dirname "$0")"/opentraces-post-commit "$@"\n',
    )
    log = tmp_path / ".git" / "opentraces-hook.log"
    log.write_text(
        json.dumps({
            "ts": "2026-04-15T10:00:00+00:00",
            "sha": "abc123",
            "candidates": 2,
            "verdicts": [{"trace_id": "t1", "tier": "tool_emitted"}],
            "notes_written": True,
            "reason": None,
            "error": None,
        }) + "\n",
    )
    status = _post_commit_hook_status(tmp_path)
    assert status["state"] == "ok"
    assert status["installed"] is True
    assert status["chained_in_post_commit"] is True
    assert status["recent_runs"] == 1
    assert status["last_run"]["candidates"] == 2


def test_installed_never_ran(tmp_path):
    _init_repo(tmp_path)
    hook = tmp_path / ".git" / "hooks" / "opentraces-post-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    hook.chmod(0o755)
    chained = tmp_path / ".git" / "hooks" / "post-commit"
    chained.write_text(
        '#!/bin/sh\n"$(dirname "$0")"/opentraces-post-commit "$@"\n',
    )
    status = _post_commit_hook_status(tmp_path)
    assert status["state"] == "installed_never_ran"
    assert status["last_run"] is None
