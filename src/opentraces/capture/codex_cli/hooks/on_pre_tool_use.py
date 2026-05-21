#!/usr/bin/env python3
"""Codex CLI PreToolUse hook for OpenTraces."""

from __future__ import annotations

from ._common import append_hook_event, auto_enroll_from_cwd, common_tool_data, read_payload


def main() -> None:
    payload = read_payload()
    if payload is None:
        return
    auto_enroll_from_cwd(payload.get("cwd"))
    append_hook_event(payload, "PreToolUse", common_tool_data(payload))


if __name__ == "__main__":
    main()
