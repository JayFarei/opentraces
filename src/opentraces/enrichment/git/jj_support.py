"""Jujutsu (`jj`) support.

When a repo is jj-managed, change_id is a stable identifier that
survives rebase + amend. Using it as GitLink.revision with
vcs_type="jj" lets attribution data follow the agent's work through
history-rewriting operations that would orphan a git-sha-based link.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=5,
        )
        return proc.returncode, proc.stdout
    except Exception:
        return (-1, "")


def is_jj_repo(cwd: Path) -> bool:
    """True iff `cwd` is inside a jj-managed working copy."""
    code, _ = _run(["jj", "root"], cwd)
    return code == 0


def current_change_id(cwd: Path) -> str | None:
    """Return the change_id for the current working-copy commit,
    or None if jj isn't available or not a jj repo."""
    code, out = _run(
        ["jj", "log", "-r", "@", "--no-graph", "-T", "change_id", "--limit", "1"],
        cwd,
    )
    if code != 0:
        return None
    change_id = out.strip()
    return change_id or None


def detect_vcs(cwd: Path) -> str:
    """Return 'jj' if a jj working copy exists at cwd, else 'git'."""
    return "jj" if is_jj_repo(cwd) else "git"
