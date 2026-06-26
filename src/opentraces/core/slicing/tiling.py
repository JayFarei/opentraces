"""The tiling invariant — the one hard contract (issue #141).

Trajectories sorted by ``start`` must satisfy: ``trajectories[0].start == 0``,
each ``next.start == prev.end + 1``, ``last.end == total_steps - 1``, no
overlaps → ``coverage == 1.0, gaps == 0, overlaps == 0``.

``trajectories_from_boundaries`` builds a perfectly-tiling array from a sorted
boundary set (always including 0), so every slicer tiles BY CONSTRUCTION — the
judge can only move boundaries, never break the invariant.

``recompute_tiling`` is the INDEPENDENT verifier: it recomputes coverage / gaps
/ overlaps from the spans ALONE and never trusts a self-reported value. It is
the single implementation reused by the CLI envelope, the conformance tests,
and the otbox journey's expectation, so "what the slicer claims" and "what an
auditor recomputes" share one definition.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from .models import Trajectory


def trajectories_from_boundaries(
    boundaries: Iterable[int],
    total: int,
    kind: str,
    label_fn: Callable[[int, int], str],
) -> list[Trajectory]:
    """Build a perfectly-tiling ``Trajectory[]`` from a boundary set.

    ``label_fn(start, end)`` returns the label for that span. The boundary set
    is filtered to ``[0, total)`` and 0 is always included, so the result tiles
    for any ``total >= 1`` (and is empty for ``total <= 0``).
    """
    if total <= 0:
        return []
    bs = sorted({b for b in boundaries if 0 <= b < total} | {0})
    out: list[Trajectory] = []
    for i, b in enumerate(bs):
        end = (bs[i + 1] - 1) if i + 1 < len(bs) else total - 1
        out.append(Trajectory(b, end, kind, label_fn(b, end)))
    return out


def recompute_tiling(trajectories: list[Any], total_steps: int) -> dict[str, Any]:
    """Recompute the tiling property from spans alone (the independent verifier).

    Accepts ``Trajectory`` instances or ``{start,end,...}`` dicts. Returns
    ``{valid, coverage, gaps, overlaps}`` (the envelope's ``tiling`` block) plus
    ``out_of_range`` / ``contiguous`` / ``n_trajectories`` for diagnostics.
    NEVER reads a self-reported tiling value.
    """
    def _se(t: Any) -> tuple[int, int]:
        if isinstance(t, Trajectory):
            return t.start, t.end
        return int(t["start"]), int(t["end"])

    n = len(trajectories)
    if total_steps <= 0:
        valid = n == 0
        return {
            "valid": valid,
            "coverage": 1.0 if valid else 0.0,
            "gaps": 0,
            "overlaps": 0,
            "out_of_range": 0,
            "contiguous": valid,
            "n_trajectories": n,
        }

    spans = sorted((_se(t) for t in trajectories), key=lambda x: (x[0], x[1]))
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

    contiguous = bool(spans)
    if spans:
        if spans[0][0] != 0 or spans[-1][1] != total_steps - 1:
            contiguous = False
        else:
            for i in range(1, len(spans)):
                if spans[i][0] != spans[i - 1][1] + 1:
                    contiguous = False
                    break

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
        "out_of_range": out_of_range,
        "contiguous": contiguous,
        "n_trajectories": n,
    }


class TilingError(ValueError):
    """Raised by ``assert_tiles`` when the trajectories do not tile."""


def assert_tiles(trajectories: list[Any], total_steps: int) -> dict[str, Any]:
    """Raise ``TilingError`` unless ``trajectories`` perfectly tile ``total_steps``.

    Returns the recomputed tiling block on success so callers can stamp it.
    """
    tiling = recompute_tiling(trajectories, total_steps)
    if not tiling["valid"]:
        raise TilingError(
            f"trajectories do not tile {total_steps} steps: "
            f"coverage={tiling['coverage']} gaps={tiling['gaps']} "
            f"overlaps={tiling['overlaps']} out_of_range={tiling['out_of_range']} "
            f"contiguous={tiling['contiguous']}"
        )
    return tiling
