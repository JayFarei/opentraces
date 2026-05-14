"""HuggingFace BERT-NER detector tool wrapping ``openai/privacy-filter``."""

from __future__ import annotations

import logging
from typing import Any

from . import DetectorMixin, Finding, ToolInfo, cfg_block
from ..walker import FieldType
from ..privacy_filter import PrivacyFilterMissingError, PrivacyFilterModel

logger = logging.getLogger(__name__)


class PrivacyFilterDetector(DetectorMixin):
    name = "privacy_filter"
    display_name = "Privacy-filter (HF NER)"

    def __init__(self, model: PrivacyFilterModel | None = None) -> None:
        self._model = model

    def enabled(self, cfg: Any) -> bool:
        block = cfg_block(cfg, self.name)
        return bool(getattr(block, "enabled", False)) if block else False

    def _resolve_model(self, cfg: Any) -> PrivacyFilterModel | None:
        if self._model is not None:
            return self._model
        block = cfg_block(cfg, self.name)
        if block is None:
            return None
        model_name = getattr(block, "model_name", "openai/privacy-filter")
        return PrivacyFilterModel.shared(model_name)

    def _resolve_threshold(self, cfg: Any) -> float:
        block = cfg_block(cfg, self.name)
        if block is None:
            return 0.7
        return float(getattr(block, "score_threshold", 0.7))

    def find(self, text: str, field_type: FieldType) -> list[Finding]:
        model = self._model
        if model is None:
            return []
        try:
            spans = model.detect(text)
        except PrivacyFilterMissingError as exc:
            logger.warning("privacy-filter find skipped: %s", exc)
            return []
        return [
            Finding(
                tool=self.name,
                pattern=span.entity_type,
                matched_text=span.matched_text,
                start=span.start,
                end=span.end,
                severity="high",
            )
            for span in spans
        ]

    def apply(self, record, ctx):
        from . import ToolResult
        from ..walker import redact_spans, walk_string_fields

        model = self._resolve_model(ctx.cfg)
        if model is None or not model.is_available():
            return ToolResult(
                name=self.name,
                kind=self.kind,
                error="privacy-filter unavailable (missing transformers / model)",
                metadata_patch={"status": "unavailable"},
            )

        threshold = self._resolve_threshold(ctx.cfg)
        findings: list[Finding] = []
        counter = [0]

        def _transform(text: str, path: str, _ft: FieldType) -> str:
            spans = model.detect(text, score_threshold=threshold)
            if not spans:
                return text
            for span in spans:
                findings.append(
                    Finding(
                        tool=self.name,
                        pattern=span.entity_type,
                        matched_text=span.matched_text,
                        start=span.start,
                        end=span.end,
                        severity="high",
                        field_path=path,
                    )
                )
            counter[0] += len(spans)
            return redact_spans(text, ((s.start, s.end) for s in spans))

        walk_string_fields(record, _transform)
        return ToolResult(
            name=self.name,
            kind=self.kind,
            findings=findings,
            redactions_applied=counter[0],
            metadata_patch={
                "status": "ran",
                "findings_count": len(findings),
                "redactions_applied": counter[0],
                "model_name": model.model_name,
                "score_threshold": threshold,
            },
        )

    def describe(self, cfg: Any) -> ToolInfo:
        is_on = self.enabled(cfg)
        if not is_on:
            return ToolInfo(
                name=self.name,
                display_name=self.display_name,
                kind=self.kind,
                enabled=False,
                state="disabled",
                detail=None,
                setup_cmd="opentraces setup privacy-filter",
                disable_cmd="opentraces setup privacy-filter --disable",
            )
        try:
            import transformers  # type: ignore  # noqa: F401
            state = "enabled"
            block = cfg_block(cfg, self.name)
            model_name = getattr(block, "model_name", "openai/privacy-filter") if block else "openai/privacy-filter"
            detail = model_name
        except ImportError:
            state = "missing"
            detail = "transformers package not installed; run 'opentraces setup privacy-filter'"
        return ToolInfo(
            name=self.name,
            display_name=self.display_name,
            kind=self.kind,
            enabled=True,
            state=state,
            detail=detail,
            setup_cmd="opentraces setup privacy-filter",
            disable_cmd="opentraces setup privacy-filter --disable",
        )

    def with_model(self, model: PrivacyFilterModel) -> "PrivacyFilterDetector":
        """Test helper: bind a pre-built model and skip auto-construction."""
        return PrivacyFilterDetector(model=model)
