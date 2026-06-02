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
from typing import Callable, Protocol, Sequence

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
            suffix = "\n" if doc[point:] and content and not content.endswith("\n") else ""
            doc = doc[:point] + sep + content + suffix + doc[point:]
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
    larger early edits, smaller late), and ``autonomous`` (a deterministic
    controller-shaped decay that preserves a larger early budget, then tapers
    more aggressively as progress accumulates). Returns an int ``>= 1`` so at
    least one edit can always apply.
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
    elif schedule == "autonomous":
        # The paper's autonomous scheduler is teacher-controlled. The offline
        # default keeps that API surface deterministic by using a smooth,
        # conservative controller curve with the same bounds as the other
        # schedules; online harnesses can replace the scheduler later without
        # changing the report contract.
        value = floor + (base_lr - floor) * (1.0 - math.sqrt(frac))
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


def split_rows_three_way(
    rows: list[RolloutRow],
    *,
    selection_fraction: float = 0.25,
    test_fraction: float = 0.2,
    seed: str = "skillopt",
) -> tuple[list[RolloutRow], list[RolloutRow], list[RolloutRow]]:
    """Deterministically partition rows into ``(Dtrain, Dsel, Dtest)``.

    This is Algorithm 1's split structure (line 2 / line 37). The selection and
    test fractions are disjoint hash bands; the remaining rows form training.
    Tiny corpora may leave one split empty, and :func:`run_optimization` applies
    explicit fallback rules so the optimizer still has a deterministic signal.
    """
    selection: list[RolloutRow] = []
    test: list[RolloutRow] = []
    train: list[RolloutRow] = []
    sel = min(max(selection_fraction, 0.0), 1.0)
    tst = min(max(test_fraction, 0.0), 1.0)
    if sel + tst > 1.0:
        scale = 1.0 / (sel + tst)
        sel *= scale
        tst *= scale
    for row in rows:
        digest = hashlib.sha256(f"{seed}:three:{row.trace_id}".encode("utf-8")).hexdigest()
        bucket = (int(digest[:8], 16) % 1000) / 1000.0
        if bucket < sel:
            selection.append(row)
        elif bucket < sel + tst:
            test.append(row)
        else:
            train.append(row)
    return train, selection, test


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


def _chunks(rows: list[RolloutRow], size: int) -> list[list[RolloutRow]]:
    n = max(int(size), 1)
    return [rows[i:i + n] for i in range(0, len(rows), n)]


def reflection_minibatches(rows: list[RolloutRow], *, size: int) -> list[list[RolloutRow]]:
    """Split rollout evidence into success/failure minibatches of size ``Bm``.

    Algorithm 1 line 8 separates failures and successes before reflection. The
    returned batches preserve that separation: all failure minibatches first,
    followed by success minibatches. Empty input yields no batches.
    """
    success, failure = split_success_failure(rows)
    return _chunks(failure, size) + _chunks(success, size)


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


@dataclass(frozen=True)
class LongitudinalComparison:
    """Offline adjacent-epoch comparison for slow/meta updates.

    The online version will re-roll the same tasks under the previous and
    current skills. Offline mode compares the same rows through the deterministic
    coverage scorer, which preserves the paper's longitudinal shape without
    invoking an agent.
    """

    regressions: tuple[str, ...] = ()
    persistent_failures: tuple[str, ...] = ()
    improvements: tuple[str, ...] = ()
    stable_successes: tuple[str, ...] = ()


def longitudinal_comparison(
    previous_skill: str,
    current_skill: str,
    rows: list[RolloutRow],
) -> LongitudinalComparison:
    regressions: list[str] = []
    persistent_failures: list[str] = []
    improvements: list[str] = []
    stable_successes: list[str] = []
    for row in rows:
        before = score_skill_on_rows(previous_skill, [row])
        after = score_skill_on_rows(current_skill, [row])
        if after < before:
            regressions.append(row.trace_id)
        elif after > before:
            improvements.append(row.trace_id)
        elif after >= 0.999:
            stable_successes.append(row.trace_id)
        else:
            persistent_failures.append(row.trace_id)
    return LongitudinalComparison(
        regressions=tuple(regressions),
        persistent_failures=tuple(persistent_failures),
        improvements=tuple(improvements),
        stable_successes=tuple(stable_successes),
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


class Harness(Protocol):
    """Execution harness seam for SkillOpt Algorithm 1 line 7.

    Offline mode implements this over already-captured bucket rows. Online mode
    implements it by executing the target agent against tasks and returning fresh
    scored rollouts.
    """

    def collect_rollouts(self, skill_text: str, tasks: Sequence[object]) -> list[RolloutRow]:
        ...

    def score(self, skill_text: str, tasks: Sequence[object]) -> float:
        ...


@dataclass(frozen=True)
class BucketHarness:
    """Harness adapter over retrospective scored-rollout bucket rows."""

    rows: list[RolloutRow]
    selection_fraction: float = 0.25
    test_fraction: float = 0.2
    seed: str = "skillopt"

    def __post_init__(self) -> None:
        train, selection, test = split_rows_three_way(
            self.rows,
            selection_fraction=self.selection_fraction,
            test_fraction=self.test_fraction,
            seed=self.seed,
        )
        object.__setattr__(self, "train_tasks", train or self.rows)
        object.__setattr__(self, "selection_tasks", selection or self.rows)
        object.__setattr__(self, "test_tasks", test or selection or self.rows)
        object.__setattr__(
            self,
            "split_counts",
            {
                "train": len(train),
                "selection": len(selection),
                "test": len(test),
                "train_effective": len(train or self.rows),
                "selection_effective": len(selection or self.rows),
                "test_effective": len(test or selection or self.rows),
            },
        )

    train_tasks: list[RolloutRow] = field(init=False, repr=False)
    selection_tasks: list[RolloutRow] = field(init=False, repr=False)
    test_tasks: list[RolloutRow] = field(init=False, repr=False)
    split_counts: dict[str, int] = field(init=False)

    def collect_rollouts(self, skill_text: str, tasks: Sequence[object]) -> list[RolloutRow]:
        del skill_text
        return [task for task in tasks if isinstance(task, RolloutRow)]

    def score(self, skill_text: str, tasks: Sequence[object]) -> float:
        rows = [task for task in tasks if isinstance(task, RolloutRow)]
        return score_skill_on_rows(skill_text, rows)


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
    # Epoch-local feedback buffer (Algorithm 1 line 5 resets B at each epoch).
    rejected_buffer: list[dict] = field(default_factory=list)
    # Run-global audit log used for export; unlike B, this never resets.
    rejected_audit_log: list[dict] = field(default_factory=list)
    step_log: list[StepRecord] = field(default_factory=list)
    meta_skill: str = ""
    test_score: float = 0.0
    split_counts: dict[str, int] = field(default_factory=dict)

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
        observed_failure_tags: Sequence[str] = (),
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
            entry = {
                "edits": edits or [],
                "score_drop": round(self.current_score - candidate_score, 6),
                "candidate_score": candidate_score,
                "failure_note": failure_note,
                "observed_failure_tags": list(observed_failure_tags),
            }
            self.rejected_buffer.append(entry)
            self.rejected_audit_log.append(entry)

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
            "test_score": self.test_score,
            "split_counts": self.split_counts,
            "accepted_edits": sum(1 for r in self.step_log if r.accepted),
            "rejected_edits": len(self.rejected_audit_log),
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
            "rejected_buffer": self.rejected_audit_log,
            "epoch_feedback_buffer": self.rejected_buffer,
            "optimizer_meta_skill": self.meta_skill,
        }
        report_path = out / "edit_apply_report.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {"best_skill": str(skill_path), "report": str(report_path)}


# ---------------------------------------------------------------------------
# Orchestrator: the propose-and-rank loop over scored-rollout rows
# ---------------------------------------------------------------------------

# A proposer turns (current_skill, train_rows, budget, rejected_buffer,
# meta_skill) into a *ranked* list of candidate edit dicts. The deterministic
# default and the LLM chain both live in ``consumers.skill_opt.proposers``. The
# rejected buffer is the epoch-local negative-feedback channel (Algorithm 1 line
# 26), while meta_skill is optimizer-side only (C.2.8).
Proposer = Callable[..., list[dict]]

# A slow-update function consolidates durable, cross-epoch guidance from the
# train rows into a guidance string written to the protected region at an epoch
# boundary (SkillOpt's slow/meta update). Returns "" when nothing to add.
SlowUpdate = Callable[..., str]
MetaUpdate = Callable[..., str]


def default_slow_update(
    current_skill: str,
    train_rows: list[RolloutRow],
    *,
    previous_skill: str | None = None,
    comparison: LongitudinalComparison | None = None,
    previous_guidance: str = "",
) -> str:
    """Deterministic epoch-boundary consolidation.

    Writes a guidance block addressing failure tags that step-level edits left
    uncovered, into the protected slow-update region. Uses the same rule marker,
    so the consolidated guidance is visible to the coverage proxy and can pass
    the held-out gate. Returns "" when the body already covers every tag.
    """
    del previous_skill, previous_guidance  # kept for custom slow-update parity
    priority_rows = train_rows
    if comparison and comparison.persistent_failures:
        persistent = set(comparison.persistent_failures)
        priority_rows = [r for r in train_rows if r.trace_id in persistent] or train_rows
    uncovered = [
        t for t in failure_tags_of(priority_rows) if not _skill_addresses_tag(current_skill, t)
    ]
    if not uncovered:
        return ""
    lines = ["Durable cross-epoch guidance (consolidated):"]
    lines += [
        f"- {RULE_MARKER.format(tag=t)}: keep enforcing the fix for `{t}`." for t in uncovered
    ]
    return "\n".join(lines)


def default_meta_update(
    current_meta_skill: str,
    *,
    comparison: LongitudinalComparison,
    rejected_buffer: Sequence[dict],
) -> str:
    """Deterministic teacher-only optimizer memory update (C.2.8)."""
    del current_meta_skill
    rejected_tags: list[str] = []
    for entry in rejected_buffer:
        rejected_tags.extend(str(t) for t in entry.get("observed_failure_tags", ()))
    lines = ["Optimizer meta-skill:"]
    if comparison.improvements:
        lines.append(
            f"- Prioritize concrete marker-backed rules; {len(comparison.improvements)} adjacent-epoch task(s) improved."
        )
    if comparison.persistent_failures:
        lines.append(
            f"- Future edits should target persistent failures before broad rewrites: {', '.join(comparison.persistent_failures[:5])}."
        )
    if rejected_tags:
        unique = sorted(set(rejected_tags))
        lines.append(
            f"- Avoid repeating rejected low-signal tags without new evidence: {', '.join(unique[:5])}."
        )
    if len(lines) == 1:
        lines.append("- Keep edits specific, bounded, and easy for the selection gate to validate.")
    return "\n".join(lines)


def _call_proposer(
    propose: Proposer,
    current_skill: str,
    rows: list[RolloutRow],
    budget_hint: int,
    rejected: Sequence[dict],
    *,
    meta_skill: str,
) -> list[dict]:
    try:
        return propose(
            current_skill,
            rows,
            budget_hint,
            rejected,
            meta_skill=meta_skill,
        )
    except TypeError:
        # Backward-compatible path for tests or external callers that still
        # provide a four-argument proposer.
        return propose(current_skill, rows, budget_hint, rejected)


def propose_from_minibatches(
    propose: Proposer,
    current_skill: str,
    train_rows: list[RolloutRow],
    budget_hint: int,
    rejected: Sequence[dict],
    *,
    reflection_minibatch_size: int,
    meta_skill: str,
) -> list[dict]:
    """Run reflection over Bm-sized success/failure minibatches and merge.

    The merge here is deterministic and conservative: preserve first ranked
    occurrence, sum support where available, and keep the resulting list stable.
    LLM-backed proposers still run their C.2 merge chain inside each minibatch;
    this helper performs the cross-minibatch merge that Algorithm 1 requires.
    """
    batches = reflection_minibatches(train_rows, size=reflection_minibatch_size)
    if not batches:
        return []
    merged: dict[str, dict] = {}
    order: list[str] = []
    for batch in batches:
        for edit in _call_proposer(
            propose,
            current_skill,
            batch,
            budget_hint,
            rejected,
            meta_skill=meta_skill,
        ):
            key = str(edit.get("content") or edit)
            if key not in merged:
                merged[key] = dict(edit)
                order.append(key)
            else:
                merged[key]["support_count"] = int(merged[key].get("support_count", 0)) + int(
                    edit.get("support_count", 0)
                )
    return [merged[k] for k in order]


@dataclass(frozen=True)
class OptimizationResult:
    initial_skill: str
    best_skill: str
    initial_score: float
    best_score: float
    test_score: float
    steps: int
    accepted: int
    rejected: int
    state: SkillOptState
    split_counts: dict[str, int] = field(default_factory=dict)


def run_optimization(
    initial_skill: str,
    rows: list[RolloutRow],
    *,
    propose: Proposer,
    budget: float = 4,
    budget_floor: float = 2,
    schedule: str = "cosine",
    selection_fraction: float = 0.4,
    test_fraction: float = 0.2,
    seed: str = "skillopt",
    max_steps: int = 8,
    epochs: int = 1,
    reflection_minibatch_size: int = 8,
    slow_update: SlowUpdate | None = None,
    meta_update: MetaUpdate | None = default_meta_update,
    harness: Harness | None = None,
    train_tasks: Sequence[object] | None = None,
    selection_tasks: Sequence[object] | None = None,
    test_tasks: Sequence[object] | None = None,
    gate_fn: Callable[[str], float] | None = None,
    test_fn: Callable[[str], float] | None = None,
) -> OptimizationResult:
    """Run the propose-and-rank loop.

    Splits ``rows`` into ``Dtrain`` / ``Dsel`` / ``Dtest``, then for each of
    ``epochs`` epochs runs up to ``max_steps`` propose-and-rank steps. Each step
    calls ``harness.collect_rollouts(scur, Dtrain_batch)`` (offline:
    retrospective bucket rows; online: fresh agent executions), reflects over
    success/failure minibatches of size ``Bm``, clips merged edits to ``L_t``,
    applies them, and gates the candidate with ``harness.score(candidate,
    Dsel)``. After optimization, ``sbest`` is evaluated on held-out ``Dtest``
    and reported separately.
    Strictly-improving candidates are accepted; others are buffered. At each
    epoch boundary (when ``slow_update`` is given) a consolidated guidance block
    is written to the protected region and passed through the same gate. Stops an
    epoch early when the proposer offers nothing new.
    """
    active_harness = harness or BucketHarness(
        rows,
        selection_fraction=selection_fraction,
        test_fraction=test_fraction,
        seed=seed,
    )
    if harness is None:
        assert isinstance(active_harness, BucketHarness)
        train_task_list = list(active_harness.train_tasks)
        selection_task_list = list(active_harness.selection_tasks)
        test_task_list = list(active_harness.test_tasks)
        split_counts = dict(active_harness.split_counts)
    else:
        train_task_list = list(train_tasks if train_tasks is not None else rows)
        selection_task_list = list(selection_tasks if selection_tasks is not None else rows)
        test_task_list = list(test_tasks if test_tasks is not None else selection_task_list)
        split_counts = {
            "train": len(train_task_list),
            "selection": len(selection_task_list),
            "test": len(test_task_list),
            "train_effective": len(train_task_list),
            "selection_effective": len(selection_task_list),
            "test_effective": len(test_task_list),
        }

    if gate_fn is not None:
        evaluate_fn = gate_fn
    else:
        def evaluate_fn(skill: str) -> float:
            return active_harness.score(skill, selection_task_list)

    if test_fn is not None:
        evaluate_test_fn = test_fn
    else:
        def evaluate_test_fn(skill: str) -> float:
            return active_harness.score(skill, test_task_list)

    state = SkillOptState(current_skill=initial_skill)
    state.split_counts = split_counts
    state.prime(evaluate_fn)
    initial_score = state.current_score

    # Schedule denominator is the index of the last step (total - 1) so a
    # decaying budget actually reaches its floor on the final step.
    schedule_total = max(epochs * max_steps - 1, 1)
    global_step = 0
    previous_epoch_end_skill = state.current_skill
    previous_guidance = ""
    for epoch in range(epochs):
        epoch_num = epoch + 1
        state.rejected_buffer = []
        last_rollout_rows: list[RolloutRow] = []
        for _ in range(max_steps):
            lt = budget_at(global_step, schedule_total, budget, budget_floor, schedule)
            rollout_rows = active_harness.collect_rollouts(state.current_skill, train_task_list)
            last_rollout_rows = rollout_rows
            proposed = propose_from_minibatches(
                propose,
                state.current_skill,
                rollout_rows,
                lt,
                state.rejected_buffer,
                reflection_minibatch_size=reflection_minibatch_size,
                meta_skill=state.meta_skill,
            )
            if not proposed:
                break
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
                observed_failure_tags=failure_tags_of(rollout_rows),
            )
        # Epoch-boundary slow/meta update: consolidate durable guidance into the
        # protected region, then gate it like any other candidate.
        if epoch_num >= 2 and (slow_update is not None or meta_update is not None):
            comparison = longitudinal_comparison(
                previous_epoch_end_skill, state.current_skill, last_rollout_rows
            )
            if slow_update is not None:
                guidance = slow_update(
                    state.current_skill,
                    last_rollout_rows,
                    previous_skill=previous_epoch_end_skill,
                    comparison=comparison,
                    previous_guidance=previous_guidance,
                )
                if guidance:
                    candidate = apply_slow_update(state.current_skill, guidance)
                    if candidate != state.current_skill:
                        out = state.propose_and_test(
                            candidate,
                            evaluate_fn,
                            budget=0,
                            edits=[{"op": "slow_update", "content": guidance}],
                            failure_note=f"epoch {epoch} slow/meta update",
                            observed_failure_tags=failure_tags_of(last_rollout_rows),
                        )
                        if out["accepted"]:
                            previous_guidance = guidance
            if meta_update is not None:
                state.meta_skill = meta_update(
                    state.meta_skill,
                    comparison=comparison,
                    rejected_buffer=state.rejected_buffer,
                )
        previous_epoch_end_skill = state.current_skill

    state.test_score = evaluate_test_fn(state.best_skill)
    return OptimizationResult(
        initial_skill=initial_skill,
        best_skill=state.best_skill,
        initial_score=initial_score,
        best_score=state.best_score,
        test_score=state.test_score,
        steps=len(state.step_log),
        accepted=sum(1 for r in state.step_log if r.accepted),
        rejected=len(state.rejected_audit_log),
        state=state,
        split_counts=split_counts,
    )
