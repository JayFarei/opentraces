"""Security/privacy pipeline orchestrator and public API.

Two resolution paths:

  1. ``tools=[...]`` explicit list   → runs exactly those tools.
  2. ``cfg=<Config>``                → runs registry tools whose ``enabled(cfg)``
                                         returns True.

Callers MUST pass one of the two. Every tool's :class:`ToolResult` lands under
``record.metadata.security.tools.<name>`` and the final tool-name list is
stamped at ``record.metadata.security.tools_applied``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from opentraces_schema import TraceRecord

from .tools import Detector, Finding, ToolContext, ToolInfo, ToolResult, Verdict
from .tools._registry import all_tools, get as get_tool, iter_enabled, iter_tools
from .walker import (
    FieldPath,
    FieldType,
    ensure_security_metadata,
    redact_spans,
    walk_dict_strings,
)


@dataclass
class PipelineReport:
    tools_applied: list[str] = field(default_factory=list)
    tool_results: dict[str, ToolResult] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    redactions_applied: int = 0
    verdicts: list[Verdict] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def flags_reviewed(self) -> int:
        total = 0
        for v in self.verdicts:
            flags = v.payload.get("flags") if v.payload else None
            if isinstance(flags, list):
                total += len(flags)
        return total

    def tool_metadata(self, tool_name: str) -> dict[str, Any]:
        res = self.tool_results.get(tool_name)
        return dict(res.metadata_patch) if res else {}


_CANONICAL_ORDER = {t.name: i for i, t in enumerate(all_tools())}


def _resolve_tools(tools: Sequence[str] | None, cfg: Any) -> list[Any]:
    if tools is None and cfg is None:
        raise ValueError("sanitize_*() requires either `tools=` or `cfg=`")
    if tools is not None:
        seen: set[str] = set()
        resolved: list[Any] = []
        for name in tools:
            if name in seen:
                continue
            seen.add(name)
            resolved.append(get_tool(name))
        # Re-sort to canonical detector→transformer→judge order so a judge
        # never observes pre-redaction text even if the caller is sloppy.
        resolved.sort(key=lambda t: _CANONICAL_ORDER.get(t.name, 1 << 20))
        return resolved
    return list(iter_enabled(cfg))


def run_tools(
    record: TraceRecord,
    tools: Iterable[Any],
    ctx: ToolContext,
) -> PipelineReport:
    """Run an ordered iterable of tool instances against ``record``.

    Errors from individual tools are caught and surfaced on the report so one
    misbehaving tool does not abort the whole pipeline.
    """
    report = PipelineReport()
    sec = ensure_security_metadata(record)
    tools_section: dict[str, Any] = {}
    sec["tools"] = tools_section

    for tool in tools:
        try:
            result = tool.apply(record, ctx)
        except Exception as exc:  # noqa: BLE001 — tools must never abort the pipeline
            report.errors.append((tool.name, str(exc)))
            tools_section[tool.name] = {"status": "error", "error": str(exc)}
            continue

        report.tool_results[tool.name] = result
        report.tools_applied.append(tool.name)
        if result.findings:
            report.findings.extend(result.findings)
        if result.redactions_applied:
            report.redactions_applied += result.redactions_applied
        if result.verdict is not None:
            report.verdicts.append(result.verdict)
        if result.error:
            report.errors.append((tool.name, result.error))

        tools_section[tool.name] = dict(result.metadata_patch)

    sec["tools_applied"] = list(report.tools_applied)
    return report


def sanitize_record(
    record: TraceRecord,
    *,
    tools: Sequence[str] | None = None,
    cfg: Any = None,
) -> tuple[TraceRecord, PipelineReport]:
    """Run the configured (or explicit) tool list against ``record`` in place."""
    resolved = _resolve_tools(tools, cfg)
    ctx = ToolContext(cfg=cfg)
    report = run_tools(record, resolved, ctx)
    return record, report


def sanitize_text(
    text: str,
    *,
    field_type: FieldType | str = FieldType.GENERAL,
    tools: Sequence[str] | None = None,
    cfg: Any = None,
) -> tuple[str, list[Finding]]:
    """Run detector tools over one string. Judges and transformers are skipped."""
    if isinstance(field_type, str):
        field_type = FieldType(field_type)

    resolved = _resolve_tools(tools, cfg)
    detectors = [t for t in resolved if isinstance(t, Detector)]

    findings: list[Finding] = []
    current = text
    for det in detectors:
        spans = det.find(current, field_type)
        if not spans:
            continue
        findings.extend(spans)
        current = redact_spans(current, ((s.start, s.end) for s in spans))
    return current, findings


def sanitize_dict(
    data: dict[str, Any],
    *,
    tools: Sequence[str] | None = None,
    cfg: Any = None,
    field_type: FieldType | str = FieldType.GENERAL,
) -> tuple[dict[str, Any], PipelineReport]:
    """Run detector tools over the string leaves of a JSON-ish dict."""
    if isinstance(field_type, str):
        field_type = FieldType(field_type)

    resolved = _resolve_tools(tools, cfg)
    detectors = [t for t in resolved if isinstance(t, Detector)]

    report = PipelineReport()
    if not detectors:
        return dict(data), report

    current: Any = data
    for det in detectors:
        det_findings: list[Finding] = []
        det_redactions = [0]

        def _transform(text: str, path: FieldPath, ft: FieldType, det=det) -> str:
            spans = det.find(text, ft)
            if not spans:
                return text
            for s in spans:
                det_findings.append(
                    Finding(
                        tool=det.name,
                        pattern=s.pattern,
                        matched_text=s.matched_text,
                        start=s.start,
                        end=s.end,
                        severity=s.severity,
                        field_path=path,
                    )
                )
            det_redactions[0] += len(spans)
            return redact_spans(text, ((s.start, s.end) for s in spans))

        current, _changes = walk_dict_strings(current, _transform, field_type=field_type)

        result = ToolResult(
            name=det.name,
            kind=det.kind,
            findings=det_findings,
            redactions_applied=det_redactions[0],
            metadata_patch={
                "findings_count": len(det_findings),
                "redactions_applied": det_redactions[0],
            },
        )
        report.tool_results[det.name] = result
        report.tools_applied.append(det.name)
        report.findings.extend(det_findings)
        report.redactions_applied += det_redactions[0]

    return current if isinstance(current, dict) else dict(data), report


def list_tools(cfg: Any = None) -> list[ToolInfo]:
    """Return per-tool descriptor list in canonical registry order."""
    return [tool.describe(cfg) for tool in iter_tools()]


__all__ = [
    "PipelineReport",
    "list_tools",
    "run_tools",
    "sanitize_dict",
    "sanitize_record",
    "sanitize_text",
]
