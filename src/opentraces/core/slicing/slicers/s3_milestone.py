"""S3 milestone slicer (cheap-LLM, issue #141).

Boundary rule (LOCKED, inlined): deterministic anchors = USER turns +
``*_notification`` drains start trajectories. Close a trajectory AFTER a
deliverable-completing step (patch Success / green test / subagent return /
answered question). The ONE judgment (delegated to the agent): when several
successes cluster on the SAME artifact, only the LAST before a goal-shift
closes — ~1 judgment per success-cluster.

Phase-0 remediation (issue #141): success detection requires an UNAMBIGUOUS
deliverable signal and NEVER closes on a failure (the prototype's loose regex
closed on "3 passed, 1 failed" and fabricated ``<test>`` artifacts). See
``steps.is_unambiguous_pass``.
"""
from __future__ import annotations

from typing import Any

from .. import steps as S
from ..models import JudgmentRequest, SliceResult
from ..tiling import trajectories_from_boundaries

NAME = "s3"
LABEL = "milestone"
TIER = "cheap_llm"

_SUBAGENT_RETURN = frozenset({"wait_agent", "close_agent", "TaskOutput"})


def success_steps(steps: list[Any]) -> list[tuple[int, str]]:
    """Deliverable-completing steps -> (index, artifact). UNAMBIGUOUS only."""
    out: list[tuple[int, str]] = []
    n = len(steps)
    for i, s in enumerate(steps):
        if S.role(s) != "agent":
            continue
        # patch success: a real edit whose observation reports no error.
        files = S.edit_files(s)
        if files and not S.obs_has_error(s):
            out.append((i, files[0]))
            continue
        # green test/build: unambiguous pass, never a failure.
        if S.is_unambiguous_pass(s):
            out.append((i, "<test>"))
            continue
        # subagent return: a collect step with no error.
        if any(t in _SUBAGENT_RETURN for t in S.tool_names(s)) and not S.obs_has_error(s):
            out.append((i, "<subagent>"))
            continue
        # answered question: substantive content, no tool calls, ending a turn.
        if S.content(s).strip() and not S.tool_names(s):
            if i == n - 1 or S.role(steps[i + 1]) == "user":
                out.append((i, "<answer>"))
    return sorted(set(out))


def same_outcome_clusters(succ: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Group consecutive successes touching the SAME artifact."""
    clusters: list[list[tuple[int, str]]] = []
    for item in succ:
        if clusters and clusters[-1][-1][1] == item[1]:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    return clusters


def slice(steps: list[Any], *, answers: dict | None = None) -> SliceResult:
    answers = answers or {}
    total = len(steps)
    succ = success_steps(steps)
    clusters = same_outcome_clusters(succ)

    boundaries = {0}
    boundaries |= set(S.human_ask_starts(steps))
    boundaries |= set(S.notification_starts(steps))

    requests: list[JudgmentRequest] = []
    for ci, cl in enumerate(clusters):
        if len(cl) < 2:
            # Singleton success: close after it.
            close = cl[0][0]
            if close + 1 < total:
                boundaries.add(close + 1)
            continue
        rid = f"s3-same-outcome-{ci}"
        ctx = S.cluster_context(steps, [c[0] for c in cl])
        requests.append(
            JudgmentRequest(
                id=rid,
                kind="same_outcome",
                window=[cl[0][0], cl[-1][0]],
                candidate_step=cl[-1][0],
                prompt=(
                    f"Steps {[c[0] for c in cl]} all report success on artifact "
                    f"{cl[0][1]!r}. Context:\n{ctx}\n"
                    f"Do they form ONE outcome (only the last closes a trajectory) or "
                    f"DISTINCT outcomes (each closes one)?"
                ),
                answer_schema={"decision": ["one_outcome", "distinct"], "confidence": "float 0..1"},
            )
        )
        decision = (answers.get(rid) or {}).get("decision", "one_outcome")  # conservative default
        if decision == "distinct":
            for (idx, _art) in cl:
                if idx + 1 < total:
                    boundaries.add(idx + 1)
        else:
            last = cl[-1][0]
            if last + 1 < total:
                boundaries.add(last + 1)

    succ_by_close = {idx: art for idx, art in succ}

    def label(a: int, b: int) -> str:
        if b in succ_by_close:
            return f"milestone: {succ_by_close[b]}"
        if S.role(steps[a]) == "user":
            t = S.content(steps[a]).strip().replace("\n", " ")
            if t:
                return t[:80]
        return "(work)"

    trajs = trajectories_from_boundaries(boundaries, total, "milestone", label)
    return SliceResult(trajectories=trajs, requests=requests)
