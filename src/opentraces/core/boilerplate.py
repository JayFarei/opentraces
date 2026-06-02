"""Injected prompt boilerplate stripping for discovery summaries.

The search index intentionally keeps broad intent text for recall. Human-facing
discovery summaries have a different job: show the operator what the trace was
actually about, not wrapper text injected by a runtime or skill launcher.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from .intent import is_trigger
from .text_redaction import redact_preview_text


SUMMARY_LIMIT = 500
HEADLINE_LIMIT = 160

_SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder\b[^>]*>.*?</system-reminder\s*>",
    re.IGNORECASE | re.DOTALL,
)
_GOAL_OBJECTIVE_RE = re.compile(
    r"<objective\b[^>]*>(?P<body>.*?)</objective\s*>",
    re.IGNORECASE | re.DOTALL,
)
_WRAPPER_TAG_RE = re.compile(
    r"</?(?:goal_context|objective|instructions|environment_context)\b[^>]*>",
    re.IGNORECASE,
)
_SKILL_GUARD_LINE_RE = re.compile(
    r"^\s*IMPORTANT:\s*Do\s+NOT\s+read\b.*\bSKILL\.md\b.*",
    re.IGNORECASE | re.MULTILINE,
)
_GOAL_META_LINE_RE = re.compile(
    r"^\s*(?:The objective below is user-provided data|Treat it as the task to pursue|Continuation behavior:|Budget:|Work from evidence:|Progress visibility:|Fidelity:|Completion audit:|Blocked audit:)\b.*$",
    re.IGNORECASE,
)
_LEADING_SKILL_TRIGGER_RE = re.compile(r"^\s*[$/][A-Za-z][\w:-]*(?:\s+|$)")
_TAG_HINT_RE = re.compile(r"</?(?:goal_context|system-reminder|objective)\b", re.IGNORECASE)
_PROMPT_TEMPLATE_PREFIX_RE = re.compile(
    r"^\s*You\s+are\s+(?:acting\s+as\b|an\s+adversarial\s+independent\s+reviewer\b)",
    re.IGNORECASE,
)
_PROMPT_SUBSTANTIVE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{pattern}\b[^.!?]*(?:[.!?]|$)", re.IGNORECASE)
    for pattern in (
        "Our goal",
        "Read the plan at",
        "Product context",
        "The goal",
        "The feature",
    )
)


def strip_injected(text: str | None) -> str:
    """Remove known injected wrappers while preserving substantive text."""

    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return ""

    objective = _GOAL_OBJECTIVE_RE.search(cleaned)
    if objective is not None:
        cleaned = objective.group("body")

    cleaned = _SYSTEM_REMINDER_RE.sub(" ", cleaned)
    cleaned = _WRAPPER_TAG_RE.sub(" ", cleaned)

    kept_lines: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            kept_lines.append("")
            continue
        if _SKILL_GUARD_LINE_RE.match(line):
            continue
        if _GOAL_META_LINE_RE.match(line):
            continue
        kept_lines.append(raw_line)
    cleaned = "\n".join(kept_lines).strip()
    cleaned = _strip_leading_skill_triggers(cleaned)
    return _collapse_ws(cleaned)


def looks_injected_boilerplate(text: str | None) -> bool:
    """Return True when ``text`` still appears to be injected boilerplate."""

    if not text or not text.strip():
        return True
    raw = text.strip()
    head = raw[:800]
    if _SKILL_GUARD_LINE_RE.search(head):
        return True
    if _TAG_HINT_RE.search(head):
        return True
    if _PROMPT_TEMPLATE_PREFIX_RE.search(head):
        return True
    stripped = strip_injected(raw)
    if not stripped:
        return True
    if is_trigger(stripped):
        return True
    return False


def substantive_user_text(record: Any) -> str:
    """Return the latest non-trigger user text after boilerplate stripping."""

    candidates: list[tuple[int, str]] = []
    for step in sorted(getattr(record, "steps", []) or [], key=lambda s: getattr(s, "step_index", 0)):
        if getattr(step, "role", None) != "user":
            continue
        raw = getattr(step, "content", None)
        text = _display_text_from_candidate(raw)
        if not text or is_trigger(text):
            continue
        candidates.append((int(getattr(step, "step_index", 0) or 0), text))
    if not candidates:
        return ""
    return candidates[-1][1]


def summary_from_parts(
    *,
    task_description: str | None = None,
    user_texts: Iterable[str | None] = (),
    fallback: str | None = None,
) -> str:
    """Compose a human-facing summary from schema fields, not prompt wrappers."""

    task = _display_text_from_candidate(task_description)
    if task and not is_trigger(task):
        return _truncate(task, SUMMARY_LIMIT)

    substantive: list[str] = []
    for text in user_texts:
        cleaned = _display_text_from_candidate(text)
        if cleaned and not is_trigger(cleaned):
            substantive.append(cleaned)
    if substantive:
        return _truncate(substantive[-1], SUMMARY_LIMIT)

    fallback_text = strip_injected(fallback)
    return _truncate(_redact_display(fallback_text), SUMMARY_LIMIT) if fallback_text else ""


def summary_for_record(record: Any, *, fallback: str | None = None) -> str:
    """Return the display summary for a TraceRecord-like object."""

    task = getattr(getattr(record, "task", None), "description", None)
    user = substantive_user_text(record)
    return summary_from_parts(task_description=task, user_texts=[user], fallback=fallback)


def intent_text_for_record(record: Any) -> str:
    """Return index-role intent text with injected prefixes stripped."""

    task = strip_injected(getattr(getattr(record, "task", None), "description", None))
    user = substantive_user_text(record)
    parts: list[str] = []
    for value in (task, user):
        if value and value not in parts:
            parts.append(value)
    return " ".join(parts)


def headline_from_summary(summary: str | None) -> str:
    return _truncate(_redact_display(strip_injected(summary)), HEADLINE_LIMIT)


def _display_text_from_candidate(text: str | None) -> str:
    cleaned = strip_injected(text)
    if not cleaned:
        return ""
    prompt_summary = _prompt_template_substantive(cleaned)
    if prompt_summary:
        return _redact_display(prompt_summary)
    if looks_injected_boilerplate(text) or looks_injected_boilerplate(cleaned):
        return ""
    return _redact_display(cleaned)


def _redact_display(text: str | None) -> str:
    if not text:
        return ""
    return redact_preview_text(text)


def _prompt_template_substantive(text: str | None) -> str:
    if not text or not _PROMPT_TEMPLATE_PREFIX_RE.search(text):
        return ""
    for pattern in _PROMPT_SUBSTANTIVE_PATTERNS:
        match = pattern.search(text)
        if match:
            candidate = _collapse_ws(match.group(0))
            if candidate:
                return candidate
    return ""


def _strip_leading_skill_triggers(text: str) -> str:
    lines = text.splitlines()
    while lines:
        first = lines[0].strip()
        match = _LEADING_SKILL_TRIGGER_RE.match(first)
        if not match:
            break
        remainder = first[match.end():].strip()
        if remainder:
            lines[0] = remainder
            break
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
