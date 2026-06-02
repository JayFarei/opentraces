"""Extract a runnable ``test`` payload (repro command + expected failure signal).

Two sources, in priority order:
  1. DECLARED — the reporter knows the repro command and passes it at export
     (``capsule export --test-command ... [--expect-error ...]``). Most reliable;
     a bug reporter just ran the failing command.
  2. CAPTURED — best-effort scan of the trace's steps for a command tool call
     (Bash/shell/run) whose observation errored, near the failing step.

Returns ``None`` when nothing runnable is found — the capsule is still shareable
and falls back to ``capsule replay`` (intent-replay), which is honest for
design-task sessions that have no failing command.
"""

from __future__ import annotations

from typing import Any

from .summary import _extract_error_line

_COMMAND_TOOLS = {"bash", "shell", "run", "execute", "terminal", "sh"}


def declared_test(command: str | None, expect_error: str | None, cwd: str | None = None) -> dict[str, Any] | None:
    if not command:
        return None
    expected = (
        {"kind": "error_string", "value": expect_error}
        if expect_error
        else {"kind": "nonzero_exit"}
    )
    return {"command": command, "expected": expected, "cwd": cwd or "", "source": "declared"}


def _command_from_tool_call(tc: Any) -> str | None:
    inp = getattr(tc, "input", None) or {}
    for key in ("command", "cmd", "script", "code"):
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def extract_test_payload(record: Any, failing_step_index: int, *, window: int = 6) -> dict[str, Any] | None:
    """Best-effort: find the failing command nearest the failing step."""

    steps = list(getattr(record, "steps", []) or [])
    if not steps:
        return None
    lo = max(0, failing_step_index - window)
    hi = min(len(steps), failing_step_index + window + 1)
    # Search outward-ish: prefer steps at/after the failing step, then before.
    order = list(range(failing_step_index, hi)) + list(range(failing_step_index - 1, lo - 1, -1))

    for idx in order:
        step = steps[idx]
        calls = list(getattr(step, "tool_calls", []) or [])
        obs = {getattr(o, "source_call_id", None): o for o in getattr(step, "observations", []) or []}
        for tc in calls:
            if getattr(tc, "tool_name", "").lower() not in _COMMAND_TOOLS:
                continue
            command = _command_from_tool_call(tc)
            if not command:
                continue
            observation = obs.get(getattr(tc, "tool_call_id", None))
            err = getattr(observation, "error", None) if observation else None
            content = getattr(observation, "content", None) if observation else None
            error_line = (err if (err and err != "no_result") else None) or _extract_error_line(content)
            if error_line:
                return {
                    "command": command,
                    "expected": {"kind": "error_string", "value": error_line[:200]},
                    "cwd": "",
                    "source": "captured",
                    "captured_at_step": idx,
                }
    return None


__all__ = ["declared_test", "extract_test_payload"]
