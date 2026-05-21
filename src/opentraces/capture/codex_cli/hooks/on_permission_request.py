#!/usr/bin/env python3
"""Codex CLI PermissionRequest hook for OpenTraces.

This hook is observational only. It deliberately emits no allow/deny decision.
"""

from __future__ import annotations

from ._common import append_hook_event, auto_enroll_from_cwd, read_payload


def main() -> None:
    payload = read_payload()
    if payload is None:
        return
    auto_enroll_from_cwd(payload.get("cwd"))
    append_hook_event(
        payload,
        "PermissionRequest",
        {
            "session_id": payload.get("session_id"),
            "turn_id": payload.get("turn_id"),
            "permission_mode": payload.get("permission_mode"),
            "tool": payload.get("tool_name"),
            "tool_input": payload.get("tool_input") or {},
        },
    )


if __name__ == "__main__":
    main()
