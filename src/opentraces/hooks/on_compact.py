#!/usr/bin/env python3
"""Claude Code PostCompact hook for opentraces.

Appends a single opentraces_hook line recording explicit compaction events.
The compact_boundary JSONL entry captures the pre-compaction state; this hook
adds the post-compaction state (messages_kept, messages_removed) which is
otherwise unavailable in the native JSONL.

Install via: opentraces hooks install
"""
import json
import sys
from datetime import datetime, timezone


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    transcript_path = payload.get("transcript_path")
    if not transcript_path:
        sys.exit(0)

    line = json.dumps({
        "type": "opentraces_hook",
        "event": "PostCompact",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": {
            "session_id": payload.get("session_id"),
            "messages_removed": payload.get("messages_removed"),
            "messages_kept": payload.get("messages_kept"),
            "summary": payload.get("summary"),
        },
    })

    try:
        with open(transcript_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Never break Claude Code on our account


if __name__ == "__main__":
    main()
