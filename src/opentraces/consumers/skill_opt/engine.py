"""Offline SkillOpt edit-engine and propose-and-rank loop (arXiv 2605.23904).

This module is the controllable text-space skill optimizer's *offline*
substrate: the parts that need no live agent. It implements the SkillOpt
mechanics that the paper shows are load-bearing,

* a four-op patch grammar (append, insert_after, replace, delete) over a
  markdown skill document, with a marker-protected slow-update region that
  step-level edits cannot mutate;
* an edit budget ``L_t`` (the "textual learning rate") with constant / linear /
  cosine schedules, used to clip a ranked edit pool;
* the Algorithm-1 validation gate and state machine: a candidate skill is
  accepted only when it *strictly* improves the held-out selection score
  (ties are rejected, so the deployed skill never silently drifts);
* a rejected-edit buffer that retains failed edits as negative feedback;
* export of ``best_skill.md`` plus an ``edit_apply_report.json`` audit.

Scope of the first slice (see kb/br/66-skillopt-text-space-skill-optimizer.md).
The paper's validation gate re-rolls a candidate skill on a held-out task split
with a live agent and scores task success. That live re-rollout and the
task-outcome scorer are deferred to the next slice. Here the gate runs against
:func:`score_skill_on_rows`, a deterministic *proxy* over already-captured
scored-rollout rows: it blends the rows' mean rollout score (constant across
candidate skills, since the evidence is fixed) with the fraction of observed
failure tags the candidate skill now addresses. That makes the propose-and-rank
loop exercise real accept / reject / buffer behavior offline; swapping
``score_skill_on_rows`` for a live re-rollout scorer is the only change the
next slice needs at this seam.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

# ---------------------------------------------------------------------------
# Protected slow-update region delimiters (verbatim from the paper)
# ---------------------------------------------------------------------------

SLOW_START = "<!-- SLOW_UPDATE_START -->"
SLOW_END = "<!-- SLOW_UPDATE_END -->"

# Canonical marker the deterministic proposer uses to encode an addressed
# failure tag. Keeping coverage detection tied to one token keeps the proxy
# scorer and the proposer in lockstep.
RULE_MARKER = "rule[{tag}]"


# ---------------------------------------------------------------------------
# 1. Patch grammar
# ---------------------------------------------------------------------------


@dataclass
class EditResult:
    """Outcome of applying one atomic edit op."""

    op: str
    applied: bool
    reason: str = ""
    target: str | None = None


def _protected_span(skill: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` char offsets of the protected region *body*.

    The span covers the text between the delimiters (exclusive of the comment
    markers themselves). Returns ``None`` if the region is absent or malformed
    (start without end, or end before start).
    """
    s = skill.find(SLOW_START)
    if s == -1:
        return None
    e = skill.find(SLOW_END, s + len(SLOW_START))
    if e == -1:
        return None
    return (s + len(SLOW_START), e)


def _full_protected_span(skill: str) -> tuple[int, int] | None:
    """Return ``(start, end)`` covering the WHOLE protected region including both
    marker comments. Step edits may not touch any of this. ``None`` if absent or
    malformed.
    """
    s = skill.find(SLOW_START)
    if s == -1:
        return None
    e = skill.find(SLOW_END, s + len(SLOW_START))
    if e == -1:
        return None
    return (s, e + len(SLOW_END))


def _region_hits_protected(skill: str, lo: int, hi: int) -> bool:
    """True if the affected region intersects the full protected span.

    ``[lo, hi)`` is the byte range an edit would change. For an insertion pass a
    zero-width point (``hi == lo``): it is rejected only when strictly inside the
    span, so inserting immediately before the start marker or after the end
    marker stays allowed.
    """
    span = _full_protected_span(skill)
    if span is None:
        return False
    ps, pe = span
    if hi == lo:  # insertion point
        return ps < lo < pe
    return lo < pe and hi > ps


def _append_insertion_point(skill: str) -> int:
    """Where an ``append`` should land: end of body, but before a protected
    region if one sits at the document tail (append must never enter it)."""
    s = skill.find(SLOW_START)
    if s == -1:
        return len(skill)
    return s


def apply_edits(skill: str, edits: list[dict]) -> tuple[str, list[EditResult]]:
    """Apply a list of atomic edit ops to a markdown skill document.

    Ops: ``append``, ``insert_after``, ``replace``, ``delete``. Step-level edits
    MUST NOT mutate text inside the protected SLOW_UPDATE region; such ops are
    skipped with a reason. Targets are matched against the *live* document, so a
    batch sees the effect of earlier edits. Returns the new document and a
    per-edit result list.
    """
    results: list[EditResult] = []
    doc = skill

    for edit in edits:
        op = edit.get("op")

        if op == "append":
            content = edit.get("content", "")
            point = _append_insertion_point(doc)
            sep = "" if (point == 0 or doc[:point].endswith("\n")) else "\n"
            doc = doc[:point] + sep + content + doc[point:]
            results.append(EditResult(op="append", applied=True))

        elif op == "insert_after":
            target = edit.get("target", "")
            content = edit.get("content", "")
            idx = doc.find(target)
            if idx == -1:
                results.append(EditResult("insert_after", False, "target not found", target))
                continue
            ins = idx + len(target)
            if _region_hits_protected(doc, idx, idx + len(target)) or _region_hits_protected(doc, ins, ins):
                results.append(EditResult("insert_after", False, "target in protected region", target))
                continue
            sep = "" if content.startswith("\n") else "\n"
            doc = doc[:ins] + sep + content + doc[ins:]
            results.append(EditResult("insert_after", True, target=target))

        elif op == "replace":
            target = edit.get("target", "")
            content = edit.get("content", "")
            idx = doc.find(target)
            if idx == -1:
                results.append(EditResult("replace", False, "target not found", target))
                continue
            if _region_hits_protected(doc, idx, idx + len(target)):
                results.append(EditResult("replace", False, "target in protected region", target))
                continue
            doc = doc[:idx] + content + doc[idx + len(target):]
            results.append(EditResult("replace", True, target=target))

        elif op == "delete":
            target = edit.get("target", "")
            idx = doc.find(target)
            if idx == -1:
                results.append(EditResult("delete", False, "target not found", target))
                continue
            if _region_hits_protected(doc, idx, idx + len(target)):
                results.append(EditResult("delete", False, "target in protected region", target))
                continue
            doc = doc[:idx] + doc[idx + len(target):]
            results.append(EditResult("delete", True, target=target))

        else:
            results.append(EditResult(str(op), False, "unknown op"))

    return doc, results


# ---------------------------------------------------------------------------
# 2. Protected region slow update
# ---------------------------------------------------------------------------


def apply_slow_update(skill: str, guidance_text: str) -> str:
    """Overwrite the protected region body with new guidance.

    This is the only sanctioned way to mutate the protected region; if none
    exists, one is appended at the end of the document.
    """
    span = _protected_span(skill)
    body = f"\n{guidance_text}\n"
    if span is None:
        sep = "" if (skill == "" or skill.endswith("\n")) else "\n"
        return skill + sep + SLOW_START + body + SLOW_END + "\n"
    lo, hi = span
    return skill[:lo] + body + skill[hi:]


# ---------------------------------------------------------------------------
# 3. Edit budget (textual learning rate) + schedules
# ---------------------------------------------------------------------------


def budget_at(
    step_or_epoch: int,
    total: int,
    base_lr: float,
    floor: float,
    schedule: str,
) -> int:
    """Compute the edit budget ``L`` (max edits) for a given step.

    Schedules: ``constant`` (always ``base_lr``), ``linear`` (decay base_lr ->
    floor across ``total`` steps), ``cosine`` (cosine decay base_lr -> floor,
    larger early edits, smaller late). Returns an int ``>= 1`` so at least one
    edit can always apply.
    """
    if total <= 0:
        frac = 1.0
    else:
        frac = min(max(step_or_epoch / total, 0.0), 1.0)

    if schedule == "constant":
        value = base_lr
    elif schedule == "linear":
        value = base_lr + (floor - base_lr) * frac
    elif schedule == "cosine":
        cos = 0.5 * (1.0 + math.cos(math.pi * frac))  # 1 -> 0
        value = floor + (base_lr - floor) * cos
    else:
        raise ValueError(f"unknown schedule: {schedule}")

    return max(int(round(value)), 1)


def clip_to_budget(ranked_edits: list, budget: int) -> list:
    """Keep the top ``budget`` edits from a pre-ranked list."""
    if budget < 0:
        budget = 0
    return list(ranked_edits[:budget])


# ---------------------------------------------------------------------------
# Scored-rollout rows + split + proxy selection scorer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RolloutRow:
    """One scored-rollout row, the evidence unit consumed by the optimizer.

    Produced by the ``skill-opt-v1`` workflow's ``build_rows.py`` from an
    already-captured trace. ``reward`` in [0,1] is the SkillOpt rollout score
    ``r(s)`` (Algorithm 1), derived from the trace's real outcome (success,
    committed, Trail survival) — this is what drives the success/failure split
    and the gate. ``score`` is the trace's ``overall_utility / 100`` (a quality
    signal kept for context). ``failure_tags`` / ``success_tags`` are the failed
    / passed quality-check names that reflection mines for procedural rules.
    """

    trace_id: str
    score: float
    reward: float = 0.0
    failure_tags: tuple[str, ...] = ()
    success_tags: tuple[str, ...] = ()
    summary: str = ""

    @classmethod
    def from_json(cls, obj: dict) -> "RolloutRow":
        score = float(obj.get("score") or 0.0)
        # Older rows without an explicit reward fall back to the quality score.
        reward = obj.get("reward")
        return cls(
            trace_id=str(obj.get("trace_id") or ""),
            score=score,
            reward=float(reward) if reward is not None else score,
            failure_tags=tuple(obj.get("failure_tags") or ()),
            success_tags=tuple(obj.get("success_tags") or ()),
            summary=str(obj.get("summary") or ""),
        )


def split_rows_by_hash(
    rows: list[RolloutRow],
    *,
    selection_fraction: float = 0.4,
    seed: str = "skillopt",
) -> tuple[list[RolloutRow], list[RolloutRow]]:
    """Deterministically partition rows into ``(train, selection)`` by trace-id
    hash. The same trace always lands on the same side for a given ``seed``, so
    the selection split is a stable held-out set across runs.
    """
    train: list[RolloutRow] = []
    selection: list[RolloutRow] = []
    frac = min(max(selection_fraction, 0.0), 1.0)
    for row in rows:
        digest = hashlib.sha256(f"{seed}:{row.trace_id}".encode("utf-8")).hexdigest()
        bucket = (int(digest[:8], 16) % 1000) / 1000.0
        (selection if bucket < frac else train).append(row)
    return train, selection


def _skill_addresses_tag(skill: str, tag: str) -> bool:
    return RULE_MARKER.format(tag=tag) in skill


SUCCESS_REWARD_THRESHOLD = 0.5


def split_success_failure(
    rows: list[RolloutRow], *, threshold: float = SUCCESS_REWARD_THRESHOLD
) -> tuple[list[RolloutRow], list[RolloutRow]]:
    """Partition rows into ``(success, failure)`` by reward (Algorithm 1's
    success/failure minibatch separation, here driven by the real reward).
    Rows with ``reward >= threshold`` are successes; the rest are failures.
    """
    success = [r for r in rows if r.reward >= threshold]
    failure = [r for r in rows if r.reward < threshold]
    return success, failure


def tag_deficit_weights(rows: list[RolloutRow]) -> dict[str, float]:
    """Weight each failure tag by the reward deficit of the traces showing it.

    A tag seen on low-reward traces (large ``1 - reward``) matters more than the
    same tag on near-perfect traces. This grounds reflection and the gate in the
    real outcome signal rather than raw tag frequency.
    """
    weights: dict[str, float] = {}
    for row in rows:
        deficit = max(0.0, 1.0 - row.reward)
        for tag in row.failure_tags:
            weights[tag] = weights.get(tag, 0.0) + deficit
    return weights


def failure_tags_of(rows: list[RolloutRow]) -> list[str]:
    """Distinct failure tags ranked by descending reward-deficit weight, then by
    descending frequency, then name (deterministic). Tags whose total deficit is
    zero (only ever seen on full-reward traces) sort last but are still listed."""
    weights = tag_deficit_weights(rows)
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.failure_tags:
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(
        counts, key=lambda t: (-weights.get(t, 0.0), -counts[t], t)
    )


_ALIVE_SURVIVAL = {
    "alive_on_path", "alive_transformed", "alive_moved", "partially_preserved", "repaired",
}
_DEAD_SURVIVAL = {"reverted", "lost"}


def outcome_reward(
    *, success: bool | None, committed: bool, survival_state: str | None = None
) -> float:
    """Real rollout reward ``r(s)`` in [0,1] from a trace's outcome.

    The reward is the SkillOpt rollout score for an already-captured trace,
    derived from real evidence rather than a synthetic proxy: a landed commit is
    the dominant signal (+0.5), a verified-success outcome adds +0.3, and Trail
    survival of the commit adds +0.2 when the change is still alive or subtracts
    0.3 when it was reverted/lost. ``survival_state`` is optional (the JSONL
    capture path may not resolve it); reward degrades gracefully without it.
    """
    r = 0.0
    if committed:
        r += 0.5
    if success is True:
        r += 0.3
    if survival_state in _ALIVE_SURVIVAL:
        r += 0.2
    elif survival_state in _DEAD_SURVIVAL:
        r -= 0.3
    return round(max(0.0, min(1.0, r)), 6)


def score_skill_on_rows(skill: str, rows: list[RolloutRow]) -> float:
    """Reward-weighted held-out gate score (slice 2).

    Offline stand-in for a live re-rollout (slice 3): a candidate skill scores
    higher when it addresses the failure modes concentrated on LOW-reward traces.
    Each distinct failure tag is weighted by the reward deficit of the traces
    exhibiting it (:func:`tag_deficit_weights`); the score is the covered share
    of that weight, blended with the mean reward as a baseline. Covering a real,
    high-deficit failure mode raises the score (accept); covering nothing new
    ties (reject). Replaced by the live re-rollout gate in slice 3.
    """
    if not rows:
        return 0.0
    base = sum(r.reward for r in rows) / len(rows)
    weights = tag_deficit_weights(rows)
    total = sum(weights.values())
    if total <= 0:
        return round(base, 6)
    covered = sum(w for tag, w in weights.items() if _skill_addresses_tag(skill, tag))
    coverage = covered / total
    return round(0.5 * base + 0.5 * coverage, 6)


# ---------------------------------------------------------------------------
# 4 & 5. Validation gate + state machine + rejected-edit buffer
# ---------------------------------------------------------------------------


def _skill_hash(skill: str) -> str:
    return hashlib.sha256(skill.encode("utf-8")).hexdigest()


@dataclass
class StepRecord:
    """One optimization step's audit record (for export)."""

    step: int
    budget: int
    edits: list[dict]
    edit_results: list[dict]
    candidate_score: float
    accepted: bool


@dataclass
class SkillOptState:
    """Algorithm-1 core loop state for the offline optimizer.

    The ``score_cache`` is keyed on the skill hash alone, so it is only valid for
    a single evaluator / selection split over the state's lifetime (which is how
    :func:`run_optimization` uses it: one fresh state, one ``evaluate_fn``). Do
    not reuse a state across different evaluators without clearing the cache.
    """

    current_skill: str
    best_skill: str = ""
    current_score: float = 0.0
    best_score: float = 0.0
    score_cache: dict[str, float] = field(default_factory=dict)
    rejected_buffer: list[dict] = field(default_factory=list)
    step_log: list[StepRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.best_skill:
            self.best_skill = self.current_skill

    def prime(self, evaluate_fn: Callable[[str], float]) -> None:
        """Score the initial skill so the first candidate is gated against a
        real baseline rather than 0.0."""
        self.current_score = self._score(self.current_skill, evaluate_fn)
        self.best_score = self.current_score

    def _score(self, skill: str, evaluate_fn: Callable[[str], float]) -> float:
        h = _skill_hash(skill)
        if h in self.score_cache:
            return self.score_cache[h]
        score = float(evaluate_fn(skill))
        self.score_cache[h] = score
        return score

    def propose_and_test(
        self,
        candidate_skill: str,
        evaluate_fn: Callable[[str], float],
        *,
        budget: int = 0,
        edits: list[dict] | None = None,
        edit_results: list[EditResult] | None = None,
        failure_note: str = "",
    ) -> dict:
        """Score a candidate and apply the strict acceptance gate.

        Accept only if ``candidate_score`` strictly exceeds ``current_score``
        (ties rejected). On accept, advance current and update best on a new
        high. On reject, push a record onto ``rejected_buffer``.
        """
        candidate_score = self._score(candidate_skill, evaluate_fn)
        accepted = candidate_score > self.current_score

        if accepted:
            self.current_skill = candidate_skill
            self.current_score = candidate_score
            if candidate_score > self.best_score:
                self.best_score = candidate_score
                self.best_skill = candidate_skill
        else:
            self.rejected_buffer.append(
                {
                    "edits": edits or [],
                    "score_drop": round(self.current_score - candidate_score, 6),
                    "candidate_score": candidate_score,
                    "failure_note": failure_note,
                }
            )

        self.step_log.append(
            StepRecord(
                step=len(self.step_log),
                budget=budget,
                edits=edits or [],
                edit_results=[er.__dict__ for er in (edit_results or [])],
                candidate_score=candidate_score,
                accepted=accepted,
            )
        )
        return {
            "candidate_score": candidate_score,
            "accepted": accepted,
            "current_score": self.current_score,
            "best_score": self.best_score,
        }

    def export(self, directory: Path | str) -> dict[str, str]:
        """Write ``best_skill.md`` and ``edit_apply_report.json`` into ``directory``."""
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        skill_path = out / "best_skill.md"
        skill_path.write_text(self.best_skill, encoding="utf-8")
        report = {
            "schema_version": "opentraces.skill_opt.report.v1",
            "best_score": self.best_score,
            "accepted_edits": sum(1 for r in self.step_log if r.accepted),
            "rejected_edits": len(self.rejected_buffer),
            "steps": [
                {
                    "step": r.step,
                    "budget": r.budget,
                    "edits": r.edits,
                    "edit_results": r.edit_results,
                    "candidate_score": r.candidate_score,
                    "accepted": r.accepted,
                }
                for r in self.step_log
            ],
            "rejected_buffer": self.rejected_buffer,
        }
        report_path = out / "edit_apply_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {"best_skill": str(skill_path), "report": str(report_path)}


# ---------------------------------------------------------------------------
# Orchestrator: the propose-and-rank loop over scored-rollout rows
# ---------------------------------------------------------------------------

# A proposer turns (current_skill, train_rows, budget, rejected_buffer) into a
# *ranked* list of candidate edit dicts. The deterministic default and the LLM
# chain both live in ``consumers.skill_opt.proposers``. The rejected buffer is
# the negative-feedback channel (Algorithm 1 line 26).
Proposer = Callable[[str, list[RolloutRow], int, Sequence[dict]], list[dict]]

# A slow-update function consolidates durable, cross-epoch guidance from the
# train rows into a guidance string written to the protected region at an epoch
# boundary (SkillOpt's slow/meta update). Returns "" when nothing to add.
SlowUpdate = Callable[[str, list[RolloutRow]], str]


def default_slow_update(current_skill: str, train_rows: list[RolloutRow]) -> str:
    """Deterministic epoch-boundary consolidation.

    Writes a guidance block addressing failure tags that step-level edits left
    uncovered, into the protected slow-update region. Uses the same rule marker,
    so the consolidated guidance is visible to the coverage proxy and can pass
    the held-out gate. Returns "" when the body already covers every tag.
    """
    uncovered = [
        t for t in failure_tags_of(train_rows) if not _skill_addresses_tag(current_skill, t)
    ]
    if not uncovered:
        return ""
    lines = ["Durable cross-epoch guidance (consolidated):"]
    lines += [
        f"- {RULE_MARKER.format(tag=t)}: keep enforcing the fix for `{t}`." for t in uncovered
    ]
    return "\n".join(lines)


@dataclass(frozen=True)
class OptimizationResult:
    initial_skill: str
    best_skill: str
    initial_score: float
    best_score: float
    steps: int
    accepted: int
    rejected: int
    state: SkillOptState


def run_optimization(
    initial_skill: str,
    rows: list[RolloutRow],
    *,
    propose: Proposer,
    budget: float = 4,
    budget_floor: float = 2,
    schedule: str = "cosine",
    selection_fraction: float = 0.4,
    seed: str = "skillopt",
    max_steps: int = 8,
    epochs: int = 1,
    slow_update: SlowUpdate | None = None,
    gate_fn: Callable[[str], float] | None = None,
) -> OptimizationResult:
    """Run the propose-and-rank loop.

    Splits ``rows`` into train / held-out selection, then for each of ``epochs``
    epochs runs up to ``max_steps`` propose-and-rank steps: ``propose`` returns
    ranked edits over the train rows, they are clipped to the scheduled budget
    ``L_t``, applied to the current skill, and the candidate is gated. The gate
    is ``gate_fn`` when provided (slice 3's live re-rollout: re-roll the candidate
    skill on held-out tasks and score the fresh outcome), else the offline
    reward-weighted proxy :func:`score_skill_on_rows` over the selection split.
    Strictly-improving candidates are accepted; others are buffered. At each
    epoch boundary (when ``slow_update`` is given) a consolidated guidance block
    is written to the protected region and passed through the same gate. Stops an
    epoch early when the proposer offers nothing new.
    """
    train, selection = split_rows_by_hash(
        rows, selection_fraction=selection_fraction, seed=seed
    )
    # Gate on the held-out selection split, and reflect over the train split;
    # both fall back to all rows when a split is empty (tiny corpora or an
    # extreme selection_fraction), so the loop always has a signal.
    gate_rows = selection or rows
    train_rows = train or rows

    if gate_fn is not None:
        evaluate_fn = gate_fn
    else:
        def evaluate_fn(skill: str) -> float:
            return score_skill_on_rows(skill, gate_rows)

    state = SkillOptState(current_skill=initial_skill)
    state.prime(evaluate_fn)
    initial_score = state.current_score

    # Schedule denominator is the index of the last step (total - 1) so a
    # decaying budget actually reaches its floor on the final step.
    schedule_total = max(epochs * max_steps - 1, 1)
    global_step = 0
    for epoch in range(epochs):
        for _ in range(max_steps):
            proposed = propose(state.current_skill, train_rows, max_steps, state.rejected_buffer)
            if not proposed:
                break
            lt = budget_at(global_step, schedule_total, budget, budget_floor, schedule)
            global_step += 1
            clipped = clip_to_budget(proposed, lt)
            if not clipped:
                break
            candidate, edit_results = apply_edits(state.current_skill, clipped)
            if candidate == state.current_skill:
                # No textual change (all edits skipped); nothing to gate.
                break
            state.propose_and_test(
                candidate,
                evaluate_fn,
                budget=lt,
                edits=clipped,
                edit_results=edit_results,
                failure_note=f"epoch {epoch} step: {len(clipped)} edit(s) under budget {lt}",
            )
        # Epoch-boundary slow/meta update: consolidate durable guidance into the
        # protected region, then gate it like any other candidate.
        if slow_update is not None:
            guidance = slow_update(state.current_skill, train)
            if guidance:
                candidate = apply_slow_update(state.current_skill, guidance)
                if candidate != state.current_skill:
                    state.propose_and_test(
                        candidate,
                        evaluate_fn,
                        budget=0,
                        edits=[{"op": "slow_update", "content": guidance}],
                        failure_note=f"epoch {epoch} slow/meta update",
                    )

    return OptimizationResult(
        initial_skill=initial_skill,
        best_skill=state.best_skill,
        initial_score=initial_score,
        best_score=state.best_score,
        steps=len(state.step_log),
        accepted=sum(1 for r in state.step_log if r.accepted),
        rejected=len(state.rejected_buffer),
        state=state,
    )
