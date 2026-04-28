#!/usr/bin/env python3
"""Claude Code PreToolUse hook for opentraces.

Captures the pre-tool boundary state needed by Trace Trails. The hook appends
one local ``opentraces_hook`` line to the transcript and never blocks Claude
Code on failure.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _git_head(cwd: Path) -> dict[str, str] | None:
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


def _trail_state(cwd: str | None) -> dict:
    if not cwd:
        return {}
    try:
        from opentraces.core.trails import write_worktree_tree

        root = Path(cwd).resolve()
        return {
            "worktree_root": str(root),
            "tree_id": write_worktree_tree(root),
            "git_head": _git_head(root),
        }
    except Exception:
        return {}


def _observe_tool_boundary(
    cwd: str | None,
    tool_name: str | None,
    transcript_path: str | None,
) -> dict | None:
    try:
        from opentraces.capture.fs_watcher.runtime import observe_tool_boundary

        exclude_paths = [transcript_path] if transcript_path else None
        result = observe_tool_boundary(cwd, tool_name, exclude_paths=exclude_paths)
    except Exception:
        return None
    if result is None:
        return None
    return {
        "baseline_initialized": result.baseline_initialized,
        "paths_seen": result.paths_seen,
        "observations": len(result.observations),
        "skipped_paths": result.skipped_paths,
    }


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        sys.exit(0)

    timestamp = datetime.now(timezone.utc).isoformat()
    tool_name = payload.get("tool_name")
    observer = _observe_tool_boundary(payload.get("cwd"), tool_name, transcript_path)
    data = {
        "session_id": payload.get("session_id"),
        "tool": tool_name,
        "tool_use_id": payload.get("tool_use_id"),
        "tool_input": payload.get("tool_input") or {},
        "trail": _trail_state(payload.get("cwd")),
    }
    if observer is not None:
        data["trail_observer"] = observer
    line = json.dumps(
        {
            "type": "opentraces_hook",
            "event": "PreToolUse",
            "timestamp": timestamp,
            "data": data,
        }
    )

    try:
        with open(transcript_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
