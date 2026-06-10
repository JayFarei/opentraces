"""`c-captured-with-revert` — plan 068 M68-1.

Composes onto ``c-captured-real-session``: after the captured
session's Edit has landed as commit ``original_commit_sha`` with a
matured Git Anchor, this delta runs ``git revert HEAD --no-edit`` so
the Trace Patch's survival state matures away from ``alive_on_path``.

The revert produces a new commit on top of the captured one that
restores the file to its pre-Edit content. After the watcher tick +
explicit ``trail mature`` against the revert commit, querying the
patch via ``opentraces trail sync --patch <id>`` should report a
survival state of ``reverted`` (the canonical outcome of an exact
``git revert``) or ``lost`` (when the surviving-line search collapses
to zero). Either is acceptable evidence that the substrate has moved
the patch off ``alive_on_path``; the checkpoint records which one
landed so journeys forked from here can assert against the actual
state.

The parent checkpoint's audit lives in
``box.notes["c_captured_session_audit"]`` and carries the
``commit_sha`` + ``trace_id`` this delta reverts. A new audit is
appended at ``box.notes["c_captured_with_revert_audit"]``.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box, resolve_cli_argv
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    capture_metadata_from_artifact,
    check as _check_helper,
    git as _git_helper,
    restore_from_capture,
    synthetic_capture_metadata,
)

_CAPTURE_NAME = "c-captured-with-revert"


def _check(result, label: str) -> None:
    _check_helper(result, checkpoint=_CAPTURE_NAME, label=label)


def _git(driver: Driver, box: Box, *args: str):
    return _git_helper(driver, box, *args, checkpoint=_CAPTURE_NAME)


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


def _derive_revert_audit_from_restored_box(
    driver: Driver, box: Box, cap_meta: dict,
) -> dict:
    """Re-derive the revert audit after an artifact restore.

    Plan 072 R2 — the artifact captures the post-revert box state, so
    HEAD is the revert commit and HEAD~1 is the original captured
    commit. The reverted trace_id is recovered from the project
    state.json (still the only captured trace; revert doesn't add a
    new TraceRecord). Survival state is queried from the same JSON
    ``trail sync`` surface the synthetic path uses.
    """
    paths = driver.paths(box)
    project = paths["project"]
    cli = resolve_cli_argv()

    head = driver.exec(box, ["git", "-C", project, "rev-parse", "HEAD"])
    parent = driver.exec(box, ["git", "-C", project, "rev-parse", "HEAD~1"])
    revert_commit_sha = head.stdout.strip() if head.ok else ""
    original_commit_sha = parent.stdout.strip() if parent.ok else ""

    # The single captured trace in state.json is the one that was
    # reverted (revert doesn't add a TraceRecord, only a commit).
    from ._captured_helpers import read_state_json

    _state_dir, state = read_state_json(driver, box)
    traces = list((state.get("traces") or {}).values())
    trace_id = traces[0].get("trace_id") if traces else None
    if not trace_id:
        raise CheckpointError(
            "c-captured-with-revert artifact restore produced no traces"
        )

    survival_state, patch_id = _resolve_survival_state(
        driver, box, cli, project, trace_id,
    )
    return {
        "revert_commit_sha": revert_commit_sha,
        "original_commit_sha": original_commit_sha,
        "reverted_trace_id": trace_id,
        "reverted_trace_patch_id": patch_id,
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

    # 1. git revert the captured commit. Using --no-edit keeps the
    #    operation non-interactive; using the explicit sha (rather
    #    than HEAD) makes the delta idempotent if the parent ever
    #    appends additional commits between capture and revert.
    _git(driver, box, "revert", original_commit_sha, "--no-edit")
    revert_commit_sha = _git(driver, box, "rev-parse", "HEAD").stdout.strip()
    if not revert_commit_sha:
        raise CheckpointError("could not resolve revert commit HEAD")

    # 2. Watcher tick — same reconciliation path the parent uses, so
    #    any filesystem mutations the revert produced are folded into
    #    the canonical event log before maturation runs.
    tick = driver.exec(box, [
        *cli, "setup", "watcher", "tick",
        "--project", project, "--json",
    ])
    _check(tick, "setup watcher tick (post-revert)")

    # 3. Explicit ``trail mature`` against the revert commit — same
    #    belt-and-suspenders backstop the parent uses against HEAD.
    #    Maturing against the revert commit is what lets the survival
    #    query see the revert in its history window.
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

    # 4. Determine the survival state after the revert. The substrate
    #    is allowed to land on ``reverted`` (exact revert detected) or
    #    ``lost`` (surviving-line search collapsed to zero); both are
    #    valid post-revert outcomes per docs in
    #    opentraces.core.trails.sync.
    survival_state, patch_id = _resolve_survival_state(
        driver, box, cli, project, trace_id
    )

    box.notes["c_captured_with_revert_audit"] = {
        "revert_commit_sha": revert_commit_sha,
        "original_commit_sha": original_commit_sha,
        "reverted_trace_id": trace_id,
        "reverted_trace_patch_id": patch_id,
        "mature_anchors_created": int(
            mature_payload.get("anchors_created", 0)
            if isinstance(mature_payload, dict) else 0
        ),
        "mature_searches_completed": int(
            mature_payload.get("searches_completed", 0)
            if isinstance(mature_payload, dict) else 0
        ),
        "survival_state_after_revert": survival_state,
        "capture_metadata": synthetic_capture_metadata(),
    }


register(
    Checkpoint(
        name="c-captured-with-revert",
        composed_from="c-captured-real-session",
        delta=_captured_with_revert_delta,
        cache=True,
        description=(
            "c-captured-real-session + ``git revert HEAD --no-edit`` "
            "of the captured commit, followed by a watcher tick + "
            "explicit ``trail mature`` against the revert commit. "
            "Exercises the substrate's revert-detection path so the "
            "captured Trace Patch's survival state matures off "
            "``alive_on_path`` (typically to ``reverted`` or "
            "``lost``). Used by plan 068's survival-state journeys."
        ),
        # Plan 069 R3: the audit accepts ``reverted`` or ``lost`` (and
        # ``alive_transformed`` / ``unknown`` on edge cases) per the
        # delta's own docstring — declaring the full accepted set lets
        # journeys assert against any of them via preconditions.
        provides={
            "captured_traces": 1,
            "survival_states": [
                "reverted",
                "lost",
                "alive_transformed",
                "unknown",
            ],
            "branch_commits": 2,
        },
    )
)
