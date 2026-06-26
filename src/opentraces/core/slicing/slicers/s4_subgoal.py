"""S4 subgoal slicer (cheap-LLM, issue #141).

Boundary rule (LOCKED, inlined): the S1 deterministic spine PLUS internal agent
pivots. The judgment (delegated to the agent): open a boundary at an agent step
where the prior outcome is complete and the agent now drives a DIFFERENT
outcome; otherwise continue (retry loops, spawn-and-collect, verification stay
in one trajectory) — ~1 judgment per candidate pivot.

The deterministic default (no judge) answers every pivot "stay", so S4 reduces
to the S1 spine. Its value is the agent-loop pivot judgment, exactly as the
Phase-0 report found.
"""
from __future__ import annotations

from typing import Any

from .. import steps as S
from ..models import JudgmentRequest, SliceResult
from ..tiling import trajectories_from_boundaries
from .s3_milestone import success_steps

NAME = "s4"
LABEL = "subgoal"
TIER = "cheap_llm"


def pivot_candidates(steps: list[Any]) -> list[int]:
    """Agent steps that MIGHT open a new sub-goal: an agent step right after a
    completed outcome that initiates new tool work. Bounded by the number of
    successes, so the request set provably converges."""
    succ_idx = {i for i, _ in success_steps(steps)}
    candidates: list[int] = []
    for i in range(1, len(steps)):
        if S.role(steps[i]) != "agent":
            continue
        if (i - 1) in succ_idx and S.tool_names(steps[i]):
            candidates.append(i)
    return candidates


def slice(steps: list[Any], *, answers: dict | None = None) -> SliceResult:
    answers = answers or {}
    total = len(steps)
    boundaries = {0} | set(S.human_ask_starts(steps))  # the S1 spine

    requests: list[JudgmentRequest] = []
    for cand in pivot_candidates(steps):
        rid = f"s4-pivot-{cand}"
        ctx_lines = []
        if cand - 1 >= 0:
            ctx_lines.append("  prior outcome — " + S.step_preview(steps[cand - 1], cand - 1))
        ctx_lines.append("  -> candidate    — " + S.step_preview(steps[cand], cand))
        if cand + 1 < total:
            ctx_lines.append("  next            — " + S.step_preview(steps[cand + 1], cand + 1))
        ctx = "\n".join(ctx_lines)
        requests.append(
            JudgmentRequest(
                id=rid,
                kind="terminal_vs_intermediate",
                window=[max(0, cand - 3), min(total - 1, cand + 3)],
                candidate_step=cand,
                prompt=(
                    f"At step {cand} the prior outcome is complete. Context:\n{ctx}\n"
                    f"Is the agent now driving a DIFFERENT outcome (pivot -> open a new "
                    f"trajectory) or continuing the same sub-goal (retry/verify/"
                    f"spawn-collect -> stay)?"
                ),
                answer_schema={"decision": ["pivot", "stay"], "confidence": "float 0..1"},
            )
        )
        decision = (answers.get(rid) or {}).get("decision", "stay")  # conservative default
        if decision == "pivot":
            boundaries.add(cand)

    def label(a: int, b: int) -> str:
        if S.role(steps[a]) == "user":
            t = S.content(steps[a]).strip().replace("\n", " ")
            if t:
                return t[:80]
        return "(work)"

    trajs = trajectories_from_boundaries(boundaries, total, "subgoal", label)
    return SliceResult(trajectories=trajs, requests=requests)
