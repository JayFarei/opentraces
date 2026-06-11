#!/usr/bin/env python3
"""Claude Code PostToolUse hook for opentraces.

Fires after the Edit and Write tools run, reads the target file from
disk, and appends an opentraces_hook/PostToolUse line to the session
transcript capturing the exact post-edit line range plus a murmur3
content hash.

This is the highest-priority layer of build_attribution's three-layer
pipeline (hook > diff > str.find). Because it reads from disk at the
moment the tool call completes, it is immune to downstream formatter
rewrites — the range it captures is the agent's truth, not the
committed-bytes truth.

Install alongside on_stop.py via the opentraces installer.

Claude Code payload shape (PostToolUse):
    {
      "session_id": "...",
      "transcript_path": "/.../session.jsonl",
      "cwd": "...",
      "tool_name": "Edit" | "Write" | "Bash" | ...,
      "tool_use_id": "toolu_01xyz",
      "tool_input": {...},   # depends on tool
      "tool_response": {...} # depends on tool
    }

Non-file-editing tools are silently ignored. Any error path exits 0
so the hook never blocks Claude Code.

All ``opentraces``/``mmh3`` imports are lazy and individually fail-open
(same contract as ``on_stop.py``): a broken or deleted pinned venv must
degrade to a best-effort transcript append — never a non-zero exit with
a traceback into the agent session.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def _hash(text: str) -> str:
    import mmh3

    return f"murmur3:{mmh3.hash128(text.encode('utf-8'), signed=False):032x}"


def _find_edit_range(
    file_content: str, old_string: str, new_string: str
) -> tuple[int, int, str]:
    """Locate new_string inside file_content and return (start, end, confidence).

    - If new_string appears exactly once: high confidence.
    - If new_string appears multiple times: pick the occurrence closest
      to old_string (for disambiguation), medium confidence.
    - If new_string cannot be located: (1, 1, "low").
    """
    if not new_string:
        return (1, 1, "low")

    matches: list[int] = []
    search_from = 0
    while True:
        idx = file_content.find(new_string, search_from)
        if idx == -1:
            break
        matches.append(idx)
        search_from = idx + 1

    if not matches:
        return (1, 1, "low")

    if len(matches) == 1:
        idx = matches[0]
        confidence = "high"
    else:
        # Disambiguate by proximity to old_string. When old_string is
        # empty or absent from the file, pick the first match.
        old_idx = file_content.find(old_string) if old_string else -1
        if old_idx == -1:
            idx = matches[0]
        else:
            idx = min(matches, key=lambda m: abs(m - old_idx))
        confidence = "medium"

    start_line = file_content[:idx].count("\n") + 1
    new_lines = new_string.count("\n") + (0 if new_string.endswith("\n") else 1)
    new_lines = max(new_lines, 1)
    end_line = start_line + new_lines - 1
    return (start_line, end_line, confidence)


def _handle_edit(tool_input: dict) -> dict | None:
    file_path = tool_input.get("file_path") or ""
    old_string = tool_input.get("old_string") or ""
    new_string = tool_input.get("new_string") or ""
    if not file_path or not new_string:
        return None

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return None

    start_line, end_line, confidence = _find_edit_range(content, old_string, new_string)
    return {
        "tool": "Edit",
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "content_hash": _hash(new_string),
        "confidence": confidence,
    }


def _handle_write(tool_input: dict) -> dict | None:
    file_path = tool_input.get("file_path") or ""
    content = tool_input.get("content") or ""
    if not file_path:
        return None

    line_count = content.count("\n") + (0 if content.endswith("\n") else 1)
    line_count = max(line_count, 1)
    return {
        "tool": "Write",
        "file_path": file_path,
        "start_line": 1,
        "end_line": line_count,
        "content_hash": _hash(content),
        "confidence": "high",
    }


def _dual_emit_agent_trace(cwd: str | None, data: dict, session_id: str | None) -> None:
    """Plan 041 R37: append an Agent Trace-compatible attribution line
    to `.agent-trace/traces.jsonl` in the repo root so any opentraces-
    instrumented repo is natively Agent Trace-readable at rest.

    Best-effort: silently swallows every error path. Skipped when cwd
    isn't set.
    """
    if not cwd:
        return
    try:
        from pathlib import Path
        root = Path(cwd)
        target_dir = root / ".agent-trace"
        target_dir.mkdir(parents=True, exist_ok=True)
        out = {
            "schema": "agent-trace/v0.1.0",
            "session_id": session_id,
            "file_path": data.get("file_path"),
            "start_line": data.get("start_line"),
            "end_line": data.get("end_line"),
            "content_hash": data.get("content_hash"),
            "confidence": data.get("confidence"),
            "tool": data.get("tool"),
            "tool_use_id": data.get("tool_use_id"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(target_dir / "traces.jsonl", "a") as f:
            f.write(json.dumps(out) + "\n")
    except Exception:
        pass


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

    tool_name = (payload.get("tool_name") or "").strip()
    tool_input = payload.get("tool_input") or {}
    tool_use_id = payload.get("tool_use_id")
    session_id = payload.get("session_id")
    cwd = payload.get("cwd")
    observer = None
    try:
        from opentraces.capture.claude_code.hooks._trails import (
            observe_tool_boundary_for_hook,
        )

        observer = observe_tool_boundary_for_hook(
            cwd, tool_name, transcript_path, tool_input
        )
    except Exception:
        observer = None

    try:
        if tool_name == "Edit":
            data = _handle_edit(tool_input)
        elif tool_name == "Write":
            data = _handle_write(tool_input)
        else:
            data = {
                "tool": tool_name or "unknown",
                "capture_status": "hook_only",
                "limitations": ["hook_only"],
            }
    except Exception:
        # mmh3 (or anything else the handlers need) is unavailable —
        # degrade to the no-range fallback below rather than crashing.
        data = None

    if data is None:
        data = {
            "tool": tool_name or "unknown",
            "capture_status": "hook_only",
            "limitations": ["posttooluse_no_file_range"],
            "confidence": "low",
        }

    data["tool_use_id"] = tool_use_id
    data["session_id"] = session_id
    data["tool_input"] = tool_input
    data["tool_response"] = payload.get("tool_response") or {}
    trail: dict = {}
    try:
        from opentraces.capture.claude_code.hooks._trails import trail_state

        trail = trail_state(cwd, tool_name, tool_input)
    except Exception:
        trail = {}
    if trail:
        data["trail"] = trail
    if observer is not None:
        data["trail_observer"] = observer

    line = json.dumps({
        "type": "opentraces_hook",
        "event": "PostToolUse",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    })

    try:
        with open(transcript_path, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass  # Never break Claude Code on our account

    if data.get("file_path"):
        _dual_emit_agent_trace(cwd, data, session_id)


if __name__ == "__main__":
    main()
