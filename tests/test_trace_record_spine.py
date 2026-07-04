"""TraceRecord spine tests (plan 080 §3 + §20 Resolution C / L).

Covers the new ``patches[]`` authoritative reference, the ``Patch`` /
``GitAnchor`` models, and the derived-fields projection helper that keeps
``outcome.committed`` / ``outcome.commit_sha`` / ``git_links`` consistent
with the spine at TraceRecord construction time.

The amend-chain test (test_patch_anchor_overwrites_on_amend) is the
load-bearing scenario from §20 Resolution L: ``Patch.anchor`` holds the
LATEST commit_sha, prior shas accumulate newest-first on
``Patch.superseded_by``, and ``git_links`` reflects the full chain.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from opentraces_schema import (
    Agent,
    GitAnchor,
    GitLink,
    Outcome,
    Patch,
    TraceRecord,
)
from opentraces_schema.version import SCHEMA_VERSION

# Track 2 cross-track contract: importing this will fail at module-collect
# time if Track 2 hasn't shipped trace_derived yet. That's the intended
# cross-track signal.
from opentraces.core.trace_derived import (
    derive_outcome_and_git_links_from_patches,
    evidence_tier_to_gitlink_tier,
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_anchor(
    commit_sha: str,
    *,
    found: bool = True,
    tier: str = "exact_range_hash",
    last_searched_at: str = "2026-01-01T00:00:00Z",
) -> GitAnchor:
    return GitAnchor(
        last_searched_at=last_searched_at,
        found=found,
        commit_sha=commit_sha if found else None,
        evidence_tier=tier if found else None,
    )


def _minimal_record(**overrides) -> TraceRecord:
    return TraceRecord(
        trace_id=overrides.pop("trace_id", "trace-1"),
        session_id=overrides.pop("session_id", "session-1"),
        agent=overrides.pop("agent", Agent(name="claude-code")),
        **overrides,
    )


# --------------------------------------------------------------------------- #
# 1. Patch minimal construction
# --------------------------------------------------------------------------- #


class TestPatchModel:

    def test_patch_minimal_only_requires_patch_id_and_file_path(self):
        p = Patch(patch_id="p-1", file_path="src/foo.py")
        assert p.patch_id == "p-1"
        assert p.file_path == "src/foo.py"
        # All other fields have safe defaults.
        assert p.step_index is None
        assert p.tool_call_id is None
        assert p.capture_method == []
        assert p.snapshot_before_id is None
        assert p.snapshot_after_id is None
        assert p.anchor is None
        assert p.superseded_by == []
        assert p.limitations == []

    def test_patch_capture_method_accepts_hook_tags(self):
        p = Patch(
            patch_id="p-1",
            file_path="a.py",
            capture_method=["hook_pretooluse", "hook_posttooluse"],
        )
        assert p.capture_method == ["hook_pretooluse", "hook_posttooluse"]

    def test_patch_capture_method_accepts_watcher_backstop(self):
        p = Patch(
            patch_id="p-1",
            file_path="a.py",
            capture_method=["watcher_backstop"],
        )
        assert p.capture_method == ["watcher_backstop"]

    def test_patch_superseded_by_defaults_empty_and_is_assignable(self):
        p = Patch(patch_id="p-1", file_path="a.py")
        assert p.superseded_by == []
        p.superseded_by = ["A", "B"]
        assert p.superseded_by == ["A", "B"]


# --------------------------------------------------------------------------- #
# 2. GitAnchor 3-state machine + vocab validation
# --------------------------------------------------------------------------- #


class TestGitAnchorStateMachine:

    def test_never_searched_state_is_anchor_none(self):
        # State 1: never searched -> anchor is None on the patch.
        p = Patch(patch_id="p-1", file_path="a.py")
        assert p.anchor is None

    def test_searched_not_matched_state(self):
        # State 2: searched at last_searched_at, found=False, no commit fields.
        a = GitAnchor(last_searched_at="2026-01-01T00:00:00Z", found=False)
        assert a.found is False
        assert a.commit_sha is None
        assert a.path is None
        assert a.blob_sha is None
        assert a.git_patch_id is None
        assert a.evidence_tier is None
        assert a.evidence_firmness is None

    def test_searched_matched_state_carries_commit_fields(self):
        # State 3: matched; commit fields populated.
        a = GitAnchor(
            last_searched_at="2026-01-01T00:00:00Z",
            found=True,
            commit_sha="A",
            path="src/foo.py",
            blob_sha="b0",
            git_patch_id="gpi-1",
            evidence_tier="exact_range_hash",
            evidence_firmness="firm_observed",
        )
        assert a.found is True
        assert a.commit_sha == "A"
        assert a.path == "src/foo.py"
        assert a.blob_sha == "b0"
        assert a.git_patch_id == "gpi-1"
        assert a.evidence_tier == "exact_range_hash"
        assert a.evidence_firmness == "firm_observed"

    def test_git_anchor_requires_last_searched_at_and_found(self):
        with pytest.raises(ValidationError):
            GitAnchor()  # type: ignore[call-arg]

    def test_evidence_tier_accepts_all_ten_vocab_values(self):
        tiers = [
            "exact_blob_hash",
            "exact_range_hash",
            "git_clean_range_hash",
            "patch_id",
            "structural_symbol",
            "formatter_divergent",
            "overlapping_hunk",
            "time_file_overlap",
            "orphan",
            "unknown",
        ]
        for tier in tiers:
            a = GitAnchor(
                last_searched_at="2026-01-01T00:00:00Z",
                found=True,
                commit_sha="A",
                evidence_tier=tier,
            )
            assert a.evidence_tier == tier

    def test_evidence_tier_rejects_junk(self):
        with pytest.raises(ValidationError):
            GitAnchor(
                last_searched_at="2026-01-01T00:00:00Z",
                found=True,
                commit_sha="A",
                evidence_tier="not_a_real_tier",
            )

    def test_evidence_firmness_accepts_all_seven_vocab_values(self):
        firmness_values = [
            "firm_observed",
            "provisional",
            "human_asserted",
            "rule_inferred",
            "model_inferred",
            "statistically_inferred",
            "unknown",
        ]
        for fm in firmness_values:
            a = GitAnchor(
                last_searched_at="2026-01-01T00:00:00Z",
                found=True,
                commit_sha="A",
                evidence_firmness=fm,
            )
            assert a.evidence_firmness == fm


# --------------------------------------------------------------------------- #
# 3. TraceRecord / Outcome shape after plan 080
# --------------------------------------------------------------------------- #


class TestTraceRecordSpineShape:

    def test_trace_record_patches_defaults_to_empty_list(self):
        r = _minimal_record()
        assert r.patches == []

    def test_outcome_devtime_fields_default_to_unanchored(self):
        r = _minimal_record()
        assert r.outcome.committed is False
        assert r.outcome.commit_sha is None

    def test_outcome_patch_field_is_removed(self):
        """Plan 080: Outcome.patch (unified diff) is gone in 0.6.0.

        Pydantic's default behavior silently ignores unknown fields on
        construction, but the attribute does not exist on the instance.
        """
        o = Outcome()
        assert not hasattr(o, "patch")

        # Silent-ignore on construction is the Pydantic v2 default; verify
        # that passing patch= does NOT round-trip a value onto the model.
        o2 = Outcome(patch="diff text")  # type: ignore[call-arg]
        assert not hasattr(o2, "patch")
        # The dumped shape must not contain a patch key either.
        assert "patch" not in o2.model_dump()

    def test_schema_version_is_current(self):
        # 0.9.0 adds the additive dataset-run facet-scoping fields (#212);
        # the TraceRecord wire shape is otherwise unchanged.
        assert SCHEMA_VERSION == "0.9.0"
        r = _minimal_record()
        assert r.schema_version == "0.9.0"

    def test_trace_record_jsonl_round_trip_preserves_patches(self):
        r = _minimal_record(
            patches=[
                Patch(
                    patch_id="p-1",
                    file_path="a.py",
                    step_index=3,
                    capture_method=["hook_posttooluse"],
                    anchor=_make_anchor("A"),
                ),
                Patch(
                    patch_id="p-2",
                    file_path="b.py",
                    capture_method=["watcher_backstop"],
                ),
            ],
        )
        line = r.to_jsonl_line()
        r2 = TraceRecord.model_validate_json(line)
        assert r2.trace_id == r.trace_id
        assert len(r2.patches) == 2
        assert r2.patches[0].patch_id == "p-1"
        assert r2.patches[0].step_index == 3
        assert r2.patches[0].capture_method == ["hook_posttooluse"]
        assert r2.patches[0].anchor is not None
        assert r2.patches[0].anchor.commit_sha == "A"
        assert r2.patches[0].anchor.evidence_tier == "exact_range_hash"
        assert r2.patches[1].patch_id == "p-2"
        assert r2.patches[1].anchor is None


# --------------------------------------------------------------------------- #
# 4. derive_outcome_and_git_links_from_patches (Track 2's projection helper)
# --------------------------------------------------------------------------- #


class TestDerivedFieldsProjection:

    def test_empty_patches_leaves_outcome_unanchored(self):
        r = _minimal_record()
        derive_outcome_and_git_links_from_patches(r)
        assert r.outcome.committed is False
        assert r.outcome.commit_sha is None
        assert r.git_links == []

    def test_patch_with_no_anchor_contributes_nothing(self):
        r = _minimal_record(
            patches=[Patch(patch_id="p-1", file_path="a.py")]
        )
        derive_outcome_and_git_links_from_patches(r)
        assert r.outcome.committed is False
        assert r.outcome.commit_sha is None
        assert r.git_links == []

    def test_patch_with_found_false_anchor_contributes_nothing(self):
        r = _minimal_record(
            patches=[
                Patch(
                    patch_id="p-1",
                    file_path="a.py",
                    anchor=GitAnchor(
                        last_searched_at="2026-01-01T00:00:00Z",
                        found=False,
                    ),
                )
            ]
        )
        derive_outcome_and_git_links_from_patches(r)
        assert r.outcome.committed is False
        assert r.outcome.commit_sha is None
        assert r.git_links == []

    def test_single_anchored_patch_populates_derived_fields(self):
        r = _minimal_record(
            patches=[
                Patch(
                    patch_id="p-1",
                    file_path="a.py",
                    anchor=_make_anchor("A"),
                )
            ]
        )
        derive_outcome_and_git_links_from_patches(r)
        assert r.outcome.committed is True
        assert r.outcome.commit_sha == "A"
        assert len(r.git_links) == 1
        assert r.git_links[0].revision == "A"
        assert r.git_links[0].tier == "tool_emitted"

    def test_multi_patch_unique_commits_dedupe_correctly(self):
        # Two patches anchored to different commits -> 2 GitLinks.
        r = _minimal_record(
            patches=[
                Patch(
                    patch_id="p-1",
                    file_path="a.py",
                    anchor=_make_anchor(
                        "A", last_searched_at="2026-01-01T00:00:00Z"
                    ),
                ),
                Patch(
                    patch_id="p-2",
                    file_path="b.py",
                    anchor=_make_anchor(
                        "B", last_searched_at="2026-01-02T00:00:00Z"
                    ),
                ),
            ]
        )
        derive_outcome_and_git_links_from_patches(r)
        assert r.outcome.committed is True
        # Latest anchor (B at 2026-01-02) is the winning commit_sha.
        assert r.outcome.commit_sha == "B"
        revs = sorted(link.revision for link in r.git_links)
        assert revs == ["A", "B"]
        assert all(link.tier == "tool_emitted" for link in r.git_links)


# --------------------------------------------------------------------------- #
# 5. evidence_tier -> GitLink.tier projection (Resolution C / Soft M)
# --------------------------------------------------------------------------- #


class TestEvidenceTierProjection:

    def test_orphan_maps_to_orphan(self):
        assert evidence_tier_to_gitlink_tier("orphan") == "orphan"
        r = _minimal_record(
            patches=[
                Patch(
                    patch_id="p-1",
                    file_path="a.py",
                    anchor=_make_anchor("A", tier="orphan"),
                )
            ]
        )
        derive_outcome_and_git_links_from_patches(r)
        assert r.git_links[0].tier == "orphan"

    def test_overlapping_hunk_maps_to_overlapping(self):
        assert evidence_tier_to_gitlink_tier("overlapping_hunk") == "overlapping"
        r = _minimal_record(
            patches=[
                Patch(
                    patch_id="p-1",
                    file_path="a.py",
                    anchor=_make_anchor("A", tier="overlapping_hunk"),
                )
            ]
        )
        derive_outcome_and_git_links_from_patches(r)
        assert r.git_links[0].tier == "overlapping"

    def test_formatter_divergent_maps_to_tool_emitted_with_divergence(self):
        assert (
            evidence_tier_to_gitlink_tier("formatter_divergent")
            == "tool_emitted_with_divergence"
        )
        r = _minimal_record(
            patches=[
                Patch(
                    patch_id="p-1",
                    file_path="a.py",
                    anchor=_make_anchor("A", tier="formatter_divergent"),
                )
            ]
        )
        derive_outcome_and_git_links_from_patches(r)
        assert r.git_links[0].tier == "tool_emitted_with_divergence"


# --------------------------------------------------------------------------- #
# 6. The amend-chain test (§20 Resolution L)
# --------------------------------------------------------------------------- #


class TestAmendChain:

    def test_patch_anchor_overwrites_on_amend(self):
        """Plan 080 §20 Resolution L: Patch.anchor holds the LATEST commit_sha;
        prior shas accumulate newest-first on Patch.superseded_by; the derived
        ``git_links`` reflects the full chain (current + all prior).
        """
        # Step 1: first commit attaches a patch to commit A.
        patch = Patch(
            patch_id="p-1",
            file_path="src/foo.py",
            anchor=_make_anchor(
                "A",
                tier="exact_range_hash",
                last_searched_at="2026-01-01T00:00:00Z",
            ),
            superseded_by=[],
        )
        record = _minimal_record(patches=[patch])
        derive_outcome_and_git_links_from_patches(record)
        assert record.patches[0].anchor.commit_sha == "A"
        assert record.patches[0].superseded_by == []
        assert record.outcome.commit_sha == "A"
        assert sorted(l.revision for l in record.git_links) == ["A"]

        # Step 2: first git commit --amend -> patch still matches at B.
        # post-commit hook updates anchor.commit_sha = B, pushes A onto
        # superseded_by (newest-first).
        record.patches[0].anchor.commit_sha = "B"
        record.patches[0].anchor.last_searched_at = "2026-01-02T00:00:00Z"
        record.patches[0].superseded_by = ["A"]
        derive_outcome_and_git_links_from_patches(record)
        assert record.patches[0].anchor.commit_sha == "B"
        assert record.patches[0].superseded_by == ["A"]
        assert record.outcome.commit_sha == "B"
        revs = [l.revision for l in record.git_links]
        # Order is newest-first by construction: anchor commit_sha first,
        # then the superseded_by chain.
        assert revs == ["B", "A"]
        assert all(l.tier == "tool_emitted" for l in record.git_links)

        # Step 3: second amend -> anchor commit_sha = C; superseded_by = [B, A].
        record.patches[0].anchor.commit_sha = "C"
        record.patches[0].anchor.last_searched_at = "2026-01-03T00:00:00Z"
        record.patches[0].superseded_by = ["B", "A"]
        derive_outcome_and_git_links_from_patches(record)
        assert record.patches[0].anchor.commit_sha == "C"
        assert record.patches[0].superseded_by == ["B", "A"]
        assert record.outcome.commit_sha == "C"
        revs = [l.revision for l in record.git_links]
        assert revs == ["C", "B", "A"]
        assert len(record.git_links) == 3
        # All three commits share the original anchor's evidence_tier
        # (exact_range_hash -> tool_emitted).
        assert all(l.tier == "tool_emitted" for l in record.git_links)
