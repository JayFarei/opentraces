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

from .companion import COMPANION_FLOOR, companion_field_type
from .tools import Detector, Finding, ToolContext, ToolInfo, ToolResult, Verdict
from .tools._registry import all_tools, get as get_tool, iter_enabled, iter_tools
from .version import SECURITY_VERSION
from .walker import (
    FieldPath,
    FieldType,
    ensure_security_metadata,
    redact_spans,
    walk_dict_strings,
)

# Frozen envelope for the companion-sanitize sidecar manifest. Counts + types
# only — never a literal secret. Additive within this version; a shape change
# bumps SECURITY_VERSION.
COMPANION_MANIFEST_SCHEMA_VERSION = "opentraces.security.companion.v1"


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


def _resolve_companion_tools(tools: Sequence[Any]) -> list[Any]:
    """Resolve a companion tool list of NAMES and/or pre-built INSTANCES.

    Names resolve from the registry; instances (e.g. an availability-gated
    ``privacy_filter`` / ``llm_pii`` bound to a model) pass through. The result
    is canonically ordered (detectors → transformers → judge) so a judge never
    observes pre-redaction text.
    """
    resolved: list[Any] = []
    seen: set[str] = set()
    for t in tools:
        if isinstance(t, str):
            if t in seen:
                continue
            seen.add(t)
            resolved.append(get_tool(t))
        else:
            name = getattr(t, "name", None)
            if name and name in seen:
                continue
            if name:
                seen.add(name)
            resolved.append(t)
    resolved.sort(key=lambda t: _CANONICAL_ORDER.get(getattr(t, "name", ""), 1 << 20))
    return resolved


def sanitize_companion_dict(
    payload: dict[str, Any],
    *,
    field_type_fn=companion_field_type,
    tools: Sequence[Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Field-aware sanitize over an inlined ctx/trail companion dict (#143 move 5).

    A sibling of :func:`sanitize_dict` — deliberately NOT an overload — so the
    ``TraceRecord``-dict path and the companion-dict path stay two legible,
    named functions. It walks ``payload`` leaf-by-leaf (via ``walk_dict_strings``,
    which threads a dotted path per leaf), calls ``field_type_fn(path)`` per leaf
    to get the ``FieldType`` + is-path-leaf flag, runs the floor Detectors at
    that field type, and — mirroring ``dataset_rows.sanitize_dataset_row`` — runs
    ``path_anonymizer``'s tail-consuming per-string transform over every leaf so
    a home-path tail embedded in message prose or authored code (not just a
    ``cwd``/``file_path`` leaf) is neutralized.

    Returns ``(redacted, manifest)``; the manifest is the counts+types sidecar
    (``tools_applied`` + per-tool patches + ``home_paths_scrubbed``), never a
    literal secret. ``tools`` defaults to :data:`COMPANION_FLOOR`; a caller may
    ADD an availability-gated NER (``privacy_filter`` / ``llm_pii``) instance.
    The ``classifier`` Judge is never in the detector loop (it never mutates).
    """
    tool_list = list(tools) if tools is not None else list(COMPANION_FLOOR)
    resolved = _resolve_companion_tools(tool_list)
    detectors = [t for t in resolved if isinstance(t, Detector)]
    anon = next((t for t in resolved if getattr(t, "name", None) == "path_anonymizer"), None)

    tool_findings: dict[str, list[Finding]] = {}
    tool_redactions: dict[str, int] = {}
    for det in detectors:
        tool_findings.setdefault(det.name, [])
        tool_redactions.setdefault(det.name, 0)
    path_scrubbed = [0]

    def _leaf(text: str, path: FieldPath, _ft_default: FieldType) -> str:
        ft, _is_path_leaf = field_type_fn(path)
        current = text
        for det in detectors:
            spans = det.find(current, ft)
            if not spans:
                continue
            for s in spans:
                tool_findings[det.name].append(
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
            tool_redactions[det.name] += len(spans)
            current = redact_spans(current, ((s.start, s.end) for s in spans))
        if anon is not None:
            new = anon.transform_text(current, consume_tail=True)
            if new != current:
                path_scrubbed[0] += 1
                current = new
        return current

    redacted, _changes = walk_dict_strings(payload, _leaf)
    redacted = redacted if isinstance(redacted, dict) else dict(payload)

    tools_applied = [d.name for d in detectors]
    if anon is not None:
        tools_applied.append("path_anonymizer")

    all_findings = [f for fl in tool_findings.values() for f in fl]
    manifest = {
        "schema_version": COMPANION_MANIFEST_SCHEMA_VERSION,
        "security_version": SECURITY_VERSION,
        "floor": list(COMPANION_FLOOR),
        "tools_applied": tools_applied,
        "floor_satisfied": set(COMPANION_FLOOR).issubset(set(tools_applied)),
        "redactions_applied": sum(tool_redactions.values()),
        "findings_total": len(all_findings),
        "home_paths_scrubbed": path_scrubbed[0],
        "by_tool": {name: len(fl) for name, fl in tool_findings.items() if fl},
    }
    return redacted, manifest


def list_tools(cfg: Any = None) -> list[ToolInfo]:
    """Return per-tool descriptor list in canonical registry order."""
    return [tool.describe(cfg) for tool in iter_tools()]


__all__ = [
    "COMPANION_FLOOR",
    "COMPANION_MANIFEST_SCHEMA_VERSION",
    "PipelineReport",
    "companion_field_type",
    "list_tools",
    "run_tools",
    "sanitize_companion_dict",
    "sanitize_dict",
    "sanitize_record",
    "sanitize_text",
]
