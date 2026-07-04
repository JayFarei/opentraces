"""C1 (#154) — replay must DERIVE trust ordinals from local / carried evidence,
NEVER trust a producer's self-reported labels.

The cardinal sin (ADR-0008 §5): a capsule must never claim more trust than it
earned from real, locally-verifiable state. A crafted or foreign capsule that
self-declares ``environment.env_tier="L4"``, ``oracle_trust="declared"``,
``slice_diff.diff_trust="exact"`` and ``sandbox_tier="microvm"`` must NOT be
graded ``high`` — with no backing evidence carried it FLOORS.

RED-first: the crafted-capsule-floors assertions name the defect BEFORE the fix.
Before the fix ``build_replay_packet`` reads those four labels straight off the
dict and reports ``verdict_trust=="high"``; after it derives them and floors.
The clamp + lattice are UNCHANGED — the fix is WHERE the factors come from.
"""

from __future__ import annotations

import json

import pytest


def _crafted_overclaiming_capsule(**overrides):
    """A hand-crafted, valid-shaped capsule that SELF-DECLARES the strongest label
    for every trust factor but carries NO backing evidence: no runnable test, no
    ``format_patch``. A malicious or foreign producer could ship exactly this to
    fake ``high``."""

    cap = {
        "capsule_id": "craftedcap000001",
        "summary": {
            "title": "totally fixed, trust me",
            "what_happened": "hit a TypeError",
            "failure": "TypeError: x",
            "is_failure": True,
        },
        "failing_step": {"index": 2, "type": "agent"},
        # self-declared L4 environment (no resolver actually ran to verify it)
        "environment": {"env_tier": "L4", "language_ecosystem": "python"},
        # self-declared exact diff with NO carried patch to back it
        "slice_diff": {"diff_trust": "exact"},
        # a free top-level self-label with NO carried test to back it
        "oracle_trust": "declared",
        # a self-claim about how the PRODUCER ran it (irrelevant to how WE grade it)
        "sandbox_tier": "microvm",
        "repo_pin": {"remote_url": "https://github.com/o/r", "commit_sha": "abc"},
        "context_resume_packet": {
            "schema_version": "opentraces.context_resume.v1",
            "node_id": "n",
        },
        "intent": {"headline": "agent tried to fix the parser"},
        "test": None,
    }
    cap.update(overrides)
    return cap


def test_crafted_capsule_cannot_overclaim_verdict_floors():
    from opentraces.core.capsule.replay import build_replay_packet

    packet = build_replay_packet(_crafted_overclaiming_capsule(), target_ref="HEAD")
    # The over-claim is IGNORED: every property reflects the ABSENT evidence.
    assert packet["properties"]["reproducible"]["ok"] is False  # env_tier forced L0
    assert packet["properties"]["sandboxed"]["ok"] is False  # sandbox_tier forced none
    assert packet["properties"]["gradable"]["ok"] is False  # no carried test
    assert packet["properties"]["scoped"]["ok"] is False  # no carried patch
    assert packet["verdict_trust"] == "floor"


def test_crafted_self_labels_are_not_read_as_trust_factors():
    from opentraces.core.capsule.replay import build_replay_packet

    tf = build_replay_packet(_crafted_overclaiming_capsule(), target_ref="HEAD")["trust_factors"]
    # env_tier is DERIVED (forced L0), never the self-declared L4.
    assert tf["env_tier"] == "L0"
    # sandbox_tier is DERIVED (forced none), never the self-declared microvm.
    assert tf["sandbox_tier"] == "none"
    # oracle_trust DERIVES from the (absent) carried test, never the free label.
    assert tf["oracle_trust"] != "declared"
    # diff_trust floors: an `exact` claim with no carried patch is not trusted.
    assert tf["diff_trust"] != "exact"


def test_bare_exact_difftrust_without_a_carried_patch_floors():
    # The diff-specific over-claim: `diff_trust="exact"` with no format_patch and
    # no changed_files must NOT be read as scoped.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _crafted_overclaiming_capsule(slice_diff={"diff_trust": "exact"})
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["scoped"]["ok"] is False
    assert packet["trust_factors"]["diff_trust"] == "unanchored"


def test_carried_exact_patch_backs_a_scoped_claim():
    # The honest positive: a carried, self-consistent single-commit patch DOES back
    # an `exact` claim (this is what a real export_capsule seals).
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _crafted_overclaiming_capsule(
        slice_diff={
            "diff_trust": "exact",
            "format_patch": "diff --git a/a.py b/a.py\n+x\n",
            "changed_files": ["a.py"],
        },
    )
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["properties"]["scoped"]["ok"] is True
    assert packet["trust_factors"]["diff_trust"] == "exact"
    # ...but env/sandbox still floor the overall verdict this train.
    assert packet["verdict_trust"] == "floor"


def test_local_run_result_sandbox_tier_is_honored_over_capsule_selfclaim():
    # The ONLY sandbox tier the consumer trusts is one a LOCAL run reported. A
    # capsule self-claiming microvm is ignored (none); a run_result the caller
    # threads in (its own local observation) IS trusted.
    from opentraces.core.capsule.replay import build_replay_packet

    cap = _crafted_overclaiming_capsule(sandbox_tier="microvm")
    p0 = build_replay_packet(cap, target_ref="HEAD")
    assert p0["trust_factors"]["sandbox_tier"] == "none"  # self-claim ignored

    p1 = build_replay_packet(cap, target_ref="HEAD", run_result={"sandbox_tier": "container"})
    assert p1["trust_factors"]["sandbox_tier"] == "container"  # local observation trusted
    assert p1["properties"]["sandboxed"]["ok"] is True


# --------------------------------------------------------------------------- #
# The other side of the contract: a LEGITIMATELY sealed capsule (via the real
# export seam) still reports its HONEST derived ordinals.
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


def _seed_trace(project_dir, trace_id, *, n_steps=6):
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
            "environment": {"language_ecosystem": ["python"]},
            "context_tree_summary": {"capture_method": "transcript_reconstruction"},
            "steps": [
                {"step_index": i, "role": "agent", "content": f"step {i}: hit a traceback error"}
                for i in range(n_steps)
            ],
        }
    )
    write_trace_record(rec, project_slug=slug, source_layer="test")
    return slug


def test_legit_sealed_capsule_reports_honest_derived_ordinals(export_env):
    from opentraces.core.capsule.export import export_capsule
    from opentraces.core.capsule.replay import build_replay_packet

    tid = "eeee1111-0000-0000-0000-000000000000"
    _seed_trace(export_env, tid)
    cap = export_capsule(project_dir=export_env, trace_id=tid)
    packet = build_replay_packet(cap, target_ref="HEAD")
    # Honest floor on today's corpus: env unpinned, no isolation.
    assert packet["properties"]["reproducible"]["ok"] is False
    assert packet["properties"]["sandboxed"]["ok"] is False
    assert packet["verdict_trust"] == "floor"


def test_legit_declared_test_derives_gradable_from_carried_evidence(export_env):
    # A maintainer declares a passing test -> the capsule CARRIES a runnable test,
    # so gradable DERIVES ok=True from real carried evidence (not a self-label).
    # env/sandbox stay floored, so verdict_trust stays floor.
    from opentraces.core.capsule.export import export_capsule
    from opentraces.core.capsule.replay import build_replay_packet

    tid = "eeee2222-0000-0000-0000-000000000000"
    _seed_trace(export_env, tid)
    cap = export_capsule(project_dir=export_env, trace_id=tid, test_command="pytest -q")
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert cap["test"] is not None and cap["test"]["source"] == "declared"
    assert packet["properties"]["gradable"]["ok"] is True  # derived from carried test
    assert packet["trust_factors"]["oracle_trust"] == "declared"
    assert packet["verdict_trust"] == "floor"  # env/sandbox floor
