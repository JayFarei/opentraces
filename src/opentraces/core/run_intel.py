"""Deterministic run-intelligence annotations (plan 086, extracted from tracebase).

Walks a trace's steps in order and emits per-step ``resteer`` / ``recovery`` /
``loop`` signals (plus the ``failure`` signals that gate recovery), with no LLM.
The pattern lists below are first-party, reviewable constants modelled on
tracebase ``src/analyze.js::analyzeSessionEvents`` (the tracebase source is not
vendored here, so these stand on their own merits, like the DEFAULT_BURST_GAP
precedent in core/bursts.py).

This is a pure, derive-on-demand projection over a loaded :class:`TraceRecord`.
It adds NO schema field and does NOT widen the closed ``TraceMapNode.action_type``
Literal: signals are an additive sidecar list keyed by the existing node-id
scheme, surfaced only through the CLI envelope ``{status, trace_id, signals,
counts}``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from opentraces_schema import TraceRecord

from .intent import is_trigger
from .trace_map import _preview

# --- Pattern constants (module-level so the contract is observable) ----------

# Failure detection. Structured signals (Observation.error, is_error, exit
# code) are authoritative; the text fallback below is deliberately restricted
# to failure-SPECIFIC phrases, NOT generic words like "failed"/"exception"/
# "error:"/"timeout" which fire on benign prose ("the old approach failed to
# scale", "we handle this exception", "set a 30s timeout"). Plan 086 eval
# finding: loose substring matching was the detector's main precision leak.
FAILURE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bis_error[\"']?\s*:\s*true\b", re.IGNORECASE),
    re.compile(r"\bexit code\s+[1-9]\d*\b", re.IGNORECASE),
    re.compile(r"\bexited with (?:code|status)\s+[1-9]\d*\b", re.IGNORECASE),
    re.compile(r"\bcommand failed\b", re.IGNORECASE),
    re.compile(r"\bno such file or directory\b", re.IGNORECASE),
    re.compile(r"\bpermission denied\b", re.IGNORECASE),
    re.compile(r"\btraceback\b", re.IGNORECASE),
    re.compile(r"\bsegmentation fault\b", re.IGNORECASE),
    re.compile(r"\bfatal:", re.IGNORECASE),
    re.compile(r"\b\d+ failed\b", re.IGNORECASE),  # pytest "1 failed", "3 failed"
    re.compile(r"\bbuild failed\b", re.IGNORECASE),
    re.compile(r"\btest(?:s)? failed\b", re.IGNORECASE),
]
_FAILURE_HIGH = [
    re.compile(r"\bpermission denied\b", re.IGNORECASE),
    re.compile(r"\btraceback\b", re.IGNORECASE),
    re.compile(r"\bsegmentation fault\b", re.IGNORECASE),
]
_FAILURE_STRONG = [
    re.compile(r"\bis_error[\"']?\s*:\s*true\b", re.IGNORECASE),
    re.compile(r"\bexit code\s+[1-9]\d*\b", re.IGNORECASE),
    re.compile(r"\bexited with (?:code|status)\s+[1-9]\d*\b", re.IGNORECASE),
]

# Resteer (user corrections). 16 phrases.
RESTEER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"try again",
        r"\bactually\b",
        r"you missed",
        r"not what i asked",
        r"\bwrong\b",
        r"fix this",
        r"fix and rerun",
        r"\brerun\b",
        r"new goal",
        r"\bredo\b",
        r"\binstead\b",
        r"that'?s not",
        r"you forgot",
        r"\bstop\b",
        r"\bno[,. ]",
        r"make this",
    )
]

# Recovery (success markers). 7 phrases; only counts after an open failure.
RECOVERY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bexit code 0\b",
        r"\btests? passed\b",
        r"\bbuild succeeded\b",
        r"\bcompiled successfully\b",
        r"\bsmoke ok\b",
        r"\bdone\b",
        r"\bsuccess\b",
    )
]
_RECOVERY_STRONG = [
    re.compile(r"\bexit code 0\b", re.IGNORECASE),
    re.compile(r"\btests? passed\b", re.IGNORECASE),
    re.compile(r"\bsmoke ok\b", re.IGNORECASE),
]

LOOP_MIN_REPEATS = 3
LOOP_WINDOW_STEPS = 120
LOOP_WINDOW_SECONDS = 600
FINGERPRINT_MAX_CHARS = 240
RUN_INTEL_SCHEMA_VERSION = "opentraces.run_intel.v1"


def _any(patterns: list[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


@dataclass
class RunSignal:
    kind: str  # resteer | recovery | loop | failure
    severity: str  # info | medium | high
    confidence: float
    reason: str
    step_index: int | None
    node_id: str | None
    matched_text: str
    evidence_ref: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "confidence": self.confidence,
            "reason": self.reason,
            "step_index": self.step_index,
            "node_id": self.node_id,
            "matched_text": self.matched_text,
            "evidence_ref": self.evidence_ref,
            "evidence": self.evidence,
        }


@dataclass
class RunIntelReport:
    trace_id: str
    fidelity: str
    signals: list[RunSignal] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"resteer": 0, "recovery": 0, "loop": 0, "failure": 0}
        for s in self.signals:
            out[s.kind] = out.get(s.kind, 0) + 1
        return out

    def to_envelope(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_INTEL_SCHEMA_VERSION,
            "status": "ok",
            "trace_id": self.trace_id,
            "fidelity": self.fidelity,
            "signals": [s.to_dict() for s in self.signals],
            "counts": self.counts(),
        }


def _fingerprint(command: str) -> str:
    return " ".join(command.split()).lower()[:FINGERPRINT_MAX_CHARS]


def _parse_epoch(ts: str | None) -> float | None:
    from ..enrichment.metrics import _parse_timestamp

    parsed = _parse_timestamp(ts or "")
    return parsed.timestamp() if parsed else None


def _detect_fidelity(record: TraceRecord) -> str:
    summary = getattr(record, "context_tree_summary", None)
    if isinstance(summary, dict):
        methods = summary.get("capture_methods") or []
        if isinstance(methods, (list, tuple)) and "otel" in methods:
            return "otel"
    return "record"


def detect_run_signals(record: TraceRecord) -> RunIntelReport:
    """Deterministically detect resteer/recovery/loop/failure signals."""

    trace_id = record.trace_id
    signals: list[RunSignal] = []
    last_failure_node: str | None = None
    # fingerprint -> ordered list of (step_index, epoch|None, tool_call_id, command)
    command_occurrences: dict[str, list[tuple[int, float | None, str, str]]] = {}

    for step in sorted(record.steps, key=lambda s: s.step_index):
        epoch = _parse_epoch(step.timestamp)

        # (resteer) user-role corrections, suppressing pure authorizations.
        if step.role == "user" and step.content:
            text = step.content
            if not is_trigger(text) and _any(RESTEER_PATTERNS, text):
                node_id = f"tu:{trace_id}:step:{step.step_index}"
                signals.append(
                    RunSignal(
                        kind="resteer",
                        severity="medium",
                        confidence=0.85,
                        reason="User redirected the agent (correction phrase).",
                        step_index=step.step_index,
                        node_id=node_id,
                        matched_text=_preview(text),
                        evidence_ref=f"signal:{trace_id}:step:{step.step_index}:resteer",
                    )
                )

        # (failure / recovery) over observations. Structured Observation.error
        # is authoritative; text patterns are the restricted fallback.
        for obs in step.observations:
            text = obs.content or obs.output_summary or obs.error or ""
            node_id = f"tu:{trace_id}:observation:{obs.source_call_id}"
            structured_error = bool(obs.error and str(obs.error).strip())

            # Structured tool error -> failure (highest-trust path).
            if structured_error:
                signals.append(
                    RunSignal(
                        kind="failure",
                        severity="high",
                        confidence=0.9,
                        reason="Tool reported a structured error.",
                        step_index=step.step_index,
                        node_id=node_id,
                        matched_text=_preview(str(obs.error)),
                        evidence_ref=f"signal:{trace_id}:observation:{obs.source_call_id}:failure",
                        evidence={"source": "observation_error"},
                    )
                )
                last_failure_node = node_id
                continue

            # recovery, gated on a failure opened by an EARLIER observation
            if last_failure_node is not None and _any(RECOVERY_PATTERNS, text):
                strong = _any(_RECOVERY_STRONG, text)
                signals.append(
                    RunSignal(
                        kind="recovery",
                        severity="info",
                        confidence=0.85 if strong else 0.55,
                        reason="Success signal observed after a prior failure.",
                        step_index=step.step_index,
                        node_id=node_id,
                        matched_text=_preview(text),
                        evidence_ref=f"signal:{trace_id}:observation:{obs.source_call_id}:recovery",
                    )
                )
                last_failure_node = None
                continue

            if _any(FAILURE_PATTERNS, text):
                severity = "high" if _any(_FAILURE_HIGH, text) else "medium"
                confidence = 0.95 if _any(_FAILURE_STRONG, text) else 0.65
                signals.append(
                    RunSignal(
                        kind="failure",
                        severity=severity,
                        confidence=confidence,
                        reason="Failure signal in tool output.",
                        step_index=step.step_index,
                        node_id=node_id,
                        matched_text=_preview(text),
                        evidence_ref=f"signal:{trace_id}:observation:{obs.source_call_id}:failure",
                        evidence={"source": "text_pattern"},
                    )
                )
                last_failure_node = node_id

        # (loop) gather command occurrences; emit ONE signal per run below.
        for call in step.tool_calls:
            command = str(call.input.get("command") or "")
            if not command:
                continue
            fp = _fingerprint(command)
            command_occurrences.setdefault(fp, []).append(
                (step.step_index, epoch, call.tool_call_id, command)
            )

    signals.extend(_loop_signals(trace_id, command_occurrences))

    # Deterministic, reader-friendly ordering: by step then kind.
    signals.sort(key=lambda s: (s.step_index if s.step_index is not None else -1, s.kind))
    return RunIntelReport(trace_id=trace_id, fidelity=_detect_fidelity(record), signals=signals)


def _within_loop_window(first, cur) -> bool:
    """Whether ``cur`` is inside the detection window anchored at ``first``."""
    first_step, first_epoch, _, _ = first
    cur_step, cur_epoch, _, _ = cur
    if first_epoch is not None and cur_epoch is not None:
        return cur_epoch - first_epoch <= LOOP_WINDOW_SECONDS
    return cur_step - first_step <= LOOP_WINDOW_STEPS


def _build_loop_signal(trace_id: str, run) -> RunSignal:
    anchor = run[LOOP_MIN_REPEATS - 1]  # the occurrence that became a loop
    anchor_step, _, anchor_call, anchor_command = anchor
    return RunSignal(
        kind="loop",
        severity="medium",
        confidence=0.75,
        reason=f"Same command repeated {len(run)} times in a loop window.",
        step_index=anchor_step,
        node_id=f"tu:{trace_id}:tool:{anchor_call}",
        matched_text=_preview(anchor_command),
        evidence_ref=f"signal:{trace_id}:tool:{anchor_call}:loop",
        evidence={
            "repeat_count": len(run),
            "first_step": run[0][0],
            "last_step": run[-1][0],
        },
    )


def _loop_signals(trace_id: str, occurrences_by_fp) -> list[RunSignal]:
    """One loop signal per run that crosses LOOP_MIN_REPEATS inside one window.

    Plan 086 eval finding: the old per-occurrence emission produced N-2 loop
    signals for N repeats. We instead segment each fingerprint's occurrences
    into true sliding-window runs and emit a single signal per run, anchored at
    the occurrence that crossed the threshold, carrying the full repeat_count.
    """
    out: list[RunSignal] = []
    for _fp, occ in occurrences_by_fp.items():
        occ = sorted(occ, key=lambda o: o[0])
        if not occ:
            continue

        recent = []
        active_run = None
        for cur in occ:
            recent = [item for item in recent if _within_loop_window(item, cur)]
            recent.append(cur)

            if active_run is not None:
                if len(recent) > 1:
                    active_run.append(cur)
                    continue
                out.append(_build_loop_signal(trace_id, active_run))
                active_run = None

            if len(recent) >= LOOP_MIN_REPEATS:
                active_run = list(recent)

        if active_run is not None:
            out.append(_build_loop_signal(trace_id, active_run))
    return out
