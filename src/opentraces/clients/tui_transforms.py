"""Flatten trace steps into conversation / full event streams.

Python port of ``src/tui/transforms.ts`` from the traces-audit reference TUI.
The opentraces step model (Step -> role, content, tool_calls[], observations[])
is flattened into a canonical event list, then folded into either:

* ``conversation_view`` — agent_text + tool_calls collapse into one agent turn
  with a short tool summary ("3 tools: Read, Edit, Bash")
* ``full_view`` — one row per raw event (tool calls and results expanded)
"""

from __future__ import annotations

import json
import re
from typing import Any

_ANSI_CSI = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_ANSI_OSC = re.compile(r"\x1b\][^\x07]*\x07")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text: Any) -> str:
    if not isinstance(text, str):
        return "" if text is None else str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ANSI_CSI.sub("", text)
    text = _ANSI_OSC.sub("", text)
    return _CTRL.sub("", text)


def format_tool_call(name: str, args: Any) -> str:
    try:
        args_str = json.dumps(args, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        args_str = str(args)
    return f"{name}({args_str})"


def _events_from_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand opentraces steps into canonical events, preserving order.

    Each agent step may carry tool_calls (expanded into tool_call events) and
    observations (expanded into tool_result events). Content becomes a
    user_message / agent_text event depending on role.
    """
    events: list[dict[str, Any]] = []
    for i, step in enumerate(steps or []):
        role = step.get("role", "agent")
        ts = step.get("timestamp") or ""
        content = step.get("content")
        tool_calls = step.get("tool_calls") or []
        observations = step.get("observations") or []
        sid = f"s{i}"

        if content and content != "[REDACTED]":
            if role == "user":
                events.append({"id": f"{sid}-u", "type": "user_message",
                               "content": content, "timestamp": ts})
            elif role == "agent":
                events.append({"id": f"{sid}-a", "type": "agent_text",
                               "content": content, "timestamp": ts})
        elif content == "[REDACTED]":
            events.append({"id": f"{sid}-r", "type": "error",
                           "message": "[REDACTED]", "timestamp": ts})

        for j, tc in enumerate(tool_calls):
            events.append({
                "id": f"{sid}-tc{j}",
                "type": "tool_call",
                "toolName": tc.get("tool_name", "?"),
                "args": tc.get("input", {}),
                "timestamp": ts,
            })

        for j, obs in enumerate(observations):
            events.append({
                "id": f"{sid}-ob{j}",
                "type": "tool_result",
                "toolName": obs.get("tool_name", ""),
                "output": obs.get("content") or obs.get("output_summary") or "",
                "status": obs.get("status", ""),
                "timestamp": obs.get("timestamp") or ts,
            })
    return events


def conversation_view(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = _events_from_steps(steps)
    items: list[dict[str, Any]] = []
    cur_text = ""
    cur_tools: list[str] = []
    turn = 0

    def flush() -> None:
        nonlocal cur_text, cur_tools, turn
        if cur_text or cur_tools:
            uniq: list[str] = []
            seen = set()
            for n in cur_tools:
                n = sanitize(n)
                if n and n not in seen:
                    seen.add(n)
                    uniq.append(n)
            items.append({
                "id": f"agent-{turn}",
                "type": "agent",
                "content": cur_text or "(no text response)",
                "tool_summary": ", ".join(uniq) if uniq else None,
                "tool_count": len(uniq) if uniq else 0,
            })
            turn += 1
            cur_text = ""
            cur_tools = []

    for ev in events:
        t = ev["type"]
        if t == "user_message":
            flush()
            items.append({"id": ev["id"], "type": "user",
                          "content": sanitize(ev["content"])})
        elif t == "agent_text":
            txt = sanitize(ev["content"])
            cur_text = f"{cur_text}\n\n{txt}" if cur_text else txt
        elif t == "tool_call":
            cur_tools.append(ev["toolName"])
        elif t == "error":
            msg = "Error: " + sanitize(ev.get("message", ""))
            cur_text = f"{cur_text}\n\n{msg}" if cur_text else msg
        # tool_result / agent_thinking folded silently in conversation mode
    flush()
    return items


def full_view(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = _events_from_steps(steps)
    items: list[dict[str, Any]] = []
    for ev in events:
        t = ev["type"]
        base = {"id": ev["id"], "event_type": t}
        if t == "user_message":
            items.append({**base, "content": sanitize(ev["content"])})
        elif t == "agent_text":
            items.append({**base, "content": sanitize(ev["content"])})
        elif t == "tool_call":
            items.append({
                **base,
                "tool_name": ev["toolName"],
                "content": sanitize(format_tool_call(ev["toolName"], ev.get("args", {}))),
            })
        elif t == "tool_result":
            items.append({
                **base,
                "tool_name": ev.get("toolName", ""),
                "tool_status": ev.get("status", ""),
                "content": sanitize(ev.get("output", "")),
            })
        elif t == "error":
            items.append({**base, "content": sanitize(ev.get("message", ""))})
    return items
