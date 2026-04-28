"""Capture-level tool-boundary observation interface."""

from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.capture.tool_boundary import (
    observe_tool_boundary,
    tool_name_may_mutate,
)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def test_tool_name_may_mutate_knows_default_agent_tools() -> None:
    assert tool_name_may_mutate("Bash") is True
    assert tool_name_may_mutate("Write") is True
    assert tool_name_may_mutate("Read") is False
    assert tool_name_may_mutate(None) is False


def test_observe_tool_boundary_skips_non_mutating_tools(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    result = observe_tool_boundary(tmp_path, tool_name="Read")

    assert result is None
    assert not (tmp_path / ".git" / "opentraces-fs-watcher-state.json").exists()


def test_observe_tool_boundary_supports_explicit_adapter_policy(tmp_path: Path) -> None:
    _init_repo(tmp_path)

    result = observe_tool_boundary(
        tmp_path,
        tool_name="CustomGenerate",
        may_mutate=True,
    )

    assert result is not None
    assert result.baseline_initialized is True
    assert result.to_hook_payload()["paths_seen"] == 1


def test_observe_tool_boundary_skips_non_git_dirs(tmp_path: Path) -> None:
    result = observe_tool_boundary(tmp_path, tool_name="Bash")

    assert result is None
    assert not (tmp_path / ".git").exists()
