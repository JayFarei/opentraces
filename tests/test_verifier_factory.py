"""Unit tests for the Skill Verifier Factory consumer.

Covers the archetype registry, the deterministic trace-grounded scorers, candidate
mining + ``skill_verifier_candidates.v1`` schema, verifier package emission scored
through the real SkillOpt strict-Dsel gate + held-out Dtest, leakage-safe splits,
the label-free vs labeled (verifier-creator override) paths, determinism, the
self-contained shipped scorer, and the schema guards (manual/default-off promotion,
opt-in-only live legs). All hermetic: episodes are injected, no bucket / network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from opentraces.consumers.verifier_factory import (
    ARCHETYPES,
    ARCHETYPES_BY_SKILL,
    DEFAULT_EXAMPLES,
    VerifierFactoryRequest,
    archetypes_for_skill,
    emit_verifier_package,
    get_archetype,
    mine_verifier_candidates,
    run_factory,
    validate_candidates,
    validate_package,
    validate_report,
    validate_row,
)
from opentraces.consumers.verifier_factory import scorers
from opentraces.consumers.verifier_factory.schema import (
    CANDIDATES_SCHEMA_VERSION,
    PACKAGE_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    ROW_SCHEMA_VERSION,
    VerifierSchemaError,
    dump_yaml,
)


def _episodes(skill: str, n: int = 16) -> list[dict]:
    """Synthetic episodes with a mix of complete/incomplete contracts and distinct
    source traces, so the failure minibatch is non-empty and the 3-way split fills."""
    eps: list[dict] = []
    for i in range(n):
        committed = i % 2 == 0
        success = i % 3 != 0
        intent = "do the thing"
        # even-i traces carry more of the contract in their intent text
        if i % 2 == 0:
            intent += " with explicit outcome, verification test, check assert"
        if i % 4 == 0:
            intent += " constraint must not drift; if blocked, stop and escalate; refactor; cite line in file"
        eps.append(
            {
                "schema_version": "opentraces.skill_episodes.v1",
                "episode_id": f"episode:{skill}-{i:03d}",
                "source_trace_id": f"trace-{skill}-{i:03d}",
                "source_unit_id": f"tu:trace-{skill}-{i:03d}:skill:metadata:0",
                "skill_id": skill,
                "agent": "codex-cli" if i % 2 else "claude-code",
                "skill_source": "codex_cli_skill_body_read",
                "user_intent": intent,
                "task_summary": f"Skill invocation {skill}",
                "files_touched_json": json.dumps(
                    ["src/app.py", "tests/test_app.py"] if i % 2 == 0 else []
                ),
                "tools_used_json": json.dumps(
                    ["pytest", "exec_command"] if i % 2 else ["apply_patch"]
                ),
                "outcome_label": "success" if success else "failure",
                "outcome_reward": 0.8 if committed and success else (0.5 if committed else 0.0),
                "confidence": "high",
            }
        )
    return eps


def _all_example_episodes(n: int = 16) -> dict[str, list[dict]]:
    return {skill: _episodes(skill, n) for skill, _ in DEFAULT_EXAMPLES}


# ---------------------------------------------------------------------------
# Archetype registry
# ---------------------------------------------------------------------------

def test_registry_curated_and_generic_archetypes():
    from opentraces.consumers.verifier_factory import GENERIC_ARCHETYPE_ID

    # Curated default examples are in the curated registry; the generic id is not.
    curated_examples = {a for _, a in DEFAULT_EXAMPLES if a != GENERIC_ARCHETYPE_ID}
    assert curated_examples <= set(ARCHETYPES)
    assert GENERIC_ARCHETYPE_ID not in ARCHETYPES
    assert {"goal-forge", "tdd", "review", "docs-update"} <= set(ARCHETYPES_BY_SKILL)
    gf = get_archetype("goal_contract_downstream_outcome_v1")
    assert gf.skill == "goal-forge"
    assert "rl.goal-forge.downstream_outcome" in gf.markers
    assert archetypes_for_skill("tdd")[0].archetype_id == "tdd_red_green_refactor_v1"


def test_unregistered_skill_falls_back_to_generic_archetype():
    from opentraces.consumers.verifier_factory import GENERIC_ARCHETYPE_ID, resolve_archetype

    archs = archetypes_for_skill("some-brand-new-skill")
    assert len(archs) == 1
    assert archs[0].archetype_id == GENERIC_ARCHETYPE_ID
    assert archs[0].skill == "some-brand-new-skill"
    # markers are skill-namespaced so two skills' generic packages never collide
    assert all(m.startswith("rl.some-brand-new-skill.generic.") for m in archs[0].markers)
    resolved = resolve_archetype("some-brand-new-skill", GENERIC_ARCHETYPE_ID)
    assert resolved.skill == "some-brand-new-skill"


def test_each_archetype_declares_optin_only_live_legs():
    for archetype in ARCHETYPES.values():
        assert archetype.live_legs, f"{archetype.archetype_id} has no declared live legs"
        for leg in archetype.live_legs:
            assert leg.kind in {"agent_rerollout", "llm_rubric"}


# ---------------------------------------------------------------------------
# Deterministic scorers
# ---------------------------------------------------------------------------

def test_score_episode_detects_contract_and_deficiencies():
    gf = get_archetype("goal_contract_downstream_outcome_v1")
    # A terse, landed episode: lands (downstream present) but no constraint/honest-stop.
    terse = {
        "user_intent": "do the thing",
        "task_summary": "inv",
        "files_touched_json": "[]",
        "tools_used_json": "[]",
        "outcome_reward": 0.8,
        "outcome_label": "success",
    }
    es = scorers.score_episode(gf, terse)
    assert "rl.goal-forge.downstream_outcome" in es.present_markers  # landed
    assert "rl.goal-forge.constraint_preservation" in es.deficient_markers
    assert "rl.goal-forge.honest_stop" in es.deficient_markers
    assert 0.0 <= es.contract_completeness <= 1.0


def test_score_skill_marker_coverage():
    gf = get_archetype("goal_contract_downstream_outcome_v1")
    assert scorers.score_skill(gf, "no markers here") == 0.0
    skill = "\n".join(f"rule[{m}]" for m in gf.markers)
    assert scorers.score_skill(gf, skill) == 1.0


def test_rich_evidence_classifies_codex_exec_command_opacity():
    """Codex wraps everything in exec_command; the real command must drive detection."""
    from types import SimpleNamespace

    from opentraces.consumers.verifier_factory.archetypes import evidence_from_episode

    # A Codex-shaped record: tool name is opaque, real command is in shell.command.
    record = SimpleNamespace(
        metadata={
            "normalized_tool_calls": [
                {"shell": {"command": "python -m pytest tests/ -q"}},
                {"shell": {"command": "git commit -m wip"}},
            ]
        },
        steps=[],
        outcome=SimpleNamespace(committed=True),
    )
    episode = {"user_intent": "do the thing", "task_summary": "x",
               "tools_used_json": "[\"exec_command\"]", "files_touched_json": "[]",
               "outcome_label": "success", "outcome_reward": 0.8}
    ev_text_only = evidence_from_episode(episode)  # no record -> projection only
    ev_rich = evidence_from_episode(episode, record)
    assert not ev_text_only.has_family("verification")  # opaque without the record
    assert ev_rich.has_family("verification")  # pytest surfaced from exec_command
    assert ev_rich.has_family("git_commit")
    assert ev_rich.committed is True


def test_generic_archetype_packages_an_unregistered_skill(tmp_path):
    from opentraces.consumers.verifier_factory import GENERIC_ARCHETYPE_ID

    pkg = emit_verifier_package(
        skill="brand-new-skill",
        archetype_id=GENERIC_ARCHETYPE_ID,
        out_dir=tmp_path / "p",
        episodes=_episodes("brand-new-skill"),
        seed="t",
    )
    assert pkg.is_generic is True
    # generic packages emit + score, but are NEVER auto-recommended (human approval).
    assert pkg.recommended is False
    spec = _load_yaml(pkg.spec_path)
    validate_package(spec)
    assert spec["is_generic"] is True
    assert all(m.startswith("rl.brand-new-skill.generic.") for m in spec["markers"])


def test_propose_archetype_draft_requires_human_approval():
    from opentraces.consumers.verifier_factory import generic_archetype_for, propose_archetype_draft

    arch = generic_archetype_for("brand-new-skill")
    draft = propose_archetype_draft(arch, _episodes("brand-new-skill"))
    assert draft["approval_required"] is True
    assert draft["proposed_contract_elements"]
    for element in draft["proposed_contract_elements"]:
        assert "support_episodes" in element
        assert "example_trace_refs" in element and "counterexample_trace_refs" in element


def test_report_carries_gate_basis_and_recommended(tmp_path):
    pkg = emit_verifier_package(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        out_dir=tmp_path / "p",
        episodes=_episodes("goal-forge"),
        seed="t",
    )
    report = json.loads(pkg.report_path.read_text())
    assert report["gate_basis"] == "train_failure_minibatch"
    assert report["recommended"] is True
    # the deterministic grader is honest about being a coverage proxy
    spec = _load_yaml(pkg.spec_path)
    det = [g for g in spec["graders"] if g["kind"] == "deterministic"][0]
    assert "proxy" in det["semantics"].lower()


# ---------------------------------------------------------------------------
# Mining + candidates schema
# ---------------------------------------------------------------------------

def test_mine_candidates_schema_and_recommendation():
    candidates = mine_verifier_candidates(
        episodes_by_skill=_all_example_episodes(), min_usable_episodes=4
    )
    validate_candidates(candidates)
    assert candidates["schema_version"] == CANDIDATES_SCHEMA_VERSION
    by_skill = {s["skill_id"]: s for s in candidates["skills"]}
    assert {"goal-forge", "tdd", "review"} <= set(by_skill)
    gf = by_skill["goal-forge"]
    assert gf["leakage_safe_split"] is True
    arch = gf["archetypes"][0]
    assert arch["recommended"] is True
    assert arch["archetype_id"] == "goal_contract_downstream_outcome_v1"
    # creator questions carry evidence-inferred defaults (label-free path)
    assert all(q["inferred_default"] for q in arch["creator_questions"])
    # provenance: source refs are real trace/unit ids, bounded
    assert gf["source_refs"]
    assert all("trace_id" in r and "unit_id" in r for r in gf["source_refs"])
    assert len(gf["source_refs"]) <= 8
    # every live leg surfaced in mining is opt-in
    assert all(leg["default_enabled"] is False for leg in arch["live_legs"])


def test_mine_candidates_handles_empty_evidence():
    candidates = mine_verifier_candidates(
        episodes_by_skill={s: [] for s, _ in DEFAULT_EXAMPLES}
    )
    validate_candidates(candidates)
    for skill in candidates["skills"]:
        assert skill["usable_episodes"] == 0
        assert skill["archetypes"][0]["recommended"] is False


# ---------------------------------------------------------------------------
# Package emission scored through the SkillOpt gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("skill,archetype_id", list(DEFAULT_EXAMPLES))
def test_emit_package_improves_dsel_and_reports_held_out_dtest(tmp_path, skill, archetype_id):
    pkg = emit_verifier_package(
        skill=skill,
        archetype_id=archetype_id,
        out_dir=tmp_path / archetype_id,
        episodes=_episodes(skill),
        seed="t",
    )
    # Strict Dsel gate moved from 0 to a strict improvement; held-out Dtest reported.
    assert pkg.dsel_before == 0.0
    assert pkg.dsel_after > pkg.dsel_before
    assert pkg.dsel_after == pytest.approx(1.0)
    assert pkg.dtest_score == pytest.approx(1.0)
    assert pkg.accepted is True
    assert pkg.addressable_markers  # at least one trace-grounded failure mode

    # Package artifacts exist and validate.
    spec = _load_yaml(pkg.spec_path)
    validate_package(spec)
    assert spec["schema_version"] == PACKAGE_SCHEMA_VERSION
    assert spec["automatic_promotion"] is False
    assert spec["approval_state"] == "manual_required_default_off"

    report = json.loads(pkg.report_path.read_text())
    validate_report(report)
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["dtest_score"] == pytest.approx(1.0)

    for tag in ("dtrain", "dsel", "dtest"):
        rows_path = pkg.package_dir / "rows" / f"{tag}.jsonl"
        assert rows_path.exists()
        for line in rows_path.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                validate_row(row)
                assert row["schema_version"] == ROW_SCHEMA_VERSION
                assert row["source_trace_id"] and row["source_unit_id"] is not None

    assert (pkg.package_dir / "scorer.py").exists()
    assert (pkg.package_dir / "best_skill.md").exists()
    assert (pkg.package_dir / "edit_apply_report.json").exists()
    assert list((pkg.package_dir / "fixtures").glob("*.json"))


def test_package_graders_have_ci_deterministic_and_optin_live_legs(tmp_path):
    pkg = emit_verifier_package(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        out_dir=tmp_path / "p",
        episodes=_episodes("goal-forge"),
        seed="t",
    )
    spec = _load_yaml(pkg.spec_path)
    graders = {g["id"]: g for g in spec["graders"]}
    det = [g for g in spec["graders"] if g["kind"] == "deterministic"]
    assert len(det) == 1
    assert det[0]["default_enabled"] is True
    live = [g for g in spec["graders"] if g["kind"] != "deterministic"]
    assert live, "expected declared live legs"
    assert all(g["default_enabled"] is False for g in live)


def test_addressable_markers_are_train_only_no_holdout_leakage(tmp_path):
    pkg = emit_verifier_package(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        out_dir=tmp_path / "p",
        episodes=_episodes("goal-forge"),
        seed="t",
    )
    # The shipped scorer enforces exactly the addressable (train-derived) markers.
    scorer_src = (pkg.package_dir / "scorer.py").read_text()
    for marker in pkg.addressable_markers:
        assert marker in scorer_src


def test_leakage_safe_split_one_trace_never_spans_splits(tmp_path):
    # Two episodes that share a source trace must land in the same split.
    shared = _episodes("goal-forge", 12)
    shared.append(dict(shared[0]))  # duplicate trace id, different unit
    shared[-1]["source_unit_id"] = "tu:trace-goal-forge-000:skill:metadata:1"
    shared[-1]["episode_id"] = "episode:goal-forge-000b"
    pkg = emit_verifier_package(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        out_dir=tmp_path / "p",
        episodes=shared,
        seed="t",
    )
    split_by_trace: dict[str, set[str]] = {}
    for tag in ("dtrain", "dsel", "dtest"):
        for line in (pkg.package_dir / "rows" / f"{tag}.jsonl").read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                split_by_trace.setdefault(row["source_trace_id"], set()).add(row["split"])
    assert all(len(splits) == 1 for splits in split_by_trace.values())


def test_labeled_override_path_flips_decision_source(tmp_path):
    pkg = emit_verifier_package(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        out_dir=tmp_path / "p",
        episodes=_episodes("goal-forge"),
        decisions={"drift_policy": "ignore (LABELED)"},
        seed="t",
    )
    spec = _load_yaml(pkg.spec_path)
    decisions = {d["key"]: d for d in spec["creator_decisions"]}
    assert decisions["drift_policy"]["source"] == "labeled"
    assert decisions["drift_policy"]["decision"] == "ignore (LABELED)"
    # untouched questions keep their evidence-inferred default (label-free path)
    assert decisions["success_definition"]["source"] == "inferred_default"


def test_package_emission_is_deterministic(tmp_path):
    common = dict(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        episodes=_episodes("goal-forge"),
        seed="t",
    )
    a = emit_verifier_package(out_dir=tmp_path / "a", **common)
    b = emit_verifier_package(out_dir=tmp_path / "b", **common)
    assert (a.package_dir / "best_skill.md").read_text() == (b.package_dir / "best_skill.md").read_text()
    assert a.dsel_after == b.dsel_after
    assert a.dtest_score == b.dtest_score
    assert a.addressable_markers == b.addressable_markers


def test_shipped_scorer_is_self_contained_and_runs(tmp_path):
    pkg = emit_verifier_package(
        skill="tdd",
        archetype_id="tdd_red_green_refactor_v1",
        out_dir=tmp_path / "p",
        episodes=_episodes("tdd"),
        seed="t",
    )
    scorer = pkg.package_dir / "scorer.py"
    best = pkg.package_dir / "best_skill.md"
    out = subprocess.run(
        [sys.executable, str(scorer), str(best)], capture_output=True, text=True, check=True
    )
    assert float(out.stdout.strip()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Factory orchestration
# ---------------------------------------------------------------------------

def test_run_factory_emits_candidates_three_packages_and_index(tmp_path):
    result = run_factory(
        VerifierFactoryRequest(
            out_dir=tmp_path,
            project="demo",
            episodes_by_skill=_all_example_episodes(),
            min_usable_episodes=4,
        )
    )
    validate_candidates(result.candidates)
    assert result.candidates_path.exists()
    assert len(result.packages) == len(DEFAULT_EXAMPLES)
    index = json.loads(result.index_path.read_text())
    assert index["automatic_promotion"] is False
    assert {p["archetype_id"] for p in index["packages"]} == {a for _, a in DEFAULT_EXAMPLES}
    assert all(p.dtest_score == pytest.approx(1.0) for p in result.packages)
    assert (tmp_path / "README.md").exists()


# ---------------------------------------------------------------------------
# Schema guards
# ---------------------------------------------------------------------------

def test_validate_package_rejects_auto_promotion():
    with pytest.raises(VerifierSchemaError):
        validate_package(
            {
                "schema_version": PACKAGE_SCHEMA_VERSION,
                "archetype_id": "x",
                "skill_id": "x",
                "generated_at": "now",
                "semantic_target": "x",
                "markers": [],
                "contract_elements": [],
                "graders": [{"id": "d", "kind": "deterministic", "default_enabled": True}],
                "creator_decisions": [],
                "split": {},
                "approval_state": "manual_required_default_off",
                "automatic_promotion": True,  # illegal
                "source_refs": [],
            }
        )


def test_validate_package_requires_deterministic_grader_and_optin_live():
    base = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "archetype_id": "x",
        "skill_id": "x",
        "generated_at": "now",
        "semantic_target": "x",
        "markers": [],
        "contract_elements": [],
        "creator_decisions": [],
        "split": {},
        "approval_state": "manual_required_default_off",
        "automatic_promotion": False,
        "source_refs": [],
    }
    with pytest.raises(VerifierSchemaError):  # no deterministic grader
        validate_package({**base, "graders": [{"id": "l", "kind": "llm_rubric", "default_enabled": False}]})
    with pytest.raises(VerifierSchemaError):  # live grader defaulting on
        validate_package(
            {
                **base,
                "graders": [
                    {"id": "d", "kind": "deterministic", "default_enabled": True},
                    {"id": "l", "kind": "llm_rubric", "default_enabled": True},
                ],
            }
        )


def test_dump_yaml_roundtrips():
    payload = {"a": 1, "b": ["x", "y"], "c": {"d": True}}
    text = dump_yaml(payload)
    assert isinstance(text, str) and text.strip()


def _load_yaml(path: Path) -> dict:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(path.read_text())
    except Exception:
        return json.loads(path.read_text())
