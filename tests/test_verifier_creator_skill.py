"""Tests for the verifier-creator authoring API (the agent tool surface).

Covers the end-to-end loop — list candidates, surface real example/counterexample
trace refs, draft, author (validate + lint), score through the deterministic gate —
and the trust boundary: a spec cannot set reward/gate, the agent never self-promotes,
and over-permissive detectors are flagged rather than silently accepted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opentraces.consumers.verifier_factory import (
    ArchetypeSpecError,
    AuthoredArchetype,
    author_archetype,
    draft_archetype,
    get_skill_examples,
    list_candidates,
    score_authored,
)
from opentraces.consumers.verifier_factory.schema import APPROVAL_STATE


def _episodes(skill: str, n: int = 16) -> list[dict]:
    eps: list[dict] = []
    for i in range(n):
        committed = i % 2 == 0
        success = i % 3 != 0
        intent = "do the thing"
        if i % 2 == 0:
            intent += " with explicit outcome verification test check assert"
        if i % 4 == 0:
            intent += " constraint must not drift; if blocked stop and escalate; refactor; cite line in file"
        eps.append(
            {
                "episode_id": f"episode:{skill}-{i:03d}",
                "source_trace_id": f"trace-{skill}-{i:03d}",
                "source_unit_id": f"tu:trace-{skill}-{i:03d}:skill:metadata:0",
                "skill_id": skill,
                "user_intent": intent,
                "task_summary": f"Skill invocation {skill}",
                "files_touched_json": json.dumps(["src/app.py", "tests/test_app.py"] if i % 2 == 0 else []),
                "tools_used_json": json.dumps(["pytest"] if i % 2 else ["apply_patch"]),
                "outcome_label": "success" if success else "failure",
                "outcome_reward": 0.8 if committed and success else (0.5 if committed else 0.0),
                "confidence": "high",
            }
        )
    return eps


_AUTHORED_SPEC = {
    "archetype_id": "my_skill_outcome_v1",
    "skill": "my-skill",
    "title": "Authored change-quality contract",
    "semantic_target": "did the invocation read context, verify, and land honestly?",
    "baseline_skill": "# my-skill (baseline)\n\nDo the work.\n",
    "contract_elements": [
        {"element_id": "verified", "label": "Ran verification", "marker": "rl.my-skill.verified",
         "weight": 1.5, "detectors": {"command_families": ["verification"], "text_any": ["test", "check"]}},
        {"element_id": "constraints", "label": "Respected constraints", "marker": "rl.my-skill.constraints",
         "detectors": {"text_any": ["constraint", "must not", "boundary"]}},
        {"element_id": "landed", "label": "Landed outcome", "marker": "rl.my-skill.landed",
         "detectors": {"committed": True, "outcome_reward_min": 0.5, "outcome_label_in": ["success"]}},
    ],
    "creator_questions": [],
    "live_legs": [{"leg_id": "rr", "kind": "agent_rerollout", "description": "opt-in", "requires": ["real_agent"]}],
}


# ---------------------------------------------------------------------------
# Tool 1: list candidates
# ---------------------------------------------------------------------------

def test_list_candidates_wraps_mining():
    out = list_candidates(
        skills=["review"],
        episodes_by_skill={"review": _episodes("review")},
        min_usable_episodes=4,
    )
    assert out["schema_version"] == "opentraces.skill_verifier_candidates.v1"
    assert {s["skill_id"] for s in out["skills"]} == {"review"}


# ---------------------------------------------------------------------------
# Tool 2: example / counterexample trace refs
# ---------------------------------------------------------------------------

def test_get_skill_examples_splits_examples_and_counterexamples():
    res = get_skill_examples("goal-forge", "goal_contract_downstream_outcome_v1",
                             episodes=_episodes("goal-forge"))
    assert res["examples_total"] + res["counterexamples_total"] == 16
    # an example satisfies the full contract; a counterexample is deficient
    for ex in res["examples"]:
        assert ex["contract_completeness"] >= 0.999
        assert not ex["deficient_markers"]
        assert ex["slice_command"].startswith("opentraces trace slice ")
    for cx in res["counterexamples"]:
        assert cx["deficient_markers"]


def test_get_skill_examples_supports_inline_slice_fetcher():
    calls: list[str] = []

    def fetch(trace_id: str):
        calls.append(trace_id)
        return {"trace_id": trace_id, "slice": "stub"}

    res = get_skill_examples("review", "review_grounded_findings_v1",
                             episodes=_episodes("review"), slice_fetcher=fetch, max_examples=2)
    assert calls  # fetcher was invoked
    surfaced = res["examples"] + res["counterexamples"]
    assert any("slice" in entry for entry in surfaced)


# ---------------------------------------------------------------------------
# Tool 3: draft
# ---------------------------------------------------------------------------

def test_draft_archetype_is_approval_required_and_editable():
    draft = draft_archetype("brand-new-skill", episodes=_episodes("brand-new-skill"))
    assert draft["approval_required"] is True
    # ships an editable serialized spec the agent contextualizes
    assert draft["archetype"]["contract_elements"]
    assert draft["archetype"]["schema_version"] == "opentraces.skill_verifier_archetype.v1"


# ---------------------------------------------------------------------------
# Tool 4: author (validate + lint) — trust boundary
# ---------------------------------------------------------------------------

def test_author_archetype_validates_and_returns_clean_warnings():
    authored = author_archetype(_AUTHORED_SPEC)
    assert isinstance(authored, AuthoredArchetype)
    assert authored.warnings == ()  # the spec uses discriminating detectors
    assert authored.auto_promote is False
    assert authored.approval_state == APPROVAL_STATE
    assert authored.archetype.markers == (
        "rl.my-skill.verified", "rl.my-skill.constraints", "rl.my-skill.landed"
    )


def test_author_archetype_rejects_reward_or_gate_fields():
    for forbidden in ("reward", "gate", "split", "automatic_promotion"):
        with pytest.raises(ArchetypeSpecError):
            author_archetype({**_AUTHORED_SPEC, forbidden: 1.0})


def test_author_archetype_flags_overpermissive_detectors_without_raising():
    spec = {
        **_AUTHORED_SPEC,
        "archetype_id": "loose_v1",
        "contract_elements": [
            {"element_id": "anything", "label": "touched a file",
             "marker": "rl.my-skill.anything", "detectors": {"has_files": True}},
        ],
    }
    authored = author_archetype(spec)  # does not raise
    assert authored.warnings  # broad detector is flagged


# ---------------------------------------------------------------------------
# Tool 5: score authored — deterministic gate, no auto-promotion
# ---------------------------------------------------------------------------

def test_end_to_end_author_score_loop(tmp_path: Path):
    authored = author_archetype(_AUTHORED_SPEC)
    pkg = score_authored(authored, out_dir=tmp_path / "p", episodes=_episodes("my-skill"))
    assert pkg.dsel_before == 0.0
    assert pkg.dsel_after == pytest.approx(1.0)
    assert pkg.dtest_score == pytest.approx(1.0)
    assert pkg.accepted is True
    # promotion stays manual / default-off
    spec = json.loads((pkg.package_dir / "report.json").read_text())
    assert spec["automatic_promotion"] is False
    assert spec["approval_state"] == APPROVAL_STATE


def test_score_authored_does_not_promote_a_broad_spec(tmp_path: Path):
    spec = {
        **_AUTHORED_SPEC,
        "archetype_id": "loose_v1",
        "contract_elements": [
            {"element_id": "anything", "label": "touched a file",
             "marker": "rl.my-skill.anything", "detectors": {"has_files": True}},
            {"element_id": "any_cmd", "label": "ran something",
             "marker": "rl.my-skill.any_cmd", "detectors": {"has_commands": True}},
        ],
    }
    authored = author_archetype(spec)
    pkg = score_authored(authored, out_dir=tmp_path / "p", episodes=_episodes("my-skill"))
    assert pkg.recommended is False
    assert any("permissive_detector" in lim for lim in pkg.limitations)


# ---------------------------------------------------------------------------
# Skill packaging
# ---------------------------------------------------------------------------

def test_verifier_creator_skill_md_declares_trust_boundary():
    skill_md = Path(__file__).resolve().parents[1] / "skill" / "verifier-creator" / "SKILL.md"
    assert skill_md.exists()
    text = skill_md.read_text()
    assert "PROPOSES" in text and "SCORES" in text and "APPROVES" in text
    # the rubric reframe + both modes are documented
    assert "evidence" in text.lower() and "calibrat" in text.lower()
    assert "AUTOVERIFY" in text and "MANUAL" in text
    assert "provisional_weak_only" in text  # the autoverify trust ceiling
    # the tool surface is documented
    for tool in ("list_candidates", "get_skill_examples", "autoverify", "align_session",
                 "calibrate_rubric", "record_human_label", "build_judge_packet", "post_verdict"):
        assert tool in text


def test_verifier_creator_skill_is_packaged():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text()
    assert '"skill/verifier-creator" = "opentraces/skill/verifier-creator"' in pyproject
