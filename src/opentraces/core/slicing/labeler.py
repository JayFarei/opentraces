"""Trajectory labeling — what makes a sliced bucket legible to a client.

A milestone or sub-goal is only useful if it SAYS what it is. The slicer emits
deterministic labels (the user ask, the burst files, the agent's wrap-up
narration) that are always present and free; this layer upgrades the *semantic*
slicers (S3 milestone, S4 sub-goal) to one-line summaries authored by the model
that's already in the loop — in ONE batched call per trace. Graceful: if no
provider is reachable, the deterministic labels stand, so labeling never
requires setup and never blocks. Boundaries are untouched — only the label text
changes — so tiling stays deterministic.
"""
from __future__ import annotations

from typing import Any, Callable

from . import steps as S
from .models import Trajectory

# Only the semantic slicers are re-labeled. S1's label IS the human ask and
# S2's label is the burst's files — both already legible, so we don't spend a
# model call on them.
_SEMANTIC = frozenset({"s3", "s4"})

Labeler = Callable[[str, list[Any], list[Trajectory]], "list[str] | None"]


def _context(steps: list[Any], t: Trajectory) -> str:
    """A compact view of one trajectory for the labeler: span + the agent's
    opening intent and closing outcome."""
    opening = S.first_narration(steps, t.start, t.end)
    closing = S.last_narration(steps, t.start, t.end)
    files = sorted({f.split("/")[-1] for i in range(t.start, min(t.end, len(steps) - 1) + 1)
                    for f in S.edit_files(steps[i])})[:4]
    bits = [f"steps {t.start}-{t.end}"]
    if opening:
        bits.append(f"opens: {opening}")
    if closing and closing != opening:
        bits.append(f"ends: {closing}")
    if files:
        bits.append("files: " + ", ".join(files))
    return " · ".join(bits)


def provider_labeler() -> Labeler | None:
    """Build a labeler backed by ``detect_provider()`` (the client's own claude
    CLI / API), or ``None`` when no provider is reachable."""
    try:
        from ..llm_provider import detect_provider
    except Exception:  # noqa: BLE001
        return None
    provider = detect_provider()
    if provider is None:
        return None

    def label(slicer_name: str, steps: list[Any], trajectories: list[Trajectory]) -> list[str] | None:
        if slicer_name not in _SEMANTIC or not trajectories:
            return None
        kind = "milestone (a verified outcome)" if slicer_name == "s3" else "sub-goal (one deliverable)"
        rows = [f"{i}. {_context(steps, t)}" for i, t in enumerate(trajectories)]
        schema = {
            "type": "object",
            "properties": {
                "labels": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["labels"],
        }
        try:
            res = provider.call_structured(
                system_prompt=(
                    "You label segments of an AI agent's work session so a person scanning "
                    "them understands each at a glance. Each label is a concise, specific "
                    "phrase (3-9 words) naming WHAT THAT SEGMENT ACCOMPLISHED — outcome-first, "
                    "no trailing punctuation, no 'the agent'."
                ),
                user_message=(
                    f"Label each of these {len(trajectories)} {kind} segments. Return a "
                    f"'labels' array with EXACTLY {len(trajectories)} entries, in order.\n\n"
                    + "\n".join(rows)
                ),
                json_schema=schema,
            )
        except Exception:  # noqa: BLE001
            return None
        labels = (res.payload or {}).get("labels")
        if not isinstance(labels, list) or len(labels) != len(trajectories):
            return None
        return [str(x).strip() or trajectories[i].label for i, x in enumerate(labels)]

    return label


def apply_labels(trajectories: list[Trajectory], labels: list[str]) -> list[Trajectory]:
    """Return new trajectories with the model labels swapped in (boundaries kept)."""
    return [Trajectory(t.start, t.end, t.kind, lbl) for t, lbl in zip(trajectories, labels)]
