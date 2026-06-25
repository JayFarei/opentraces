"""S1 user-turn slicer (deterministic, issue #141).

Boundary rule (LOCKED, inlined): a new trajectory starts at a genuine human
ask. Control/loop/notification messages and short continuation directives never
open a trajectory — they absorb into the open trajectory. Each trajectory =
exactly one genuine human ask plus all work serving it.
"""
from __future__ import annotations

from typing import Any

from .. import steps as S
from ..models import SliceResult
from ..tiling import trajectories_from_boundaries

NAME = "s1"
LABEL = "user-turn"
TIER = "deterministic"


def _label(steps, start, end) -> str:
    if S.role(steps[start]) == "user":
        t = S.content(steps[start]).strip().replace("\n", " ")
        if t:
            return t[:80]
    return "(work)"


def slice(steps: list[Any], *, answers: dict | None = None) -> SliceResult:
    from ...faultpoints import armed

    total = len(steps)
    boundaries = {0} | set(S.human_ask_starts(steps))

    if armed("slicer-boundary-off-by-one"):
        # Shift the first non-zero boundary by +1 — a classic off-by-one the
        # conformance journey's pinned boundary assertion must catch.
        nonzero = sorted(b for b in boundaries if b > 0)
        if nonzero:
            b = nonzero[0]
            boundaries.discard(b)
            boundaries.add(b + 1)

    trajs = trajectories_from_boundaries(
        boundaries, total, "user_turn", lambda a, b: _label(steps, a, b)
    )
    return SliceResult(trajectories=trajs, requests=[])
