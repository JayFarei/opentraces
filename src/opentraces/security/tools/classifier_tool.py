"""Content-classifier judge wrapping ``classify_trace_record``."""

from __future__ import annotations

from typing import Any

from opentraces_schema import TraceRecord

from . import ToolContext, ToolInfo, ToolResult, Verdict, cfg_block
from ..classifier import classify_trace_record


class ClassifierJudge:
    name = "classifier"
    display_name = "Content classifier"
    kind = "judge"

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        if block is None:
            return False
        return bool(getattr(block, "enabled", False))

    def _sensitivity(self, cfg: Any) -> str:
        block = cfg_block(cfg, self.name)
        if block is not None:
            return getattr(block, "sensitivity", "medium")
        return "medium"

    def judge(self, record: TraceRecord, ctx: ToolContext) -> Verdict:
        sensitivity = self._sensitivity(ctx.cfg)
        result = classify_trace_record(record, sensitivity)
        # Artifact safety (plan 090 U0): never surface the literal hit. ``matched_text``
        # IS the sensitive value, and ``reason`` can echo matched content for some
        # patterns — keep only the non-sensitive {pattern, severity}. No consumer reads
        # the dropped fields: flag counts are taken via ``len`` only (core/pipeline.py
        # ``_classifier_flag_count``, security/pipeline.py ``flags_reviewed``, and this
        # tool's own ``apply``), and the web review UI reads a separate
        # ``_security_flags`` structure, not this verdict payload.
        flag_payload = [
            {
                "pattern": f.pattern_name,
                "severity": f.severity,
            }
            for f in result.flags
        ]
        decision = "flagged" if result.flags else "clean"
        summary = (
            f"{len(result.flags)} flag(s), risk {result.risk_score:.2f}"
            if result.flags
            else "no classifier flags"
        )
        return Verdict(
            name=self.name,
            summary=summary,
            decision=decision,
            payload={
                "flags": flag_payload,
                "risk_score": result.risk_score,
                "sensitivity": sensitivity,
            },
        )

    def apply(self, record: TraceRecord, ctx: ToolContext) -> ToolResult:
        verdict = self.judge(record, ctx)
        flags = verdict.payload.get("flags") or []
        return ToolResult(
            name=self.name,
            kind=self.kind,
            verdict=verdict,
            metadata_patch={
                "decision": verdict.decision,
                "summary": verdict.summary,
                "flags_count": len(flags),
                "risk_score": verdict.payload.get("risk_score", 0.0),
                "sensitivity": verdict.payload.get("sensitivity", "medium"),
            },
        )

    def describe(self, cfg: Any) -> ToolInfo:
        is_on = self.enabled(cfg)
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=is_on,
            state="enabled" if is_on else "disabled",
            detail=f"sensitivity={self._sensitivity(cfg)}",
            setup_cmd=None,
            disable_cmd=None,
        )
