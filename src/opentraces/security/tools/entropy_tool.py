"""Always-on Shannon-entropy detector.

Standalone wrapper around the entropy half of
:func:`opentraces.security.secrets.scan_text`. Runs after :mod:`regex_tool` so
spans that were already redacted to ``[REDACTED]`` no longer appear as
high-entropy candidates — the placeholder fails the
``[A-Za-z0-9+/=_-]{20,}`` candidate regex.

Field-type policy mirrors the legacy ``scan_content`` behaviour: entropy
detection is suppressed for TOOL_RESULT and REASONING fields because the
false-positive rate on tool output and on agent reasoning text is high enough
to outweigh the catch rate.
"""

from __future__ import annotations

from typing import Any

from . import DetectorMixin, Finding, ToolInfo
from ..walker import FieldType
from ..secrets import (  # private but in-package
    DEFAULT_ENTROPY_THRESHOLD,
    _find_high_entropy,
    _is_allowlisted,
)


_ENTROPY_FIELDS = (FieldType.TOOL_INPUT, FieldType.GENERAL)


class EntropyDetector(DetectorMixin):
    """High-entropy string detector. Always-on, field-type gated."""

    name = "entropy"
    display_name = "Shannon entropy"

    def enabled(self, cfg: Any) -> bool:
        return True

    def find(self, text: str, field_type: FieldType) -> list[Finding]:
        if field_type not in _ENTROPY_FIELDS:
            return []
        matches = _find_high_entropy(text, threshold=DEFAULT_ENTROPY_THRESHOLD)
        out: list[Finding] = []
        for m in matches:
            if _is_allowlisted(m.pattern_name, m.matched_text, text, m.start):
                continue
            out.append(
                Finding(
                    tool=self.name,
                    pattern=m.pattern_name,
                    matched_text=m.matched_text,
                    start=m.start,
                    end=m.end,
                    severity=m.severity,
                )
            )
        return out

    def describe(self, cfg: Any) -> ToolInfo:
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=True,
            state="always-on",
            detail="high-entropy strings flagged in tool inputs and general prose",
        )
