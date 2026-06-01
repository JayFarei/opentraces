"""Tests for the two-mode skill verifier (manual alignment + autoverify self-alignment)
and the agent-judge protocol they rest on.

Core invariant: AUTOVERIFY (self-align + self-judge) can PROPOSE and reach at most
`provisional_weak_only` (via a deterministic external anchor), is NEVER auto-recommended, and
NEVER reaches `calibrated` without human gold. MANUAL mode + human labels reaches `calibrated`.
The judge packet is evidence-blind; the gold ledger has a single human-confirmed writer.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from opentraces.consumers.verifier_factory import (
    CalibrationPolicy,
    JudgeError,
    agent_verdict_map,
    align_session,
    autoverify,
    autoverify_draft_rubric,
    build_judge_packet,
    post_verdict,
    read_human_labels,
    record_human_label,
    resolve_evidence,
    validate_rubric_dict,
)
from opentraces.consumers.verifier_factory.rubric import EvidenceSpec

_POLICY = CalibrationPolicy(min_human_labels=8, min_human_per_class=3, min_eff_neg=3.0,
                            rho_min=0.30, rho_min_n=8, auc_min_pairs=8)


def _rec(committed, survival=None):
    return SimpleNamespace(outcome=SimpleNamespace(committed=committed, survival_state=survival), steps=[])


def _world(skill, n_eff=10, n_neg=10):
    """Effective traces verify+commit; ineffective neither verify nor commit (weak negatives)."""
    eps, records = [], {}
    for i in range(n_eff):
        e = {"episode_id": f"e{i}", "source_trace_id": f"t{i}", "skill_id": skill,
             "user_intent": "do the thing well", "task_summary": "s",
             "files_touched_json": '["a.py"]', "tools_used_json": '["pytest"]',
             "outcome_label": "success", "outcome_reward": 0.8}
        eps.append(e); records[f"t{i}"] = _rec(True)
    for i in range(n_neg):
        e = {"episode_id": f"e{100+i}", "source_trace_id": f"t{100+i}", "skill_id": skill,
             "user_intent": "do the thing", "task_summary": "s",
             "files_touched_json": "[]", "tools_used_json": '["edit"]',
             "outcome_label": "unknown", "outcome_reward": 0.0}
        eps.append(e); records[f"t{100+i}"] = _rec(False)
    return eps, records


# --- autoverify: self-aligned rubric, trust ceiling --------------------------

def test_autoverify_draft_is_self_aligned_and_valid():
    eps, recs = _world("review")
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs,
                                  skill_definition={"goal": "findings cite real evidence"})
    validate_rubric_dict(rub.to_dict())                       # author-valid
    methods = {c.judge_method for c in rub.criteria}
    assert "deterministic" in methods and "agent" in methods  # det floor + self-aligned semantic
    eff = [c for c in rub.criteria if c.id == "effective_outcome"][0]
    assert eff.judge_method == "agent" and "findings cite real evidence" in eff.rubric_text


def test_autoverify_caps_at_provisional_via_external_anchor():
    # deterministic verification criterion separates the committed weak negative -> provisional,
    # never calibrated, never recommended (no human gold).
    eps, recs = _world("review")
    res = autoverify("review", episodes=eps, records=recs, policy=_POLICY)
    assert res.mode == "autoverify"
    assert res.recommended is False
    assert res.calibration.status in {"provisional_weak_only", "blocked_needs_human_labels"}
    assert res.calibration.status != "calibrated"


def test_autoverify_never_calibrated_even_if_agent_self_judges():
    # the agent self-judges its OWN semantic criterion perfectly; without human gold the status
    # still cannot be `calibrated` (self-judgment earns no trust).
    eps, recs = _world("review")
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    # agent rates every effective trace 1.0 and every weak-negative 0.0 (a perfect self-judge)
    av = {("effective_outcome", f"t{i}"): 1.0 for i in range(10)}
    av.update({("effective_outcome", f"t{100+i}"): 0.0 for i in range(10)})
    res = autoverify("review", episodes=eps, records=recs, agent_verdicts=av, policy=_POLICY)
    assert res.calibration.status != "calibrated"
    assert res.recommended is False


def test_autoverify_with_human_gold_can_reach_calibrated():
    # supplying human labels lifts the ceiling exactly as manual mode does.
    eps, recs = _world("review")
    labels = {f"t{i}": 1 for i in range(10)}
    labels.update({f"t{100+i}": 0 for i in range(10)})
    res = autoverify("review", episodes=eps, records=recs, human_labels=labels, policy=_POLICY)
    assert res.calibration.status == "calibrated", res.calibration.blockers
    assert res.recommended is False  # autoverify is still never AUTO-recommended


# --- manual alignment session ------------------------------------------------

def test_align_session_scaffolds_outcome_draft_and_labels():
    eps, recs = _world("review")
    sess = align_session("review", episodes=eps, records=recs,
                         skill_definition={"goal": "cite real evidence"})
    assert sess["mode"] == "manual"
    assert "desired_outcome_prompt" in sess and "draft_rubric" in sess
    validate_rubric_dict(sess["draft_rubric"])
    assert "effective_examples" in sess["label_these"]
    assert sess["labels_needed"]["min_total"] == 8


# --- agent-judge protocol: evidence-blind packet, grounded post --------------

def test_judge_packet_is_evidence_blind():
    eps, recs = _world("review")
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    packet = build_judge_packet(rub, "effective_outcome", "t0", episode=eps[0], record=recs["t0"])
    blob = repr(packet).lower()
    assert "rule[" not in blob                       # no marker tokens
    assert "baseline" not in blob                    # no skill text
    assert packet["criterion"]["criterion_id"] == "effective_outcome"
    assert "bound_evidence" in packet and packet["evidence_digest"].startswith("sha256:")


def test_post_verdict_requires_grounded_quote(tmp_path):
    eps, recs = _world("review")
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    packet = build_judge_packet(rub, "effective_outcome", "t0", episode=eps[0], record=recs["t0"])
    packet["refs"] = {"trace_id": "t0"}
    # ungrounded quote -> rejected
    with pytest.raises(JudgeError):
        post_verdict(tmp_path, packet, value=1.0, rationale="looks good",
                     evidence_quote="THIS TEXT IS NOT IN THE EVIDENCE", judge_id="agent:claude@s1")
    # a single ubiquitous char / JSON punctuation does NOT count (M8)
    with pytest.raises(JudgeError):
        post_verdict(tmp_path, packet, value=1.0, rationale="x", evidence_quote='"',
                     judge_id="agent:claude@s1")
    # a verbatim span of the evidence VALUES -> accepted (the episode intent is in the bundle)
    v = post_verdict(tmp_path, packet, value=1.0, rationale="grounded",
                     evidence_quote="do the thing", judge_id="agent:claude@s1")
    assert v.judge_method == "agent" and v.value == 1.0
    assert agent_verdict_map(tmp_path)[("effective_outcome", "t0")] == 1.0


def test_agent_cannot_write_gold_and_human_label_needs_confirm(tmp_path):
    eps, recs = _world("review")
    rub = autoverify_draft_rubric("review", episodes=eps, records=recs)
    packet = build_judge_packet(rub, "effective_outcome", "t0", episode=eps[0], record=recs["t0"])
    packet["refs"] = {"trace_id": "t0"}
    # the agent path cannot impersonate a human gold writer
    with pytest.raises(JudgeError):
        post_verdict(tmp_path, packet, value=1.0, rationale="x", evidence_quote="",
                     judge_id="human:alice")
    # gold requires explicit human confirmation
    with pytest.raises(JudgeError):
        record_human_label(tmp_path, rubric_id=rub.rubric_id, trace_id="t0", label=1)
    record_human_label(tmp_path, rubric_id=rub.rubric_id, trace_id="t0", label=1, human_confirm=True)
    assert read_human_labels(tmp_path) == {"t0": 1}


def test_resolve_evidence_is_bounded_and_marks_unavailable_rich_sources():
    eps, recs = _world("review")
    spec = EvidenceSpec(episode_fields=("user_intent", "outcome_label"), trace_get=True,
                        trace_slice={"template": "bursts"})
    b = resolve_evidence(spec, trace_id="t0", episode=eps[0], record=recs["t0"])
    assert b["available"] is True
    assert b["payload"]["episode_fields"]["outcome_label"] == "success"
    assert b["payload"]["trace_slice"]["status"] == "unavailable"   # rich source -> abstain, not silent pass
