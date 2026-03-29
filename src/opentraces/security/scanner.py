"""Context-aware scanning orchestrator.

Applies different secret-detection rules depending on where the content
appears in a trace record (tool input, tool result, reasoning, general).
Two-pass design: first per-field, then on final serialized bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from opentraces_schema.models import TraceRecord

from .secrets import SecretMatch, redact_text, scan_text


class FieldType(Enum):
    """Content field classification for context-aware scanning."""

    TOOL_INPUT = "tool_input"
    TOOL_RESULT = "tool_result"
    REASONING = "reasoning"
    GENERAL = "general"


@dataclass
class ScanResult:
    """Aggregated result of scanning one or more fields."""

    matches: list[SecretMatch] = field(default_factory=list)
    redaction_count: int = 0
    field_counts: dict[str, int] = field(default_factory=dict)

    def merge(self, other: ScanResult) -> None:
        """Merge another ScanResult into this one."""
        self.matches.extend(other.matches)
        self.redaction_count += other.redaction_count
        for k, v in other.field_counts.items():
            self.field_counts[k] = self.field_counts.get(k, 0) + v


def scan_content(text: str, field_type: FieldType) -> ScanResult:
    """Scan a single text field with rules appropriate for its context.

    - TOOL_INPUT / GENERAL: full regex + entropy scan
    - TOOL_RESULT: regex only, no entropy (too many false positives on output)
    - REASONING: regex only, no entropy (hallucination risk)

    Args:
        text: The text to scan.
        field_type: The type of field the text came from.

    Returns:
        ScanResult with matches found.
    """
    if not text:
        return ScanResult()

    include_entropy = field_type in (FieldType.TOOL_INPUT, FieldType.GENERAL)
    matches = scan_text(text, include_entropy=include_entropy)

    result = ScanResult(
        matches=matches,
        redaction_count=len(matches),
        field_counts={field_type.value: len(matches)},
    )
    return result


# ---------------------------------------------------------------------------
# Tool name classification
# ---------------------------------------------------------------------------

_INPUT_TOOLS = {"bash", "write", "edit", "Write", "Edit", "Bash"}
_RESULT_TOOLS = {"read", "grep", "glob", "Read", "Grep", "Glob"}


def _classify_tool(tool_name: str) -> FieldType:
    """Classify a tool name into input or result field type."""
    base = tool_name.split("__")[-1] if "__" in tool_name else tool_name
    if base in _INPUT_TOOLS:
        return FieldType.TOOL_INPUT
    if base in _RESULT_TOOLS:
        return FieldType.TOOL_RESULT
    # Default: treat unknown tools as inputs (more conservative)
    return FieldType.TOOL_INPUT


# ---------------------------------------------------------------------------
# Record-level scanning
# ---------------------------------------------------------------------------

def _scan_dict_values(d: dict[str, Any], field_type: FieldType) -> ScanResult:
    """Recursively scan all string values in a dict."""
    result = ScanResult()
    for v in d.values():
        if isinstance(v, str):
            result.merge(scan_content(v, field_type))
        elif isinstance(v, dict):
            result.merge(_scan_dict_values(v, field_type))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    result.merge(scan_content(item, field_type))
                elif isinstance(item, dict):
                    result.merge(_scan_dict_values(item, field_type))
    return result


def scan_trace_record(record: TraceRecord) -> ScanResult:
    """Scan all fields of a TraceRecord with appropriate field types.

    Pass 1: scan individual fields with context-aware rules.

    Args:
        record: The trace record to scan.

    Returns:
        ScanResult aggregating all matches across fields.
    """
    result = ScanResult()

    # System prompts (general text)
    for _hash, prompt_text in record.system_prompts.items():
        result.merge(scan_content(prompt_text, FieldType.GENERAL))

    # Task description
    if record.task.description:
        result.merge(scan_content(record.task.description, FieldType.GENERAL))

    # Steps
    for step in record.steps:
        # Step content
        if step.content:
            result.merge(scan_content(step.content, FieldType.GENERAL))

        # Reasoning content
        if step.reasoning_content:
            result.merge(scan_content(step.reasoning_content, FieldType.REASONING))

        # Tool calls
        for tc in step.tool_calls:
            ft = _classify_tool(tc.tool_name)
            result.merge(_scan_dict_values(tc.input, ft))

        # Observations (tool results)
        for obs in step.observations:
            if obs.content:
                result.merge(scan_content(obs.content, FieldType.TOOL_RESULT))
            if obs.output_summary:
                result.merge(scan_content(obs.output_summary, FieldType.TOOL_RESULT))
            if obs.error:
                result.merge(scan_content(obs.error, FieldType.TOOL_RESULT))

        # Snippets
        for snippet in step.snippets:
            if snippet.text:
                result.merge(scan_content(snippet.text, FieldType.GENERAL))

    # Outcome
    if record.outcome.description:
        result.merge(scan_content(record.outcome.description, FieldType.GENERAL))
    if record.outcome.patch:
        result.merge(scan_content(record.outcome.patch, FieldType.GENERAL))

    # VCS diff
    if record.environment.vcs.diff:
        result.merge(scan_content(record.environment.vcs.diff, FieldType.GENERAL))

    return result


def scan_serialized(jsonl_bytes: bytes) -> ScanResult:
    """Pass 2: scan final serialized JSONL bytes for any remaining secrets.

    This catches anything introduced during enrichment or serialization
    that was not present in the original fields.

    Args:
        jsonl_bytes: The serialized JSONL content as bytes.

    Returns:
        ScanResult from scanning the raw serialized content.
    """
    text = jsonl_bytes.decode("utf-8", errors="replace")
    return scan_content(text, FieldType.GENERAL)


def apply_redactions(record: TraceRecord) -> int:
    """Apply redactions to all string fields in a TraceRecord in-place.

    Scans each field with context-appropriate rules and replaces matches
    with [REDACTED]. Returns total number of redactions applied.
    """
    total = 0

    for _hash, prompt_text in list(record.system_prompts.items()):
        matches = scan_text(prompt_text)
        if matches:
            record.system_prompts[_hash] = redact_text(prompt_text, matches)
            total += len(matches)

    if record.task.description:
        matches = scan_text(record.task.description)
        if matches:
            record.task.description = redact_text(record.task.description, matches)
            total += len(matches)

    for step in record.steps:
        if step.content:
            matches = scan_text(step.content)
            if matches:
                step.content = redact_text(step.content, matches)
                total += len(matches)

        if step.reasoning_content:
            matches = scan_text(step.reasoning_content, include_entropy=False)
            if matches:
                step.reasoning_content = redact_text(step.reasoning_content, matches)
                total += len(matches)

        for tc in step.tool_calls:
            for key, val in list(tc.input.items()):
                if isinstance(val, str):
                    matches = scan_text(val)
                    if matches:
                        tc.input[key] = redact_text(val, matches)
                        total += len(matches)

        for obs in step.observations:
            if obs.content:
                matches = scan_text(obs.content, include_entropy=False)
                if matches:
                    obs.content = redact_text(obs.content, matches)
                    total += len(matches)

        for snippet in step.snippets:
            if snippet.text:
                matches = scan_text(snippet.text)
                if matches:
                    snippet.text = redact_text(snippet.text, matches)
                    total += len(matches)

    if record.outcome.patch:
        matches = scan_text(record.outcome.patch)
        if matches:
            record.outcome.patch = redact_text(record.outcome.patch, matches)
            total += len(matches)

    return total


def two_pass_scan(record: TraceRecord) -> tuple[ScanResult, ScanResult]:
    """Run the full two-pass scan on a trace record.

    Pass 1: Context-aware per-field scanning.
    Pass 2: Raw scan of the serialized JSONL output.

    Args:
        record: The trace record to scan.

    Returns:
        Tuple of (pass1_result, pass2_result).
    """
    pass1 = scan_trace_record(record)
    serialized = record.to_jsonl_line().encode("utf-8")
    pass2 = scan_serialized(serialized)
    return pass1, pass2
