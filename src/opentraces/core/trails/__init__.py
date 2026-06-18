"""Trace Trails substrate and client projections."""
from __future__ import annotations

from .anchors import reconcile_commit_anchors
from .attach import attach_trace_to_commit
from .capture_limitations import (
    CAPTURE_LIMITATIONS,
    assert_known_capture_limitations,
    is_known_capture_limitation,
)
from .event_log import (
    CACHED_SURVIVAL_EVENT_TYPE,
    EVENT_LOG_REF,
    append_event_batch,
    append_survival_cache_events,
    build_survival_cache_index,
    event_log_verification_status,
    event_log_status,
    get_cached_survival,
    import_event_log,
    invalidate_read_events_cache,
    make_survival_cache_draft,
    read_events,
    verify_event_log,
)
from .exact import append_exact_patch_trail
from .explain import explain_commit, explain_file_line, explain_trace_step
from .sync import BatchSyncContext, batch_sync, sync_anchor, sync_patch
from .models import GitObjectID, TrailEvent, TrailEventDraft
from .maturation import MaturationSummary, has_unsearched_recent_patches, mature_trails
from .query import (
    TrailQueryProjection,
    build_trail_query_projection,
    trail_query_summary,
)
from .reconciler import reconcile_watcher_observations
from .search_records import (
    ANCHOR_SEARCH_EVENT_TYPE,
    is_summary_search_event,
    iter_search_records,
    summary_search_touches_trace,
)
from .rebuild import rebuild_projections
from .resources import resolve_resource
from .slices import (
    DEFAULT_TRACE_SLICE_STEP_RADIUS,
    resource_ref_for_file_line,
    resource_ref_for_git_anchor,
    resource_ref_for_trace_patch_trail,
    trace_slice_for_event,
    trace_slice_id_for,
)
from .supersede import supersede_anchors_for_rewrite
from .workspace import (
    export_trace_workspace,
    list_trace_snapshots,
    open_trace_workspace,
    play_trace_timeline,
    snapshot_checkout_packet,
    snapshot_resume_packet,
)
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
    "ANCHOR_SEARCH_EVENT_TYPE",
    "CACHED_SURVIVAL_EVENT_TYPE",
    "CAPTURE_LIMITATIONS",
    "EVENT_LOG_REF",
    "GitObjectID",
    "MaturationSummary",
    "TrailEvent",
    "TrailEventDraft",
    "TrailQueryProjection",
    "SnapshotResult",
    "StepTrailEmissionResult",
    "StepWindowOpenResult",
    "DEFAULT_TRACE_SLICE_STEP_RADIUS",
    "BatchSyncContext",
    "append_event_batch",
    "append_exact_patch_trail",
    "append_step_snapshot",
    "append_survival_cache_events",
    "assert_known_capture_limitations",
    "batch_sync",
    "attach_trace_to_commit",
    "build_survival_cache_index",
    "build_trail_query_projection",
    "get_cached_survival",
    "import_event_log",
    "make_survival_cache_draft",
    "is_known_capture_limitation",
    "close_step_window_with_snapshot",
    "diff_step_snapshots",
    "emit_step_window_events_from_record",
    "event_log_verification_status",
    "event_log_status",
    "explain_commit",
    "explain_file_line",
    "invalidate_read_events_cache",
    "is_summary_search_event",
    "iter_search_records",
    "summary_search_touches_trace",
    "open_step_window",
    "explain_trace_step",
    "sync_anchor",
    "sync_patch",
    "has_unsearched_recent_patches",
    "export_trace_workspace",
    "list_trace_snapshots",
    "mature_trails",
    "open_trace_workspace",
    "play_trace_timeline",
    "read_events",
    "rebuild_projections",
    "reconcile_commit_anchors",
    "reconcile_watcher_observations",
    "resolve_resource",
    "resource_ref_for_file_line",
    "resource_ref_for_git_anchor",
    "resource_ref_for_trace_patch_trail",
    "snapshot_checkout_packet",
    "snapshot_resume_packet",
    "supersede_anchors_for_rewrite",
    "trail_query_summary",
    "trace_slice_for_event",
    "trace_slice_id_for",
    "verify_event_log",
    "write_worktree_tree",
]
