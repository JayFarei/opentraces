"""Domain model for the Trace Slicer Library (issue #141, LOCKED).

    slice(trace, slicer) -> Trajectory[]      # the array TILES the trace

A ``Trajectory`` span is step indices, END-INCLUSIVE. v1 is a flat array only:
NO nesting, NO relations, NO per-trajectory provenance.

The cheap-LLM step (S3/S4) is resolved by a pluggable judge through a frozen
``JudgmentRequest`` / ``JudgmentAnswer`` schema, versioned alongside the
``opentraces.slicing.v1`` envelope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Trajectory:
    """One trajectory: a contiguous, end-inclusive span of step indices."""

    start: int
    end: int  # END-INCLUSIVE
    kind: str  # user_turn | change_burst | milestone | subgoal
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "kind": self.kind, "label": self.label}


@dataclass(frozen=True)
class JudgmentRequest:
    """A single cheap-LLM judgment the slicer needs to finalize boundaries.

    ``kind``:
      - ``terminal_vs_intermediate`` (S4): is this candidate step a genuine
        sub-goal pivot, or a continuation (retry/verify/spawn-collect)?
      - ``same_outcome`` (S3): do clustered successes on one artifact form a
        single outcome (only the last closes) or distinct outcomes?
    """

    id: str
    kind: str
    window: list[int]  # [start, end] step window the judgment reasons over
    candidate_step: int
    prompt: str
    answer_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "window": list(self.window),
            "candidate_step": self.candidate_step,
            "prompt": self.prompt,
            "answer_schema": dict(self.answer_schema),
        }


@dataclass(frozen=True)
class JudgmentAnswer:
    """One answer to a JudgmentRequest, keyed by ``id``."""

    id: str
    decision: str
    confidence: float = 1.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "JudgmentAnswer":
        return cls(
            id=str(d["id"]),
            decision=str(d["decision"]),
            confidence=float(d.get("confidence", 1.0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "decision": self.decision, "confidence": self.confidence}


@dataclass
class SliceResult:
    """What a slicer returns: the tiling plus any judgment requests it raised.

    ``trajectories`` always TILES the trace (guaranteed by construction from a
    boundary set), regardless of whether judgments were supplied — the judge
    only moves boundaries, never breaks the invariant.
    """

    trajectories: list[Trajectory]
    requests: list[JudgmentRequest] = field(default_factory=list)
