#!/usr/bin/env python3
"""Codex CLI PostToolUse hook for OpenTraces."""

from __future__ import annotations

from ._common import append_hook_event, auto_enroll_from_cwd, common_tool_data, read_payload


def main() -> None:
    payload = read_payload()
    if payload is None:
        return
    auto_enroll_from_cwd(payload.get("cwd"))
    data = common_tool_data(payload)
    data["tool_response"] = payload.get("tool_response") or {}
    data["capture_status"] = "hook_only"
    data["limitations"] = ["hook_only"]
    append_hook_event(payload, "PostToolUse", data)


if __name__ == "__main__":
    main()
