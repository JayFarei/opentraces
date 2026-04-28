"""Shared Trace Trails helpers for Claude Code hook scripts."""
from __future__ import annotations

import subprocess
from pathlib import Path


def git_head(cwd: Path) -> dict[str, str] | None:
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except Exception:
        return None
    return {"algo": "sha1", "hex": sha}


def trail_state(cwd: str | None) -> dict:
    if not cwd:
        return {}
    try:
        from opentraces.core.trails import write_worktree_tree

        root = Path(cwd).resolve()
        return {
            "worktree_root": str(root),
            "tree_id": write_worktree_tree(root),
            "git_head": git_head(root),
        }
    except Exception:
        return {}


def observe_tool_boundary_for_hook(
    cwd: str | None,
    tool_name: str | None,
    transcript_path: str | None,
) -> dict | None:
    try:
        from opentraces.capture.tool_boundary import observe_tool_boundary

        exclude_paths = [transcript_path] if transcript_path else None
        summary = observe_tool_boundary(
            cwd,
            tool_name=tool_name,
            exclude_paths=exclude_paths,
        )
    except Exception:
        return None
    return summary.to_hook_payload() if summary is not None else None
