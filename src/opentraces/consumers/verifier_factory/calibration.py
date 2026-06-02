"""Calibration: the measurement that earns a rubric the right to feed reward.

This is "how we measure ourselves" in code. A rubric does not get to drive reward because
its tests are green; it gets to drive reward only after its per-trace verdicts demonstrably
SEPARATE effective from ineffective skill use — measured against human gold labels and the
one tamper-resistant signal git gives us (commit-landing; Trail survival when present) — and
only after a hard adversarial probe shows a marker-stuffed skill cannot move the rubric's
verdict. Until then the rubric is BLOCKED with a named remedy, never a silent 1.0.

Pure-Python, deterministic, network-free (no numpy/sklearn). See
``runs/skill-verifier-factory/RUBRIC_DESIGN.md`` §5-§6.

Trust posture (br/56): nothing here is author-settable. The agent proposes a rubric; this
module computes the calibration; a human approves. Per the locked decisions: provisional
(weak-only) rubrics may feed reward but are flagged and never recommended; judge
repeatability (G4) is advisory; judges are pooled with ``judge_id`` recorded.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from .archetypes import evidence_from_episode
from .detectors import detector_permissiveness_flags
from .rubric import (
    _MIN_FLOOR_WEIGHT_FRACTION,
    CalibrationPolicy,
    CalibrationReport,
    Criterion,
    Rubric,
    Verdict,
)
from .scorers import score_skill

#: Trail survival states (load-bearing only once `trail track` resolves the bucket).
_DEAD_SURVIVAL = frozenset({"reverted", "lost"})
_ALIVE_SURVIVAL = frozenset({"alive_on_path", "alive_transformed", "alive_moved",
                             "partially_preserved", "repaired"})

#: A deterministic floor criterion firing on more than this fraction of traces cannot
#: discriminate (e.g. committed=True at ~97%) and is rejected as the floor.
_FLOOR_MAX_FIRE_RATE = 0.95


# ---------------------------------------------------------------------------
# Weak label — the negative class the bucket ACTUALLY has (committed=False)
# ---------------------------------------------------------------------------

def weak_label(episode: dict[str, Any], record: Any = None) -> int | None:
    """Best-effort weak truth: 1=effective, 0=ineffective, None=abstain (dropped).

    Survival is load-bearing only once Trail resolves; today the only natural negative is
    "claimed success but nothing committed" (the contradiction the data actually contains).
    """
    outcome = getattr(record, "outcome", None) if record is not None else None
    survival = getattr(outcome, "survival_state", None)
    if survival in _DEAD_SURVIVAL:
        return 0
    if survival in _ALIVE_SURVIVAL:
        return 1
    committed = getattr(outcome, "committed", None)
    label = str(episode.get("outcome_label") or "")
    reward = float(episode.get("outcome_reward") or 0.0)
    if committed is False or label == "unknown" or (label == "failure"):
        return 0
    if committed is True or (label == "success" and reward >= 0.5):
        return 1
    return None


# ---------------------------------------------------------------------------
# Per-criterion prediction over a trace (deterministic computed; agent/human posted)
# ---------------------------------------------------------------------------

def _criterion_prediction(
    criterion: Criterion,
    episode: dict[str, Any],
    record: Any,
    agent_verdicts: dict[tuple[str, str], float] | None,
    trace_id: str,
) -> tuple[bool | None, float | None]:
    """Return (fired, value) for this criterion on this trace, or (None, None) if unavailable.

    ``fired`` is the boolean prediction (detector fired / posted value>=0.5); for a
    negative-direction criterion ``fired`` means the failure is present. ``value`` is the
    raw [0,1] verdict value (positive-sense) used for rubric scoring.
    """
    if criterion.judge_method == "deterministic":
        ev = evidence_from_episode(episode, record)
        fired = criterion.detect(ev)
        return fired, (1.0 if fired else 0.0)
    posted = (agent_verdicts or {}).get((criterion.id, trace_id))
    if posted is None:
        return None, None
    return (float(posted) >= 0.5), float(posted)


def trace_verdicts(
    rubric: Rubric, episode: dict[str, Any], record: Any = None,
    agent_verdicts: dict[tuple[str, str], float] | None = None,
) -> dict[str, Verdict]:
    """Per-criterion verdicts for ONE trace (deterministic computed; agent/human from the map)."""
    tid = _trace_id(episode)
    out: dict[str, Verdict] = {}
    for c in rubric.criteria:
        fired, value = _criterion_prediction(c, episode, record, agent_verdicts, tid)
        out[c.id] = Verdict(criterion_id=c.id, blocked=value is None, value=value or 0.0)
    return out


def trace_failure_markers(
    rubric: Rubric, episode: dict[str, Any], record: Any = None,
    agent_verdicts: dict[tuple[str, str], float] | None = None,
) -> list[str]:
    """Markers of CALIBRATION-GATED (effective_weight>0) criteria that FAILED on this trace.

    A positive criterion fails when its verdict value < 0.5; a negative-direction criterion fails
    when it FIRES. Only gated criteria contribute — a non-calibrated/demoted marker can never enter
    the optimizer's addressable set (closes the marker-stuffing channel at the reward layer)."""
    tid = _trace_id(episode)
    out: list[str] = []
    for c in rubric.criteria:
        if rubric._effective_weight(c) <= 0:
            continue
        fired, value = _criterion_prediction(c, episode, record, agent_verdicts, tid)
        if value is None:
            continue
        failed = (fired if c.direction == "negative" else value < 0.5)
        if failed:
            out.append(c.marker)
    return out


def _is_outcome_derived(criterion: Criterion) -> bool:
    """True if a deterministic criterion reads outcome-derived fields (shares inputs with y*)."""
    if criterion.judge_method != "deterministic":
        return False
    d = criterion.detectors
    return bool(d.committed or d.outcome_reward_min is not None or d.outcome_label_in)


def _fire_rate(criterion: Criterion, episodes, records) -> float:
    if not episodes:
        return 0.0
    n = 0
    for ep in episodes:
        ev = evidence_from_episode(ep, records.get(str(ep.get("source_trace_id") or "")) if records else None)
        if criterion.detect(ev):
            n += 1
    return n / len(episodes)


# ---------------------------------------------------------------------------
# Pure statistics
# ---------------------------------------------------------------------------

def mann_whitney_auc(pos: list[float], neg: list[float]) -> float | None:
    """AUC = P(score(pos) > score(neg)), ties credited 0.5. None if either class empty."""
    if not pos or not neg:
        return None
    wins = 0.0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1.0
            elif p == n:
                wins += 0.5
    return round(wins / (len(pos) * len(neg)), 6)


def _rankdata(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0  # average rank (1-based)
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation. None if <2 points or zero variance in either rank."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _rankdata(xs), _rankdata(ys)
    n = len(xs)
    mx = sum(rx) / n
    my = sum(ry) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / (sxx ** 0.5 * syy ** 0.5), 6)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def _trace_id(ep: dict[str, Any]) -> str:
    return str(ep.get("source_trace_id") or ep.get("episode_id") or "")


def _combined_truth(
    episodes, records, human_labels: dict[str, int] | None
) -> dict[str, tuple[int, float]]:
    """trace_id -> (y*, weight). human gold wins (weight 1.0); else weak_label (weight W_WEAK)."""
    human_labels = human_labels or {}
    out: dict[str, tuple[int, float]] = {}
    for ep in episodes:
        tid = _trace_id(ep)
        if tid in human_labels:
            out[tid] = (int(human_labels[tid]), 1.0)
        else:
            wl = weak_label(ep, records.get(tid) if records else None)
            if wl is not None:
                out[tid] = (wl, None)  # weight filled by caller (policy.weak_neg_weight)
    return out


def calibrate_rubric(
    rubric: Rubric,
    *,
    episodes: list[dict[str, Any]],
    records: dict[str, Any] | None = None,
    human_labels: dict[str, int] | None = None,
    agent_verdicts: dict[tuple[str, str], float] | None = None,
    policy: CalibrationPolicy = CalibrationPolicy(),
    same_session_self_judge: bool = False,
    gold_is_emulated: bool = False,
) -> CalibrationReport:
    """Compute the calibration report + status for ``rubric`` over ``episodes``.

    ``human_labels``: trace_id -> {0,1} gold (1=effective). ``agent_verdicts``:
    (criterion_id, trace_id) -> value in [0,1] posted by the in-loop agent judge.
    ``gold_is_emulated``: the provided labels are an emulated stand-in (a pipeline demo, not real
    human gold) — the status is then CAPPED at ``provisional_weak_only`` (never ``calibrated``).
    """
    records = records or {}
    human_labels = human_labels or {}
    W = policy.weak_neg_weight
    truth = _combined_truth(episodes, records, human_labels)

    n_human_pos = sum(1 for v in human_labels.values() if int(v) == 1)
    n_human_neg = sum(1 for v in human_labels.values() if int(v) == 0)
    n_weak_neg = sum(
        1 for tid, (y, w) in truth.items() if w is None and y == 0
    )
    n_eff_neg = n_human_neg + W * n_weak_neg

    # --- per-criterion precision / recall / discrimination vs independent truth ---
    # KEY (M2): an `agent` (self-judged) criterion is validated ONLY against independent HUMAN
    # gold — never against the weak label it may have been aligned to. So a self-judged criterion
    # earns reward weight only from human labels; with no gold it stays demoted to 0.
    per_criterion: list[dict] = []
    blockers: list[str] = []
    for c in rubric.criteria:
        is_agent = c.judge_method != "deterministic"
        outcome_derived = _is_outcome_derived(c)
        tp = fp = fn = tn = 0.0
        n_labels = 0
        pred_pos: list[float] = []
        pred_neg: list[float] = []
        for ep in episodes:
            tid = _trace_id(ep)
            if tid not in truth:
                continue
            y, w = truth[tid]
            is_human = w is not None
            # M2: an `agent` (self-judged) criterion earns trust ONLY from INDEPENDENT
            # human gold — never from the weak label it aligned to, and never from
            # emulated stand-in labels (a demo, not independent gold). Deterministic
            # criteria are tamper-resistant, so they still use the (capped) emulated signal.
            human_independent = is_human and not gold_is_emulated
            if is_agent and not human_independent:
                continue  # self-judged criterion: only independent human gold counts toward its trust
            weight = 1.0 if is_human else W
            fired, _ = _criterion_prediction(c, ep, records.get(tid), agent_verdicts, tid)
            if fired is None:
                continue
            n_labels += 1
            p = 1 if fired else 0
            (pred_pos if y == 1 else pred_neg).append(float(p))
            if p == 1 and y == 1:
                tp += weight
            elif p == 1 and y == 0:
                fp += weight
            elif p == 0 and y == 1:
                fn += weight
            else:
                tn += weight
        precision = round(tp / (tp + fp), 6) if (tp + fp) > 0 else 0.0
        recall = round(tp / (tp + fn), 6) if (tp + fn) > 0 else 0.0
        f1 = round(2 * precision * recall / (precision + recall), 6) if (precision + recall) > 0 else 0.0
        disc = None
        if pred_pos and pred_neg:
            disc = round(sum(pred_pos) / len(pred_pos) - sum(pred_neg) / len(pred_neg), 6)
        # demotion -> effective_weight 0 (advisory): too few labels, or imprecise/non-discriminating.
        demoted = False
        eff_weight = c.weight
        if n_labels < policy.per_criterion_min_labels:
            demoted, eff_weight = True, 0.0
        elif (precision < policy.per_criterion_precision_min
              or (disc is not None and disc < policy.per_criterion_discrimination_min)):
            demoted, eff_weight = True, 0.0
        per_criterion.append({
            "criterion_id": c.id, "judge_method": c.judge_method, "direction": c.direction,
            "outcome_derived": outcome_derived,
            "precision": precision, "recall": recall, "f1": f1, "discrimination": disc,
            "n_labels": n_labels, "effective_weight": eff_weight, "demoted": demoted,
        })

    report_stub = CalibrationReport(rubric_id=rubric.rubric_id, policy=policy,
                                    per_criterion=tuple(per_criterion))
    rubric_cal = dataclasses.replace(rubric, calibration=report_stub)

    # --- rubric score per trace (with demoted weights) ---
    def _score(rb: Rubric, ep: dict[str, Any]) -> float | None:
        tid = _trace_id(ep)
        verdicts: dict[str, Verdict] = {}
        for c in rb.criteria:
            fired, value = _criterion_prediction(c, ep, records.get(tid), agent_verdicts, tid)
            if value is None:
                verdicts[c.id] = Verdict(criterion_id=c.id, blocked=True)
            else:
                verdicts[c.id] = Verdict(criterion_id=c.id, value=value)
        return rb.score(verdicts)

    # --- (1) AUC vs gold (primary) ---
    gold_pos, gold_neg = [], []
    for ep in episodes:
        tid = _trace_id(ep)
        if tid not in human_labels:
            continue
        s = _score(rubric_cal, ep)
        if s is None:
            continue
        (gold_pos if int(human_labels[tid]) == 1 else gold_neg).append(s)
    auc_human = None
    if gold_pos and gold_neg and len(gold_pos) * len(gold_neg) >= policy.auc_min_pairs:
        auc_human = mann_whitney_auc(gold_pos, gold_neg)

    # --- (2) Spearman vs weak class (secondary, always attempted) ---
    # non-independence guard: if any deterministic criterion is outcome-derived, score the
    # rho leg over a sub-rubric EXCLUDING those criteria (else rho is circular with y*).
    has_outcome_det = any(_is_outcome_derived(c) for c in rubric.criteria)
    rho_rubric = rubric_cal
    rho_independent = True
    if has_outcome_det:
        kept = tuple(c for c in rubric.criteria if not _is_outcome_derived(c))
        if kept:
            rho_rubric = dataclasses.replace(rubric_cal, criteria=kept)
        else:
            # (M4) ALL criteria are outcome-derived: there is NO independent discriminator, so the
            # weak-class rho would be circular (committed vs committed). Refuse to compute it.
            rho_independent = False
    rho_scores, rho_truth = [], []
    if rho_independent:
        for ep in episodes:
            tid = _trace_id(ep)
            if tid not in truth:
                continue
            s = _score(rho_rubric, ep)
            if s is None:
                continue
            rho_scores.append(s)
            rho_truth.append(float(truth[tid][0]))
    rho_secondary = None
    if rho_independent and len(rho_scores) >= policy.rho_min_n and len(set(rho_truth)) > 1:
        rho_secondary = spearman(rho_scores, rho_truth)

    # --- (3) discrimination floor: at least one weighted criterion separates ---
    def _disc_ok(row) -> bool:
        return (row["effective_weight"] > 0 and row["discrimination"] is not None
                and row["discrimination"] >= policy.per_criterion_discrimination_min)

    any_disc = any(_disc_ok(row) for row in per_criterion)
    # autoverify (self-judge) may only claim provisional via an EXTERNAL anchor: a DETERMINISTIC,
    # NON-outcome-derived criterion separating the weak class (M3: an outcome-derived detector is
    # just the weak label re-read — it is not an independent anchor and never counts here).
    any_disc_det = any(
        _disc_ok(row) for row in per_criterion
        if row["judge_method"] == "deterministic" and not row["outcome_derived"]
    )
    # (M5) re-assert the un-self-gradeable floor on EFFECTIVE (post-demotion) weight: the surviving
    # INDEPENDENT (non-outcome-derived) deterministic weight must still carry >= the floor fraction,
    # else reward would be dominated by self-judged or circular (outcome-derived) criteria.
    eff_total = sum(r["effective_weight"] for r in per_criterion)
    eff_indep_det = sum(
        r["effective_weight"] for r in per_criterion
        if r["judge_method"] == "deterministic" and not r["outcome_derived"]
    )
    eff_floor_ok = eff_total > 0 and (eff_indep_det / eff_total) >= _MIN_FLOOR_WEIGHT_FRACTION

    # --- (G0) structural + non-degenerate deterministic floor (declared AND effective) ---
    det = [c for c in rubric.criteria if c.judge_method == "deterministic"]
    floor_declared_ok = False
    for c in det:
        if detector_permissiveness_flags(c.detectors, c.id):
            continue
        if _fire_rate(c, episodes, records) >= _FLOOR_MAX_FIRE_RATE:
            continue
        floor_declared_ok = True
        break
    floor_ok = floor_declared_ok and eff_floor_ok

    # --- adversarial probe (hard gate) ---
    adversarial = adversarial_probe(rubric_cal, episodes, records, agent_verdicts)

    # --- status machine (§5.5) ---
    status, gate_blockers = _status(
        policy=policy, n_human_labels=len(human_labels), n_human_pos=n_human_pos,
        n_human_neg=n_human_neg, n_eff_neg=n_eff_neg, floor_ok=floor_ok,
        auc_human=auc_human, rho_secondary=rho_secondary, any_disc=any_disc,
        any_disc_det=any_disc_det, adversarial=adversarial,
        same_session_self_judge=same_session_self_judge, gold_is_emulated=gold_is_emulated,
    )
    blockers.extend(gate_blockers)

    return CalibrationReport(
        rubric_id=rubric.rubric_id, policy=policy,
        n_human_labels=len(human_labels), n_human_pos=n_human_pos, n_human_neg=n_human_neg,
        n_weak_neg=n_weak_neg, n_eff_neg=round(n_eff_neg, 6),
        per_criterion=tuple(per_criterion),
        auc_human=auc_human, rho_secondary=rho_secondary, adversarial=adversarial,
        max_flip_rate=None,  # G4 advisory (no live re-judge in this path)
        status=status, blockers=tuple(blockers),
    )


def _status(*, policy, n_human_labels, n_human_pos, n_human_neg, n_eff_neg, floor_ok,
            auc_human, rho_secondary, any_disc, any_disc_det, adversarial,
            same_session_self_judge, gold_is_emulated=False):
    blockers: list[str] = []
    # G0 SHAPE
    if not floor_ok:
        return "blocked_no_floor", ["no non-degenerate deterministic floor criterion "
                                    "(floor must fire on <95% of traces and not be presence-only)"]
    # G3 ADVERS (independent of labels; meaningful even when blocked)
    if not adversarial.get("passed", False):
        return "blocked_adversarial", [f"adversarial gate failed: {adversarial.get('reason', 'unknown')}"]
    # G2 DISCRIM direction
    if auc_human is not None and auc_human < 0.5 - 1e-9:
        return "blocked_inverted", [f"rubric is INVERTED vs gold (auc={auc_human})"]
    if rho_secondary is not None and rho_secondary < -1e-9 and auc_human is None:
        return "blocked_inverted", [f"rubric is INVERTED vs weak class (rho={rho_secondary})"]
    discriminates = (
        (auc_human is not None and auc_human >= policy.auc_min)
        or (auc_human is None and rho_secondary is not None and rho_secondary >= policy.rho_min)
    ) and any_disc
    # G1 LABELS (path to calibrated)
    have_labels = (
        n_human_labels >= policy.min_human_labels
        and n_human_pos >= policy.min_human_per_class
        and n_human_neg >= policy.min_human_per_class
        and n_eff_neg >= policy.min_eff_neg
    )
    if have_labels:
        # G5 SELF: with gold present this is advisory; calibrated requires discrimination
        if not discriminates:
            return "blocked_non_discriminating", [
                "rubric does not separate effective/ineffective use vs gold "
                f"(auc={auc_human}, rho={rho_secondary})"]
        if gold_is_emulated:
            # (M1) emulated labels are a pipeline demo, never real gold -> cap at provisional.
            return "provisional_weak_only", [
                "gold labels are EMULATED (a pipeline demo, not real human gold); supply real "
                "human labels (>= 3/class) for `calibrated`."]
        return "calibrated", []
    # no/insufficient human labels -> provisional iff it discriminates vs the weak class
    if same_session_self_judge:
        # autoverify / same-session: NEVER calibrated without human gold. May reach provisional
        # ONLY via an EXTERNAL anchor — a DETERMINISTIC criterion that separates the weak
        # (committed-derived) negative — never via the agent's own self-judgments.
        if rho_secondary is not None and rho_secondary >= policy.rho_min and any_disc_det:
            return "provisional_weak_only", [
                f"weak-only (autoverify): a deterministic criterion separates the committed-derived "
                f"negative (rho={rho_secondary}); supply {policy.min_human_labels} human labels "
                "(>= 3/class) for full calibration. Self-judged criteria do not count toward trust."]
        return "blocked_needs_human_labels", [
            f"autoverify needs human gold to calibrate ({policy.min_human_labels} labels, "
            f">= {policy.min_human_per_class}/class) OR a deterministic criterion that separates the "
            f"weak negative; have {n_human_labels} labels, agent authored + judged in-session"]
    if rho_secondary is not None and rho_secondary >= policy.rho_min and any_disc:
        return "provisional_weak_only", [
            f"weak-only: discriminates vs the committed-derived negative (rho={rho_secondary}); "
            f"supply {policy.min_human_labels} human labels for full calibration"]
    if n_human_labels == 0 and rho_secondary is None:
        return "blocked_insufficient_labels", [
            f"no human labels and the weak negative class is too small "
            f"(need >= {policy.rho_min_n} resolved points with both classes); "
            f"gather human counterexamples"]
    return "blocked_insufficient_labels", [
        f"need {policy.min_human_labels} human labels (>= {policy.min_human_per_class}/class) "
        f"and n_eff_neg >= {policy.min_eff_neg}; have labels={n_human_labels}, n_eff_neg={round(n_eff_neg,3)}"]


# ---------------------------------------------------------------------------
# Adversarial probe (the proven attacks become a permanent gate)
# ---------------------------------------------------------------------------

def adversarial_probe(
    rubric: Rubric, episodes, records, agent_verdicts=None
) -> dict[str, Any]:
    """Two hard checks, both meaningful even when the rubric is BLOCKED.

    G3a (status-flip invariance): the rubric's per-trace verdicts are computed from EVIDENCE,
    never from skill text — so a marker-stuffed skill cannot change any verdict, and therefore
    cannot flip the rubric's status. We assert this structurally and confirm the LEGACY
    score_skill(stuffed)~=1.0 as a regression sentinel (the old gate WAS gameable).
    G3b (permissive floor): a rubric whose only floor is a presence-only / >95%-firing
    deterministic criterion is not a valid floor.
    """
    records = records or {}
    # G3a: stuffed skill -> verdicts unchanged (verdicts don't read skill text).
    stuffed = "I will do no useful work.\n" + "\n".join(f"rule[{m}]" for m in rubric.markers)
    legacy_score = score_skill(rubric.to_archetype(), stuffed) if rubric.markers else 0.0

    def _verdict_vector(_skill_text: str) -> list[tuple[str, float, bool]]:
        # verdicts are a pure function of (criterion, episode, record) — _skill_text is ignored
        # by construction; we pass it only to prove independence.
        out = []
        for ep in episodes:
            tid = _trace_id(ep)
            for c in rubric.criteria:
                fired, value = _criterion_prediction(c, ep, records.get(tid), agent_verdicts, tid)
                out.append((f"{c.id}:{tid}", -1.0 if value is None else value, fired is None))
        return out

    v_stuffed = _verdict_vector(stuffed)
    v_empty = _verdict_vector("")
    status_flip = v_stuffed != v_empty

    # G3b: a valid non-degenerate deterministic floor must exist.
    det = [c for c in rubric.criteria if c.judge_method == "deterministic"]
    permissive_floor_only = bool(det) and all(
        detector_permissiveness_flags(c.detectors, c.id)
        or _fire_rate(c, episodes, records) >= _FLOOR_MAX_FIRE_RATE
        for c in det
    )

    reason = ""
    passed = True
    if status_flip:
        passed, reason = False, "verdicts changed with skill text (reward-input not invariant)"
    elif permissive_floor_only:
        passed, reason = False, "only floor is a permissive / >95%-firing deterministic criterion"
    return {
        "stuffed_status_flip": status_flip,
        "legacy_score_skill_stuffed": legacy_score,
        "permissive_floor_only": permissive_floor_only,
        "passed": passed,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Emulated labeling (transparent stand-in for human gold — a pipeline demo, not real gold)
# ---------------------------------------------------------------------------

def emulate_human_labels(
    episodes, records=None, *, effective_rule=None,
) -> dict[str, int]:
    """Produce EMULATED human labels to exercise the pipeline end-to-end.

    These are NOT real human gold — they are a documented stand-in so the calibration loop
    can be demonstrated on a bucket that has no labels. Callers MUST flag provenance
    ("emulated") and keep recommended=False. The default rule labels a trace effective(1)
    iff it committed AND ran verification, ineffective(0) iff it claimed success but did not
    commit (or ran no verification) — an INDEPENDENT-ish heuristic over evidence the rubric's
    own criteria need not use.
    """
    records = records or {}
    labels: dict[str, int] = {}
    for ep in episodes:
        tid = _trace_id(ep)
        ev = evidence_from_episode(ep, records.get(tid))
        if effective_rule is not None:
            labels[tid] = int(effective_rule(ev, ep))
            continue
        committed = bool(getattr(getattr(records.get(tid), "outcome", None), "committed", False)) \
            or str(ep.get("outcome_label")) == "success"
        verified = ev.has_family("verification")
        labels[tid] = 1 if (committed and verified) else 0
    return labels
