#!/usr/bin/env python3
"""Claude Code Stop hook for opentraces.

Appends a single opentraces_hook line to the session transcript capturing
git state at the exact moment the session ends. This data is not otherwise
present in the JSONL and enriches commit/outcome signals in the parser.

Install via: opentraces hooks install
"""
import json
import subprocess
import sys
from datetime import datetime, timezone


def _git_info(cwd: str) -> dict:
    """Return git state dict, empty on any failure."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=cwd, text=True,
            stderr=subprocess.DEVNULL,
        )
        changed_files = [l[:2].strip() for l in status_out.splitlines() if l.strip()]
        return {
            "sha": sha,
            "dirty": bool(changed_files),
            "files_changed": len(changed_files),
        }
    except Exception:
        return {}


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        sys.exit(0)

    cwd = payload.get("cwd") or ""
    line = json.dumps({
        "type": "opentraces_hook",
        "event": "Stop",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "session_id": payload.get("session_id"),
            "agent_type": payload.get("agent_type"),
            "permission_mode": payload.get("permission_mode"),
            "stop_hook_active": payload.get("stop_hook_active"),
            "git": _git_info(cwd),
        },
    })

    try:
        with open(transcript_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Never break Claude Code on our account


if __name__ == "__main__":
    main()
