"""Tool shared types for the security/privacy pipeline.

Each privacy or security action is a *tool*: a small class with a ``name``,
``kind``, an ``enabled(cfg) -> bool``, an ``apply(record, ctx) -> ToolResult``,
and a ``describe(cfg) -> ToolInfo``. Tools are stored in the static registry
(:mod:`._registry`) in canonical execution order. Detectors override
:class:`DetectorMixin` for the default per-field walk; tools that need
record-level dispatch (TruffleHog) or produce verdicts (classifier) implement
``apply`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from opentraces_schema import TraceRecord

from ..walker import FieldPath, FieldType, redact_spans, walk_string_fields

Severity = Literal["critical", "high", "medium", "low"]
ToolKind = Literal["detector", "judge", "transformer"]


@dataclass(frozen=True)
class Finding:
    tool: str
    pattern: str
    matched_text: str
    start: int
    end: int
    severity: Severity = "medium"
    field_path: FieldPath = ""


@dataclass(frozen=True)
class Verdict:
    name: str
    summary: str
    decision: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """One tool's contribution to a pipeline run.

    ``payload`` carries a tool's native object to in-tree callers (TruffleHog's
    report) and is not persisted.
    """

    name: str
    kind: ToolKind
    findings: list[Finding] = field(default_factory=list)
    redactions_applied: int = 0
    verdict: Verdict | None = None
    metadata_patch: dict[str, Any] = field(default_factory=dict)
    payload: Any = None
    error: str | None = None


@dataclass(frozen=True)
class ToolInfo:
    name: str
    display_name: str
    kind: ToolKind
    enabled: bool
    state: str  # "enabled" / "disabled" / "missing" / "unreachable"
    detail: str | None = None
    setup_cmd: str | None = None
    disable_cmd: str | None = None


@dataclass
class ToolContext:
    cfg: Any = None


@runtime_checkable
class Detector(Protocol):
    """Structural marker for tools that emit redactable spans.

    Used by :func:`opentraces.security.pipeline.sanitize_text` /
    ``sanitize_dict`` to filter out judges and transformers when running over
    a single string or dict.
    """

    name: str
    kind: ToolKind

    def find(self, text: str, field_type: FieldType) -> list[Finding]: ...


def cfg_block(cfg: Any, name: str) -> Any:
    """Return ``cfg.security.<name>`` or None if missing at any level."""
    if cfg is None:
        return None
    sec = getattr(cfg, "security", None)
    if sec is None:
        return None
    return getattr(sec, name, None)


class DetectorMixin:
    """Default per-field ``apply()`` for detector tools.

    Tools whose detection requires the whole serialized record (TruffleHog)
    or that produce verdicts override ``apply()`` directly.
    """

    name: str
    display_name: str
    kind: ToolKind = "detector"

    def find(self, text: str, field_type: FieldType) -> list[Finding]:  # pragma: no cover - override
        raise NotImplementedError

    def apply(self, record: TraceRecord, ctx: ToolContext) -> ToolResult:
        findings: list[Finding] = []
        counter = [0]

        def _transform(text: str, path: FieldPath, ft: FieldType) -> str:
            spans = self.find(text, ft)
            if not spans:
                return text
            for span in spans:
                findings.append(replace(span, field_path=path))
            counter[0] += len(spans)
            return redact_spans(text, ((s.start, s.end) for s in spans))

        walk_string_fields(record, _transform)
        return ToolResult(
            name=self.name,
            kind=self.kind,
            findings=findings,
            redactions_applied=counter[0],
            metadata_patch={
                "findings_count": len(findings),
                "redactions_applied": counter[0],
            },
        )


__all__ = [
    "Detector",
    "DetectorMixin",
    "FieldPath",
    "FieldType",
    "Finding",
    "Severity",
    "ToolContext",
    "ToolInfo",
    "ToolKind",
    "ToolResult",
    "Verdict",
    "cfg_block",
    "redact_spans",
]
