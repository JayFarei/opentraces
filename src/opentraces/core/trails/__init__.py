"""Trace Trails substrate (Phases 1–5)."""
from __future__ import annotations

from .anchors import reconcile_commit_anchors
from .attach import attach_trace_to_commit
from .capture_limitations import (
    CAPTURE_LIMITATIONS,
    assert_known_capture_limitations,
    is_known_capture_limitation,
)
from .event_log import (
    EVENT_LOG_REF,
    append_event_batch,
    event_log_status,
    read_events,
    verify_event_log,
)
from .exact import append_exact_patch_trail
from .explain import explain_commit, explain_file_line, explain_trace_step
from .follow import follow_anchor, follow_patch
from .models import GitObjectID, TrailEvent, TrailEventDraft
from .reconciler import reconcile_watcher_observations
from .rebuild import rebuild_projections
from .supersede import supersede_anchors_for_rewrite
from .snapshots import (
    SnapshotResult,
    StepTrailEmissionResult,
    StepWindowOpenResult,
    append_step_snapshot,
    close_step_window_with_snapshot,
    diff_step_snapshots,
    emit_step_window_events_from_record,
    open_step_window,
    write_worktree_tree,
)

__all__ = [
    "CAPTURE_LIMITATIONS",
    "EVENT_LOG_REF",
    "GitObjectID",
    "TrailEvent",
    "TrailEventDraft",
    "SnapshotResult",
    "StepTrailEmissionResult",
    "StepWindowOpenResult",
    "append_event_batch",
    "append_exact_patch_trail",
    "append_step_snapshot",
    "assert_known_capture_limitations",
    "attach_trace_to_commit",
    "is_known_capture_limitation",
    "close_step_window_with_snapshot",
    "diff_step_snapshots",
    "emit_step_window_events_from_record",
    "event_log_status",
    "explain_commit",
    "explain_file_line",
    "open_step_window",
    "explain_trace_step",
    "follow_anchor",
    "follow_patch",
    "read_events",
    "rebuild_projections",
    "reconcile_commit_anchors",
    "reconcile_watcher_observations",
    "supersede_anchors_for_rewrite",
    "verify_event_log",
    "write_worktree_tree",
]
