"""Tier 2 LLM: session-level semantic review.

Runs over the full transcript after all field-level tiers. Optional,
never blocking. Returns a structured verdict:

    {
      "shareable": "yes" | "no" | "manual_review",
      "missed_sensitive_data": "yes" | "no" | "maybe",
      "flagged_parts": [{ "reason": str, "evidence": str }],
      "summary": str
    }

Caching uses ``review_key = sha256(content + model + prompt_version +
context)`` so re-running the same content with the same model is free.

Pessimistic aggregation across chunks so a single 'no' always wins and
any 'maybe' can never be drowned out by a majority of 'no's on the
missed-data axis.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from .llm_provider import LLMProvider

logger = logging.getLogger(__name__)

PROMPT_VERSION = "1"

_REVIEW_PROMPT = """You are a security reviewer for an AI coding agent trace dataset.
The session has already been processed by automated secret detection
(regex + entropy + optional TruffleHog). Your role is to catch anything
missed — semantically sensitive content that pattern matching cannot detect.

Project context: {context}
Session transcript (may be truncated):
{transcript}

Respond with ONLY valid JSON:
{{"shareable": "yes"|"no"|"manual_review",
  "missed_sensitive_data": "yes"|"no"|"maybe",
  "flagged_parts": [{{"reason": string, "evidence": string}}],
  "summary": string}}

Guidelines:
  - "shareable=yes" only if confident nothing sensitive remains
  - "missed_sensitive_data=yes" for passwords, API keys, private
    project names, personal contacts, financial/health data NOT
    already redacted
  - "missed_sensitive_data=maybe" if uncertain
  - "flagged_parts" evidence: max 100 chars each, verbatim only
  - Project-internal file paths are NOT sensitive
  - Git usernames in git log are generally public
  - Focus on content harmful if published
"""


# ---------------------------------------------------------------------------
# Verdict dataclass
# ---------------------------------------------------------------------------


@dataclass
class LLMReviewVerdict:
    """Structured output of a Tier 2 review call."""

    shareable: str = "manual_review"
    missed_sensitive_data: str = "maybe"
    flagged_parts: list[dict[str, str]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "shareable": self.shareable,
            "missed_sensitive_data": self.missed_sensitive_data,
            "flagged_parts": list(self.flagged_parts),
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------


def chunk_transcript(
    steps: list[str],
    max_chars: int = 400_000,
) -> list[str]:
    """Group ``steps`` into chunks of at most ``max_chars`` characters.

    Chunks split on step boundaries only — a single oversized step is
    kept intact in its own chunk rather than cut mid-content.
    """
    if not steps:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for step in steps:
        size = len(step)
        if size >= max_chars:
            if current:
                chunks.append("\n".join(current))
                current, current_size = [], 0
            chunks.append(step)
            continue
        if current_size + size + 1 > max_chars and current:
            chunks.append("\n".join(current))
            current, current_size = [], 0
        current.append(step)
        current_size += size + 1

    if current:
        chunks.append("\n".join(current))
    return chunks


# ---------------------------------------------------------------------------
# Aggregation — pessimistic across chunks
# ---------------------------------------------------------------------------


_SHAREABLE_RANK = {"no": 2, "manual_review": 1, "yes": 0}
_MISSED_RANK = {"yes": 2, "maybe": 1, "no": 0}


def aggregate_verdicts(
    verdicts: list[LLMReviewVerdict],
) -> LLMReviewVerdict:
    """Pessimistic aggregation across per-chunk verdicts.

    ``shareable``: ``no`` > ``manual_review`` > ``yes``.
    ``missed_sensitive_data``: ``yes`` > ``maybe`` > ``no``.
    ``flagged_parts`` is the concatenation in input order.
    ``summary`` joins non-empty per-chunk summaries with `` | ``.
    """
    if not verdicts:
        return LLMReviewVerdict(shareable="manual_review", missed_sensitive_data="maybe")

    worst_share = max(verdicts, key=lambda v: _SHAREABLE_RANK.get(v.shareable, 1))
    worst_miss = max(verdicts, key=lambda v: _MISSED_RANK.get(v.missed_sensitive_data, 1))

    flagged: list[dict[str, str]] = []
    summaries: list[str] = []
    for v in verdicts:
        flagged.extend(v.flagged_parts)
        if v.summary:
            summaries.append(v.summary)

    return LLMReviewVerdict(
        shareable=worst_share.shareable,
        missed_sensitive_data=worst_miss.missed_sensitive_data,
        flagged_parts=flagged,
        summary=" | ".join(summaries),
    )


# ---------------------------------------------------------------------------
# Review key — cache identity
# ---------------------------------------------------------------------------


def review_key(
    content: str,
    model: str,
    prompt_version: str,
    context: str,
    provider: str = "",
    base_url: str = "",
) -> str:
    """Stable cache key for a review result.

    Any change to content, model, provider, base_url, prompt_version,
    or context produces a fresh key so a re-review runs. ``provider``
    and ``base_url`` default to empty for backwards compatibility with
    callers that pre-date multi-backend support.
    """
    h = hashlib.sha256()
    parts = (
        content, "\0", model, "\0", prompt_version, "\0", context,
        "\0", provider, "\0", base_url,
    )
    for part in parts:
        h.update(part.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Cost estimator
# ---------------------------------------------------------------------------


# Rough token:char ratio (4 chars per token) + 500-token overhead for
# the review prompt and JSON envelope.
_TOKENS_PER_CHAR = 0.25
_PROMPT_OVERHEAD_TOKENS = 500

# Anthropic Haiku 4.5 pricing (public): input $1/M, output $5/M. We
# assume input-heavy reviews (small JSON output).
_PRICE_PER_INPUT_TOKEN = 1.0 / 1_000_000
_PRICE_PER_OUTPUT_TOKEN = 5.0 / 1_000_000
_EXPECTED_OUTPUT_TOKENS = 300


def estimate_cost(num_chars: int, model: str) -> dict[str, float]:
    """Estimate cost for reviewing ``num_chars`` of transcript.

    Returns ``{"tokens", "cost_usd"}``. Pricing is approximate; always
    presented to the user as an estimate, not a quote.
    """
    input_tokens = int(num_chars * _TOKENS_PER_CHAR) + _PROMPT_OVERHEAD_TOKENS
    cost = (
        input_tokens * _PRICE_PER_INPUT_TOKEN
        + _EXPECTED_OUTPUT_TOKENS * _PRICE_PER_OUTPUT_TOKEN
    )
    return {"tokens": float(input_tokens), "cost_usd": round(cost, 6)}


# ---------------------------------------------------------------------------
# Review driver
# ---------------------------------------------------------------------------


def _parse_verdict(response: dict[str, Any]) -> LLMReviewVerdict:
    """Coerce a provider response into a :class:`LLMReviewVerdict`."""
    shareable = str(response.get("shareable", "manual_review"))
    missed = str(response.get("missed_sensitive_data", "maybe"))

    parts_raw = response.get("flagged_parts") or []
    flagged: list[dict[str, str]] = []
    if isinstance(parts_raw, list):
        for item in parts_raw:
            if not isinstance(item, dict):
                continue
            flagged.append({
                "reason": str(item.get("reason", "")),
                "evidence": str(item.get("evidence", "")),
            })

    summary = str(response.get("summary", ""))
    return LLMReviewVerdict(
        shareable=shareable,
        missed_sensitive_data=missed,
        flagged_parts=flagged,
        summary=summary,
    )


def review_trace(
    steps: list[str],
    provider: LLMProvider,
    context: str = "",
    max_chars_per_chunk: int = 400_000,
) -> LLMReviewVerdict:
    """Run Tier 2 LLM review across all chunks and aggregate the result."""
    chunks = chunk_transcript(steps, max_chars=max_chars_per_chunk)
    if not chunks:
        return LLMReviewVerdict(shareable="yes", missed_sensitive_data="no")

    verdicts: list[LLMReviewVerdict] = []
    for chunk in chunks:
        prompt = _REVIEW_PROMPT.format(
            context=context or "No additional context provided",
            transcript=chunk,
        )
        try:
            raw = provider.complete_json(prompt)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("LLM review call failed on chunk: %s", exc)
            verdicts.append(LLMReviewVerdict(
                shareable="manual_review",
                missed_sensitive_data="maybe",
                summary=f"review error: {exc}",
            ))
            continue
        verdicts.append(_parse_verdict(raw))

    return aggregate_verdicts(verdicts)
