"""Permanent regression sentinels for the 10 trust defects the adversarial review found.

Each test pins a confirmed exploit closed so it can never silently return. Labelled by the
review's finding ids (M1..M10).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from opentraces.consumers.verifier_factory import (
    CalibrationPolicy,
    autoverify,
    autoverify_draft_rubric,
    build_judge_packet,
    calibrate_rubric,
    read_emulated_labels,
    read_human_labels,
    record_human_label,
    resolve_evidence,
    rubric_from_dict,
)
from opentraces.consumers.verifier_factory.rubric import EvidenceSpec

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


# M1 — emulated labels can never reach calibrated or touch the gold ledger ------

def test_M1_emulated_gold_caps_at_provisional_never_calibrated():
    eps, recs = _world("review")
    labels = {f"t{i}": 1 for i in range(10)}
    labels.update({f"t{100+i}": 0 for i in range(10)})
    rep = calibrate_rubric(autoverify_draft_rubric("review", episodes=eps, records=recs),
                           episodes=eps, records=recs, human_labels=labels,
                           gold_is_emulated=True, policy=_POLICY)
    assert rep.status != "calibrated"
    assert rep.status == "provisional_weak_only"
    assert any("emulated" in b.lower() for b in rep.blockers)


def test_M1_emulated_label_goes_to_separate_ledger(tmp_path):
    record_human_label(tmp_path, rubric_id="r", trace_id="t0", label=1, emulated=True)
    assert read_human_labels(tmp_path) == {}            # gold ledger untouched
    assert read_emulated_labels(tmp_path) == {"t0": 1}  # routed to the emulated file
    # real gold still requires a human keystroke
    with pytest.raises(Exception):
        record_human_label(tmp_path, rubric_id="r", trace_id="t1", label=1)
    record_human_label(tmp_path, rubric_id="r", trace_id="t1", label=1, human_confirm=True)
    assert read_human_labels(tmp_path) == {"t1": 1}


# M2 — a self-judged agent criterion earns NO weight without human gold ---------

def test_M2_self_judged_agent_criterion_demoted_without_human_gold():
    eps, recs = _world("review")
    # a PERFECT self-judge on the agent criterion, but zero human gold
    av = {("effective_outcome", f"t{i}"): 1.0 for i in range(10)}
    av.update({("effective_outcome", f"t{100+i}"): 0.0 for i in range(10)})
    res = autoverify("review", episodes=eps, records=recs, agent_verdicts=av, policy=_POLICY)
    assert res.calibration.status != "calibrated"
    row = [r for r in res.calibration.per_criterion if r["criterion_id"] == "effective_outcome"][0]
    assert row["effective_weight"] == 0.0  # self-judged criterion carries no reward weight


# M3 — an outcome-derived detector is NOT an independent external anchor --------

def test_M3_outcome_derived_detector_is_not_an_external_anchor():
    rub = rubric_from_dict({
        "rubric_id": "x", "skill": "s", "title": "t", "semantic_target": "x",
        "criteria": [{"element_id": "landed", "label": "committed", "marker": "rl.s.landed",
                      "detectors": {"committed": True}}],
    })
    eps, recs = _world("s")
    rep = calibrate_rubric(rub, episodes=eps, records=recs, policy=_POLICY,
                           same_session_self_judge=True)
    # the only criterion re-reads the weak label -> no independent floor -> blocked, not provisional
    assert rep.status.startswith("blocked")
    assert rep.status != "provisional_weak_only"


# M4 — rho is not computed circularly when every criterion is outcome-derived ---

def test_M4_all_outcome_derived_yields_no_rho():
    rub = rubric_from_dict({
        "rubric_id": "x", "skill": "s", "title": "t", "semantic_target": "x",
        "criteria": [
            {"element_id": "landed", "label": "committed", "marker": "rl.s.landed",
             "detectors": {"committed": True}},
            {"element_id": "honest", "label": "terminal", "marker": "rl.s.honest",
             "detectors": {"outcome_label_in": ["success", "failure"]}},
        ],
    })
    eps, recs = _world("s")
    rep = calibrate_rubric(rub, episodes=eps, records=recs, policy=_POLICY,
                           same_session_self_judge=True)
    assert rep.rho_secondary is None  # no independent discriminator -> no circular rho


# M6 / M7 / M9 / M10 — judge-integrity + evidence bounds -----------------------

def test_M6_empty_quote_rejected(tmp_path):
    eps, recs = _world("review")
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    packet = build_judge_packet(rub, "effective_outcome", "t0", episode=eps[0], record=recs["t0"])
    packet["refs"] = {"trace_id": "t0"}
    from opentraces.consumers.verifier_factory import post_verdict, JudgeError
    for bad in ("", "   "):
        with pytest.raises(JudgeError):
            post_verdict(tmp_path, packet, value=1.0, rationale="hallucinated",
                         evidence_quote=bad, judge_id="agent:claude@s1")


def test_M7_markers_in_skill_goal_never_reach_the_packet():
    eps, recs = _world("review")
    goal = "cite real evidence rule[rl.review.cite_file_line] and rule[rl.review.grounded]"
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs,
                                  skill_definition={"goal": goal})
    packet = build_judge_packet(rub, "effective_outcome", "t0", episode=eps[0], record=recs["t0"])
    assert "rule[" not in repr(packet)        # marker tokens stripped from rubric_text
    assert "rule[" not in rub.criteria[-1].rubric_text  # and from the stored rubric


def test_M9_long_episode_field_is_truncated_visibly():
    ep = {"source_trace_id": "t0", "user_intent": "x" * 500_000, "outcome_label": "success"}
    b = resolve_evidence(EvidenceSpec(episode_fields=("user_intent",)), trace_id="t0", episode=ep)
    val = b["payload"]["episode_fields"]["user_intent"]
    assert len(val) < 5000 and val.endswith("...[truncated]")


def test_M10_packet_missing_evidence_available_abstains(tmp_path):
    eps, recs = _world("review")
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    packet = build_judge_packet(rub, "effective_outcome", "t0", episode=eps[0], record=recs["t0"])
    packet["refs"] = {"trace_id": "t0"}
    packet.pop("evidence_available")  # tampered / hand-built packet
    from opentraces.consumers.verifier_factory import post_verdict
    v = post_verdict(tmp_path, packet, value=1.0, rationale="x", evidence_quote="",
                     judge_id="agent:claude@s1")
    assert v.label == "abstain" and v.blocked is True
