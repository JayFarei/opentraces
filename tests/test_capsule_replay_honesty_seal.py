"""Replay/seal HONESTY fixes — two over-claims on the seal/replay surface.

RED-first: every assertion names a defect that is TRUE on the pre-fix code and
FALSE after the fix. The fix only ever LOWERS a claim (never raises trust); the
lattice/clamp and the seal-side pure mappers are unchanged.

Defect A — ``privacy_scope.messages_completeness`` over-claims ``"full"``. The
seal keys the field on the capture METHOD, so an ``otel`` (or ``None``) capture
whose messages layer is absent / approximated / hash-only reports ``"full"``
while ``source.completeness`` reads the real weaker value. Derive from the
messages LAYER's own carried ``completeness`` instead.

Defect B — the replay PROPERTIES surface inflates from carried self-labels: a
fabricated ``{command:"true", source:"declared"}`` reaches ``gradable=declared``
with no runnable assertion, and a garbage/inconsistent ``slice_diff`` reaches
``scoped=exact`` without the patch ever being parsed. Corroborate before
crediting: a graded oracle needs a real runnable assertion, and an ``exact``
diff must carry a well-formed patch whose paths BOUND the declared files.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- #
# Defect A — messages_completeness derives from the LAYER, not the method.
# --------------------------------------------------------------------------- #


def test_messages_completeness_helper_derives_from_layer():
    # RED-first: the net-new derivation must exist and read the LAYER's own
    # carried completeness, never launder a weak layer up to "full".
    from opentraces.core.capsule.export import _messages_completeness

    # transcript_reconstruction carries sha256 hashes, never real bodies — it
    # self-reports "full" at the layer, so it must still read hash_only (#195 F2).
    assert _messages_completeness(
        "transcript_reconstruction", {"completeness": "full"}
    ) == "hash_only"

    # A genuinely-full messages layer still reports "full".
    assert _messages_completeness("otel", {"completeness": "full"}) == "full"

    # An otel capture whose bodies were not paired carries an APPROXIMATED layer
    # (documented v1.1 gap) — it must NOT be laundered up to "full".
    assert _messages_completeness("otel", {"completeness": "approximated"}) != "full"

    # A None/absent capture_method with a weak layer likewise never reports full.
    assert _messages_completeness(None, {"completeness": "stub"}) != "full"

    # Zero message layers (the packet carries no messages_layer) is not "full".
    assert _messages_completeness("otel", None) != "full"


# --------------------------------------------------------------------------- #
# Defect B — the replay PROPERTIES surface corroborates before crediting.
# --------------------------------------------------------------------------- #


def _base_capsule(**overrides):
    cap = {
        "capsule_id": "sealhonesty0001",
        "summary": {
            "title": "fix the parser",
            "what_happened": "hit a TypeError",
            "failure": "TypeError: x",
            "is_failure": True,
        },
        "failing_step": {"index": 2, "type": "agent"},
        "repo_pin": {"remote_url": "https://github.com/o/r", "commit_sha": "abc"},
        "context_resume_packet": {
            "schema_version": "opentraces.context_resume.v1",
            "node_id": "n",
        },
        "intent": {"headline": "agent tried to fix the parser"},
    }
    cap.update(overrides)
    return cap


def test_fabricated_declared_oracle_without_a_runnable_assertion_floors():
    # RED-first: a fabricated test that self-labels source="declared" but whose
    # command (`true`) is NOT a recognized test framework and carries no
    # error-string assertion grades NOTHING — it must NOT reach gradable=declared.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _base_capsule(test={"command": "true", "source": "declared"})
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["gradable"]["ok"] is False
    assert packet["properties"]["gradable"]["level"] != "declared"
    assert packet["trust_factors"]["oracle_trust"] != "declared"


def test_declared_oracle_backed_by_a_real_framework_stays_declared():
    # The honest positive: a declared test whose command DOES invoke a recognized
    # framework carries a real runnable assertion → it legitimately grades.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _base_capsule(
        test={"command": "pytest -q", "source": "declared",
              "expected": {"kind": "nonzero_exit"}}
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["gradable"]["ok"] is True
    assert packet["trust_factors"]["oracle_trust"] == "declared"


def test_declared_oracle_with_error_string_assertion_stays_declared():
    # A generic command WITH an explicit error-string assertion is a real check
    # (the error is gone / present) → it stays gradable even without a framework.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _base_capsule(
        test={"command": "./repro.sh", "source": "declared",
              "expected": {"kind": "error_string", "value": "TypeError: x"}}
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["gradable"]["ok"] is True
    assert packet["trust_factors"]["oracle_trust"] == "declared"


def test_garbage_patch_with_arbitrary_file_list_is_not_scoped_exact():
    # RED-first: `diff_trust="exact"` with GARBAGE patch bytes + an arbitrary
    # file list must not reach scoped=exact — the patch is never a real diff.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _base_capsule(
        slice_diff={
            "diff_trust": "exact",
            "format_patch": "this is not a patch at all, just prose",
            "changed_files": ["a.py"],
        }
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["scoped"]["ok"] is False
    assert packet["trust_factors"]["diff_trust"] != "exact"


def test_inconsistent_patch_paths_are_not_scoped_exact():
    # A well-formed patch whose touched paths do NOT match the declared
    # changed_files does not BOUND the slice → it must floor, not read exact.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _base_capsule(
        slice_diff={
            "diff_trust": "exact",
            "format_patch": "diff --git a/real.py b/real.py\n+x\n",
            "changed_files": ["totally_different.py"],
        }
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["scoped"]["ok"] is False
    assert packet["trust_factors"]["diff_trust"] != "exact"


def test_wellformed_consistent_patch_backs_a_scoped_exact_claim():
    # The honest positive (mirrors the real export seam): a well-formed patch
    # whose touched paths EQUAL the declared changed_files DOES back exact.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _base_capsule(
        slice_diff={
            "diff_trust": "exact",
            "format_patch": "diff --git a/a.py b/a.py\n+x\n",
            "changed_files": ["a.py"],
        }
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["scoped"]["ok"] is True
    assert packet["trust_factors"]["diff_trust"] == "exact"


def test_combined_fabrication_does_not_show_declared_or_exact():
    # The exact scenario the task names: fabricated test.source="declared" + a
    # garbage/inconsistent slice_diff shows NEITHER gradable=declared NOR
    # scoped=exact on the PROPERTIES surface; verdict_trust still floors.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _base_capsule(
        test={"command": "true", "source": "declared"},
        slice_diff={"diff_trust": "exact", "format_patch": "garbage", "changed_files": ["x.py"]},
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["gradable"]["level"] != "declared"
    assert packet["properties"]["scoped"]["ok"] is False
    assert packet["verdict_trust"] == "floor"
