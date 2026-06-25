"""Step accessors shared across slicers (plan: Trace Slicer Library, issue #141).

Slicers operate over ``TraceRecord.steps`` (each step is one TAO cycle). These
helpers work on either a pydantic ``Step`` or its dict form so the slicers stay
agnostic to how the record was loaded, mirroring the dual-shape accessor idiom
already used in ``core/bursts.py``.
"""
from __future__ import annotations

import re
from typing import Any

# Edit/Write tool names across the supported agents (Claude Edit/Write/MultiEdit/
# NotebookEdit; Codex apply_patch). Kept in sync with ``core/bursts.py`` burst
# inputs so S2 clusters the same edit surface.
EDIT_TOOLS = frozenset(
    {"apply_patch", "Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_editor"}
)

# S1 control-message blocklist (LOCKED inlined rule from issue #141). A user
# step whose trimmed text matches this does NOT open a trajectory. Covers the
# named tag family AND the ``<<…>>`` synthetic-marker family the ticket
# explicitly widened to.
TAG_BLOCK_RE = re.compile(
    r"^\s*(<<|<(skill|subagent_notification|task-notification|turn_aborted)\b)"
)
# Short continuation directive (LOCKED inlined rule).
CONTINUATION_RE = re.compile(r"^\s*(continue|keep going|Continue\b.*)\s*$", re.IGNORECASE)

# Notification-drain markers — S3 anchors. A user step matching one starts a
# trajectory in S3 (the drain), unlike S1 which absorbs it.
NOTIFICATION_RE = re.compile(r"^\s*<([a-zA-Z_-]*notification|task-notification)\b")

# Unambiguous test/build PASS signal (S3 remediation, issue #141 Phase-0): we
# require a clear pass token and separately exclude failure tokens, so an output
# like "3 passed, 1 failed" is NOT treated as a deliverable-completing success.
_PASS_RE = re.compile(
    r"\b(\d+\s+passed|all tests?\s+passed?|tests?\s+passed|build succeeded|"
    r"0 failed|✓ all|compilation successful)\b",
    re.IGNORECASE,
)
_FAIL_RE = re.compile(
    r"\b(\d+\s+failed|fail(ed|ure)?|error|exception|traceback|did not pass|"
    r"assertionerror|✗|exit code [1-9])\b",
    re.IGNORECASE,
)


def role(step: Any) -> str:
    return step.get("role") if isinstance(step, dict) else getattr(step, "role", "")


def content(step: Any) -> str:
    c = step.get("content") if isinstance(step, dict) else getattr(step, "content", None)
    return c or ""


def tool_names(step: Any) -> list[str]:
    tcs = step.get("tool_calls") if isinstance(step, dict) else getattr(step, "tool_calls", [])
    out: list[str] = []
    for tc in tcs or []:
        n = tc.get("tool_name") if isinstance(tc, dict) else getattr(tc, "tool_name", None)
        if n:
            out.append(n)
    return out


def tool_inputs(step: Any) -> list[dict]:
    tcs = step.get("tool_calls") if isinstance(step, dict) else getattr(step, "tool_calls", [])
    out: list[dict] = []
    for tc in tcs or []:
        i = tc.get("input") if isinstance(tc, dict) else getattr(tc, "input", None)
        out.append(i or {})
    return out


def observations(step: Any) -> list[Any]:
    obs = step.get("observations") if isinstance(step, dict) else getattr(step, "observations", [])
    return obs or []


def obs_text(step: Any) -> str:
    parts: list[str] = []
    for o in observations(step):
        if isinstance(o, dict):
            parts.append(o.get("content") or o.get("output_summary") or "")
        else:
            parts.append(getattr(o, "content", None) or getattr(o, "output_summary", None) or "")
    return "\n".join(p for p in parts if p)


def obs_has_error(step: Any) -> bool:
    for o in observations(step):
        e = o.get("error") if isinstance(o, dict) else getattr(o, "error", None)
        if e:
            return True
    return False


def edit_files(step: Any) -> list[str]:
    """Relative-ish file paths touched by edit tool calls in this step."""
    files: list[str] = []
    for name, inp in zip(tool_names(step), tool_inputs(step)):
        if name in EDIT_TOOLS:
            fp = inp.get("file_path") or inp.get("path") or inp.get("filename")
            if isinstance(fp, str):
                files.append(fp)
            elif name == "apply_patch":
                files.append("<apply_patch>")
    return files


def is_notification(step: Any) -> bool:
    return role(step) == "user" and bool(NOTIFICATION_RE.match(content(step).strip()))


def is_unambiguous_pass(step: Any) -> bool:
    """A green-test/build signal that is NOT also a failure (S3 remediation)."""
    text = obs_text(step)
    if not text:
        return False
    return bool(_PASS_RE.search(text)) and not _FAIL_RE.search(text)


def human_ask_starts(steps: list[Any]) -> list[int]:
    """Step indices that are genuine human asks per the LOCKED S1 rule.

    A user step opens a trajectory iff its trimmed text is non-empty, does NOT
    match the control-tag blocklist, and is NOT a short continuation directive.
    Shared by S1 (spine), S2, S3, and S4.
    """
    from ..faultpoints import armed  # local import: faultpoints has no deps

    drop_continuation = armed("slicer-drop-continuation")
    starts: list[int] = []
    for i, s in enumerate(steps):
        if role(s) != "user":
            continue
        t = content(s).strip()
        if not t:
            continue
        if not drop_continuation:
            # The continuation-heuristic: control/loop messages never open a
            # trajectory. The `slicer-drop-continuation` faultpoint disables
            # this so the otbox guard can prove it would catch the regression.
            if TAG_BLOCK_RE.match(t):
                continue
            if CONTINUATION_RE.match(t):
                continue
        starts.append(i)
    return starts


def notification_starts(steps: list[Any]) -> list[int]:
    """Step indices of ``*_notification`` drains (S3 anchors)."""
    return [i for i, s in enumerate(steps) if is_notification(s)]
