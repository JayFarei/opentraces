"""Tests for the ``openai/privacy-filter`` BERT-NER detector tool.

Unit tests use a stub model so no HF download / transformers install is
required. The opt-in live test (gated by ``OT_REAL_PRIVACY_FILTER=1``)
exercises the actual model end-to-end.
"""

from __future__ import annotations

import os

import pytest

from opentraces_schema import TraceRecord
from opentraces_schema.models import Agent, Step

from opentraces.core.config import Config
from opentraces.security import sanitize_record
from opentraces.security.privacy_filter import PrivacyFilterModel, PrivacyFilterSpan
from opentraces.security.tools import ToolContext
from opentraces.security.tools.privacy_filter_tool import PrivacyFilterDetector


class _StubModel(PrivacyFilterModel):
    """In-memory model that emits a fixed list of spans for a known string."""

    def __init__(self, fixtures: dict[str, list[PrivacyFilterSpan]]) -> None:
        super().__init__(model_name="stub", score_threshold=0.0)
        self._fixtures = fixtures
        self._available = True

    def is_available(self) -> bool:  # type: ignore[override]
        return True

    def detect(  # type: ignore[override]
        self,
        text: str,
        *,
        score_threshold: float | None = None,  # noqa: ARG002
    ) -> list[PrivacyFilterSpan]:
        return list(self._fixtures.get(text, []))


def _record(description: str = "", step_content: str = "") -> TraceRecord:
    rec = TraceRecord(
        trace_id="t-privacy-filter",
        session_id="s-privacy-filter",
        agent=Agent(name="test"),
    )
    if description:
        rec.task.description = description
    if step_content:
        rec.steps = [Step(step_index=0, role="agent", content=step_content)]
    return rec


# ---------------------------------------------------------------------------
# Tool — disabled / unavailable paths
# ---------------------------------------------------------------------------


class TestDisabledByDefault:
    def test_enabled_returns_false_for_bare_config(self) -> None:
        assert PrivacyFilterDetector().enabled(Config()) is False

    def test_apply_short_circuits_when_disabled(self) -> None:
        rec = _record(description="hi there")
        result = PrivacyFilterDetector().apply(rec, ToolContext(cfg=Config()))
        assert result.error is not None
        assert "unavailable" in result.error
        assert rec.task.description == "hi there"


# ---------------------------------------------------------------------------
# Tool — with stub model
# ---------------------------------------------------------------------------


class TestStubbedDetection:
    def test_detects_and_redacts_known_entity(self) -> None:
        text = "Alice met Bob in Paris"
        stub = _StubModel({
            text: [
                PrivacyFilterSpan(entity_type="PERSON", matched_text="Alice", start=0, end=5, score=0.99),
                PrivacyFilterSpan(entity_type="PERSON", matched_text="Bob", start=10, end=13, score=0.97),
            ],
        })
        tool = PrivacyFilterDetector(model=stub)
        rec = _record(description=text)
        cfg = Config()
        cfg.security.privacy_filter.enabled = True
        result = tool.apply(rec, ToolContext(cfg=cfg))

        assert result.error is None
        assert result.redactions_applied == 2
        assert rec.task.description == "[REDACTED] met [REDACTED] in Paris"
        patterns = {f.pattern for f in result.findings}
        assert patterns == {"PERSON"}

    def test_no_entities_returns_clean_result(self) -> None:
        text = "plain prose"
        stub = _StubModel({text: []})
        tool = PrivacyFilterDetector(model=stub)
        rec = _record(description=text)
        cfg = Config()
        cfg.security.privacy_filter.enabled = True
        result = tool.apply(rec, ToolContext(cfg=cfg))
        assert result.error is None
        assert result.redactions_applied == 0
        assert rec.task.description == text


# ---------------------------------------------------------------------------
# Pipeline integration via sanitize_record
# ---------------------------------------------------------------------------


class TestPipelineIntegration:
    def test_pipeline_runs_after_regex(self) -> None:
        """A trace containing both an AWS key and a person name should have
        the AWS key redacted by regex and the name redacted by privacy_filter.
        Tools run in canonical order, so when privacy_filter sees the text
        the AWS key is already gone."""
        text = "AKIAIOSFODNN7EXAMPLE was used by Alice"

        class CapturingStub(_StubModel):
            seen: list[str] = []

            def detect(self, t: str, *, score_threshold=None):  # type: ignore[override] # noqa: ARG002
                CapturingStub.seen.append(t)
                if "Alice" in t:
                    idx = t.find("Alice")
                    return [PrivacyFilterSpan(
                        entity_type="PERSON",
                        matched_text="Alice",
                        start=idx,
                        end=idx + 5,
                        score=0.99,
                    )]
                return []

        # Inject the stub via the tool instance in the registry.
        from opentraces.security.tools import _registry

        original = _registry.get("privacy_filter")
        stub_tool = PrivacyFilterDetector(model=CapturingStub({}))
        new_tools = tuple(
            stub_tool if t.name == "privacy_filter" else t
            for t in _registry._TOOLS
        )
        _registry._TOOLS = new_tools  # type: ignore[assignment]
        try:
            cfg = Config()
            cfg.security.privacy_filter.enabled = True
            rec = _record(description=text)
            rec, report = sanitize_record(rec, cfg=cfg)
            # AWS key is gone (regex), Alice is gone (privacy_filter).
            assert "AKIA" not in rec.task.description
            assert "Alice" not in rec.task.description
            assert "privacy_filter" in report.tools_applied
            assert report.tool_metadata("privacy_filter")["findings_count"] >= 1
            # By the time privacy_filter sees the text, the AWS key has
            # been replaced with [REDACTED] — confirms canonical ordering.
            assert any("[REDACTED]" in t for t in CapturingStub.seen)
        finally:
            _registry._TOOLS = tuple(
                original if t.name == "privacy_filter" else t
                for t in _registry._TOOLS
            )


# ---------------------------------------------------------------------------
# Opt-in live test — requires transformers + real model download
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("OT_REAL_PRIVACY_FILTER") != "1",
    reason="Live HF model test; set OT_REAL_PRIVACY_FILTER=1 to enable.",
)
class TestLive:
    def test_live_detects_obvious_person_name(self) -> None:
        model = PrivacyFilterModel()
        spans = model.detect("My name is Barack Obama and I live in Hawaii.")
        assert any(s.entity_type.startswith("PER") or s.entity_type == "PERSON" for s in spans)
