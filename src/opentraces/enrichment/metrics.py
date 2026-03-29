"""Metrics computation: token aggregation, cost estimation, duration."""

from __future__ import annotations

from datetime import datetime

from opentraces_schema.models import Metrics, Step

# Default pricing per 1M tokens (approximate)
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "sonnet": {
        "input": 3.0,
        "output": 15.0,
        "cache_read": 0.30,
    },
    "opus": {
        "input": 15.0,
        "output": 75.0,
        "cache_read": 1.50,
    },
    "haiku": {
        "input": 0.80,
        "output": 4.0,
        "cache_read": 0.08,
    },
}


def _detect_model_tier(model_name: str | None) -> str:
    """Detect pricing tier from model name string."""
    if not model_name:
        return "sonnet"  # Default fallback

    lower = model_name.lower()
    if "opus" in lower:
        return "opus"
    elif "haiku" in lower:
        return "haiku"
    return "sonnet"


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse an ISO 8601 timestamp, tolerant of various formats."""
    if not ts:
        return None
    try:
        # Handle trailing Z
        ts = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def compute_metrics(
    steps: list[Step],
    pricing: dict | None = None,
) -> Metrics:
    """Compute aggregated session-level metrics from steps.

    Sums token counts, computes cache hit rate, estimates duration,
    and calculates cost using static or user-provided pricing.

    Args:
        steps: List of Step objects from the trace.
        pricing: Optional custom pricing dict overriding DEFAULT_PRICING.
            Format: {"sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.30}}
    """
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0

    first_ts: datetime | None = None
    last_ts: datetime | None = None

    # Track tokens per model tier for cost estimation
    tier_tokens: dict[str, dict[str, int]] = {}

    for step in steps:
        usage = step.token_usage
        total_input += usage.input_tokens
        total_output += usage.output_tokens
        total_cache_read += usage.cache_read_tokens
        total_cache_write += usage.cache_write_tokens

        # Track per-tier usage
        tier = _detect_model_tier(step.model)
        if tier not in tier_tokens:
            tier_tokens[tier] = {"input": 0, "output": 0, "cache_read": 0}
        tier_tokens[tier]["input"] += usage.input_tokens
        tier_tokens[tier]["output"] += usage.output_tokens
        tier_tokens[tier]["cache_read"] += usage.cache_read_tokens

        # Track timestamps for duration
        if step.timestamp:
            parsed = _parse_timestamp(step.timestamp)
            if parsed:
                if first_ts is None or parsed < first_ts:
                    first_ts = parsed
                if last_ts is None or parsed > last_ts:
                    last_ts = parsed

    # Cache hit rate
    cache_hit_rate: float | None = None
    denominator = total_input + total_cache_read
    if denominator > 0:
        cache_hit_rate = round(total_cache_read / denominator, 4)

    # Duration
    total_duration_s: float | None = None
    if first_ts and last_ts and first_ts != last_ts:
        total_duration_s = round((last_ts - first_ts).total_seconds(), 2)

    # Cost estimation
    effective_pricing = pricing if pricing else DEFAULT_PRICING
    estimated_cost = 0.0
    for tier, tokens in tier_tokens.items():
        tier_pricing = effective_pricing.get(tier, effective_pricing.get("sonnet", {}))
        if not tier_pricing:
            continue
        # Price per 1M tokens
        estimated_cost += tokens["input"] * tier_pricing.get("input", 0) / 1_000_000
        estimated_cost += tokens["output"] * tier_pricing.get("output", 0) / 1_000_000
        estimated_cost += tokens["cache_read"] * tier_pricing.get("cache_read", 0) / 1_000_000

    return Metrics(
        total_steps=len(steps),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_duration_s=total_duration_s,
        cache_hit_rate=cache_hit_rate,
        estimated_cost_usd=round(estimated_cost, 6) if estimated_cost > 0 else None,
    )
