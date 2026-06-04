"""Tests for the BLOCKED status wiring (Step 5 of the CLI restructure).

The BLOCKED enum value (core/state.py:39) and StateManager.block_trace()
(core/state.py:181) exist today but are never written by the parse pipeline.
This step wires them in:

- core/workflow.py: add 'blocked' visible stage; map TraceStatus.BLOCKED -> 'blocked'.
- new helper decide_post_parse_status(result, review_policy) -> (TraceStatus, reason):
    TruffleHog blocked      -> (BLOCKED, reason describing the findings)
    auto + not needs_review -> (COMMITTED, None)
    otherwise               -> (STAGED, None)
- cli/__init__.py:844-859 and cli/import_hf.py:247 call the helper instead
  of the inline two-way branch.

The decide_post_parse_status helper makes the policy testable in isolation;
the integration with _capture_sessions_into_project is covered by smoke
tests that feed a synthetic ProcessedTrace and assert the state manager
ends up with the right status + block_reason.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from opentraces.core.state import TraceStatus
from opentraces.core.trace_stage import (
    STAGE_PRESENTATIONS,
    VISIBLE_STAGE_ORDER,
    decide_post_parse_status,
    resolve_visible_stage,
)


@dataclass
class _FakeReport:
    """Minimal stand-in for TruffleHogReport in unit tests."""

    blocked: bool = False
    n_findings: int = 0
    detector_names: tuple[str, ...] = ()


@dataclass
class _FakeProcessedTrace:
    """Minimal stand-in for ProcessedTrace in unit tests."""

    needs_review: bool = False
    trufflehog_report: _FakeReport | None = None

    @property
    def trufflehog_blocked(self) -> bool:
        return bool(self.trufflehog_report and self.trufflehog_report.blocked)


# ---------------------------------------------------------------------------
# Visible-stage map
# ---------------------------------------------------------------------------


class TestVisibleStageBlocked:
    def test_blocked_status_resolves_to_blocked_stage(self) -> None:
        assert resolve_visible_stage(TraceStatus.BLOCKED) == "blocked"

    def test_blocked_string_form_resolves_to_blocked(self) -> None:
        assert resolve_visible_stage("blocked") == "blocked"

    def test_blocked_appears_in_visible_stage_order(self) -> None:
        assert "blocked" in VISIBLE_STAGE_ORDER

    def test_blocked_has_stage_presentation(self) -> None:
        assert "blocked" in STAGE_PRESENTATIONS
        pres = STAGE_PRESENTATIONS["blocked"]
        assert pres.label  # non-empty label
        assert pres.description  # non-empty description


# ---------------------------------------------------------------------------
# decide_post_parse_status helper
# ---------------------------------------------------------------------------


class TestDecidePostParseStatus:
    """Post-phase-shift: TruffleHog findings no longer block. The pipeline
    redacts matches in-place (same Tier 1 mitigation) and the trace
    still flows to STAGED / COMMITTED based on ``needs_review`` and
    review policy. Blocking was the wrong posture for a deterministic
    scanner — we already know exactly what to mask."""

    def test_trufflehog_findings_do_not_block(self) -> None:
        """A trace with TH findings and needs_review=True goes to STAGED,
        not BLOCKED. The review policy still decides auto-promotion."""
        result = _FakeProcessedTrace(
            needs_review=True,
            trufflehog_report=_FakeReport(blocked=True, n_findings=2,
                                          detector_names=("AWS",)),
        )
        status, reason = decide_post_parse_status(result, review_policy="auto")
        assert status == TraceStatus.STAGED
        assert reason is None

    def test_trufflehog_findings_allow_auto_promotion_when_clean(self) -> None:
        """needs_review=False + review_policy=auto → COMMITTED, even with TH
        findings, because the matches were already redacted upstream."""
        result = _FakeProcessedTrace(
            needs_review=False,
            trufflehog_report=_FakeReport(blocked=True, n_findings=1),
        )
        status, reason = decide_post_parse_status(result, review_policy="auto")
        assert status == TraceStatus.COMMITTED
        assert reason is None

    def test_trufflehog_findings_stay_staged_under_review_policy(self) -> None:
        result = _FakeProcessedTrace(
            needs_review=True,
            trufflehog_report=_FakeReport(blocked=True, n_findings=1),
        )
        status, _ = decide_post_parse_status(result, review_policy="review")
        assert status == TraceStatus.STAGED

    def test_auto_promote_when_safe(self) -> None:
        result = _FakeProcessedTrace(needs_review=False, trufflehog_report=None)
        status, reason = decide_post_parse_status(result, review_policy="auto")
        assert status == TraceStatus.COMMITTED
        assert reason is None

    def test_staged_when_needs_review_under_auto(self) -> None:
        result = _FakeProcessedTrace(needs_review=True, trufflehog_report=None)
        status, reason = decide_post_parse_status(result, review_policy="auto")
        assert status == TraceStatus.STAGED
        assert reason is None

    def test_staged_under_review_policy_even_when_safe(self) -> None:
        """review policy never auto-promotes; safe traces still wait for ot add."""
        result = _FakeProcessedTrace(needs_review=False, trufflehog_report=None)
        status, _ = decide_post_parse_status(result, review_policy="review")
        assert status == TraceStatus.STAGED

    def test_unknown_policy_treated_as_review(self) -> None:
        result = _FakeProcessedTrace(needs_review=False, trufflehog_report=None)
        status, _ = decide_post_parse_status(result, review_policy="garbage")
        assert status == TraceStatus.STAGED

    def test_empty_findings_report_does_not_block(self) -> None:
        """A TruffleHog report with no findings (blocked=False) should not block."""
        result = _FakeProcessedTrace(
            needs_review=False,
            trufflehog_report=_FakeReport(blocked=False, n_findings=0),
        )
        status, _ = decide_post_parse_status(result, review_policy="auto")
        assert status == TraceStatus.COMMITTED
