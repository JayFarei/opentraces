"""Agent-session slice CI guard (plan 064 M64-3 + M64-7).

This guard locks in plan 064's vertical slice as a make-target-shaped
regression test. The slice proves that the consumer-API surfaces
(`trail explain`, etc.) return real evidence on a real captured agent
session — not just the empty-state envelope today's
`build-dataset-lineage-*` journeys assert.

The slice is driven entirely through the otbox harness (driver +
journey runner), so this guard wraps a single TOML journey + verifies
the underlying checkpoint produces the audit data the journey reads
out of ``box.notes``.
"""

from __future__ import annotations

import pytest

from tests.otbox.drivers import get_driver
from tests.otbox.checkpoints import resolve_checkpoint
from tests.otbox.journey import run_journey


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Same override as tests/otbox/test_otbox_slice.py — otbox boxes
    isolate HOME themselves via the driver, so this fixture neutralises
    the repo-wide ``conftest`` autouse fixture (which would otherwise
    redirect HOME elsewhere and break the box lifecycle)."""
    yield


@pytest.fixture
def driver():
    return get_driver("local")


def test_captured_session_checkpoint_produces_real_evidence(driver):
    """c-captured-real-session must mint a real trace_id + commit_sha,
    and the resulting consumer-API journey must PASS happy-path.

    Source-dependent expectations (plan 072 / B0 flip): real-agent
    artifacts mint UUID session ids and their audits omit the
    intermediate tick/mature counters (command stdout is not captured
    in the archive), so the anchor-count assertion only applies to the
    synthetic chain. The journey below asserts the anchor evidence at
    the consumer surface on BOTH sources.
    """
    cp = resolve_checkpoint(driver, "c-captured-real-session")
    try:
        audit = cp.box.notes.get("c_captured_session_audit") or {}
        assert audit.get("trace_id"), (
            "checkpoint did not record a captured trace_id; "
            "the harness or _ingest-session failed silently"
        )
        assert audit.get("commit_sha"), audit
        assert int(audit.get("edit_step_index") or 0) >= 1
        source = (audit.get("capture_metadata") or {}).get("source")
        assert source in {"artifact", "synthetic"}, audit
        if source == "artifact":
            assert audit.get("session_id"), audit
        else:
            assert audit.get("session_id") == "sess-otbox-simple-refactor"
            # Either the watcher tick or trail mature must have created
            # the anchor — assert at least one path produced a real one.
            anchors = (
                int(audit.get("tick_trail_maturation_anchors") or 0)
                + int(audit.get("mature_anchors_created") or 0)
            )
            assert anchors >= 1, (
                f"no Git Anchors were materialised by the checkpoint "
                f"delta; audit={audit}"
            )

        result = run_journey(
            driver, cp.box, "agent-session-trail-explain-happy",
        )
        assert result.verdict == "PASS", (
            f"slice journey {result.name}: {result.verdict} — {result.reason}\n"
            + "\n".join(
                f"  {a.kind} step={a.spec.get('step')} "
                f"path={a.spec.get('path')!r}: {a.message}"
                for a in result.assertions
                if not a.ok
            )
        )
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)


def test_captured_with_revert_checkpoint_produces_reverted_state(driver):
    """c-captured-with-revert must run ``git revert`` against the
    captured commit and land the patch on ``reverted`` — exactly.

    Post-#32 (the before-blob revert guard in
    ``core/trails/anchors.py``, commit 5cb6ff9f9a0) the substrate can
    no longer mis-anchor the patch onto the revert commit, so
    ``reverted`` is deterministic on BOTH world sources (restored
    real-agent artifact or synthetic fake-claude chain — the artifact
    path applies the revert in the delta when the capture's revert
    turn didn't land a commit). Any other state is a substrate or
    checkpoint-harness regression, not an accepted outcome.
    """
    cp = resolve_checkpoint(driver, "c-captured-with-revert")
    try:
        audit = cp.box.notes.get("c_captured_with_revert_audit") or {}
        assert audit.get("revert_commit_sha"), audit
        assert audit.get("original_commit_sha"), audit
        assert audit.get("reverted_trace_id"), audit
        assert audit.get("reverted_trace_patch_id"), audit
        assert (audit.get("capture_metadata") or {}).get("source") in {
            "artifact", "synthetic",
        }, audit
        assert audit.get("survival_state_after_revert") == "reverted", audit
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)


def test_captured_multi_skill_checkpoint_has_history_depth(driver):
    cp = resolve_checkpoint(driver, "c-captured-multi-skill")
    try:
        audit = cp.box.notes.get("c_captured_multi_skill_audit") or {}
        assert int(audit.get("captured_session_count") or 0) >= 3, audit
        trace_ids = audit.get("trace_ids") or []
        assert len(trace_ids) >= 3, audit
        # All trace_ids must be distinct (no accidental re-use)
        assert len(set(trace_ids)) == len(trace_ids), audit
        commit_shas = audit.get("commit_shas") or []
        assert len(commit_shas) >= 3, audit
        assert len(set(commit_shas)) == len(commit_shas), audit
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)


def test_captured_with_secrets_checkpoint_drives_security_pipeline(driver):
    cp = resolve_checkpoint(driver, "c-captured-with-secrets")
    try:
        audit = cp.box.notes.get("c_captured_with_secrets_audit") or {}
        assert audit.get("trace_id"), audit
        # At least record that the security pipeline saw the trace —
        # whether tools fired depends on which are enabled by default.
        assert "tools_applied" in audit, audit
        assert "secret_present_in_disk" in audit, audit
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)


def test_captured_with_pr_branch_checkpoint_has_branch_history(driver):
    cp = resolve_checkpoint(driver, "c-captured-with-pr-branch")
    try:
        audit = cp.box.notes.get("c_captured_with_pr_branch_audit") or {}
        assert audit.get("base_commit_sha"), audit
        assert audit.get("branch_name") == "feat/pr-branch-test", audit
        assert int(audit.get("branch_commit_count") or 0) >= 2, audit
        branch_commits = audit.get("branch_commit_shas") or []
        assert len(branch_commits) >= 2, audit
        assert len(set(branch_commits)) == len(branch_commits), audit
        branch_trace_ids = audit.get("branch_trace_ids") or []
        # Each branch commit should have its own trace
        assert len(branch_trace_ids) >= 2, audit
    finally:
        if cp.box.root.exists():
            driver.teardown(cp.box)
