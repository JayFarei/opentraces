"""Render-time interestingness scoring for trace steps."""

from __future__ import annotations

import math
from typing import Any


def compute_interestingness(steps: list[dict[str, Any]]) -> list[float]:
    """Compute z-score based interestingness for each step.

    - z-score: (step_token_count - session_mean) / session_stddev
    - Bonus: security flags (+1.0), failed tool calls (+0.5)
    - Steps with z-score > 1.5 get the ! marker
    """
    if not steps:
        return []

    # Gather per-step token counts
    token_counts: list[int] = []
    for step in steps:
        usage = step.get("token_usage", {})
        total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        token_counts.append(total)

    n = len(token_counts)
    mean = sum(token_counts) / n if n > 0 else 0.0
    variance = sum((tc - mean) ** 2 for tc in token_counts) / n if n > 0 else 0.0
    stddev = math.sqrt(variance) if variance > 0 else 1.0

    scores: list[float] = []
    for i, step in enumerate(steps):
        # Base z-score from token usage
        z = (token_counts[i] - mean) / stddev

        # Bonus for security flags on the step
        flags = step.get("_security_flags", [])
        if flags:
            z += 1.0

        # Bonus for failed / errored tool calls
        observations = step.get("observations", [])
        for obs in observations:
            if obs.get("error"):
                z += 0.5
                break

        scores.append(round(z, 3))

    return scores


def interestingness_marker(score: float, threshold: float = 1.5) -> str:
    """Return a Rich markup marker if the score exceeds the threshold."""
    if score > threshold:
        return "[bold #EF4444]![/bold #EF4444]"
    return " "
