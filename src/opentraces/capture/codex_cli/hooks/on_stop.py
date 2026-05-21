#!/usr/bin/env python3
"""Codex CLI Stop hook for OpenTraces."""

from __future__ import annotations

from ._common import (
    append_hook_event,
    auto_enroll_from_cwd,
    git_info,
    read_payload,
    spawn_ingest,
    trail_state,
)


def main() -> None:
    payload = read_payload()
    if payload is None:
        return
    auto_enroll_from_cwd(payload.get("cwd"))
    cwd = payload.get("cwd") or ""
    append_hook_event(
        payload,
        "Stop",
        {
            "session_id": payload.get("session_id"),
            "turn_id": payload.get("turn_id"),
            "permission_mode": payload.get("permission_mode"),
            "stop_hook_active": payload.get("stop_hook_active"),
            "git": git_info(cwd),
            "trail": trail_state(cwd),
        },
    )
    spawn_ingest(payload)


if __name__ == "__main__":
    main()
