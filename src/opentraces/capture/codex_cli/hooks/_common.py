"""Shared helpers for Codex CLI hook entrypoints."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opentraces.capture.claude_code.hooks._trails import (
    arm_hook_watchdog,
    auto_enroll_from_cwd,
    observe_tool_boundary_for_hook,
    trail_state,
)

SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


def read_payload() -> dict[str, Any] | None:
    arm_hook_watchdog()
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_session_id(value: Any) -> str:
    text = str(value or "unknown")
    text = SAFE_NAME.sub("_", text).strip("._")
    return text or "unknown"


def sidecar_path(payload: dict[str, Any]) -> Path | None:
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    session_id = safe_session_id(payload.get("session_id"))
    try:
        root = Path(cwd).expanduser().resolve()
    except Exception:
        return None
    return root / ".opentraces" / "codex-cli" / "hooks" / f"{session_id}.jsonl"


def append_hook_event(payload: dict[str, Any], event: str, data: dict[str, Any]) -> None:
    path = sidecar_path(payload)
    if path is None:
        return
    line = json.dumps(
        {
            "type": "opentraces_hook",
            "event": event,
            "timestamp": utc_now(),
            "data": data,
        },
        sort_keys=True,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        return


def git_info(cwd: str | None) -> dict[str, Any]:
    if not cwd:
        return {}
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        try:
            diff_names = subprocess.check_output(
                ["git", "diff", "HEAD", "--name-only"],
                cwd=cwd,
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            changed_paths = [p for p in diff_names.splitlines() if p.strip()]
        except Exception:
            changed_paths = []
        status_lines = [line for line in status_out.splitlines() if line.strip()]
        return {
            "sha": sha,
            "dirty": bool(status_lines),
            "files_changed": len(status_lines),
            "changed_paths": changed_paths,
        }
    except Exception:
        return {}


def observe_boundary(payload: dict[str, Any]) -> dict[str, Any] | None:
    return observe_tool_boundary_for_hook(
        payload.get("cwd"),
        payload.get("tool_name"),
        payload.get("transcript_path"),
        payload.get("tool_input") or {},
    )


def spawn_ingest(payload: dict[str, Any]) -> None:
    transcript_path = payload.get("transcript_path")
    cwd = payload.get("cwd") or ""
    if not isinstance(transcript_path, str) or not transcript_path:
        return
    try:
        argv = [
            sys.executable,
            "-m",
            "opentraces",
            "_ingest-session",
            transcript_path,
            "--agent",
            "codex-cli",
        ]
        if cwd:
            argv.extend(["--project", cwd])
        subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            cwd=cwd or None,
            env=os.environ.copy(),
        )
    except Exception:
        return


def common_tool_data(payload: dict[str, Any]) -> dict[str, Any]:
    tool_name = payload.get("tool_name") or "unknown"
    tool_input = payload.get("tool_input") or {}
    data = {
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "tool": tool_name,
        "tool_use_id": payload.get("tool_use_id"),
        "tool_input": tool_input if isinstance(tool_input, dict) else {},
        "trail": trail_state(payload.get("cwd"), tool_name, tool_input),
    }
    observer = observe_boundary(payload)
    if observer is not None:
        data["trail_observer"] = observer
    return data


def record_lifecycle_event(event: str) -> None:
    payload = read_payload()
    if payload is None:
        return
    auto_enroll_from_cwd(payload.get("cwd"))
    append_hook_event(
        payload,
        event,
        {
            "session_id": payload.get("session_id"),
            "turn_id": payload.get("turn_id"),
            "transcript_path": payload.get("transcript_path"),
            "permission_mode": payload.get("permission_mode"),
            "model": payload.get("model"),
            "cwd": payload.get("cwd"),
            "source": payload.get("source"),
            "prompt": payload.get("prompt") if event == "UserPromptSubmit" else None,
            "trigger": payload.get("trigger")
            if event in {"PreCompact", "PostCompact"}
            else None,
        },
    )
