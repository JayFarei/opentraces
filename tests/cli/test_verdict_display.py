"""Tests for shared verdict / entity renderers consumed by CLI, TUI, web."""

from __future__ import annotations

from opentraces.security.anonymizer import EntityMap
from opentraces.security.llm_review import LLMReviewVerdict
from opentraces.security.verdict_display import (
    entity_counts,
    entity_summary_line,
    verdict_badge,
    verdict_summary_lines,
    verdict_to_payload,
)


class TestVerdictBadge:
    def test_none_badge(self) -> None:
        assert "not run" in verdict_badge(None)

    def test_clean_badge(self) -> None:
        v = LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")
        assert "shareable" in verdict_badge(v)

    def test_blocked_badge(self) -> None:
        v = LLMReviewVerdict(shareable="no", missed_sensitive_data="no")
        assert "blocked" in verdict_badge(v)

    def test_missed_yes_is_blocked(self) -> None:
        v = LLMReviewVerdict(shareable="yes", missed_sensitive_data="yes")
        assert "blocked" in verdict_badge(v)

    def test_manual_review_badge(self) -> None:
        v = LLMReviewVerdict(shareable="manual_review", missed_sensitive_data="maybe")
        assert "manual" in verdict_badge(v)


class TestVerdictSummaryLines:
    def test_none_has_message(self) -> None:
        out = verdict_summary_lines(None)
        assert any("not run" in line for line in out)

    def test_flagged_evidence_truncated(self) -> None:
        long = "x" * 400
        v = LLMReviewVerdict(
            shareable="no",
            missed_sensitive_data="yes",
            flagged_parts=[{"reason": "long", "evidence": long}],
            summary="short",
        )
        out = "\n".join(verdict_summary_lines(v))
        assert "..." in out
        assert "x" * 400 not in out


class TestEntityCounts:
    def test_none_is_empty(self) -> None:
        assert entity_counts(None) == {}

    def test_counts_per_type(self) -> None:
        em = EntityMap()
        em.get_placeholder("PERSON", "Alice")
        em.get_placeholder("PERSON", "Bob")
        em.get_placeholder("EMAIL", "a@b.com")
        assert entity_counts(em) == {"PERSON": 2, "EMAIL": 1}

    def test_summary_line_empty(self) -> None:
        assert "no PII" in entity_summary_line(None)

    def test_summary_line_populated(self) -> None:
        em = EntityMap()
        em.get_placeholder("PERSON", "Alice")
        em.get_placeholder("PERSON", "Bob")
        em.get_placeholder("EMAIL", "a@b.com")
        line = entity_summary_line(em)
        assert "2 PERSON" in line
        assert "1 EMAIL" in line


class TestVerdictPayload:
    def test_none_payload(self) -> None:
        assert verdict_to_payload(None) == {"status": "not_run"}

    def test_populated_payload(self) -> None:
        v = LLMReviewVerdict(
            shareable="yes",
            missed_sensitive_data="no",
            summary="ok",
            flagged_parts=[{"reason": "x", "evidence": "y"}],
        )
        payload = verdict_to_payload(v)
        assert payload["status"] == "complete"
        assert payload["shareable"] == "yes"
        assert payload["flagged_parts"] == [{"reason": "x", "evidence": "y"}]
        assert "badge" in payload
