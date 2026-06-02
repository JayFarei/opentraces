"""Phase-2 tests: calibration is the measurement that earns reward.

Proves the gate is honest: it reaches `calibrated` ONLY on a rubric whose verdicts separate
effective from ineffective use against gold; it BLOCKS (never silent 1.0) on zero-separation,
insufficient labels, inversion, and a missing/permissive floor; the pure statistics match
hand-computed fixtures; and a marker-stuffed skill cannot move the calibration (adversarial).
"""

from __future__ import annotations

import pytest

from opentraces.consumers.verifier_factory import (
    CalibrationPolicy,
    adversarial_probe,
    calibrate_rubric,
    mann_whitney_auc,
    rubric_from_dict,
    spearman,
    weak_label,
)

# --- a rubric with a real deterministic floor + a deterministic discriminator ---
_RUBRIC = rubric_from_dict({
    "rubric_id": "rev_v1", "skill": "review", "title": "t", "semantic_target": "x",
    "criteria": [
        # discriminating floor: fires when the trace ran verification (not ~all/~none)
        {"element_id": "verified", "label": "ran verification", "marker": "rl.review.verified",
         "weight": 1.0, "detectors": {"command_families": ["verification"]}},
        # a second deterministic signal
        {"element_id": "grounded", "label": "touched files", "marker": "rl.review.grounded",
         "weight": 1.0, "detectors": {"has_files": True}},
    ],
})

_POLICY = CalibrationPolicy(min_human_labels=8, min_human_per_class=3, min_eff_neg=3.0,
                            auc_min=0.70, auc_min_pairs=8, rho_min=0.30, rho_min_n=8)


def _ep(i, *, verified, files, label="success", reward=0.8):
    return {
        "episode_id": f"e{i}", "source_trace_id": f"t{i}",
        "user_intent": "review the change", "task_summary": "r",
        "files_touched_json": '["a.py"]' if files else "[]",
        "tools_used_json": '["pytest"]' if verified else '["edit"]',
        "outcome_label": label, "outcome_reward": reward,
    }


# --- pure statistics --------------------------------------------------------

def test_mann_whitney_auc_matches_hand_fixture():
    assert mann_whitney_auc([1.0, 1.0, 0.9], [0.1, 0.2]) == 1.0      # perfect separation
    assert mann_whitney_auc([0.5], [0.5]) == 0.5                      # tie -> 0.5
    assert mann_whitney_auc([0.0], [1.0]) == 0.0                      # inverted
    assert mann_whitney_auc([], [0.1]) is None


def test_spearman_matches_hand_fixture():
    assert spearman([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert spearman([1, 1, 1], [1, 2, 3]) is None                    # zero variance


def test_weak_label_uses_committed_as_the_only_natural_negative():
    from types import SimpleNamespace
    rec_no_commit = SimpleNamespace(outcome=SimpleNamespace(committed=False, survival_state=None))
    assert weak_label({"outcome_label": "unknown", "outcome_reward": 0.0}, rec_no_commit) == 0
    rec_commit = SimpleNamespace(outcome=SimpleNamespace(committed=True, survival_state=None))
    assert weak_label({"outcome_label": "success", "outcome_reward": 0.8}, rec_commit) == 1


# --- the honest gate --------------------------------------------------------

def test_calibrated_on_separable_gold():
    # effective traces ran verification + touched files; ineffective did neither.
    eps = ([_ep(i, verified=True, files=True) for i in range(6)]
           + [_ep(100 + i, verified=False, files=False, label="unknown", reward=0.0) for i in range(6)])
    labels = {f"t{i}": 1 for i in range(6)}
    labels.update({f"t{100 + i}": 0 for i in range(6)})
    rep = calibrate_rubric(_RUBRIC, episodes=eps, human_labels=labels, policy=_POLICY)
    assert rep.status == "calibrated", rep.blockers
    assert rep.auc_human == pytest.approx(1.0)
    assert rep.adversarial["passed"] is True


def test_blocked_non_discriminating_when_evidence_uncorrelated_with_gold():
    # floor is non-degenerate (verified fires 50%) but the criteria don't separate the gold
    # classes -> each is demoted on precision and the rubric BLOCKS (never a silent pass).
    eps = [_ep(i, verified=(i < 6), files=(i < 6)) for i in range(12)]
    labels = {f"t{i}": (i % 2) for i in range(12)}  # gold uncorrelated with verified/files
    rep = calibrate_rubric(_RUBRIC, episodes=eps, human_labels=labels, policy=_POLICY)
    # both criteria demote on precision -> no surviving independent floor weight -> BLOCKED
    # (the effective-weight floor (M5) fires before the discrimination gate; either is honest).
    assert rep.status in {"blocked_no_floor", "blocked_non_discriminating"}, (rep.status, rep.blockers)
    assert rep.status != "calibrated"


def test_blocked_anticorrelated_gold_is_demoted_not_silently_passed():
    # criteria point the WRONG way vs gold -> demoted on precision -> BLOCKED (honest).
    eps = ([_ep(i, verified=False, files=False) for i in range(6)]
           + [_ep(100 + i, verified=True, files=True) for i in range(6)])
    labels = {f"t{i}": 1 for i in range(6)}
    labels.update({f"t{100 + i}": 0 for i in range(6)})
    rep = calibrate_rubric(_RUBRIC, episodes=eps, human_labels=labels, policy=_POLICY)
    assert rep.status.startswith("blocked"), rep.status
    assert rep.status != "calibrated"


def test_blocked_insufficient_labels_is_the_honest_default():
    # all-positive bucket, no human labels, no weak negatives -> BLOCKED, never a silent 1.0.
    # verified varies (floor non-degenerate) so we reach the labels gate, not blocked_no_floor.
    eps = [_ep(i, verified=(i < 8), files=True) for i in range(16)]
    rep = calibrate_rubric(_RUBRIC, episodes=eps, human_labels={}, policy=_POLICY)
    assert rep.status.startswith("blocked")
    assert rep.auc_human is None
    assert any("human" in b or "negative" in b for b in rep.blockers), rep.blockers


def test_provisional_weak_only_without_human_labels():
    # no human labels, but the committed=False weak negative separates -> provisional (reward
    # usable, recommended stays False per the locked decision)
    from types import SimpleNamespace
    eps, records = [], {}
    for i in range(8):  # effective: verified + committed
        e = _ep(i, verified=True, files=True, label="success", reward=0.8)
        eps.append(e); records[f"t{i}"] = SimpleNamespace(outcome=SimpleNamespace(committed=True, survival_state=None), steps=[])
    for i in range(8):  # weak negatives: not verified + not committed
        e = _ep(100 + i, verified=False, files=False, label="unknown", reward=0.0)
        eps.append(e); records[f"t{100 + i}"] = SimpleNamespace(outcome=SimpleNamespace(committed=False, survival_state=None), steps=[])
    rep = calibrate_rubric(_RUBRIC, episodes=eps, records=records, human_labels={}, policy=_POLICY)
    assert rep.status == "provisional_weak_only", (rep.status, rep.blockers)
    assert rep.rho_secondary is not None and rep.rho_secondary > 0


def test_self_judge_without_gold_blocks():
    # floor non-degenerate (verified fires 50%); no gold + same-session judge -> blocked.
    eps = [_ep(i, verified=(i < 4), files=True) for i in range(8)]
    rep = calibrate_rubric(_RUBRIC, episodes=eps, human_labels={}, policy=_POLICY,
                           same_session_self_judge=True)
    assert rep.status == "blocked_needs_human_labels", (rep.status, rep.blockers)


# --- adversarial: stuffing cannot move the calibration ----------------------

def test_adversarial_stuffing_invariance_and_legacy_sentinel():
    eps = [_ep(i, verified=(i % 2 == 0), files=True) for i in range(10)]
    probe = adversarial_probe(_RUBRIC, eps, {})
    assert probe["passed"] is True
    assert probe["stuffed_status_flip"] is False           # verdicts don't read skill text
    assert probe["legacy_score_skill_stuffed"] == pytest.approx(1.0)  # the OLD gate WAS gameable


def test_blocked_no_floor_when_floor_fires_on_everything():
    # a rubric whose only deterministic criterion is presence-only (fires ~always)
    rub = rubric_from_dict({
        "rubric_id": "x", "skill": "s", "title": "t", "semantic_target": "x",
        "criteria": [{"element_id": "any", "label": "touched", "marker": "rl.s.any",
                      "detectors": {"has_files": True}}],
    })
    eps = [_ep(i, verified=True, files=True) for i in range(10)]  # has_files fires 100%
    rep = calibrate_rubric(rub, episodes=eps, human_labels={}, policy=_POLICY)
    assert rep.status == "blocked_no_floor", rep.status
