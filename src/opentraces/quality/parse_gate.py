"""Quality filter for parsed sessions.

Runs between parse and enrich. Filters out sessions that don't meet
minimum contribution thresholds.
"""

from __future__ import annotations

from opentraces_schema import TraceRecord


def meets_quality_threshold(record: TraceRecord) -> bool:
    """Check if a trace record meets minimum quality for contribution.

    Criteria (per discussion-log Q8):
    - Min 1 tool call across all steps
    - Min 2 steps total
    """
    if len(record.steps) < 2:
        return False

    total_tool_calls = sum(len(step.tool_calls) for step in record.steps)
    if total_tool_calls < 1:
        return False

    return True
