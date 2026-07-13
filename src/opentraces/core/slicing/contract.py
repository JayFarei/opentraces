"""Frozen contracts for the Trace Slicer Library (issue #141).

``opentraces.slicing.v1`` — the slice envelope (downstream-consumer contract;
``trajectories[].start/end`` are zero-based positions in the input step array;
changing that meaning or the envelope shape requires a schema_version bump).
Derive-on-demand like
``--waste`` / ``--run-intel`` / ``trace compare`` — NO captured-schema change,
NO ``TraceRecord.SCHEMA_VERSION`` bump.

``opentraces.slicing.judgment.v1`` — the JudgmentRequest / JudgmentAnswer schema.
``opentraces.slicing.needs_judgment.v1`` — the ``rc=10`` agent-loop handshake.
"""
from __future__ import annotations

from typing import Any

from .models import Trajectory
from .tiling import recompute_tiling

SLICING_SCHEMA_VERSION = "opentraces.slicing.v1"
JUDGMENT_SCHEMA_VERSION = "opentraces.slicing.judgment.v1"
NEEDS_JUDGMENT_SCHEMA_VERSION = "opentraces.slicing.needs_judgment.v1"

# Return code the CLI exits with when an agent-loop judgment is needed.
RC_NEEDS_JUDGMENT = 10


def build_envelope(
    *,
    trace_id: str,
    slicer: str,
    tier: str,
    total_steps: int,
    trajectories: list[Trajectory],
    judge: str | None = None,
) -> dict[str, Any]:
    """Build the ``opentraces.slicing.v1`` envelope.

    ``tiling`` is recomputed from the emitted ``trajectories[]`` by the shared
    independent verifier — the producer does not get to self-grade with a
    different definition.
    """
    from ..faultpoints import armed

    emitted = list(trajectories)
    if armed("slicer-self-report-valid-gap"):
        # Drop a real trajectory (leaving an uncovered gap) but CLAIM the tiling
        # is valid. The conformance journey / independent verifier recompute
        # from trajectories[] and MUST catch this; an envelope that trusted its
        # own tiling.valid would not.
        if len(emitted) > 1:
            emitted = emitted[:-1]
        tiling = {"valid": True, "coverage": 1.0, "gaps": 0, "overlaps": 0}
    else:
        full = recompute_tiling(emitted, total_steps)
        tiling = {
            "valid": full["valid"],
            "coverage": full["coverage"],
            "gaps": full["gaps"],
            "overlaps": full["overlaps"],
        }

    env: dict[str, Any] = {
        "schema_version": SLICING_SCHEMA_VERSION,
        "status": "ok",
        "trace_id": trace_id,
        "slicer": slicer,
        "tier": tier,
        "total_steps": total_steps,
        "trajectories": [t.to_dict() for t in emitted],
        "tiling": tiling,
    }
    if judge is not None:
        env["judge"] = judge
    return env


def build_needs_judgment(
    *,
    trace_id: str,
    slicer: str,
    tier: str,
    total_steps: int,
    requests: list[Any],
) -> dict[str, Any]:
    """Build the ``rc=10`` agent-loop handshake envelope."""
    return {
        "schema_version": NEEDS_JUDGMENT_SCHEMA_VERSION,
        "status": "needs_judgment",
        "trace_id": trace_id,
        "slicer": slicer,
        "tier": tier,
        "total_steps": total_steps,
        "judgment_schema_version": JUDGMENT_SCHEMA_VERSION,
        "judgment_requests": [
            (r.to_dict() if hasattr(r, "to_dict") else r) for r in requests
        ],
        "instruction": (
            "This slicer needs cheap-LLM judgments to finalize trajectory "
            "boundaries. Answer EACH request above with a decision from its "
            "answer_schema.decision set and a confidence in [0,1], write them to "
            "an answers JSON file shaped like "
            '{"answers": [{"id": "...", "decision": "...", "confidence": 0.9}]}, '
            "and re-run the same command with --answers <file>. Same answers -> "
            "byte-identical trajectories."
        ),
    }
