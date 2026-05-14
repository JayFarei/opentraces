"""TruffleHog subprocess detector wrapping the vendor binary."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord

from . import Finding, ToolContext, ToolInfo, ToolResult, cfg_block
from ..walker import FieldType, locate_substrings, redact_spans, walk_string_fields
from ..trufflehog import (
    TruffleHogMissingError,
    TruffleHogScanError,
    find_trufflehog,
    scan_trace_jsonl,
)

logger = logging.getLogger(__name__)


class TruffleHogDetector:
    name = "trufflehog"
    display_name = "TruffleHog"
    kind = "detector"

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        return bool(getattr(block, "enabled", False)) if block else False

    def apply(self, record: TraceRecord, ctx: ToolContext) -> ToolResult:
        block = cfg_block(ctx.cfg, self.name)
        verify = bool(getattr(block, "verify_secrets", False)) if block else False

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", encoding="utf-8", delete=False,
        ) as fh:
            fh.write(record.to_jsonl_line() + "\n")
            tmp_path = Path(fh.name)
        try:
            try:
                report = scan_trace_jsonl(tmp_path, verify=verify)
            except (TruffleHogMissingError, TruffleHogScanError) as exc:
                return ToolResult(
                    name=self.name,
                    kind=self.kind,
                    error=str(exc),
                    metadata_patch={"status": "error", "error": str(exc)},
                )
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

        findings_struct = [
            {
                "detector": f.detector_name,
                "verified": bool(f.verified),
                "line": f.line_number,
                "source_file": f.source_file,
            }
            for f in report.findings
        ]
        metadata_patch: dict[str, Any] = {
            "status": "findings" if report.findings else "clean",
            "version": report.trufflehog_version,
            "scanned_at": report.scanned_at,
            "findings_count": len(report.findings),
            "findings": findings_struct,
            "verify": verify,
        }

        if not report.findings:
            return ToolResult(
                name=self.name,
                kind=self.kind,
                redactions_applied=0,
                metadata_patch=metadata_patch,
                payload=report,
            )

        raw_matches = sorted(
            {(f.raw_match or "").strip() for f in report.findings if (f.raw_match or "").strip()},
            key=len,
            reverse=True,
        )

        recorded: list[Finding] = []
        redaction_counter = [0]

        def _transform(text: str, path: str, _ft: FieldType) -> str:
            located = locate_substrings(text, raw_matches)
            if not located:
                return text
            for rm, start, end in located:
                recorded.append(
                    Finding(
                        tool=self.name,
                        pattern=_detector_for(rm, report.findings),
                        matched_text=rm,
                        start=start,
                        end=end,
                        severity="critical",
                        field_path=path,
                    )
                )
            redaction_counter[0] += len(located)
            return redact_spans(text, [(s, e) for _, s, e in located])

        walk_string_fields(record, _transform)

        final_patch = {**metadata_patch, "redactions_applied": redaction_counter[0]}
        return ToolResult(
            name=self.name,
            kind=self.kind,
            findings=recorded,
            redactions_applied=redaction_counter[0],
            metadata_patch=final_patch,
            payload=report,
        )

    def describe(self, cfg: Any) -> ToolInfo:
        is_on = self.enabled(cfg)
        version = find_trufflehog()
        if not is_on:
            state, detail = "disabled", None
        elif version is None:
            state = "missing"
            detail = "binary not found; run 'opentraces setup trufflehog --enable'"
        else:
            state = "enabled"
            detail = f"{version}"
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=is_on,
            state=state,
            detail=detail,
            setup_cmd="opentraces setup trufflehog",
            disable_cmd="opentraces setup trufflehog --disable",
        )


def _detector_for(raw: str, findings) -> str:
    for f in findings:
        if (f.raw_match or "").strip() == raw:
            return f.detector_name or "trufflehog"
    return "trufflehog"
