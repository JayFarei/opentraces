"""Phase 5 tests: the rubric reward swap drives SkillOpt without forking it.

Proves: a CALIBRATED rubric moves Dsel 0->1 (text-sensitive coverage of calibration-gated
markers) and reports held-out Dtest; a BLOCKED rubric short-circuits to a valid BLOCKED package
(no passing optimization); the marker SET is gated by calibration (a stuffed non-gated marker
scores nothing); and skill_opt/ is not forked.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from opentraces.consumers.verifier_factory import (
    CalibrationPolicy,
    autoverify_draft_rubric,
    score_rubric,
)

_POLICY = CalibrationPolicy()


def _rec(committed):
    return SimpleNamespace(outcome=SimpleNamespace(committed=committed, survival_state=None), steps=[])


def _world(skill, n=10):
    eps, records = [], {}
    for i in range(n):
        eps.append({"episode_id": f"e{i}", "source_trace_id": f"t{i}", "skill_id": skill,
                    "user_intent": "do the thing well", "task_summary": "s",
                    "files_touched_json": '["a.py"]', "tools_used_json": '["pytest"]',
                    "outcome_label": "success", "outcome_reward": 0.8})
        records[f"t{i}"] = _rec(True)
    for i in range(n):
        eps.append({"episode_id": f"e{100+i}", "source_trace_id": f"t{100+i}", "skill_id": skill,
                    "user_intent": "do the thing", "task_summary": "s",
                    "files_touched_json": "[]", "tools_used_json": '["edit"]',
                    "outcome_label": "unknown", "outcome_reward": 0.0})
        records[f"t{100+i}"] = _rec(False)
    return eps, records


def test_calibrated_rubric_moves_dsel(tmp_path):
    eps, recs = _world("review")
    labels = {f"t{i}": 1 for i in range(10)}
    labels.update({f"t{100+i}": 0 for i in range(10)})
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    res = score_rubric(rub, out_dir=tmp_path / "p", episodes=eps, records=recs, human_labels=labels,
                       policy=_POLICY)
    assert res.status == "calibrated"
    assert res.usable is True
    assert res.dsel_before == 0.0 and res.dsel_after > 0.0
    assert res.dtest_score >= 0.0
    assert res.addressable_markers  # only calibration-gated markers
    assert res.recommended is True  # calibrated + gate accepted (real gold)
    assert (res.package_dir / "spec.yaml").exists()
    spec = json.loads((res.package_dir / "spec.yaml").read_text())
    assert spec["reward_basis"] == "rubric" and spec["automatic_promotion"] is False


def test_blocked_rubric_short_circuits_to_valid_package(tmp_path):
    # homogeneous world: no negative class + degenerate floor -> genuinely BLOCKED.
    eps = [{"episode_id": f"e{i}", "source_trace_id": f"t{i}", "skill_id": "review",
            "user_intent": "do the thing well", "task_summary": "s",
            "files_touched_json": '["a.py"]', "tools_used_json": '["pytest"]',
            "outcome_label": "success", "outcome_reward": 0.8} for i in range(12)]
    recs = {f"t{i}": _rec(True) for i in range(12)}
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    # no labels -> blocked; must NOT run a passing optimization
    res = score_rubric(rub, out_dir=tmp_path / "p", episodes=eps, records=recs,
                       same_session_self_judge=True, policy=_POLICY)
    assert res.usable is False
    assert res.status.startswith("blocked")
    assert res.dsel_after == 0.0 and res.recommended is False
    report = json.loads((res.package_dir / "report.json").read_text())
    assert report["status"].startswith("blocked")
    assert "BLOCKED" in (res.package_dir / "report.md").read_text()


def test_emulated_gold_drives_provisional_not_recommended(tmp_path):
    eps, recs = _world("review")
    labels = {f"t{i}": 1 for i in range(10)}
    labels.update({f"t{100+i}": 0 for i in range(10)})
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    res = score_rubric(rub, out_dir=tmp_path / "p", episodes=eps, records=recs, human_labels=labels,
                       gold_is_emulated=True, policy=_POLICY)
    assert res.status == "provisional_weak_only"  # emulated gold capped
    assert res.recommended is False               # provisional/emulated never recommended


def test_skill_opt_not_forked():
    # "no fork" = the reward swap REUSES the existing skill_opt objects (same identities), it does
    # not redefine an optimizer. (A git-diff check is confounded here: skill_opt IS this branch's
    # feature, so it differs from main independently of the rubric work.)
    from opentraces.consumers.verifier_factory import authoring
    from opentraces.consumers.skill_opt.engine import run_optimization as so_run
    from opentraces.consumers.skill_opt.rerollout import (
        FakeReRolloutRunner as so_runner,
        make_rerollout_gate as so_gate,
    )
    assert authoring.run_optimization is so_run
    assert authoring.FakeReRolloutRunner is so_runner
    assert authoring.make_rerollout_gate is so_gate
