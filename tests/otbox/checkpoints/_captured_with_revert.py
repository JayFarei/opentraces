"""`c-captured-with-revert` — plan 068 M68-1.

Composes onto ``c-captured-real-session``: after the captured
session's Edit has landed as commit ``original_commit_sha`` with a
matured Git Anchor, this delta runs ``git revert <sha> --no-edit`` so
the Trace Patch's survival state matures away from ``alive_on_path``.

The revert produces a new commit on top of the captured one that
restores the file to its pre-Edit content. After the watcher tick +
explicit ``trail mature`` against the revert commit, querying the
patch via ``opentraces trail sync --patch <id>`` reports a survival
state of ``reverted`` — deterministically. Pre-#32 the structural
matcher could mis-anchor the patch onto the revert commit (whose blob
is the patch's before-state) and the alive-looking anchor outranked
``reverted`` under ``any_alive_anchor_wins``; the before-blob guard
(``anchors.py``, commit 5cb6ff9f9a0) closed that, so ``reverted`` is
now the only honest outcome and the checkpoint's ``provides`` says
exactly that. ``verify_provides`` re-checks it live on every build
(``trail search --survival reverted`` must return >= 1 trail).

Artifact-preferred (plan 072 / B0 flip): when the real-agent capture
``tests/otbox/captures/claude-with-revert/`` is present it is restored
in-place. The committed 2.1.170 capture landed the scenario's Edit
commit but its revert turn produced NO commit, so the delta detects a
missing revert on the restored HEAD and applies the same
``git revert`` + tick + mature chain the synthetic path uses — the
checkpoint's contract is the post-revert world, however the capture
landed. A future capture whose revert DID land is detected via the
``This reverts commit`` body literal and consumed as-is.

The parent checkpoint's audit lives in
``box.notes["c_captured_session_audit"]`` and carries the
``commit_sha`` + ``trace_id`` this delta reverts. On the artifact path
that audit describes the world the restore just wiped, so it is
re-derived from the restored state (journey templating reads
``{trace_id}`` / ``{step_index}`` from it). A new audit is appended at
``box.notes["c_captured_with_revert_audit"]``.
"""

from __future__ import annotations

import json

from ..drivers.base import Driver
from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    capture_metadata_from_artifact,
    check as _check_helper,
    edit_step_index_from_record,
    git as _git_helper,
    load_trace_record,
    read_state_json,
    restore_from_capture,
    synthetic_capture_metadata,
    traces_by_created_at,
    transcript_path_for_session,
)
from ._captured_session import EDIT_STEP_INDEX

_CHECKPOINT_NAME = "c-captured-with-revert"
# Plan B0 flip: the real-agent artifact produced by
# `make capture-refresh SCENARIO=claude-with-revert` (scenario-named dir).
_CAPTURE_NAME = "claude-with-revert"

# The literal `git revert --no-edit` writes into the revert commit's
# body — the same marker sync.py::_find_revert_commit greps for.
_REVERT_BODY_MARKER = "This reverts commit "


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CHECKPOINT_NAME, label=label)


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_CHECKPOINT_NAME)


def _resolve_survival_state(
    driver: Driver,
    box: Box,
    cli: list[str],
    project: str,
    trace_id: str,
) -> tuple[str, str | None]:
    """Look up the survival state for the captured Trace Patch after
    the revert + maturation steps.

    Returns ``(survival_state, trace_patch_id)``. The state defaults
    to ``"unknown"`` if we cannot find a ``trace_patch_created`` event
    for the captured ``trace_id`` — the audit still records the
    revert facts so downstream assertions can decide.

    We avoid importing ``opentraces.core.trails`` directly because
    Tier 1 drivers may not have the project's package importable from
    the harness host; instead we use the existing JSON CLI surface
    (``trail sync --patch <id> --json``), which is the documented
    consumer path and is exercised by the substrate's CI suites.
    """
    # Find the captured Trace Patch id by scanning the trail timeline
    # via the JSON CLI (``trail timeline`` enumerates events). We
    # don't need to know its hash up front; the first
    # ``trace_patch_created`` event for the captured trace is the one
    # the revert targets.
    timeline = driver.exec(box, [
        *cli, "trail", "timeline", trace_id,
        "--project", project,
        "--json",
    ])
    patch_id: str | None = None
    if timeline.ok and timeline.stdout.strip():
        try:
            payload = json.loads(timeline.stdout)
        except json.JSONDecodeError:
            payload = None
        events = []
        if isinstance(payload, dict):
            events = payload.get("events") or payload.get("timeline") or []
        elif isinstance(payload, list):
            events = payload
        for event in events:
            if not isinstance(event, dict):
                continue
            etype = event.get("event_type") or event.get("type")
            if etype != "trace_patch_created":
                continue
            event_payload = event.get("payload") or {}
            candidate = (
                event_payload.get("trace_patch_id")
                or event.get("trace_patch_id")
            )
            if candidate:
                patch_id = candidate
                break

    if not patch_id:
        return "unknown", None

    sync = driver.exec(box, [
        *cli, "trail", "sync",
        "--patch", patch_id,
        "--project", project,
        "--json",
    ])
    if not sync.ok or not sync.stdout.strip():
        return "unknown", patch_id
    try:
        sync_payload = json.loads(sync.stdout)
    except json.JSONDecodeError:
        return "unknown", patch_id
    if not isinstance(sync_payload, dict):
        return "unknown", patch_id
    current = sync_payload.get("current_survival") or {}
    state = (
        current.get("survival_state")
        or sync_payload.get("survival_state")
        or "unknown"
    )
    return state, patch_id


def _revert_and_mature(
    driver: Driver,
    box: Box,
    cli: list[str],
    project: str,
    original_commit_sha: str,
) -> tuple[str, dict]:
    """Apply ``git revert <sha> --no-edit`` + watcher tick + explicit
    ``trail mature`` against the revert commit.

    Shared by the synthetic chain and the artifact path (the committed
    real-agent capture landed the Edit commit but not the revert).
    Returns ``(revert_commit_sha, mature_payload)``.
    """
    # Using --no-edit keeps the operation non-interactive; using the
    # explicit sha (rather than HEAD) makes the delta idempotent if
    # the parent ever appends additional commits between capture and
    # revert.
    _git(driver, box, "revert", original_commit_sha, "--no-edit")
    revert_commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()
    if not revert_commit_sha:
        raise CheckpointError("could not resolve revert commit HEAD")

    # Watcher tick — same reconciliation path the parent uses, so any
    # filesystem mutations the revert produced are folded into the
    # canonical event log before maturation runs.
    tick = driver.exec(box, [
        *cli, "setup", "watcher", "tick",
        "--project", project, "--json",
    ])
    _check(tick, "setup watcher tick (post-revert)")

    # Explicit ``trail mature`` against the revert commit — same
    # belt-and-suspenders backstop the parent uses against HEAD.
    # Maturing against the revert commit is what lets the survival
    # query see the revert in its history window. Post-#32 the
    # before-blob guard refuses to anchor the patch onto the revert
    # commit itself, so this records an honest search, not a spurious
    # alive anchor.
    mature = driver.exec(box, [
        *cli, "trail", "mature",
        "--commit", revert_commit_sha,
        "--project", project, "--json",
    ])
    _check(mature, "trail mature --commit revert")
    try:
        mature_payload = json.loads(mature.stdout) if mature.stdout else {}
    except json.JSONDecodeError:
        mature_payload = {}
    if not isinstance(mature_payload, dict):
        mature_payload = {}
    return revert_commit_sha, mature_payload


def _derive_revert_audit_from_restored_box(
    driver: Driver, box: Box, cap_meta: dict,
) -> dict:
    """Restore-path audit: ensure the revert exists, then re-derive.

    Plan 072 R2 + B0 flip — the committed ``claude-with-revert``
    artifact captures the post-Edit box state (the scenario's revert
    turn did not land a commit), so HEAD is normally the captured Edit
    commit and the delta applies the revert itself. When a future
    capture's revert DID land, HEAD carries the ``This reverts
    commit`` body literal and is consumed as-is. The reverted trace is
    the most recent capture in state.json (real agents mint UUID
    session ids, so capture order is the stable lookup). Survival
    state is queried from the same JSON ``trail sync`` surface the
    synthetic path uses.
    """
    paths = driver.paths(box)
    project = paths["project"]
    cli = resolve_cli_argv()

    state_dir, state = read_state_json(driver, box)
    ordered = traces_by_created_at(state)
    trace = ordered[-1] if ordered else {}
    trace_id = trace.get("trace_id")
    if not trace_id:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} artifact restore produced no traces"
        )
    session_id = str(trace.get("session_id") or "")

    head_body = driver.exec(
        box, ["git", "-C", project, "log", "-1", "--format=%B", "HEAD"],
    )
    body = head_body.stdout if head_body.ok else ""
    if _REVERT_BODY_MARKER in body:
        # Scenario's revert landed in the capture: HEAD is the revert.
        revert_commit_sha = driver.exec(
            box, ["git", "-C", project, "rev-parse", "HEAD"],
        ).stdout.strip()
        marker_idx = body.index(_REVERT_BODY_MARKER)
        original_commit_sha = (
            body[marker_idx + len(_REVERT_BODY_MARKER):].strip().split()[0]
            .rstrip(".")
        )
        revert_applied_by = "scenario"
        mature_payload: dict = {}
    else:
        # HEAD is the captured Edit commit; apply the revert here —
        # the same deterministic chain the synthetic path runs.
        original_commit_sha = driver.exec(
            box, ["git", "-C", project, "rev-parse", "HEAD"],
        ).stdout.strip()
        if not original_commit_sha:
            raise CheckpointError(
                f"{_CHECKPOINT_NAME} could not resolve restored HEAD"
            )
        revert_commit_sha, mature_payload = _revert_and_mature(
            driver, box, cli, project, original_commit_sha,
        )
        revert_applied_by = "checkpoint_delta"

    # Re-derive the parent-shaped session audit from the RESTORED
    # world: journey templating ({trace_id} / {step_index} /
    # {commit_sha}) reads box.notes["c_captured_session_audit"], and
    # the parent chain's copy describes the world this restore wiped.
    record = load_trace_record(driver, box, trace)
    step_count = len(record.get("steps") or [])
    edit_step_index = edit_step_index_from_record(record) or EDIT_STEP_INDEX
    box.notes["c_captured_session_audit"] = {
        "session_id": session_id,
        "session_corpus": str(cap_meta.get("scenario_name") or _CAPTURE_NAME),
        "trace_id": trace_id,
        "step_count": step_count,
        "edit_step_index": edit_step_index,
        # The ORIGINAL (pre-revert) landing commit — same semantics as
        # the synthetic parent audit's commit_sha.
        "commit_sha": original_commit_sha,
        "transcript_path": transcript_path_for_session(driver, box, session_id),
        "state_dir": state_dir,
        "capture_metadata": capture_metadata_from_artifact(cap_meta),
    }

    survival_state, patch_id = _resolve_survival_state(
        driver, box, cli, project, trace_id,
    )
    return {
        "revert_commit_sha": revert_commit_sha,
        "original_commit_sha": original_commit_sha,
        "reverted_trace_id": trace_id,
        "reverted_trace_patch_id": patch_id,
        "revert_applied_by": revert_applied_by,
        "mature_anchors_created": int(
            mature_payload.get("anchors_created", 0)
        ),
        "mature_searches_completed": int(
            mature_payload.get("searches_completed", 0)
        ),
        "survival_state_after_revert": survival_state,
        "capture_metadata": capture_metadata_from_artifact(cap_meta),
    }


def _captured_with_revert_delta(driver: Driver, box: Box) -> None:
    # Plan 072 R3 — artifact-preferred, synthetic-fallback.
    cap_meta = restore_from_capture(driver, box, _CAPTURE_NAME)
    if cap_meta is not None:
        box.notes["c_captured_with_revert_audit"] = (
            _derive_revert_audit_from_restored_box(driver, box, cap_meta)
        )
        return

    parent_audit = box.notes.get("c_captured_session_audit") or {}
    original_commit_sha = parent_audit.get("commit_sha")
    trace_id = parent_audit.get("trace_id")
    if not original_commit_sha or not trace_id:
        raise CheckpointError(
            "c-captured-with-revert requires parent audit "
            "(c_captured_session_audit) with commit_sha + trace_id; "
            f"got {parent_audit!r}"
        )

    paths = driver.paths(box)
    project = paths["project"]
    cli = resolve_cli_argv()

    revert_commit_sha, mature_payload = _revert_and_mature(
        driver, box, cli, project, original_commit_sha,
    )

    # Determine the survival state after the revert. Post-#32 (the
    # before-blob revert guard in opentraces.core.trails.anchors) the
    # substrate deterministically lands ``reverted``: the authored
    # lines are gone (preserved == 0) and the revert commit's body
    # names the anchor commit. The audit records the actual state and
    # ``verify_provides`` re-checks it live on every build.
    survival_state, patch_id = _resolve_survival_state(
        driver, box, cli, project, trace_id
    )

    box.notes["c_captured_with_revert_audit"] = {
        "revert_commit_sha": revert_commit_sha,
        "original_commit_sha": original_commit_sha,
        "reverted_trace_id": trace_id,
        "reverted_trace_patch_id": patch_id,
        "revert_applied_by": "checkpoint_delta",
        "mature_anchors_created": int(
            mature_payload.get("anchors_created", 0)
        ),
        "mature_searches_completed": int(
            mature_payload.get("searches_completed", 0)
        ),
        "survival_state_after_revert": survival_state,
        "capture_metadata": synthetic_capture_metadata(),
    }


register(
    Checkpoint(
        name="c-captured-with-revert",
        composed_from="c-captured-real-session",
        delta=_captured_with_revert_delta,
        # cache=False: artifact presence/freshness live outside the
        # checkpoint source hash (same rationale as the codex/pi lanes).
        cache=False,
        description=(
            "c-captured-real-session + ``git revert`` of the captured "
            "commit, followed by a watcher tick + explicit ``trail "
            "mature`` against the revert commit. Exercises the "
            "substrate's revert-detection path so the captured Trace "
            "Patch's survival state matures off ``alive_on_path`` onto "
            "``reverted``. Used by plan 068's survival-state journeys."
        ),
        # Post-#32 (before-blob revert guard, 5cb6ff9f9a0) the delta
        # deterministically lands ``reverted`` — the wider
        # accepted-outcome set the checkpoint used to advertise was
        # both dishonest and unbuildable: verify_provides AND-probes
        # every trust-critical state via ``trail search --survival
        # <state>``, which only supports ``reverted`` (a cold build
        # would always fail the ``lost`` probe).
        provides={
            "captured_traces": 1,
            "survival_states": ["reverted"],
            "branch_commits": 2,
        },
    )
)
