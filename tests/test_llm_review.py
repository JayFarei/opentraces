"""Part F3 tests: Tier 2 LLM session-level semantic review.

Runs over the full transcript after field-level tiers. Optional, never
blocking. Pessimistic aggregation across chunks: any 'no' beats any
'yes'. Caching via review_key = sha256(content + model + prompt_version
+ context).
"""

from __future__ import annotations

import pytest

from opentraces.security.llm_provider import FakeProvider
from opentraces.security.llm_review import (
    LLMReviewVerdict,
    aggregate_verdicts,
    chunk_transcript,
    estimate_cost,
    review_session,
    review_key as compute_review_key,
)


class TestChunkTranscript:
    def test_short_transcript_one_chunk(self) -> None:
        steps = [f"step {i}: hello" for i in range(5)]
        chunks = chunk_transcript(steps, max_chars=1000)
        assert len(chunks) == 1
        assert "step 0" in chunks[0]
        assert "step 4" in chunks[0]

    def test_splits_on_step_boundaries(self) -> None:
        steps = ["a" * 300, "b" * 300, "c" * 300]
        chunks = chunk_transcript(steps, max_chars=500)
        # Each step is 300 chars; 500-cap forces one-per-chunk.
        assert len(chunks) == 3

    def test_oversize_single_step_is_own_chunk(self) -> None:
        steps = ["x" * 2000]
        chunks = chunk_transcript(steps, max_chars=500)
        assert len(chunks) == 1
        assert len(chunks[0]) >= 2000


class TestAggregate:
    def test_all_yes_shareable(self) -> None:
        v1 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")
        v2 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")
        agg = aggregate_verdicts([v1, v2])
        assert agg.shareable == "yes"
        assert agg.missed_sensitive_data == "no"

    def test_single_no_wins_shareable(self) -> None:
        v1 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")
        v2 = LLMReviewVerdict(shareable="no", missed_sensitive_data="no")
        v3 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")
        agg = aggregate_verdicts([v1, v2, v3])
        assert agg.shareable == "no"

    def test_manual_review_beats_yes(self) -> None:
        v1 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")
        v2 = LLMReviewVerdict(shareable="manual_review", missed_sensitive_data="no")
        agg = aggregate_verdicts([v1, v2])
        assert agg.shareable == "manual_review"

    def test_missed_yes_beats_maybe_beats_no(self) -> None:
        v1 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")
        v2 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="maybe")
        v3 = LLMReviewVerdict(shareable="yes", missed_sensitive_data="yes")
        agg = aggregate_verdicts([v1, v2, v3])
        assert agg.missed_sensitive_data == "yes"

    def test_flagged_parts_union(self) -> None:
        v1 = LLMReviewVerdict(
            shareable="yes",
            missed_sensitive_data="no",
            flagged_parts=[{"reason": "api key", "evidence": "sk_live_1"}],
        )
        v2 = LLMReviewVerdict(
            shareable="yes",
            missed_sensitive_data="no",
            flagged_parts=[{"reason": "email", "evidence": "a@b.com"}],
        )
        agg = aggregate_verdicts([v1, v2])
        assert len(agg.flagged_parts) == 2


class TestReviewKey:
    def test_same_inputs_same_key(self) -> None:
        k1 = compute_review_key("content", "model", "v1", "ctx")
        k2 = compute_review_key("content", "model", "v1", "ctx")
        assert k1 == k2

    def test_different_content_different_key(self) -> None:
        k1 = compute_review_key("a", "m", "v1", "")
        k2 = compute_review_key("b", "m", "v1", "")
        assert k1 != k2

    def test_model_changes_key(self) -> None:
        k1 = compute_review_key("a", "m1", "v1", "")
        k2 = compute_review_key("a", "m2", "v1", "")
        assert k1 != k2

    def test_prompt_version_changes_key(self) -> None:
        k1 = compute_review_key("a", "m", "v1", "")
        k2 = compute_review_key("a", "m", "v2", "")
        assert k1 != k2


class TestEstimateCost:
    def test_small_session_is_cheap(self) -> None:
        est = estimate_cost(num_chars=5_000, model="claude-haiku-4-5-20251001")
        assert est["tokens"] < 2_000
        assert est["cost_usd"] < 0.01

    def test_large_session_scales(self) -> None:
        est = estimate_cost(num_chars=400_000, model="claude-haiku-4-5-20251001")
        assert est["tokens"] > 50_000
        assert est["cost_usd"] > 0.01


class TestReviewSession:
    def test_clean_session_shareable(self) -> None:
        p = FakeProvider(
            responses=[{
                "shareable": "yes",
                "missed_sensitive_data": "no",
                "flagged_parts": [],
                "summary": "Looks clean",
            }]
        )
        verdict = review_session(
            steps=["user: hello", "assistant: hi"],
            provider=p,
        )
        assert verdict.shareable == "yes"
        assert verdict.missed_sensitive_data == "no"
        assert verdict.summary == "Looks clean"

    def test_session_with_leak_blocked(self) -> None:
        p = FakeProvider(
            responses=[{
                "shareable": "no",
                "missed_sensitive_data": "yes",
                "flagged_parts": [
                    {"reason": "API key", "evidence": "sk_live_abc"}
                ],
                "summary": "Found unredacted key",
            }]
        )
        verdict = review_session(
            steps=["leaked sk_live_abc in the output"],
            provider=p,
        )
        assert verdict.shareable == "no"
        assert verdict.missed_sensitive_data == "yes"
        assert len(verdict.flagged_parts) == 1

    def test_multi_chunk_aggregates(self) -> None:
        p = FakeProvider(
            responses=[
                {
                    "shareable": "yes",
                    "missed_sensitive_data": "no",
                    "flagged_parts": [],
                    "summary": "chunk 1 ok",
                },
                {
                    "shareable": "manual_review",
                    "missed_sensitive_data": "maybe",
                    "flagged_parts": [],
                    "summary": "chunk 2 unclear",
                },
            ]
        )
        verdict = review_session(
            steps=["a" * 300_000, "b" * 300_000],
            provider=p,
            max_chars_per_chunk=400_000,
        )
        # Pessimistic aggregation.
        assert verdict.shareable == "manual_review"
        assert verdict.missed_sensitive_data == "maybe"
