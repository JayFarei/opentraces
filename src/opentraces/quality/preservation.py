"""Preservation comparator: measures how well parsed TraceRecord preserves
signals from the raw Claude Code session.

Compares a TraceRecord against a RawSessionSummary (produced independently by
raw_reader.py) and reports per-category preservation ratios, signal losses,
and impossible signal detection.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from opentraces_schema import TraceRecord

from .raw_reader import RawSessionSummary


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SignalLoss:
    """One category of signal lost between raw session and parsed trace."""

    category: str  # messages, tool_calls, tool_results, thinking, token_usage, timestamps, subagents
    raw_count: int
    parsed_count: int
    description: str


@dataclass
class PreservationReport:
    """Full preservation comparison report."""

    ratios: dict[str, float] = field(default_factory=dict)  # per-category 0.0-1.0
    overall: float = 0.0  # weighted average
    signal_losses: list[SignalLoss] = field(default_factory=list)
    impossible_signals: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Category weights for overall score
# ---------------------------------------------------------------------------

_WEIGHTS: dict[str, float] = {
    "messages": 1.0,
    "tool_calls": 1.0,
    "tool_results": 0.9,
    "thinking": 0.5,
    "token_usage": 0.8,
    "timestamps": 0.6,
    "subagents": 0.7,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_ratio(parsed: int, raw: int, *, cap: float = 1.0) -> float:
    """Return parsed/raw capped at *cap*. Returns 1.0 when raw is 0."""
    if raw == 0:
        return 1.0
    return min(parsed / raw, cap)


# ---------------------------------------------------------------------------
# Per-category ratio calculators
# ---------------------------------------------------------------------------

def _ratio_messages(record: TraceRecord, raw: RawSessionSummary) -> float:
    raw_total = raw.user_messages + raw.assistant_messages
    parsed_count = len(record.steps)
    return _safe_ratio(parsed_count, raw_total)


def _ratio_tool_calls(record: TraceRecord, raw: RawSessionSummary) -> float:
    parsed_count = sum(len(s.tool_calls) for s in record.steps)
    return _safe_ratio(parsed_count, raw.tool_use_blocks)


def _ratio_tool_results(record: TraceRecord, raw: RawSessionSummary) -> float:
    parsed_count = sum(len(s.observations) for s in record.steps)
    return _safe_ratio(parsed_count, raw.tool_result_blocks)


def _ratio_thinking(record: TraceRecord, raw: RawSessionSummary) -> float:
    if raw.thinking_blocks_total == 0:
        return 1.0
    if raw.thinking_blocks_with_content == 0:
        # All thinking blocks are encrypted, 0.5 is expected
        return 0.5
    steps_with_reasoning = sum(
        1 for s in record.steps
        if s.reasoning_content and s.reasoning_content.strip()
    )
    return _safe_ratio(steps_with_reasoning, max(raw.thinking_blocks_with_content, 1))


def _ratio_token_usage(record: TraceRecord, raw: RawSessionSummary) -> float:
    parsed_count = sum(
        1 for s in record.steps
        if s.role == "agent"
        and (s.token_usage.input_tokens > 0 or s.token_usage.output_tokens > 0)
    )
    return _safe_ratio(parsed_count, raw.usage_entries)


def _ratio_timestamps(record: TraceRecord, raw: RawSessionSummary) -> float:
    parsed_count = sum(1 for s in record.steps if s.timestamp)
    return _safe_ratio(parsed_count, raw.timestamps)


def _ratio_subagents(record: TraceRecord, raw: RawSessionSummary) -> float:
    parsed_count = sum(1 for s in record.steps if s.call_type == "subagent")
    if parsed_count == 0 and raw.subagent_tool_calls == 0:
        return 1.0
    return _safe_ratio(parsed_count, raw.subagent_tool_calls)


_RATIO_FNS: dict[str, callable] = {
    "messages": _ratio_messages,
    "tool_calls": _ratio_tool_calls,
    "tool_results": _ratio_tool_results,
    "thinking": _ratio_thinking,
    "token_usage": _ratio_token_usage,
    "timestamps": _ratio_timestamps,
    "subagents": _ratio_subagents,
}


# ---------------------------------------------------------------------------
# Signal loss detection
# ---------------------------------------------------------------------------

def _detect_signal_losses(
    record: TraceRecord,
    raw: RawSessionSummary,
) -> list[SignalLoss]:
    losses: list[SignalLoss] = []

    # Thinking signal loss
    if raw.thinking_blocks_with_content > 0:
        steps_with_reasoning = sum(
            1 for s in record.steps
            if s.reasoning_content and s.reasoning_content.strip()
        )
        if steps_with_reasoning == 0:
            losses.append(SignalLoss(
                category="thinking",
                raw_count=raw.thinking_blocks_with_content,
                parsed_count=0,
                description=(
                    f"Raw session has {raw.thinking_blocks_with_content} thinking "
                    "blocks with content but no reasoning_content in parsed steps"
                ),
            ))

    # Tool results signal loss
    parsed_obs = sum(len(s.observations) for s in record.steps)
    if raw.tool_result_blocks > parsed_obs:
        losses.append(SignalLoss(
            category="tool_results",
            raw_count=raw.tool_result_blocks,
            parsed_count=parsed_obs,
            description=(
                f"Raw session has {raw.tool_result_blocks} tool_result blocks "
                f"but only {parsed_obs} observations in parsed trace"
            ),
        ))

    # Timestamps signal loss
    parsed_ts = sum(1 for s in record.steps if s.timestamp)
    if raw.timestamps > parsed_ts:
        losses.append(SignalLoss(
            category="timestamps",
            raw_count=raw.timestamps,
            parsed_count=parsed_ts,
            description=(
                f"Raw session has {raw.timestamps} timestamped lines "
                f"but only {parsed_ts} steps with timestamps"
            ),
        ))

    # Token usage signal loss
    parsed_usage = sum(
        1 for s in record.steps
        if s.role == "agent"
        and (s.token_usage.input_tokens > 0 or s.token_usage.output_tokens > 0)
    )
    if raw.usage_entries > parsed_usage:
        losses.append(SignalLoss(
            category="token_usage",
            raw_count=raw.usage_entries,
            parsed_count=parsed_usage,
            description=(
                f"Raw session has {raw.usage_entries} usage entries "
                f"but only {parsed_usage} agent steps with token counts"
            ),
        ))

    return losses


# ---------------------------------------------------------------------------
# Impossible signal detection
# ---------------------------------------------------------------------------

def _detect_impossible_signals(
    record: TraceRecord,
    raw: RawSessionSummary,
) -> list[str]:
    flags: list[str] = []

    # Committed but no Bash/git tool use in raw
    if record.outcome.committed:
        has_bash_or_git = any(
            s.tool_calls
            for s in record.steps
            for tc in s.tool_calls
            if "Bash" in tc.tool_name or "git" in tc.tool_name
        )
        if raw.tool_use_blocks == 0 and not has_bash_or_git:
            flags.append(
                "outcome.committed is True but raw session has 0 tool_use blocks "
                "containing Bash or git (suspicious)"
            )

    # Attribution with files but no Edit/Write tool use in raw
    if record.attribution is not None and record.attribution.files:
        has_edit_write = any(
            tc.tool_name in ("Edit", "Write")
            for s in record.steps
            for tc in s.tool_calls
        )
        if raw.tool_use_blocks == 0 and not has_edit_write:
            flags.append(
                "attribution has files but raw session has 0 tool_use blocks "
                "containing Edit or Write (suspicious)"
            )

    return flags


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compare_preservation(
    record: TraceRecord,
    raw: RawSessionSummary,
) -> PreservationReport:
    """Compare a parsed TraceRecord against a RawSessionSummary.

    Returns a PreservationReport with per-category ratios, a weighted overall
    score, signal loss entries, and impossible signal flags.
    """
    ratios: dict[str, float] = {}
    for category, fn in _RATIO_FNS.items():
        ratios[category] = fn(record, raw)

    # Weighted average
    total_weight = sum(_WEIGHTS.values())
    weighted_sum = sum(ratios[cat] * _WEIGHTS[cat] for cat in ratios)
    overall = weighted_sum / total_weight if total_weight > 0 else 0.0

    signal_losses = _detect_signal_losses(record, raw)
    impossible_signals = _detect_impossible_signals(record, raw)

    return PreservationReport(
        ratios=ratios,
        overall=round(overall, 4),
        signal_losses=signal_losses,
        impossible_signals=impossible_signals,
    )
