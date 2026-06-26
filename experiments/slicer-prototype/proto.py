"""THROWAWAY Phase-0 prototype for the Trace Slicer Library (issue #141).

NOT wired into src/. Disposable. The deliverable of Phase 0 is REPORT.md +
the GO/NO-GO verdict, not this code.

Design spine (the reliability guarantee):
    Every slicer produces a *sorted boundary set* B over step indices, always
    including 0. Trajectories then fill the half-open gaps between consecutive
    boundaries, with the last running to total_steps - 1. This *guarantees*
    perfect tiling by construction (coverage 1.0 / 0 gaps / 0 overlaps) for
    any non-degenerate trace. The cheap-LLM judge (S3/S4) only decides *which*
    boundaries exist; it can never break the tiling invariant.

The four slicers (definitions inlined from issue #141, LOCKED):
    S1 user-turn     deterministic   genuine human asks open trajectories
    S2 change-burst  deterministic   edit-bursts (gap<=35) + S1 human-ask starts
    S3 milestone     cheap_llm       anchors + close-after-success (same-outcome judgment)
    S4 subgoal       cheap_llm       S1 spine + judged internal agent pivots
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any, Iterable

# Reuse the canonical burst gap so S2 stays faithful to core/bursts.py.
DEFAULT_BURST_GAP = 35

EDIT_TOOLS = {"apply_patch", "Edit", "Write", "MultiEdit", "NotebookEdit", "str_replace_editor"}

# S1 control-message blocklist (inlined rule, LOCKED). A user step whose
# trimmed text matches this does NOT open a trajectory. Covers the named tag
# family AND the `<<...>>` synthetic-marker family.
_TAG_BLOCK = re.compile(
    r"^\s*(<<|<(skill|subagent_notification|task-notification|turn_aborted)\b)"
)
# Short continuation directive (inlined rule, LOCKED).
_CONT = re.compile(r"^\s*(continue|keep going|Continue\b.*)\s*$", re.IGNORECASE)

# Notification-drain markers (S3 anchors). A user step matching one of these
# *does* start a trajectory in S3 (the drain), unlike S1 which absorbs them.
_NOTIFICATION = re.compile(r"^\s*<([a-zA-Z_-]*notification|task-notification)\b")

# Deterministic "deliverable-completing" signals, used to detect milestone
# close points and S4 pivot candidates. Best-effort over whatever the record
# retained (record fidelity, not otel). Honestly surfaced as heuristic.
_TEST_PASS = re.compile(
    r"\b(\d+\s+passed|all tests? pass|tests? passed|PASS\b|✓|build succeeded|0 failed|"
    r"passed,\s*\d+\s*failed|ok\b.*\bpassed)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# domain model
# ---------------------------------------------------------------------------


@dataclass
class Trajectory:
    start: int
    end: int  # END-INCLUSIVE
    kind: str
    label: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JudgmentRequest:
    id: str
    kind: str  # "terminal_vs_intermediate" | "same_outcome"
    window: list[int]
    candidate_step: int
    prompt: str
    answer_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# step accessors (work on pydantic Step or dict)
# ---------------------------------------------------------------------------


def _role(step: Any) -> str:
    return step.get("role") if isinstance(step, dict) else getattr(step, "role", "")


def _content(step: Any) -> str:
    c = step.get("content") if isinstance(step, dict) else getattr(step, "content", None)
    return c or ""


def _tool_names(step: Any) -> list[str]:
    tcs = step.get("tool_calls") if isinstance(step, dict) else getattr(step, "tool_calls", [])
    out = []
    for tc in tcs or []:
        n = tc.get("tool_name") if isinstance(tc, dict) else getattr(tc, "tool_name", None)
        if n:
            out.append(n)
    return out


def _tool_inputs(step: Any) -> list[dict]:
    tcs = step.get("tool_calls") if isinstance(step, dict) else getattr(step, "tool_calls", [])
    out = []
    for tc in tcs or []:
        i = tc.get("input") if isinstance(tc, dict) else getattr(tc, "input", None)
        out.append(i or {})
    return out


def _observations(step: Any) -> list[Any]:
    obs = step.get("observations") if isinstance(step, dict) else getattr(step, "observations", [])
    return obs or []


def _obs_text(step: Any) -> str:
    parts = []
    for o in _observations(step):
        if isinstance(o, dict):
            parts.append(o.get("content") or o.get("output_summary") or "")
        else:
            parts.append(getattr(o, "content", None) or getattr(o, "output_summary", None) or "")
    return "\n".join(p for p in parts if p)


def _obs_error(step: Any) -> bool:
    for o in _observations(step):
        e = o.get("error") if isinstance(o, dict) else getattr(o, "error", None)
        if e:
            return True
    return False


def _edit_files(step: Any) -> list[str]:
    files = []
    for name, inp in zip(_tool_names(step), _tool_inputs(step)):
        if name in EDIT_TOOLS:
            fp = inp.get("file_path") or inp.get("path") or inp.get("filename")
            if isinstance(fp, str):
                files.append(fp)
            elif name == "apply_patch":
                files.append("<apply_patch>")
    return files


# ---------------------------------------------------------------------------
# boundary -> trajectory construction (the tiling guarantee)
# ---------------------------------------------------------------------------


def trajectories_from_boundaries(
    boundaries: Iterable[int],
    total: int,
    kind: str,
    label_fn,
) -> list[Trajectory]:
    """Build a perfectly-tiling Trajectory[] from a boundary set.

    `label_fn(start, end)` returns the label string for that span.
    """
    if total <= 0:
        return []
    bs = sorted({b for b in boundaries if 0 <= b < total} | {0})
    out: list[Trajectory] = []
    for i, b in enumerate(bs):
        end = (bs[i + 1] - 1) if i + 1 < len(bs) else total - 1
        out.append(Trajectory(b, end, kind, label_fn(b, end)))
    return out


# ---------------------------------------------------------------------------
# S1 user-turn (deterministic)
# ---------------------------------------------------------------------------


def s1_human_ask_starts(steps: list[Any]) -> list[int]:
    """Return step indices that are genuine human asks per the LOCKED S1 rule."""
    starts = []
    for i, s in enumerate(steps):
        if _role(s) != "user":
            continue
        t = _content(s).strip()
        if not t:
            continue
        if _TAG_BLOCK.match(t):
            continue
        if _CONT.match(t):
            continue
        starts.append(i)
    return starts


def _user_label(steps, start, end) -> str:
    t = _content(steps[start]).strip().replace("\n", " ")
    return (t[:80] or "(work)") if _role(steps[start]) == "user" else "(work)"


def slice_s1(steps: list[Any], answers=None):
    total = len(steps)
    starts = set(s1_human_ask_starts(steps)) | {0}
    trajs = trajectories_from_boundaries(
        starts, total, "user_turn", lambda a, b: _user_label(steps, a, b)
    )
    return trajs, []


# ---------------------------------------------------------------------------
# S2 change-burst (deterministic) — adapts the core/bursts.py gap clustering
# ---------------------------------------------------------------------------


def cluster_edit_steps(edit_steps: list[int], gap: int = DEFAULT_BURST_GAP) -> list[tuple[int, int]]:
    """Cluster sorted edit step indices: split when consecutive gap > `gap`."""
    if not edit_steps:
        return []
    edit_steps = sorted(edit_steps)
    clusters = []
    cur_start = edit_steps[0]
    prev = edit_steps[0]
    for s in edit_steps[1:]:
        if s - prev > gap:
            clusters.append((cur_start, prev))
            cur_start = s
        prev = s
    clusters.append((cur_start, prev))
    return clusters


def slice_s2(steps: list[Any], answers=None, gap: int = DEFAULT_BURST_GAP):
    total = len(steps)
    edit_steps = [i for i, s in enumerate(steps) if any(n in EDIT_TOOLS for n in _tool_names(s))]
    clusters = cluster_edit_steps(edit_steps, gap)
    boundaries = {0}
    for (cs, ce) in clusters:
        boundaries.add(cs)
        if ce + 1 < total:
            boundaries.add(ce + 1)
    boundaries |= set(s1_human_ask_starts(steps))  # cut on goal change too

    def label(a, b):
        files = []
        for i in range(a, b + 1):
            files.extend(_edit_files(steps[i]))
        if files:
            uniq = sorted(set(files))[:3]
            return "burst: " + ", ".join(p.split("/")[-1] for p in uniq)
        return "explore"

    trajs = trajectories_from_boundaries(boundaries, total, "change_burst", label)
    return trajs, []


# ---------------------------------------------------------------------------
# success / pivot detection (shared by S3/S4)
# ---------------------------------------------------------------------------


def _is_notification(step: Any) -> bool:
    return _role(step) == "user" and bool(_NOTIFICATION.match(_content(step).strip()))


def success_steps(steps: list[Any]) -> list[tuple[int, str]]:
    """Deterministic, heuristic deliverable-completing steps -> (index, artifact).

    Honest about being a record-fidelity heuristic. Signals:
      - successful edit (Edit/Write/apply_patch) with no observation error
      - a step whose observation text matches a test/build-pass pattern
      - the final substantive agent response (answered question)
    """
    out = []
    n = len(steps)
    for i, s in enumerate(steps):
        if _role(s) != "agent":
            continue
        files = _edit_files(s)
        if files and not _obs_error(s):
            out.append((i, files[0]))
            continue
        otext = _obs_text(s)
        if otext and _TEST_PASS.search(otext):
            out.append((i, "<test>"))
            continue
    # final answered-question: last agent step with content and no tool calls
    for i in range(n - 1, -1, -1):
        if _role(steps[i]) == "agent" and _content(steps[i]).strip() and not _tool_names(steps[i]):
            out.append((i, "<final>"))
            break
    return sorted(set(out))


def same_outcome_clusters(succ: list[tuple[int, str]]) -> list[list[tuple[int, str]]]:
    """Group consecutive successes touching the SAME artifact into clusters."""
    clusters: list[list[tuple[int, str]]] = []
    for item in succ:
        if clusters and clusters[-1][-1][1] == item[1]:
            clusters[-1].append(item)
        else:
            clusters.append([item])
    return clusters


# ---------------------------------------------------------------------------
# S3 milestone (cheap_llm — same-outcome judgment)
# ---------------------------------------------------------------------------


def _s3_requests(steps):
    succ = success_steps(steps)
    clusters = same_outcome_clusters(succ)
    requests = []
    for ci, cl in enumerate(clusters):
        if len(cl) < 2:
            continue  # singletons need no judgment; close after them
        window = [cl[0][0], cl[-1][0]]
        requests.append(
            JudgmentRequest(
                id=f"s3-same-outcome-{ci}",
                kind="same_outcome",
                window=window,
                candidate_step=cl[-1][0],
                prompt=(
                    f"Steps {[c[0] for c in cl]} all report success on artifact "
                    f"{cl[0][1]!r}. Do they belong to ONE outcome (only the last closes a "
                    f"trajectory) or are they distinct outcomes (each closes one)?"
                ),
                answer_schema={"decision": ["one_outcome", "distinct"], "confidence": "float"},
            )
        )
    return succ, clusters, requests


def slice_s3(steps: list[Any], answers: dict | None = None):
    total = len(steps)
    answers = answers or {}
    succ, clusters, requests = _s3_requests(steps)

    boundaries = {0}
    boundaries |= set(s1_human_ask_starts(steps))
    boundaries |= {i for i, s in enumerate(steps) if _is_notification(s)}

    # Close after deliverable-completing steps. Same-outcome clusters collapse
    # to their LAST success unless an answer says "distinct".
    req_by_window_last = {}
    for ci, cl in enumerate(clusters):
        if len(cl) >= 2:
            req_by_window_last[ci] = f"s3-same-outcome-{ci}"
    for ci, cl in enumerate(clusters):
        if len(cl) < 2:
            close = cl[0][0]
            if close + 1 < total:
                boundaries.add(close + 1)
            continue
        rid = req_by_window_last[ci]
        decision = (answers.get(rid) or {}).get("decision", "one_outcome")
        if decision == "distinct":
            for (idx, _art) in cl:
                if idx + 1 < total:
                    boundaries.add(idx + 1)
        else:  # one_outcome -> only the last closes
            last = cl[-1][0]
            if last + 1 < total:
                boundaries.add(last + 1)

    def label(a, b):
        # name the closing outcome if a success ends the span
        for idx, art in succ:
            if idx == b:
                return f"milestone: {art}"
        return _user_label(steps, a, b)

    trajs = trajectories_from_boundaries(boundaries, total, "milestone", label)
    return trajs, [r.to_dict() for r in requests]


# ---------------------------------------------------------------------------
# S4 subgoal (cheap_llm — terminal-vs-intermediate pivot judgment)
# ---------------------------------------------------------------------------


def s4_pivot_candidates(steps: list[Any]) -> list[int]:
    """Agent steps that *might* open a new sub-goal: an agent step right after a
    completed outcome that initiates new work. Bounded by the number of
    successes."""
    succ_idx = {i for i, _ in success_steps(steps)}
    candidates = []
    n = len(steps)
    for i in range(1, n):
        if _role(steps[i]) != "agent":
            continue
        # prior step was a success and this step starts new tool work
        if (i - 1) in succ_idx and _tool_names(steps[i]):
            candidates.append(i)
    return candidates


def slice_s4(steps: list[Any], answers: dict | None = None):
    total = len(steps)
    answers = answers or {}
    boundaries = {0}
    boundaries |= set(s1_human_ask_starts(steps))  # the S1 spine

    candidates = s4_pivot_candidates(steps)
    requests = []
    for ci, cand in enumerate(candidates):
        rid = f"s4-pivot-{cand}"
        requests.append(
            JudgmentRequest(
                id=rid,
                kind="terminal_vs_intermediate",
                window=[max(0, cand - 3), min(total - 1, cand + 3)],
                candidate_step=cand,
                prompt=(
                    f"At step {cand} the prior outcome is complete. Is the agent now "
                    f"driving a DIFFERENT outcome (pivot -> open a new trajectory) or "
                    f"continuing the same sub-goal (retry/verify/spawn-collect -> stay)?"
                ),
                answer_schema={"decision": ["pivot", "stay"], "confidence": "float"},
            )
        )
        decision = (answers.get(rid) or {}).get("decision", "stay")  # conservative default
        if decision == "pivot":
            boundaries.add(cand)

    trajs = trajectories_from_boundaries(
        boundaries, total, "subgoal", lambda a, b: _user_label(steps, a, b)
    )
    return trajs, [r.to_dict() for r in requests]


SLICERS = {
    "s1": slice_s1,
    "s2": slice_s2,
    "s3": slice_s3,
    "s4": slice_s4,
}

SLICER_TIER = {"s1": "deterministic", "s2": "deterministic", "s3": "cheap_llm", "s4": "cheap_llm"}
