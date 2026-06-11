#!/usr/bin/env python3
"""Claude Code PreToolUse hook for opentraces.

Captures the pre-tool boundary state needed by Trace Trails. The hook appends
one local ``opentraces_hook`` line to the transcript and never blocks Claude
Code on failure.

All ``opentraces`` imports are lazy and individually fail-open (same
contract as ``on_stop.py``): a broken or deleted pinned venv must degrade
to a best-effort transcript append — never a non-zero exit with a
traceback into the agent session.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def main() -> None:
    try:
        from opentraces.capture.claude_code.hooks._trails import arm_hook_watchdog

        arm_hook_watchdog()
    except Exception:
        pass

    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        from opentraces.capture.claude_code.hooks._trails import auto_enroll_from_cwd

        auto_enroll_from_cwd(payload.get("cwd"))
    except Exception:
        pass

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        sys.exit(0)

    timestamp = datetime.now(timezone.utc).isoformat()
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    observer = None
    try:
        from opentraces.capture.claude_code.hooks._trails import (
            observe_tool_boundary_for_hook,
        )

        observer = observe_tool_boundary_for_hook(
            payload.get("cwd"),
            tool_name,
            transcript_path,
            tool_input,
        )
    except Exception:
        observer = None
    trail: dict = {}
    try:
        from opentraces.capture.claude_code.hooks._trails import trail_state

        trail = trail_state(payload.get("cwd"), tool_name, tool_input)
    except Exception:
        trail = {}
    data = {
        "session_id": payload.get("session_id"),
        "tool": tool_name,
        "tool_use_id": payload.get("tool_use_id"),
        "tool_input": tool_input,
        "trail": trail,
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
