"""Trace Slicer Library (issue #141).

One operation: ``slice(trace, slicer) -> Trajectory[]`` that decomposes a
captured agent session into an array of trajectories which TILES the whole
trace. Four slicers (S1 user-turn, S2 change-burst, S3 milestone, S4 subgoal),
a pluggable judge for the cheap-LLM step, and the frozen ``opentraces.slicing.v1``
envelope. Derive-on-demand: NO captured-schema change, NO ``SCHEMA_VERSION``
bump.

Public surface:
  - :func:`partition_trace` — the full operation (rc=0 envelope or rc=10
    agent-loop handshake), used by ``opentraces trace partition``.
  - :func:`slice_steps` — the bare ``slice(steps, answers) -> SliceResult``.
  - :func:`load_answers` — parse a ``--answers`` JSON file into the answers map.
  - :data:`SLICING_SCHEMA_VERSION`, :func:`recompute_tiling`, model types.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import judges, registry
from .contract import (
    JUDGMENT_SCHEMA_VERSION,
    NEEDS_JUDGMENT_SCHEMA_VERSION,
    RC_NEEDS_JUDGMENT,
    SLICING_SCHEMA_VERSION,
    build_envelope,
    build_needs_judgment,
)
from .models import JudgmentAnswer, JudgmentRequest, SliceResult, Trajectory
from .tiling import assert_tiles, recompute_tiling, trajectories_from_boundaries

__all__ = [
    "partition_trace",
    "slice_steps",
    "load_answers",
    "SliceResult",
    "Trajectory",
    "JudgmentRequest",
    "JudgmentAnswer",
    "SLICING_SCHEMA_VERSION",
    "JUDGMENT_SCHEMA_VERSION",
    "NEEDS_JUDGMENT_SCHEMA_VERSION",
    "RC_NEEDS_JUDGMENT",
    "recompute_tiling",
    "assert_tiles",
    "trajectories_from_boundaries",
    "registry",
    "judges",
]


def slice_steps(slicer_name: str, steps: list[Any], *, answers: dict | None = None) -> SliceResult:
    """Bare slice operation: ``slice(steps, answers) -> SliceResult``.

    Pure function of ``(steps, answers)`` — same inputs, byte-identical output.
    """
    return registry.get(slicer_name).slice(steps, answers=answers)


def load_answers(path: str | Path) -> dict[str, dict[str, Any]]:
    """Parse a ``--answers`` JSON file into ``{id: {decision, confidence}}``.

    Accepts ``{"answers": [JudgmentAnswer, ...]}`` or a bare ``[JudgmentAnswer]``.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    items = raw.get("answers", raw) if isinstance(raw, dict) else raw
    out: dict[str, dict[str, Any]] = {}
    for a in items or []:
        ans = JudgmentAnswer.from_dict(a)
        out[ans.id] = {"decision": ans.decision, "confidence": ans.confidence}
    return out


def partition_trace(
    *,
    trace_id: str,
    slicer_name: str,
    steps: list[Any],
    judge: str = judges.DEFAULT_JUDGE,
    answers: dict | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run the full slicing operation.

    Returns ``(returncode, envelope)``:
      - ``(0, opentraces.slicing.v1)`` when complete.
      - ``(10, opentraces.slicing.needs_judgment.v1)`` when the agent/human judge
        needs answers it cannot supply inline (the rc=10 handshake).
    """
    slicer = registry.get(slicer_name)
    tier = slicer.TIER
    total = len(steps)

    first = slicer.slice(steps, answers=answers)

    outcome = judges.resolve(
        judge,
        slicer_name=slicer_name,
        steps=steps,
        requests=first.requests,
        provided_answers=answers,
    )
    if outcome is judges.NEEDS_JUDGMENT:
        env = build_needs_judgment(
            trace_id=trace_id,
            slicer=slicer_name,
            tier=tier,
            total_steps=total,
            requests=first.requests,
        )
        return RC_NEEDS_JUDGMENT, env

    # outcome is an answers dict ({} = deterministic defaults). Re-slice only if
    # it differs from what `first` already used.
    if outcome and outcome != (answers or {}):
        final = slicer.slice(steps, answers=outcome)
    else:
        final = first

    env = build_envelope(
        trace_id=trace_id,
        slicer=slicer_name,
        tier=tier,
        total_steps=total,
        trajectories=final.trajectories,
        judge=judge,
    )
    return 0, env
