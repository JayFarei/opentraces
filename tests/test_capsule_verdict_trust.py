"""Verdict-trust clamp (#154, U1) — the cross-cutting honesty spine.

Lands ADR-0008 §5's RATIFIED trust lattice: a pure ``clamp`` over four
incommensurable factor vocabularies (each mapped onto one shared 0..3 position
ladder, min taken, re-expressed in the ``{floor,low,medium,high}`` output
namespace), the four floor-defaulted ordinals read by ``build_replay_packet``,
and the ``verdict_trust`` / ``honest_claim`` / ``env_caveat`` stamping.

All SYNTHETIC + RED-first: every net-new symbol (``clamp``, the packet-level
computed ``verdict_trust``, ``honest_claim``, ``env_caveat``, the four ordinal
reads) is asserted to exist and be correct here BEFORE it exists in ``src/``. A
RED test that names a missing symbol IS the spec; it is never deleted to go
green.

STOP-CONDITION: this gate only ever goes green by LOWERING a claim, never by
raising trust. Every acceptance below floors today's corpus honestly; the one
non-floor test drives a fully-loaded SYNTHETIC capsule to prove the clamp reads
real state (it is never a today's-corpus capsule).

Mirrors the pure-function shape of ``tests/test_capsule_oracle.py::_v``.
"""

from __future__ import annotations

import json

import pytest


# --------------------------------------------------------------------------- #
# The pure clamp — mirror of the oracle ``_v`` shape
# --------------------------------------------------------------------------- #


def _c(oracle, env_tier, diff, sandbox):
    """Call the pure clamp with the canonical keyword names, return its token."""

    from opentraces.core.capsule.replay import clamp

    return clamp(
        oracle_trust=oracle, env_tier=env_tier, diff_trust=diff, sandbox_tier=sandbox
    )


def test_clamp_symbol_exists():
    # RED-first: the net-new pure function must exist and be callable.
    from opentraces.core.capsule import replay

    assert hasattr(replay, "clamp"), "net-new symbol `clamp` must exist (RED-first spec)"
    assert callable(replay.clamp)


def test_output_vocabulary_is_floor_low_medium_high():
    from opentraces.core.capsule.replay import VERDICT_TRUST_OUTPUT

    assert VERDICT_TRUST_OUTPUT == {0: "floor", 1: "low", 2: "medium", 3: "high"}


def test_verdict_trust_is_min_of_factors():
    # ADR-0008 §5 RATIFIED-form acceptance set (defect-2 + defect-3 corrected).
    assert _c("declared", "L3", "exact", "none") == "floor"      # sandbox floors
    assert _c("none", "L3", "exact", "microvm") == "floor"       # oracle floors
    assert _c("declared", "L0", "exact", "microvm") == "floor"   # env floors
    assert _c("declared", "L4", "exact", "microvm") == "high"    # CORRECTED high (L4)
    assert _c("declared", "L3", "exact", "microvm") == "medium"  # L3 (pos 2) caps at medium


def test_diff_exact_is_position_three_not_two():
    # ADR-0008 defect-2: `exact` sits at position 3 (the strongest diff claim
    # does NOT degrade the verdict), so `high` is reachable through the diff min.
    assert _c("declared", "L4", "exact", "microvm") == "high"
    # diff positions are SPARSE — `partial` is position 1 (no rung at 2).
    assert _c("declared", "L4", "partial", "microvm") == "low"
    # both weakest diff labels floor.
    assert _c("declared", "L4", "file_list_only", "microvm") == "floor"
    assert _c("declared", "L4", "unanchored", "microvm") == "floor"


def test_env_tier_ladder_has_no_L2():
    from opentraces.core.capsule.replay import FACTOR_POSITIONS

    env = FACTOR_POSITIONS["env_tier"]
    assert env == {"L0": 0, "L1": 1, "L3": 2, "L4": 3}
    assert "L2" not in env  # deliberate gap; L2 is never emitted (ADR-0008 defect-1)
    assert _c("declared", "L3", "exact", "microvm") == "medium"  # L3 = pos 2
    assert _c("declared", "L4", "exact", "microvm") == "high"    # L4 = pos 3


def test_oracle_trust_positions():
    # declared(3) > captured_error(2) == captured_pass(2) > intent_reposed(1) > none(0).
    assert _c("declared", "L4", "exact", "microvm") == "high"
    assert _c("captured_error", "L4", "exact", "microvm") == "medium"
    assert _c("captured_pass", "L4", "exact", "microvm") == "medium"
    assert _c("intent_reposed", "L4", "exact", "microvm") == "low"
    assert _c("none", "L4", "exact", "microvm") == "floor"


def test_sandbox_tier_positions():
    # none/S0=0, jail/S1=1, container/S2=2, microvm/S3=3.
    assert _c("declared", "L4", "exact", "microvm") == "high"
    assert _c("declared", "L4", "exact", "container") == "medium"
    assert _c("declared", "L4", "exact", "jail") == "low"
    assert _c("declared", "L4", "exact", "none") == "floor"


def test_any_single_floor_factor_floors_the_verdict():
    loaded = dict(oracle="declared", env_tier="L4", diff="exact", sandbox="microvm")
    assert _c(**loaded) == "high"  # baseline: fully loaded → high

    floors = {
        "oracle": "none",
        "env_tier": "L0",
        "diff": "unanchored",
        "sandbox": "none",
    }
    for factor, floor_val in floors.items():
        dialed = dict(loaded)
        dialed[factor] = floor_val
        assert _c(**dialed) == "floor", f"{factor}={floor_val} must floor the verdict"

    # `file_list_only` is the other diff floor value.
    dialed = dict(loaded)
    dialed["diff"] = "file_list_only"
    assert _c(**dialed) == "floor"


def test_floor_output_is_a_distinct_namespace_from_sandbox_none():
    # The KEY collision guard: verdict_trust=='floor' and sandbox_tier=='none'
    # both mean position 0 but live in DISTINCT vocabularies. The output token is
    # 'floor', NEVER the factor label 'none'.
    result = _c("none", "L0", "unanchored", "none")
    assert result == "floor"
    assert result != "none"


def test_clamp_raises_on_unknown_vocab_value():
    # "do not silently floor a typo" — an unknown value is a loud error, and a
    # MISSING field (handled at the read site) is what uses the floor default.
    from opentraces.core.capsule.replay import clamp

    with pytest.raises(ValueError):
        # L2 is deliberately not in the ladder.
        clamp(oracle_trust="declared", env_tier="L2", diff_trust="exact", sandbox_tier="microvm")
    with pytest.raises(ValueError):
        clamp(oracle_trust="typo", env_tier="L4", diff_trust="exact", sandbox_tier="microvm")


# --------------------------------------------------------------------------- #
# The four floor-defaulted ordinals + verdict_trust/honest_claim/env_caveat
# stamped on build_replay_packet
# --------------------------------------------------------------------------- #


def _floor_capsule(**overrides):
    """A minimal SYNTHETIC capsule dict — a typical today-record shape.

    No resolver (no env_tier in the environment block → floor default L0), no
    slice_diff (→ unanchored), no oracle_trust (→ none), no sandbox_tier (→
    none). So build_replay_packet must clamp it to 'floor'.
    """

    cap = {
        "capsule_id": "abc123def456abcd",
        "summary": {
            "title": "fix the parser",
            "what_happened": "hit a TypeError",
            "failure": "TypeError: x",
            "is_failure": True,
        },
        "failing_step": {"index": 3, "type": "agent"},
        "environment": {"dependencies": [], "language_ecosystem": "python", "setup": []},
        "repo_pin": {"remote_url": "https://github.com/o/r", "commit_sha": "abc"},
        "context_resume_packet": {
            "schema_version": "opentraces.context_resume.v1",
            "node_id": "n",
        },
        "intent": {"headline": "agent tried to fix the parser"},
    }
    cap.update(overrides)
    return cap


def test_build_replay_packet_stamps_verdict_trust_honest_claim_env_caveat():
    # RED-first: the three net-new packet keys must exist.
    from opentraces.core.capsule.replay import build_replay_packet

    packet = build_replay_packet(_floor_capsule(), target_ref="HEAD")
    assert "verdict_trust" in packet
    assert "honest_claim" in packet
    assert "env_caveat" in packet
    assert packet["verdict_trust"] == "floor"
    assert "not reproducible" in packet["honest_claim"].lower()
    # The env caveat honestly names the unpinned L0 environment.
    assert "L0" in packet["env_caveat"] or "env_tier" in packet["env_caveat"].lower()


def test_build_replay_packet_preserves_existing_v1_shape():
    # Additive-within-v1: the new keys do NOT bump the envelope version and do
    # NOT disturb the pre-existing packet contract.
    from opentraces.core.capsule.replay import (
        REPLAY_SCHEMA_VERSION,
        build_replay_packet,
    )

    packet = build_replay_packet(_floor_capsule(), target_ref="HEAD")
    assert packet["schema_version"] == REPLAY_SCHEMA_VERSION == "opentraces.capsule_replay.v1"
    assert packet["capsule_id"] == "abc123def456abcd"
    assert packet["oracle"]["strategy"] == "error_string_gone"


def test_packet_verdict_rises_only_when_all_four_fields_are_loaded():
    # Proves build_replay_packet READS the four ordinals off the capsule (rather
    # than hardcoding 'floor'): a fully-loaded SYNTHETIC capsule clamps to
    # 'high'. This is the ONLY way the gate goes non-floor — by real stamped
    # state, never by softening the clamp.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _floor_capsule(
        environment={"env_tier": "L4"},
        slice_diff={"diff_trust": "exact"},
        oracle_trust="declared",
        sandbox_tier="microvm",
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["verdict_trust"] == "high"
    assert "not reproducible" not in packet["honest_claim"].lower()


def test_partial_load_clamps_to_weakest_link():
    # Everything strong except the environment (still L0) → floored by env_tier.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _floor_capsule(
        environment={"env_tier": "L0"},
        slice_diff={"diff_trust": "exact"},
        oracle_trust="declared",
        sandbox_tier="microvm",
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["verdict_trust"] == "floor"
    assert "not reproducible" in packet["honest_claim"].lower()


# --------------------------------------------------------------------------- #
# Today's corpus, via the REAL export seam — proves the env_tier=L0 floor lands
# as a real field in the environment block (DELTA #4).
# --------------------------------------------------------------------------- #


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    from opentraces.core import paths

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / paths.MARKER_FILENAME).write_text(
        json.dumps({"project_id": "test-project-id"}), encoding="utf-8"
    )
    return project_dir


def _seed_trace(project_dir, trace_id, *, n_steps=6, eco=("python",)):
    from opentraces_schema import TraceRecord

    from opentraces.core.bucket_trace_records import write_trace_record
    from opentraces.core.config import get_project_dir

    slug = get_project_dir(project_dir).name
    rec = TraceRecord.model_validate(
        {
            "trace_id": trace_id,
            "session_id": trace_id,
            "agent": {"name": "codex", "version": "1.0", "model": "gpt"},
            "task": {"description": "fix the flaky parser", "base_commit": "abc123def456"},
            "environment": {"language_ecosystem": list(eco)},
            "context_tree_summary": {"capture_method": "transcript_reconstruction"},
            "steps": [
                {"step_index": i, "role": "agent", "content": f"step {i}: hit a traceback error"}
                for i in range(n_steps)
            ],
        }
    )
    write_trace_record(rec, project_slug=slug, source_layer="test")
    return slug


def test_exported_capsule_carries_env_tier_floor_in_environment_block(export_env):
    # DELTA #4: env_tier=L0 lands as a real field in the environment block, so
    # #202's resolver can later raise it WITHOUT a second envelope change.
    from opentraces.core.capsule.export import export_capsule

    tid = "cccc1111-0000-0000-0000-000000000000"
    _seed_trace(export_env, tid)
    cap = export_capsule(project_dir=export_env, trace_id=tid)
    assert cap["environment"]["env_tier"] == "L0"


def test_todays_corpus_every_capsule_is_floor(export_env):
    from opentraces.core.capsule.export import export_capsule
    from opentraces.core.capsule.replay import build_replay_packet

    tid = "cccc2222-0000-0000-0000-000000000000"
    _seed_trace(export_env, tid)
    cap = export_capsule(project_dir=export_env, trace_id=tid)
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["verdict_trust"] == "floor"
    assert "not reproducible" in packet["honest_claim"].lower()


# --------------------------------------------------------------------------- #
# #154 reshape — the four named PROPERTIES are the PRIMARY honest surface.
# verdict_trust stays as the DERIVED weakest-link summary (computed the same way,
# exercised by the clamp/min tests above). These add the property-block spec.
# --------------------------------------------------------------------------- #

_PROPERTY_NAMES = ("reproducible", "gradable", "scoped", "sandboxed")


def test_replay_properties_symbol_exists():
    # RED-first: the net-new derivation must exist and be callable.
    from opentraces.core.capsule import replay

    assert hasattr(replay, "_replay_properties"), (
        "net-new symbol `_replay_properties` must exist (RED-first spec)"
    )
    assert callable(replay._replay_properties)


def test_packet_carries_properties_block_with_ok_level_note():
    from opentraces.core.capsule.replay import build_replay_packet

    packet = build_replay_packet(_floor_capsule(), target_ref="HEAD")
    assert "properties" in packet, "the properties block is the PRIMARY surface"
    props = packet["properties"]
    assert set(props) == set(_PROPERTY_NAMES)
    for name in _PROPERTY_NAMES:
        prop = props[name]
        assert set(prop) == {"ok", "level", "note"}
        assert isinstance(prop["ok"], bool)
        assert isinstance(prop["level"], str) and prop["level"]
        assert isinstance(prop["note"], str) and prop["note"]


def test_each_property_ok_and_level_derive_from_its_ordinal():
    # Fully-loaded SYNTHETIC capsule: every property flips ok=True, and `level`
    # is exactly the stamped ordinal value.
    from opentraces.core.capsule.replay import build_replay_packet

    loaded = _floor_capsule(
        environment={"env_tier": "L4"},
        slice_diff={"diff_trust": "exact"},
        oracle_trust="declared",
        sandbox_tier="microvm",
    )
    props = build_replay_packet(loaded, target_ref="HEAD")["properties"]
    assert props["reproducible"] == {"ok": True, "level": "L4",
                                     "note": "cross-platform hermetic"}
    assert props["gradable"]["ok"] is True and props["gradable"]["level"] == "declared"
    assert props["scoped"]["ok"] is True and props["scoped"]["level"] == "exact"
    assert props["sandboxed"]["ok"] is True and props["sandboxed"]["level"] == "microvm"


def test_property_ok_boundaries_are_lattice_derived():
    # The ok boundary is derived from the ordinal, not hardcoded:
    #   reproducible ok ONLY at env L3/L4; gradable ok ONLY with a runnable/declared
    #   oracle; scoped ok ONLY at exact; sandboxed ok at ANY non-none tier.
    from opentraces.core.capsule.replay import _replay_properties

    # L1 dependencies-pinned is NOT reproducible (only L3/L4 are).
    assert _replay_properties(
        {"oracle_trust": "none", "env_tier": "L1",
         "diff_trust": "unanchored", "sandbox_tier": "none"}
    )["reproducible"]["ok"] is False
    assert _replay_properties(
        {"oracle_trust": "none", "env_tier": "L3",
         "diff_trust": "unanchored", "sandbox_tier": "none"}
    )["reproducible"]["ok"] is True
    # intent_reposed is NOT gradable (no runnable assertion); captured_pass IS.
    assert _replay_properties(
        {"oracle_trust": "intent_reposed", "env_tier": "L0",
         "diff_trust": "unanchored", "sandbox_tier": "none"}
    )["gradable"]["ok"] is False
    assert _replay_properties(
        {"oracle_trust": "captured_pass", "env_tier": "L0",
         "diff_trust": "unanchored", "sandbox_tier": "none"}
    )["gradable"]["ok"] is True
    # partial diff is NOT scoped (only exact is).
    assert _replay_properties(
        {"oracle_trust": "none", "env_tier": "L0",
         "diff_trust": "partial", "sandbox_tier": "none"}
    )["scoped"]["ok"] is False
    # a jail IS sandboxed (any non-none tier).
    assert _replay_properties(
        {"oracle_trust": "none", "env_tier": "L0",
         "diff_trust": "unanchored", "sandbox_tier": "jail"}
    )["sandboxed"]["ok"] is True


def test_todays_corpus_reproducible_and_sandboxed_false_and_verdict_floor(export_env):
    # The load-bearing honesty floor under the new surface: on today's corpus the
    # environment is unpinned and the run has no isolation, so BOTH properties read
    # False and the derived weakest-link verdict_trust is still floor.
    from opentraces.core.capsule.export import export_capsule
    from opentraces.core.capsule.replay import build_replay_packet

    tid = "cccc3333-0000-0000-0000-000000000000"
    _seed_trace(export_env, tid)
    cap = export_capsule(project_dir=export_env, trace_id=tid)
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["reproducible"]["ok"] is False
    assert packet["properties"]["sandboxed"]["ok"] is False
    assert packet["verdict_trust"] == "floor"


def test_honest_claim_leads_with_the_named_properties():
    # The claim mentions all four property names in plain language, ahead of the
    # derived verdict_trust summary.
    from opentraces.core.capsule.replay import build_replay_packet

    claim = build_replay_packet(_floor_capsule(), target_ref="HEAD")["honest_claim"]
    lowered = claim.lower()
    for name in _PROPERTY_NAMES:
        assert name in lowered, f"honest_claim must mention `{name}`"
    assert "verdict_trust" in lowered  # the demoted weakest-link summary is still named
