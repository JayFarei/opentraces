"""Context-aware scanning primitives.

Field-type classification plus the per-field / serialized-bytes scan
functions. The record-level orchestration lives in
:mod:`opentraces.security.pipeline`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .secrets import SecretMatch, scan_text


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


def scan_content(
    text: str,
    field_type: FieldType,
    *,
    include_entropy: bool | None = None,
) -> ScanResult:
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

    default_entropy = field_type in (FieldType.TOOL_INPUT, FieldType.GENERAL)
    use_entropy = default_entropy if include_entropy is None else include_entropy and default_entropy
    matches = scan_text(text, include_entropy=use_entropy)

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


# ---------------------------------------------------------------------------
# Field-filter calibration (Part E of plan 032)
#
# Path suffixes and prefixes that never contain PII or secrets for an agent
# trace. Used by Tier 1.8 (LLM PII detection) and any future scanner tiers
# that walk the JSON tree by key path. The existing regex+entropy pipeline
# is value-only and already ignores these in practice; exposing them as a
# shared API keeps behaviour consistent across tiers.
# ---------------------------------------------------------------------------

_SAFE_FIELD_SUFFIXES: tuple[str, ...] = (
    ".type",
    ".id",
    ".timestamp",
    ".stopReason",
    ".model",
    ".provider",
    ".parentId",
    ".mimeType",
)

_SAFE_FIELD_PREFIXES: tuple[str, ...] = (
    "usage.",
    "message.usage.",
)

_BASE64_MIN_LEN: int = 256


def is_safe_field_path(path: str) -> bool:
    """Return True if ``path`` names a metadata field never containing PII."""
    if not path:
        return False
    if any(path.endswith(suffix) for suffix in _SAFE_FIELD_SUFFIXES):
        return True
    if any(path.startswith(prefix) for prefix in _SAFE_FIELD_PREFIXES):
        return True
    return False


def is_base64_blob(value: object, siblings: dict[str, object]) -> bool:
    """Return True if ``value`` looks like inline base64 media data.

    Heuristic: sibling ``mimeType`` key present and the string value is
    at least ``_BASE64_MIN_LEN`` characters long. Used to skip inline
    images and other binary blobs during scanning.
    """
    if not isinstance(value, str):
        return False
    if "mimeType" not in siblings:
        return False
    return len(value) >= _BASE64_MIN_LEN


def _classify_tool(tool_name: str) -> FieldType:
    """Classify a tool name into input or result field type."""
    base = tool_name.split("__")[-1] if "__" in tool_name else tool_name
    if base in _INPUT_TOOLS:
        return FieldType.TOOL_INPUT
    if base in _RESULT_TOOLS:
        return FieldType.TOOL_RESULT
    # Default: treat unknown tools as inputs (more conservative)
    return FieldType.TOOL_INPUT


def scan_serialized(
    jsonl_bytes: bytes,
    *,
    include_entropy: bool | None = None,
) -> ScanResult:
    """Scan serialized JSONL bytes for secrets.

    Catches anything introduced during enrichment or serialization that was
    not present in the original record fields.
    """
    text = jsonl_bytes.decode("utf-8", errors="replace")
    return scan_content(text, FieldType.GENERAL, include_entropy=include_entropy)
