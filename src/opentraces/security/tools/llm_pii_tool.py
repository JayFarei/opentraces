"""LLM-driven per-field PII detector wrapping :class:`LLMPIIDetector`."""

from __future__ import annotations

import logging
from typing import Any

from opentraces_schema import TraceRecord

from . import Finding, ToolContext, ToolInfo, ToolResult, cfg_block
from ..walker import FieldType, locate_substrings, redact_spans, walk_string_fields
from ..pii_detector import LLMPIIDetector, PROMPT_VERSION
from ..llm_provider import build_provider

logger = logging.getLogger(__name__)


class LLMPIIDetectorTool:
    name = "llm_pii"
    display_name = "LLM PII"
    kind = "detector"

    def __init__(self) -> None:
        self._cached_detector: LLMPIIDetector | None = None
        self._cached_cfg_id: int | None = None

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        return bool(getattr(block, "enabled", False)) if block else False

    def _build_provider(self, cfg: Any):
        block = cfg_block(cfg, self.name)
        if block is None:
            raise RuntimeError("llm_pii config block missing")
        kwargs: dict[str, Any] = {"timeout": getattr(block, "timeout", 120.0)}
        api_format = getattr(block, "api_format", "openai-compat")
        if api_format == "openai-compat":
            kwargs["base_url"] = getattr(block, "base_url", "http://localhost:11434/v1")
            api_key_env = getattr(block, "api_key_env", "") or ""
            if api_key_env:
                kwargs["api_key_env"] = api_key_env
        return build_provider(api_format, model=getattr(block, "model", "gemma3n:e4b"), **kwargs)

    def _build_detector(self, cfg: Any) -> LLMPIIDetector | None:
        if not self.enabled(cfg):
            return None
        try:
            provider = self._build_provider(cfg)
        except Exception as exc:  # noqa: BLE001
            logger.warning("llm_pii provider build failed: %s", exc)
            return None
        return LLMPIIDetector(provider=provider)

    def _resolve_detector(self, ctx: ToolContext) -> LLMPIIDetector | None:
        """Return the LLMPIIDetector, reusing the cached instance across calls
        with the same cfg (keyed by id) so its per-instance prompt cache
        survives. cfg is process-stable for the run, so id() reuse can't occur.
        """
        cfg_id = id(ctx.cfg) if ctx.cfg is not None else None
        if self._cached_detector is None or self._cached_cfg_id != cfg_id:
            self._cached_detector = self._build_detector(cfg=ctx.cfg)
            self._cached_cfg_id = cfg_id
        return self._cached_detector

    def apply(self, record: TraceRecord, ctx: ToolContext) -> ToolResult:
        det = self._resolve_detector(ctx)
        if det is None:
            return ToolResult(
                name=self.name,
                kind=self.kind,
                error="llm_pii disabled or provider unavailable",
                metadata_patch={"status": "disabled"},
            )

        findings: list[Finding] = []
        redaction_counter = [0]

        def _transform(text: str, path: str, ft: FieldType) -> str:
            ft_label = ft.value if hasattr(ft, "value") else str(ft)
            try:
                pairs = det.detect_for_path(path, text, ft_label, siblings={})
            except Exception as exc:  # noqa: BLE001 — defensive
                logger.warning("llm_pii detect failed at %s: %s", path, exc)
                return text
            if not pairs:
                return text
            spans: list[tuple[int, int]] = []
            for etype, etext in pairs:
                for _, start, end in locate_substrings(text, [etext]):
                    spans.append((start, end))
                    findings.append(
                        Finding(
                            tool=self.name,
                            pattern=etype,
                            matched_text=etext,
                            start=start,
                            end=end,
                            severity="high",
                            field_path=path,
                        )
                    )
            if not spans:
                return text
            redaction_counter[0] += len(spans)
            return redact_spans(text, spans)

        walk_string_fields(record, _transform)

        return ToolResult(
            name=self.name,
            kind=self.kind,
            findings=findings,
            redactions_applied=redaction_counter[0],
            metadata_patch={
                "status": "ran",
                "findings_count": len(findings),
                "redactions_applied": redaction_counter[0],
                "prompt_version": PROMPT_VERSION,
            },
        )

    def with_detector(self, detector: LLMPIIDetector) -> "LLMPIIDetectorTool":
        clone = LLMPIIDetectorTool()
        clone._injected_detector = detector
        return clone

    def describe(self, cfg: Any) -> ToolInfo:
        block = cfg_block(cfg, self.name)
        is_on = self.enabled(cfg)
        if not is_on:
            state, detail = "disabled", None
        else:
            backend = getattr(block, "api_format", "?")
            model = getattr(block, "model", "?")
            state = "enabled"
            detail = f"{backend} / {model}"
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=is_on,
            state=state,
            detail=detail,
            setup_cmd="opentraces setup llm-pii",
            disable_cmd="opentraces setup llm-pii --disable",
        )
