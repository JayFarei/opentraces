"""INDEPENDENT tiling verifier (Phase-0).

Standalone. Takes ONLY a list of trajectory dicts ({start,end,...}) and the
real total_steps. NEVER reads any self-reported `tiling` block — it recomputes
coverage / gaps / overlaps from the spans alone. This is the check that the
mutant "self-report tiling.valid=true while leaving a real gap" must NOT fool.

A crash/exception is the caller's FAIL, never a silent skip.
"""
from __future__ import annotations

from typing import Any


def recompute_tiling(trajectories: list[dict[str, Any]], total_steps: int) -> dict[str, Any]:
    """Recompute the tiling property from spans alone.

    Returns {valid, coverage, gaps, overlaps, n_trajectories, detail}.
    Definition (from issue #141 domain model):
      sorted by start: trajectories[0].start == 0; each next.start == prev.end+1;
      last.end == total_steps-1; no overlaps -> coverage 1.0 / gaps 0 / overlaps 0.
    """
    n = len(trajectories)
    if total_steps <= 0:
        # Empty trace: only the empty tiling is valid.
        valid = (n == 0)
        return {
            "valid": valid,
            "coverage": 1.0 if valid else 0.0,
            "gaps": 0,
            "overlaps": 0,
            "n_trajectories": n,
            "detail": "empty-trace" if valid else "empty-trace-with-spans",
        }

    spans = []
    for t in trajectories:
        s = int(t["start"])
        e = int(t["end"])
        spans.append((s, e))
    spans.sort(key=lambda x: (x[0], x[1]))

    covered = [0] * total_steps
    out_of_range = 0
    for (s, e) in spans:
        if e < s:
            out_of_range += 1
            continue
        for i in range(s, e + 1):
            if 0 <= i < total_steps:
                covered[i] += 1
            else:
                out_of_range += 1

    covered_cells = sum(1 for c in covered if c >= 1)
    overlaps = sum(1 for c in covered if c >= 2)
    gaps = sum(1 for c in covered if c == 0)
    coverage = covered_cells / total_steps

    # Contiguity check (independent of cell counting, catches ordering bugs).
    contiguous = True
    if spans:
        if spans[0][0] != 0:
            contiguous = False
        if spans[-1][1] != total_steps - 1:
            contiguous = False
        for i in range(1, len(spans)):
            if spans[i][0] != spans[i - 1][1] + 1:
                contiguous = False
                break
    else:
        contiguous = False  # non-empty trace must have >=1 trajectory

    valid = (
        coverage == 1.0
        and overlaps == 0
        and gaps == 0
        and out_of_range == 0
        and contiguous
    )
    return {
        "valid": valid,
        "coverage": round(coverage, 6),
        "gaps": gaps,
        "overlaps": overlaps,
        "n_trajectories": n,
        "out_of_range": out_of_range,
        "contiguous": contiguous,
    }
