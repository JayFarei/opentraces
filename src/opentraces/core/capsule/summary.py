"""Deterministic, honest one-line summary of a capsule's session.

The raw ``task.description`` is a verbose ``$cmd TASK: ... EXPECTED OUTCOME: ...``
dump. This distills:
  - ``title``        : a clean one-liner of what the agent was trying to do
  - ``what_happened``: an honest scope + outcome line (only framed as a failure
                       when there is a real error signal, not a keyword match in code)
  - ``failure``      : the extracted error line when one is genuinely present

No LLM: pure string distillation so the capsule is reproducible byte-for-byte.
"""

from __future__ import annotations

import re
from typing import Any

# Strip a leading "$skill-command " and a "TASK:" / "GOAL:" style label.
_LABEL_RE = re.compile(
    r"^\s*(?:\$[\w.\-]+\s+)?(?:TASK|GOAL|OBJECTIVE|REQUEST|PROMPT)\s*[:\-]\s*",
    re.IGNORECASE,
)
# The first structured section header marks the end of the actual ask.
_SECTION_RE = re.compile(
    r"\b(?:EXPECTED OUTCOME|CONTEXT|CONSTRAINTS|MUST DO|MUST NOT DO|"
    r"OUTPUT FORMAT|DELIVERABLE|ACCEPTANCE)\b\s*[:\-]?",
    re.IGNORECASE,
)
# A line that is a GENUINE runtime error, not the word "Exception" in source code.
# Anchored to the start of a (stripped) line so `except Exception:`, `raise X(`, and
# `# TypeError: ...` comments do NOT match — only a traceback header, a final
# "ExceptionName: message" line, or a tool/shell failure line.
_REAL_ERROR_RE = re.compile(
    r"(?m)^[ \t>|]*(?:"
    r"Traceback \(most recent call last\)"
    r"|[A-Z][A-Za-z0-9_.]*(?:Error|Exception): \S.*"
    r"|fatal: \S.*"
    r"|panic: \S.*"
    r"|.*\bexit code [1-9]\d*\b.*"
    r"|.*\b(?:command not found|No such file or directory|Permission denied|"
    r"segmentation fault|core dumped)\b.*"
    r")$"
)


def distill_title(text: str | None, *, max_chars: int = 160) -> str:
    """Clean one-liner: drop the ``$cmd``/``TASK:`` scaffolding and section dumps."""

    if not text:
        return ""
    t = text.strip()
    t = _LABEL_RE.sub("", t)
    section = _SECTION_RE.search(t)
    if section:
        t = t[: section.start()]
    t = t.split("\n\n", 1)[0]
    t = re.sub(r"\s+", " ", t).strip().strip("\"'")
    if len(t) > max_chars:
        cut = t[:max_chars]
        space = cut.rfind(" ")
        if space > max_chars // 2:
            cut = cut[:space]
        t = cut.rstrip(" ,;:.") + "…"
    return t


def _extract_error_line(excerpt: str | None) -> str | None:
    if not excerpt:
        return None
    match = _REAL_ERROR_RE.search(excerpt)
    if not match:
        return None
    line = match.group(0).strip()
    return (line[:200] + "…") if len(line) > 200 else line


def build_summary(
    *,
    record: Any,
    slice_payload: dict[str, Any],
    failing_step: dict[str, Any],
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Build the honest, deterministic capsule summary block."""

    title = distill_title(intent.get("headline")) or "agent session"

    outcome = getattr(record, "outcome", None)
    success = getattr(outcome, "success", None)
    terminal_state = getattr(outcome, "terminal_state", None)

    total_steps = len(getattr(record, "steps", []) or [])
    start = slice_payload.get("start_step_index")
    end = slice_payload.get("end_step_index")
    slice_steps = (end - start + 1) if (isinstance(start, int) and isinstance(end, int)) else None
    changed_files = len(getattr(record, "patches", []) or [])

    error_line = _extract_error_line(failing_step.get("error_excerpt"))
    # A capsule is a "failure" only when the session actually failed OR a genuine
    # runtime error was captured — not merely because the word "exception"
    # appeared in some source code the agent read.
    is_failure = (success is False) or bool(error_line)

    scope_bits = []
    if slice_steps:
        scope_bits.append(f"{slice_steps} steps around step {failing_step.get('index')}")
    scope_bits.append(f"{total_steps} total")
    if changed_files:
        scope_bits.append(f"{changed_files} file edits")
    scope = ", ".join(scope_bits)

    if error_line:
        what = f"The agent hit: {error_line}"
    elif success is False:
        what = f"The session did not succeed (terminal_state={terminal_state!r})."
    else:
        what = (
            "No explicit runtime error was captured"
            + (f" (terminal_state={terminal_state!r})" if terminal_state else "")
            + "; this capsule pins the session so the intent can be re-posed against current code."
        )

    return {
        "title": title,
        "what_happened": what,
        "failure": error_line,
        "is_failure": is_failure,
        "outcome": {"success": success, "terminal_state": terminal_state},
        "outcome_taxonomy": _derive_outcome_taxonomy(success, terminal_state, is_failure),
        "scope": scope,
    }


# Plan 090 (usage episode): the full vocabulary of the additive
# ``summary.outcome_taxonomy`` field. v1 confidently derives only the subset that
# the three coarse signals (success / terminal_state / is_failure) can support
# honestly: ``completed`` / ``abandoned`` / ``unclear``. ``workaround_found`` and the
# ``blocked_by_*`` causes need per-step signals we do not capture cheaply yet, so we
# do NOT fabricate them — they remain reserved in the vocabulary for richer
# derivation (or downstream annotation) without a schema change.
OUTCOME_TAXONOMY_VALUES = (
    "completed",
    "workaround_found",
    "abandoned",
    "blocked_by_dependency",
    "blocked_by_docs",
    "unclear",
)


def _derive_outcome_taxonomy(
    success: bool | None, terminal_state: Any, is_failure: bool
) -> str:
    """Coarse, honest outcome bucket from the three signals the design declares.

    Deliberately conservative: a captured failure whose cause the coarse signals
    cannot identify maps to ``unclear`` rather than guessing dependency-vs-docs.
    """

    ts = (terminal_state or "").lower() if isinstance(terminal_state, str) else ""
    if success is True or ts in {"goal_reached", "completed", "success"}:
        return "completed"
    if ts in {"abandoned", "interrupted"}:
        return "abandoned"
    # success is False / terminal error / or a captured failure of unknown cause.
    return "unclear"


__all__ = ["build_summary", "distill_title", "OUTCOME_TAXONOMY_VALUES"]
