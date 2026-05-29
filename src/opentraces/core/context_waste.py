"""Deterministic context-waste detection (plan 086, extracted from tracebase).

Detects three patterns that burn agent context without clearly moving the
task forward, over a single trace's steps:

* ``large_output``        — a single tool output >= 12000 chars
* ``repeated_file_read``  — the same file read >= 3 times within 20 minutes
* ``repeated_search``     — a search command (rg|grep|find|ag|ack) repeated
                            >= 5 times within 10 minutes

The thresholds, severities, and confidences match tracebase
(``src/analyze.js::classifyWaste``) verbatim. This is a pure, derive-on-demand
projection over a loaded :class:`TraceRecord` — nothing is persisted and no
schema field is added. The detector derives from the bare record (always
present); when the trace was captured via OTel (``capture_method=otel``, plan
078) the record carries full wire bodies, so output char-counts are exact and
``fidelity`` is reported as ``"otel"``; otherwise ``fidelity`` is ``"record"``
and the char-counts are best-effort over whatever the record retained.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from opentraces_schema import TraceRecord

from ..enrichment.metrics import _parse_timestamp
from .trace_map import _files_for_tool, _preview, _tool_action_type

# Thresholds — module constants so the contract is observable/tunable, exactly
# mirroring the DEFAULT_BURST_GAP precedent in core/bursts.py.
LARGE_OUTPUT_CHARS = 12000
FILE_READ_WINDOW_MINUTES = 20
FILE_READ_REPEAT_THRESHOLD = 3
SEARCH_WINDOW_MINUTES = 10
SEARCH_REPEAT_THRESHOLD = 5

# tracebase isSearchCommand: /\b(rg|grep|find|ag|ack)\b/
_SEARCH_RE = re.compile(r"\b(rg|grep|find|ag|ack)\b")

CONTEXT_WASTE_SCHEMA_VERSION = "opentraces.context_waste.v1"


@dataclass
class ContextWasteFinding:
    """One detected context-waste occurrence."""

    finding_id: str
    pattern: str  # large_output | repeated_file_read | repeated_search
    severity: str  # low | medium
    confidence: float
    reason: str
    step_index: int | None
    node_id: str | None
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "kind": "context_waste",
            "pattern": self.pattern,
            "severity": self.severity,
            "confidence": self.confidence,
            "reason": self.reason,
            "step_index": self.step_index,
            "node_id": self.node_id,
            "evidence": self.evidence,
        }


@dataclass
class ContextWasteReport:
    """Frozen ``opentraces.context_waste.v1`` projection for a trace."""

    trace_id: str
    thresholds: dict[str, int]
    findings: list[ContextWasteFinding]
    fidelity: str  # record | otel
    limitations: list[str] = field(default_factory=list)

    def _summary(self) -> dict[str, int]:
        by_pattern = {"large_output": 0, "repeated_file_read": 0, "repeated_search": 0}
        for f in self.findings:
            by_pattern[f.pattern] = by_pattern.get(f.pattern, 0) + 1
        return {
            "context_waste_count": len(self.findings),
            "large_output_count": by_pattern["large_output"],
            "repeated_file_read_count": by_pattern["repeated_file_read"],
            "repeated_search_count": by_pattern["repeated_search"],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_WASTE_SCHEMA_VERSION,
            "trace_id": self.trace_id,
            "fidelity": self.fidelity,
            "thresholds": self.thresholds,
            "findings": [f.to_dict() for f in self.findings],
            "summary": self._summary(),
            "limitations": self.limitations,
        }


def _finding_id(trace_id: str, step_index: int | None, node_id: str | None, pattern: str) -> str:
    raw = f"{trace_id}:{step_index}:{node_id}:{pattern}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _detect_fidelity(record: TraceRecord) -> str:
    summary = getattr(record, "context_tree_summary", None)
    if isinstance(summary, dict):
        methods = summary.get("capture_methods") or []
        if isinstance(methods, (list, tuple)) and "otel" in methods:
            return "otel"
    return "record"


def detect_context_waste(
    record: TraceRecord,
    *,
    large_output_chars: int = LARGE_OUTPUT_CHARS,
    file_read_window_minutes: int = FILE_READ_WINDOW_MINUTES,
    search_window_minutes: int = SEARCH_WINDOW_MINUTES,
) -> ContextWasteReport:
    """Deterministically detect context-waste patterns in ``record``."""

    trace_id = record.trace_id
    findings: list[ContextWasteFinding] = []
    limitations: list[str] = []

    # State: sliding windows keyed by epoch seconds (tracebase uses ms windows).
    file_reads: dict[str, list[float]] = {}
    searches: list[float] = []

    # Global tool_name lookup for large_output evidence (an observation's
    # source_call_id may name a tool_call from an earlier step).
    tool_name_by_call: dict[str, str] = {}
    for step in record.steps:
        for call in step.tool_calls:
            tool_name_by_call[call.tool_call_id] = call.tool_name

    file_window_s = file_read_window_minutes * 60
    search_window_s = search_window_minutes * 60
    missing_ts = False
    last_clock: float = 0.0

    for step in sorted(record.steps, key=lambda s: s.step_index):
        parsed = _parse_timestamp(step.timestamp or "")
        if parsed is None:
            # Degenerate-but-deterministic fallback (plan 086): reuse the last
            # known clock so windowed checks still run; flag reduced fidelity.
            now = last_clock
            if step.timestamp is not None or step.tool_calls:
                missing_ts = True
        else:
            now = parsed.timestamp()
            last_clock = now

        # (a) LARGE OUTPUT — per observation in this step.
        for obs in step.observations:
            text = obs.content or obs.output_summary or ""
            output_chars = len(text)
            if output_chars >= large_output_chars:
                node_id = f"tu:{trace_id}:observation:{obs.source_call_id}"
                findings.append(
                    ContextWasteFinding(
                        finding_id=_finding_id(trace_id, step.step_index, node_id, "large_output"),
                        pattern="large_output",
                        severity="medium",
                        confidence=0.8,
                        reason="Tool output was large enough to risk wasting context.",
                        step_index=step.step_index,
                        node_id=node_id,
                        evidence={
                            "output_chars": output_chars,
                            "source_call_id": _preview(obs.source_call_id),
                            "tool_name": (
                                _preview(tool_name_by_call[obs.source_call_id])
                                if obs.source_call_id in tool_name_by_call
                                else None
                            ),
                        },
                    )
                )

        for call in step.tool_calls:
            node_id = f"tu:{trace_id}:tool:{call.tool_call_id}"
            action_type = _tool_action_type(call.tool_name, call.input)

            # (b) REPEATED FILE READ.
            if action_type == "file_read":
                for path in _files_for_tool(call.tool_name, call.input, read=True):
                    history = file_reads.setdefault(path, [])
                    in_window = [t for t in history if now - t <= file_window_s]
                    if len(in_window) >= FILE_READ_REPEAT_THRESHOLD - 1:
                        findings.append(
                            ContextWasteFinding(
                                finding_id=_finding_id(
                                    trace_id, step.step_index, node_id, "repeated_file_read"
                                ),
                                pattern="repeated_file_read",
                                severity="medium",
                                confidence=0.78,
                                reason="Same file was read at least three times within twenty minutes.",
                                step_index=step.step_index,
                                node_id=node_id,
                                evidence={
                                    "file_path": _preview(path),
                                    "read_count": len(in_window) + 1,
                                    "window_minutes": file_read_window_minutes,
                                },
                            )
                        )
                    in_window.append(now)
                    file_reads[path] = in_window

            # (c) REPEATED SEARCH.
            command = str(call.input.get("command") or "")
            match = _SEARCH_RE.search(command)
            if match:
                in_window = [t for t in searches if now - t <= search_window_s]
                if len(in_window) >= SEARCH_REPEAT_THRESHOLD - 1:
                    findings.append(
                        ContextWasteFinding(
                            finding_id=_finding_id(
                                trace_id, step.step_index, node_id, "repeated_search"
                            ),
                            pattern="repeated_search",
                            severity="low",
                            confidence=0.7,
                            reason="Search commands repeated at least five times within ten minutes.",
                            step_index=step.step_index,
                            node_id=node_id,
                            evidence={
                                "command_family": _preview(match.group(1)),
                                "search_count": len(in_window) + 1,
                                "window_minutes": search_window_minutes,
                            },
                        )
                    )
                in_window.append(now)
                searches = in_window

    if missing_ts:
        limitations.append("missing_timestamps_window_checks_approximate")

    return ContextWasteReport(
        trace_id=trace_id,
        thresholds={
            "large_output_chars": large_output_chars,
            "file_read_window_minutes": file_read_window_minutes,
            "file_read_repeat_threshold": FILE_READ_REPEAT_THRESHOLD,
            "search_window_minutes": search_window_minutes,
            "search_repeat_threshold": SEARCH_REPEAT_THRESHOLD,
        },
        findings=findings,
        fidelity=_detect_fidelity(record),
        limitations=limitations,
    )
