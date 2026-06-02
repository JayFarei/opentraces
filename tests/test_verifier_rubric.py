"""Phase-0 tests for the rubric-centric verifier spine.

Proves: (1) a legacy archetype is a degenerate, byte-identical all-deterministic rubric
(no archetype-path regression); (2) the trust boundary extends to criteria/verdicts (no
reward/gate/split/value); (3) Rubric.score is well-defined at the BLOCKED edges; and pins
the proven marker-stuffing attack as a permanent regression sentinel.
"""

from __future__ import annotations

import pytest

from opentraces.consumers.verifier_factory import (
    ARCHETYPES,
    GENERIC_ARCHETYPE_ID,
    Criterion,
    EvidenceSpec,
    Rubric,
    RubricSpecError,
    Verdict,
    archetype_to_dict,
    generic_archetype_for,
    get_archetype,
    rubric_from_archetype,
    rubric_from_dict,
    scorers,
    validate_rubric_dict,
    validate_verdict_dict,
)


def _all_archetypes():
    yield from ARCHETYPES.values()
    yield generic_archetype_for("architecture-patterns")


# --- (1) legacy archetype ⊂ rubric, byte-identical round-trip ----------------

@pytest.mark.parametrize("archetype_id", sorted(ARCHETYPES))
def test_legacy_archetype_is_degenerate_rubric(archetype_id):
    arch = get_archetype(archetype_id)
    ad = archetype_to_dict(arch)
    rub = rubric_from_dict(ad)                      # accepts archetype dict via aliases
    assert rub.is_legacy_archetype()
    assert all(c.judge_method == "deterministic" and c.evidence.is_empty()
               and c.direction == "positive" for c in rub.criteria)
    # degenerate rubric -> archetype shape -> byte-identical to the original
    assert archetype_to_dict(rub.to_archetype()) == ad


def test_generic_archetype_round_trips_through_rubric():
    arch = generic_archetype_for("brand-new-skill")
    rub = rubric_from_archetype(arch)
    assert archetype_to_dict(rub.to_archetype()) == archetype_to_dict(arch)
    assert rub.markers == arch.markers


def test_generic_seed_round_trips_via_dict_path():
    # §10 Phase-0 verify: the round-trip holds for all 5 seeds via the DICT path, incl. generic.
    arch = generic_archetype_for("architecture-patterns")
    ad = archetype_to_dict(arch)
    rub = rubric_from_dict(ad)
    assert rub.is_legacy_archetype()
    assert archetype_to_dict(rub.to_archetype()) == ad


def test_rubric_to_dict_round_trips():
    rub = rubric_from_archetype(get_archetype("review_grounded_findings_v1"))
    again = rubric_from_dict(rub.to_dict())
    assert again.to_dict() == rub.to_dict()


# --- (2) trust boundary: criteria/verdicts cannot carry reward/gate/value ----

_VALID = {
    "rubric_id": "r1", "skill": "s", "title": "t", "semantic_target": "x",
    "criteria": [
        {"element_id": "det", "label": "ran tests", "marker": "rl.s.det",
         "detectors": {"command_families": ["verification"]}},
        {"element_id": "sem", "label": "substantive", "marker": "rl.s.sem",
         "judge_method": "agent", "rubric_text": "Are findings substantive?",
         "evidence": {"trace_slice": {"template": "bursts"}}},
    ],
}


def test_valid_rubric_builds():
    rub = rubric_from_dict(_VALID)
    assert rub.skill == "s"
    assert {c.judge_method for c in rub.criteria} == {"deterministic", "agent"}


@pytest.mark.parametrize("forbidden", ["reward", "gate", "split", "calibration"])
def test_rubric_rejects_reward_gate_keys(forbidden):
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, forbidden: 1.0})


@pytest.mark.parametrize("forbidden", ["value", "score", "reward", "calibration", "recommended"])
def test_criterion_rejects_value_and_reward_keys(forbidden):
    bad = {**_VALID, "criteria": [{**_VALID["criteria"][0], forbidden: 0.5}]}
    with pytest.raises(RubricSpecError):
        validate_rubric_dict(bad)


def test_agent_criterion_must_be_grounded_and_carry_no_detector():
    # agent criterion without evidence -> rejected
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, "criteria": [
            {"element_id": "x", "label": "l", "marker": "rl.s.x", "judge_method": "agent",
             "rubric_text": "q?"}]})
    # agent criterion smuggling a detector -> rejected
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, "criteria": [
            {"element_id": "x", "label": "l", "marker": "rl.s.x", "judge_method": "agent",
             "rubric_text": "q?", "evidence": {"trace_get": True},
             "detectors": {"text_any": ["x"]}}]})


def test_rubric_requires_un_self_gradeable_deterministic_floor():
    # a 100%-agent rubric is rejected
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, "criteria": [
            {"element_id": "a", "label": "l", "marker": "rl.s.a", "judge_method": "agent",
             "rubric_text": "q?", "evidence": {"trace_get": True}}]})
    # a negative-direction AGENT criterion is STILL agent-graded -> does NOT satisfy the floor
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, "criteria": [
            {"element_id": "neg", "label": "claimed success no commit", "marker": "rl.s.neg",
             "judge_method": "agent", "direction": "negative", "rubric_text": "claimed but no commit?",
             "evidence": {"episode_fields": ["outcome_label"]}}]})
    # a DETERMINISTIC negative-direction criterion (reads committed/outcome) IS un-self-gradeable
    validate_rubric_dict({**_VALID, "criteria": [
        {"element_id": "neg", "label": "claimed success no commit", "marker": "rl.s.neg",
         "direction": "negative", "detectors": {"outcome_label_in": ["unknown"]}}]})


def test_floor_cannot_be_weighted_to_zero():
    # deterministic floor present but weighted ~0 while an agent criterion dominates -> rejected
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, "criteria": [
            {"element_id": "floor", "label": "l", "marker": "rl.s.floor", "weight": 0.0001,
             "detectors": {"command_families": ["verification"]}},
            {"element_id": "sem", "label": "l", "marker": "rl.s.sem", "judge_method": "agent",
             "weight": 1000.0, "rubric_text": "q?", "evidence": {"trace_get": True}}]})


def test_ctx_reads_alone_is_rejected_for_semantic_criterion():
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, "criteria": [
            {"element_id": "c", "label": "l", "marker": "rl.s.c", "judge_method": "agent",
             "rubric_text": "q?", "evidence": {"ctx_reads": {"max_layers": 4}}}]})


def test_deterministic_criterion_cannot_bind_rich_agent_only_evidence():
    with pytest.raises(RubricSpecError):
        validate_rubric_dict({**_VALID, "criteria": [
            {"element_id": "d", "label": "l", "marker": "rl.s.d",
             "detectors": {"text_any": ["x"]}, "evidence": {"trace_slice": {"template": "bursts"}}}]})


def test_verdict_rejects_reward_and_out_of_range_value():
    validate_verdict_dict({"value": 0.5, "label": "pass"})
    with pytest.raises(RubricSpecError):
        validate_verdict_dict({"value": 5.0})
    with pytest.raises(RubricSpecError):
        validate_verdict_dict({"value": 0.5, "reward": 1.0})


# --- (3) Rubric.score well-defined at the BLOCKED edges ----------------------

def _det(cid, marker, direction="positive"):
    return Criterion(element_id=cid, label=cid, marker=marker, weight=1.0, direction=direction)


def test_score_weighted_and_negative_direction():
    rub = Rubric(rubric_id="r", skill="s", criteria=(
        _det("a", "rl.s.a"), _det("b", "rl.s.b", direction="negative")))
    v = {
        "a": Verdict(criterion_id="a", value=1.0),
        "b": Verdict(criterion_id="b", value=0.0),  # negative criterion did NOT fire -> contributes 1.0
    }
    assert rub.score(v) == pytest.approx(1.0)
    v["b"] = Verdict(criterion_id="b", value=1.0)   # negative fired -> contributes 0.0
    assert rub.score(v) == pytest.approx(0.5)


def test_score_blocks_on_missing_or_blocked_verdict():
    rub = Rubric(rubric_id="r", skill="s", criteria=(_det("a", "rl.s.a"),))
    assert rub.score({}) is None                                   # missing -> BLOCKED
    assert rub.score({"a": Verdict(criterion_id="a", blocked=True)}) is None


# --- attack pin: the proven marker-stuffing exploit ships RED ----------------

def test_marker_stuffing_attack_pin_legacy_score_skill_is_gameable():
    """Regression sentinel: the OLD reward (marker token coverage) is trivially gamed.

    This documents WHY the rubric reward must not read skill text to grade a trace.
    score_skill MUST be retired from the reward path; this test guards that we never
    re-introduce a text-coverage reward as the gate without noticing.
    """
    arch = get_archetype("review_grounded_findings_v1")
    garbage = "I will do nothing useful.\n" + "\n".join(f"rule[{m}]" for m in arch.markers)
    assert scorers.score_skill(arch, garbage) == pytest.approx(1.0)
