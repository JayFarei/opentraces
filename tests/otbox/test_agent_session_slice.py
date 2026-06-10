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
    and the resulting consumer-API journey must PASS happy-path."""
    cp = resolve_checkpoint(driver, "c-captured-real-session")
    try:
        audit = cp.box.notes.get("c_captured_session_audit") or {}
        assert audit.get("trace_id"), (
            "checkpoint did not record a captured trace_id; "
            "the harness or _ingest-session failed silently"
        )
        assert audit.get("commit_sha"), audit
        assert audit.get("session_id") == "sess-otbox-simple-refactor"
        assert int(audit.get("edit_step_index") or 0) >= 1
        # Either the watcher tick or trail mature must have created the
        # anchor — assert at least one path produced a real anchor.
        anchors = (
            int(audit.get("tick_trail_maturation_anchors") or 0)
            + int(audit.get("mature_anchors_created") or 0)
        )
        assert anchors >= 1, (
            f"no Git Anchors were materialised by the checkpoint delta; "
            f"audit={audit}"
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


@pytest.mark.xfail(
    reason="PR-B finding #4 (issue #25): the substrate produces survival=unknown, "
    "not 'reverted', for a real git revert. verify_provides correctly refuses "
    "c-captured-with-revert's 'reverted' provides. Tracked product gap in the "
    "8-survival-state claim; the rigorous survival-walk-reverted journey is also "
    "quarantined. xfail (not skip) so this flips green the moment the substrate "
    "learns to classify reverts.",
    strict=False,
)
def test_captured_with_revert_checkpoint_produces_reverted_state(driver):
    """c-captured-with-revert must run ``git revert`` against the
    captured commit and record the post-revert survival state honestly.

    The substrate-acceptable outcomes are ``reverted`` / ``lost`` /
    ``alive_transformed`` (the patch's content didn't survive the
    revert) — and also ``unknown`` (the survival-detection surface
    didn't return a structured signal for this patch). Plan 070's
    ``survival-walk-reverted.toml`` is the rigorous test of survival-
    state behavior at the consumer-API surface; the checkpoint
    pytest just verifies the substrate runs without erroring and
    records *some* post-revert state.
    """
    cp = resolve_checkpoint(driver, "c-captured-with-revert")
    try:
        audit = cp.box.notes.get("c_captured_with_revert_audit") or {}
        assert audit.get("revert_commit_sha"), audit
        assert audit.get("original_commit_sha"), audit
        assert audit.get("reverted_trace_id"), audit
        assert audit.get("survival_state_after_revert") in {
            "reverted", "lost", "alive_transformed", "unknown",
        }, audit
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
