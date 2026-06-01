"""Declarative-archetype migration tests for the Skill Verifier Factory.

These prove the closure->data migration is lossless and that the trust boundary
holds:

* **Detection parity** — every migrated archetype's declarative detector reproduces
  the original Python-closure detector bit-for-bit across a generated evidence corpus.
  The legacy closures are embedded here verbatim as the reference oracle.
* **Round-trip** — an archetype serializes to a dict and rebuilds identically, and
  the rebuilt archetype detects identically (no eval, no callable).
* **Trust boundary** — a spec cannot smuggle a reward/gate/split field past the
  validator, and the binary full-contract reward is computed mechanically regardless
  of archetype content.
* **Over-permissive detectors** — broad detectors are flagged (limitations), never
  silently accepted, and never auto-recommended.
* **Agent-authored verifier** — an archetype authored purely from a serializable spec
  drives the real SkillOpt gate to Dsel 0->1 / Dtest 1.
"""

from __future__ import annotations

import json
import re

import pytest

from opentraces.consumers.verifier_factory import (
    ARCHETYPES,
    GENERIC_ARCHETYPE_ID,
    ArchetypeSpecError,
    DetectorSpecError,
    archetype_from_dict,
    archetype_permissiveness_flags,
    archetype_to_dict,
    detector_from_dict,
    emit_verifier_package,
    generic_archetype_for,
    get_archetype,
    validate_archetype_dict,
)
from opentraces.consumers.verifier_factory.archetypes import EpisodeEvidence
from opentraces.consumers.verifier_factory.detectors import (
    DetectorSpec,
    classify_command,
    detector_permissiveness_flags,
)


# ---------------------------------------------------------------------------
# Legacy closure oracle (verbatim copy of the pre-migration detectors)
# ---------------------------------------------------------------------------

_TEST_FILE_RE = re.compile(r"(^|/)(test_|tests?/|.*_test\.)")


def _legacy_goal_outcome(e):
    return e.has_any("outcome", "goal", "done means", "definition of done", "finish line")


def _legacy_goal_verification(e):
    return e.has_family("verification") or e.has_any(
        "verif", "verified by", "test", "check", "assert", "pass"
    )


def _legacy_goal_constraints(e):
    return e.has_any("constraint", "must not", "preserve", "do not", "boundary", "scope")


def _legacy_goal_honest_stop(e):
    return e.has_any("blocked", "stop", "halt", "ask for", "give up", "escalat")


def _legacy_goal_downstream(e):
    return e.committed or e.outcome_reward >= 0.5 or e.outcome_label == "success"


def _legacy_tdd_red(e):
    return (
        e.any_file("test")
        or any(_TEST_FILE_RE.search(f) for f in e.files)
        or e.any_command("test_", "_test", "tests/")
        or e.has_any("failing test", "write a test", "red", "test first")
    )


def _legacy_tdd_green(e):
    return e.has_family("verification") or e.has_any(
        "make it pass", "green", "passing", "tests pass"
    )


def _legacy_tdd_refactor(e):
    return e.has_any("refactor", "clean up", "tidy", "simplif", "extract")


def _legacy_review_grounded(e):
    return bool(e.files) or e.has_family("search_read") or e.has_any("file", "diff", "line", "hunk")


def _legacy_review_cite(e):
    return e.any_command(".py:", ".ts:", ".md:", "line ") or e.has_any(
        "line", "evidence", "cite", "grounded", "reference"
    )


def _legacy_docs_code_grounded(e):
    return bool(e.files) or e.has_family("search_read") or e.has_any("diff", "code", "file", "function")


def _legacy_docs_artifact_touched(e):
    return (
        e.any_file("readme", "changelog", ".md", "docs/", ".rst", ".mdx")
        or e.any_command(".md", "readme", "changelog", "docs/")
        or e.has_any("readme", "changelog", "docs", "documentation")
    )


def _legacy_docs_changelog(e):
    return e.has_any("changelog", "release note", "version bump", "what's new", "whats new")


def _legacy_docs_no_stale(e):
    return e.has_any("stale", "outdated", "out of date", "no longer", "deprecat", "remove", "obsolete")


def _legacy_generic_context(e):
    return e.has_family("search_read") or e.any_tool("read", "grep", "glob", "exec") or bool(e.commands)


def _legacy_generic_changes(e):
    return bool(e.files) or e.has_family("edit_write", "git_commit")


def _legacy_generic_verification(e):
    return e.has_family("verification")


def _legacy_generic_landed(e):
    return e.committed or e.has_family("git_commit") or e.outcome_reward >= 0.5 or e.outcome_label == "success"


def _legacy_generic_honest_completion(e):
    return e.outcome_label in {"success", "failure"}


#: element_id -> legacy oracle, per archetype id.
_ORACLE: dict[str, dict[str, object]] = {
    "goal_contract_downstream_outcome_v1": {
        "outcome": _legacy_goal_outcome,
        "verification": _legacy_goal_verification,
        "constraints": _legacy_goal_constraints,
        "honest_stop": _legacy_goal_honest_stop,
        "downstream": _legacy_goal_downstream,
    },
    "tdd_red_green_refactor_v1": {
        "red_first": _legacy_tdd_red,
        "green_pass": _legacy_tdd_green,
        "refactor": _legacy_tdd_refactor,
        "downstream": _legacy_goal_downstream,
    },
    "review_grounded_findings_v1": {
        "grounded": _legacy_review_grounded,
        "cite": _legacy_review_cite,
        "downstream": _legacy_goal_downstream,
    },
    "docs_update_reflects_change_v1": {
        "code_grounded": _legacy_docs_code_grounded,
        "doc_artifact": _legacy_docs_artifact_touched,
        "changelog": _legacy_docs_changelog,
        "no_stale": _legacy_docs_no_stale,
        "downstream": _legacy_goal_downstream,
    },
    GENERIC_ARCHETYPE_ID: {
        "context_read": _legacy_generic_context,
        "produced_changes": _legacy_generic_changes,
        "verification_run": _legacy_generic_verification,
        "landed_outcome": _legacy_generic_landed,
        "honest_completion": _legacy_generic_honest_completion,
    },
}


# ---------------------------------------------------------------------------
# Evidence corpus (deterministic cartesian product exercising every predicate)
# ---------------------------------------------------------------------------

_TEXTS = (
    "",
    "explicit outcome goal definition of done finish line",
    "we must verify, run the test, check, assert; verified by it",
    "constraint must not drift, preserve boundary scope; if blocked stop and escalate",
    "write a failing test first then make it pass green refactor clean up; changelog release note",
    "cite the line as evidence in the file diff hunk; readme docs documentation; stale outdated deprecated remove",
)
_FILES = (
    (),
    ("src/app.py",),
    ("tests/test_app.py",),
    ("README.md", "CHANGELOG.md"),
    ("latest_release.py", "docs/guide.rst"),  # 'latest' contains 'test' substring (regex-False edge)
)
_TOOLS = ((), ("exec_command",), ("apply_patch", "read"))
_COMMANDS = (
    (),
    ("python -m pytest tests/ -q",),
    ("git commit -m wip",),
    ("rg pattern src/ && cat src/app.py:42",),
)
_REWARDS = (0.0, 0.8)
_LABELS = ("success", "failure", "unknown")
_COMMITTED = (False, True)


def _evidence(text, files, tools, commands, reward, label, committed) -> EpisodeEvidence:
    families: set[str] = set()
    for c in commands:
        families |= classify_command(c)
    for t in tools:
        families |= classify_command(t)
    return EpisodeEvidence(
        text=text.lower(),
        tools=tools,
        files=files,
        outcome_label=label,
        outcome_reward=reward,
        commands=commands,
        command_families=frozenset(families),
        committed=committed,
        step_count=len(commands),
    )


def _corpus() -> list[EpisodeEvidence]:
    out: list[EpisodeEvidence] = []
    for text in _TEXTS:
        for files in _FILES:
            for tools in _TOOLS:
                for commands in _COMMANDS:
                    for reward in _REWARDS:
                        for label in _LABELS:
                            for committed in _COMMITTED:
                                out.append(
                                    _evidence(text, files, tools, commands, reward, label, committed)
                                )
    return out


def _all_archetypes():
    yield from ARCHETYPES.values()
    yield generic_archetype_for("architecture-patterns")


# ---------------------------------------------------------------------------
# Detection parity
# ---------------------------------------------------------------------------

def test_declarative_detectors_match_legacy_closures_across_corpus():
    corpus = _corpus()
    assert len(corpus) > 2000  # a real corpus, not a token sample
    coverage: dict[str, list[bool]] = {}
    for archetype in _all_archetypes():
        oracle = _ORACLE[archetype.archetype_id]
        for element in archetype.contract_elements:
            legacy = oracle[element.element_id]
            seen_true = seen_false = False
            for ev in corpus:
                got = element.detect(ev)
                want = bool(legacy(ev))
                assert got == want, (
                    f"{archetype.archetype_id}:{element.element_id} diverged on "
                    f"text={ev.text!r} files={ev.files} cmds={ev.commands} "
                    f"reward={ev.outcome_reward} label={ev.outcome_label} committed={ev.committed}"
                )
                seen_true |= got
                seen_false |= not got
            coverage[f"{archetype.archetype_id}:{element.element_id}"] = [seen_true, seen_false]
    # The corpus is non-vacuous: every element fires AND abstains at least once.
    for key, (t, f) in coverage.items():
        assert t, f"{key} never matched anything in the corpus"
        assert f, f"{key} matched everything in the corpus (parity would be vacuous)"


def test_every_element_has_a_declarative_detector_no_callables():
    for archetype in _all_archetypes():
        for element in archetype.contract_elements:
            assert isinstance(element.detectors, DetectorSpec)
            # the detector is pure data: to_dict round-trips it
            assert detector_from_dict(element.detectors.to_dict()) == element.detectors


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("archetype_id", sorted(ARCHETYPES))
def test_curated_archetype_dict_round_trips_and_detects_identically(archetype_id):
    original = get_archetype(archetype_id)
    payload = archetype_to_dict(original)
    # serializes to JSON cleanly (YAML/JSON spec)
    json.loads(json.dumps(payload))
    validate_archetype_dict(payload)
    rebuilt = archetype_from_dict(payload)
    assert archetype_to_dict(rebuilt) == payload
    assert rebuilt.markers == original.markers
    for a, b in zip(rebuilt.contract_elements, original.contract_elements):
        assert a.detectors == b.detectors


def test_emitted_package_spec_is_self_describing_and_round_trips(tmp_path):
    """The shipped spec.yaml carries the declarative detectors and rebuilds an archetype."""
    pkg = emit_verifier_package(
        skill="tdd",
        archetype_id="tdd_red_green_refactor_v1",
        out_dir=tmp_path / "p",
        episodes=_synth_episodes("tdd"),
        seed="t",
    )
    try:
        import yaml  # type: ignore

        spec = yaml.safe_load(pkg.spec_path.read_text())
    except Exception:
        spec = json.loads(pkg.spec_path.read_text())
    assert "archetype_spec" in spec
    rebuilt = archetype_from_dict(spec["archetype_spec"])
    original = get_archetype("tdd_red_green_refactor_v1")
    assert rebuilt.markers == original.markers
    for a, b in zip(rebuilt.contract_elements, original.contract_elements):
        assert a.detectors == b.detectors
    # each emitted contract element carries its declarative detector
    for el in spec["contract_elements"]:
        assert "detectors" in el


def test_generic_archetype_round_trips():
    original = generic_archetype_for("brand-new-skill")
    rebuilt = archetype_from_dict(archetype_to_dict(original))
    assert rebuilt.markers == original.markers
    assert archetype_to_dict(rebuilt) == archetype_to_dict(original)


# ---------------------------------------------------------------------------
# Trust boundary: a spec cannot set/alter reward or gate
# ---------------------------------------------------------------------------

def test_archetype_spec_rejects_reward_and_gate_fields():
    base = archetype_to_dict(get_archetype("tdd_red_green_refactor_v1"))
    for forbidden in ("reward", "gate", "split", "split_reward", "automatic_promotion", "recommended"):
        bad = {**base, forbidden: 1.0}
        with pytest.raises(ArchetypeSpecError):
            validate_archetype_dict(bad)


def test_detector_spec_rejects_unknown_and_unsafe_keys():
    with pytest.raises(DetectorSpecError):
        detector_from_dict({"reward": 1.0})
    with pytest.raises(DetectorSpecError):
        detector_from_dict({"command_families": ["not_a_family"]})
    with pytest.raises(DetectorSpecError):
        detector_from_dict({"outcome_reward_min": 5.0})
    with pytest.raises(DetectorSpecError):
        detector_from_dict({"text_any": "should-be-a-list"})


def test_authored_archetype_with_injected_reward_is_rejected_before_packaging(tmp_path):
    payload = archetype_to_dict(get_archetype("tdd_red_green_refactor_v1"))
    payload["reward"] = 0.0  # attempt to pin reward via the spec
    with pytest.raises(ArchetypeSpecError):
        archetype_from_dict(payload)


def test_binary_reward_is_mechanical_not_set_by_archetype(tmp_path):
    """The contract_pass reward is computed by packaging (completeness>=0.999), not the spec."""
    pkg = emit_verifier_package(
        skill="goal-forge",
        archetype_id="goal_contract_downstream_outcome_v1",
        out_dir=tmp_path / "p",
        episodes=_synth_episodes("goal-forge"),
        seed="t",
    )
    for tag in ("dtrain", "dsel", "dtest"):
        path = pkg.package_dir / "rows" / f"{tag}.jsonl"
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            expected = 1.0 if row["contract_completeness"] >= 0.999 else 0.0
            assert row["contract_pass"] == expected


# ---------------------------------------------------------------------------
# Over-permissive detectors are flagged, never silently accepted
# ---------------------------------------------------------------------------

def test_curated_and_generic_archetypes_have_no_permissiveness_flags():
    for archetype in _all_archetypes():
        assert archetype_permissiveness_flags(archetype) == []


def test_overpermissive_detector_is_flagged():
    # sole presence check
    assert detector_permissiveness_flags(DetectorSpec(has_files=True), "x")
    # short substring token
    assert detector_permissiveness_flags(DetectorSpec(text_any=("a",)), "y")
    # a discriminating detector is NOT flagged
    assert detector_permissiveness_flags(DetectorSpec(text_any=("refactor",)), "z") == []


def test_overpermissive_authored_archetype_surfaces_limitations_and_not_recommended(tmp_path):
    spec = {
        "archetype_id": "loose_v1",
        "skill": "loose-skill",
        "title": "Loose",
        "semantic_target": "anything goes",
        "contract_elements": [
            {"element_id": "any_file", "label": "touched a file",
             "marker": "rl.loose-skill.any_file", "detectors": {"has_files": True}},
            {"element_id": "short", "label": "short token",
             "marker": "rl.loose-skill.short", "detectors": {"text_any": ["a"]}},
        ],
        "creator_questions": [],
        "live_legs": [],
    }
    archetype = archetype_from_dict(spec)
    flags = archetype_permissiveness_flags(archetype)
    assert flags  # both elements are broad
    pkg = emit_verifier_package(
        skill="loose-skill",
        archetype_id="loose_v1",
        out_dir=tmp_path / "p",
        episodes=_synth_episodes("loose-skill"),
        seed="t",
        archetype=archetype,
    )
    assert any("permissive_detector" in lim for lim in pkg.limitations)
    assert pkg.recommended is False  # broad authored spec is never auto-recommended


# ---------------------------------------------------------------------------
# Agent-authored verifier drives the real SkillOpt gate
# ---------------------------------------------------------------------------

def _synth_episodes(skill: str, n: int = 16) -> list[dict]:
    """Mixed-contract episodes with distinct source traces (non-empty failure minibatch)."""
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


def test_agent_authored_spec_runs_to_dsel_one_and_dtest_one(tmp_path):
    """An archetype authored purely from a serializable spec drives the gate to 0->1."""
    spec = {
        "archetype_id": "authored_change_quality_v1",
        "skill": "my-skill",
        "title": "Authored change-quality contract",
        "semantic_target": "did the invocation read context, verify, and land honestly?",
        "baseline_skill": "# my-skill (baseline)\n\nDo the work.\n",
        "contract_elements": [
            {"element_id": "verified", "label": "Ran verification",
             "marker": "rl.my-skill.verified",
             "detectors": {"command_families": ["verification"], "text_any": ["test", "check", "assert"]},
             "weight": 1.5},
            {"element_id": "constraints", "label": "Respected constraints",
             "marker": "rl.my-skill.constraints",
             "detectors": {"text_any": ["constraint", "must not", "boundary"]}},
            {"element_id": "landed", "label": "Landed outcome",
             "marker": "rl.my-skill.landed",
             "detectors": {"committed": True, "outcome_reward_min": 0.5, "outcome_label_in": ["success"]}},
        ],
        "creator_questions": [
            {"key": "floor", "question": "minimum?", "inferred_default": "verified+landed",
             "rationale": "authored", "options": ["a", "b"]},
        ],
        "live_legs": [
            {"leg_id": "rerollout", "kind": "agent_rerollout",
             "description": "opt-in", "requires": ["real_agent"]},
        ],
    }
    archetype = archetype_from_dict(spec)
    pkg = emit_verifier_package(
        skill="my-skill",
        archetype_id="authored_change_quality_v1",
        out_dir=tmp_path / "p",
        episodes=_synth_episodes("my-skill"),
        seed="t",
        archetype=archetype,
    )
    assert pkg.dsel_before == 0.0
    assert pkg.dsel_after == pytest.approx(1.0)
    assert pkg.dtest_score == pytest.approx(1.0)
    assert pkg.accepted is True
    assert pkg.addressable_markers
    # the authored live leg is opt-in / default-off in the emitted spec
    import yaml  # type: ignore

    spec_yaml = yaml.safe_load(pkg.spec_path.read_text()) if _has_yaml() else json.loads(pkg.spec_path.read_text())
    live = [g for g in spec_yaml["graders"] if g["kind"] != "deterministic"]
    assert live and all(g["default_enabled"] is False for g in live)


def _has_yaml() -> bool:
    try:
        import yaml  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False
