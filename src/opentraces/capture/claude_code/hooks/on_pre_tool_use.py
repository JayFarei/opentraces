#!/usr/bin/env python3
"""Claude Code PreToolUse hook for opentraces.

Captures the pre-tool boundary state needed by Trace Trails. The hook appends
one local ``opentraces_hook`` line to the transcript and never blocks Claude
Code on failure.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from opentraces.capture.claude_code.hooks._trails import (
    observe_tool_boundary_for_hook,
    trail_state,
)


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
    observer = observe_tool_boundary_for_hook(
        payload.get("cwd"),
        tool_name,
        transcript_path,
    )
    data = {
        "session_id": payload.get("session_id"),
        "tool": tool_name,
        "tool_use_id": payload.get("tool_use_id"),
        "tool_input": payload.get("tool_input") or {},
        "trail": trail_state(payload.get("cwd")),
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
