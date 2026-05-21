"""Opt-in regex pattern detector."""

from __future__ import annotations

from typing import Any

from . import DetectorMixin, Finding, ToolInfo, cfg_block
from ..walker import FieldType
from ..secrets import scan_text


class RegexDetector(DetectorMixin):
    """Pattern-driven secret detector."""

    name = "regex"
    display_name = "Regex patterns"

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        return bool(getattr(block, "enabled", False)) if block else False

    def find(self, text: str, field_type: FieldType) -> list[Finding]:
        matches = scan_text(text, include_entropy=False)
        return [
            Finding(
                tool=self.name,
                pattern=m.pattern_name,
                matched_text=m.matched_text,
                start=m.start,
                end=m.end,
                severity=m.severity,
            )
            for m in matches
        ]

    def describe(self, cfg: Any) -> ToolInfo:
        from ..secrets import _PATTERNS  # private — fine, same package

        is_on = self.enabled(cfg)
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=is_on,
            state="enabled" if is_on else "disabled",
            detail=f"{len(_PATTERNS)} built-in detectors",
        )
