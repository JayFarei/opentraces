"""S2 change-burst slicer (deterministic, issue #141).

Boundary rule (LOCKED, inlined): cluster edit events
(apply_patch/Edit/Write/MultiEdit/NotebookEdit) where consecutive edits are
≤ ``DEFAULT_BURST_GAP`` (35) steps apart — the same clustering ``core/bursts.py``
applies to its ``file_edit``/``patch_created`` candidates, reused here over the
raw edit-step surface so ``slice`` stays a pure function of the steps (no
TraceMap / Trace Index dependency). Boundaries =
``{0} ∪ {cluster starts} ∪ {cluster end+1} ∪ {S1 human-ask starts}`` — we cut
on S1 boundaries (a goal change), NOT edit-distance.
"""
from __future__ import annotations

from typing import Any

from ...bursts import DEFAULT_BURST_GAP  # reuse the load-bearing burst gap (35)
from .. import steps as S
from ..models import SliceResult
from ..tiling import trajectories_from_boundaries

NAME = "s2"
LABEL = "change-burst"
TIER = "deterministic"


def cluster_edit_steps(edit_steps: list[int], gap: int = DEFAULT_BURST_GAP) -> list[tuple[int, int]]:
    """Cluster sorted edit step indices: split when consecutive gap > ``gap``.

    Mirrors the candidate clustering inside ``core/bursts.py::detect_bursts``.
    """
    if not edit_steps:
        return []
    edit_steps = sorted(edit_steps)
    clusters: list[tuple[int, int]] = []
    cur_start = prev = edit_steps[0]
    for s in edit_steps[1:]:
        if s - prev > gap:
            clusters.append((cur_start, prev))
            cur_start = s
        prev = s
    clusters.append((cur_start, prev))
    return clusters


def slice(steps: list[Any], *, answers: dict | None = None) -> SliceResult:
    total = len(steps)
    edit_steps = [i for i, s in enumerate(steps) if any(n in S.EDIT_TOOLS for n in S.tool_names(s))]
    clusters = cluster_edit_steps(edit_steps)

    boundaries = {0}
    for (cs, ce) in clusters:
        boundaries.add(cs)
        if ce + 1 < total:
            boundaries.add(ce + 1)
    boundaries |= set(S.human_ask_starts(steps))

    def label(a: int, b: int) -> str:
        files: list[str] = []
        for i in range(a, b + 1):
            files.extend(S.edit_files(steps[i]))
        if files:
            uniq = sorted({f.split("/")[-1] for f in files})[:3]
            return "burst: " + ", ".join(uniq)
        return "explore"

    trajs = trajectories_from_boundaries(boundaries, total, "change_burst", label)
    return SliceResult(trajectories=trajs, requests=[])
