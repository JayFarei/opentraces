"""Local bucket-shaped trace substrate.

The bucket is the local mirror of the future remote sync substrate. It stores
content-addressed trace evidence, optional raw source artifacts, portable
Trace Trail event exports, and rebuildable projections; versioned datasets
stay outside the bucket as HF-shaped repositories.

This module is the PUBLIC FACADE. The implementation is split across focused
sibling modules to keep each cluster cohesive:

  _bucket_io.py            — Pure I/O utilities (atomic writes, gzip, digests)
  bucket_events.py         — Trail/events-mirror cluster (plan 080 §4)
  bucket_context_store.py  — Context Tree bucket projection (plan 079/080)
  bucket_trace_records.py  — TraceRecord object store and legacy JSONL bridge
  bucket_raw_sources.py    — Raw-source artifact links and snapshots
  bucket_envelope.py       — Per-trace envelope projection and summary rows

Every symbol that existed here before the split is re-exported from this
module so all ~91 existing call sites remain importable from
``opentraces.core.bucket_store`` without change.
"""

from __future__ import annotations

import fcntl
import gzip
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from opentraces.core._time import utc_now_str
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

from pydantic import ValidationError

from opentraces_schema import TraceRecord, load_record_json

from ..security.version import SECURITY_VERSION
from ..security.privacy import (
    bucket_security_state,
    record_privacy_tier,
)
from . import paths
from .bucket_layout import (
    _digest_hex,
    _path_part,
    blobs_v1_context_path,
    blobs_v1_raw_path,
    blobs_v1_root,
    bucket_manifest_path,
    bucket_sync_state_path,
    context_tree_dir,
    context_tree_head_path,
    context_tree_nodes_path,
    context_tree_reconciliation_path,
    contexts_root,
    events_v1_batches_dir,
    events_v1_index_path,
    events_v1_root,
    legacy_trace_records_root,
    raw_sources_root,
    trace_records_root,
    trace_v1_context_path,
    trace_v1_history_dir,
    trace_v1_json_path,
    trace_v1_sources_path,
    trace_v1_trail_path,
    traces_v1_dir,
    traces_v1_root,
    trail_events_root,
)

# ---------------------------------------------------------------------------
# Re-export I/O utilities from the extracted _bucket_io module.
# All internal callers within this file still work; external callers that
# do ``from opentraces.core.bucket_store import _atomic_write_json`` etc.
# continue to resolve.
# ---------------------------------------------------------------------------
from ._bucket_io import (
    _atomic_write_bytes,
    _atomic_write_gzip,
    _atomic_write_json,
    _atomic_write_text,
    _canonical_json,
    _digest_bytes,
    _digest_payload,
    _gzip_deterministic,
    _read_gzip_bytes,
)

# ---------------------------------------------------------------------------
# Re-export trail/events-mirror cluster from bucket_events.
# ---------------------------------------------------------------------------
from .bucket_events import (
    BUCKET_EVENTS_INDEX_SCHEMA,
    TRAIL_EVENT_SNAPSHOT_SCHEMA,
    read_events_mirror_batches,
    read_trail_event_export,
    restore_trail_events_to_repo,
    sync_events_mirror,
    sync_trail_events_from_repo,
    trail_event_snapshot,
)

# ---------------------------------------------------------------------------
# Re-export Context Tree bucket projection cluster from bucket_context_store.
# ---------------------------------------------------------------------------
from .bucket_context_store import (
    CONTEXT_LAYER_BLOB_SCHEMA,
    CONTEXT_TREE_BUCKET_SCHEMA,
    CONTEXT_TREE_REMOTE_SYNC_BLOCKER,
    CONTEXT_TREE_SNAPSHOT_SCHEMA,
    _BRANCH_TYPE_ORDINAL,
    _build_context_head,
    _build_context_layer_blob,
    _context_blob_scope,
    _head_payload_to_row,
    _iter_context_tree_head_payloads,
    compute_context_tree_status,
    context_tree_snapshot,
    iter_context_tree_traces,
    project_context_tree_to_bucket,
    read_context_tree_head,
    verify_context_tree_layer_refs,
)


# Bucket data models + schema-version constants live in the dependency-free base
# module ``bucket_models``; re-exported here so ``from ...bucket_store import
# BucketTraceRecord`` / ``BUCKET_MANIFEST_SCHEMA`` (and the rest) keep working.
# Imported eagerly: ``bucket_models`` imports nothing from this package, so there
# is no cycle, and these names are used throughout the manifest code below.
from .bucket_models import (
    BUCKET_MANIFEST_SCHEMA,
    BUCKET_PER_TRACE_SCHEMA,
    BUCKET_REMOTE_SCHEMA,
    BucketLayoutError,
    BucketSyncSummary,
    BucketTraceRecord,
    BucketTraceRecordPointer,
    RAW_SOURCE_SCHEMA,
    RAW_SOURCE_SNAPSHOT_SCHEMA,
    TRACE_RECORD_BUCKET_SCHEMA,
    TRACE_RECORD_POINTER_SCHEMA,
    TRACE_RECORD_PROJECT_STAGING,
    TRAIL_EVENT_EXPORT_SCHEMA,
)

# Per-trace I/O lives in the lower-layer ``bucket_envelope`` module; imported
# here (manifest/sync code below uses these) and re-exported for external
# callers. One-way dep: bucket_envelope never imports bucket_store -> no cycle.
from .bucket_envelope import (
    _context_events_for_trace_readonly,
    _events_for_export_loop,
    _events_for_trace_from_iter,
    _is_legacy_read_in_place_mirror,
    _iter_opted_in_projects,
    _normalized_record,
    _per_trace_v2_summary,
    _project_store_bucket_record,
    _read_jsonl_trace_records,
    _resolve_record_envelope,
    _resolve_trace_record_pointer,
    _trace_ids_for_project,
    _write_per_trace_envelope,
    canonical_anchor_maps,
    iter_trace_record_objects,
    iter_trace_record_pointers,
    iter_traces_v2,
    project_per_trace_exports,
    project_store_record_from_path,
    raw_source_snapshot,
    read_bucket_record_for_trace,
    read_trace_record_object,
    trace_record_object_path,
    trace_record_path,
    trace_record_snapshot,
    trace_v2_summary_by_id,
    write_raw_source_artifact,
    write_trace_record,
)


def read_bucket_sync_state() -> dict[str, Any]:
    path = bucket_sync_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}


def write_bucket_sync_state(
    *,
    provider: str,
    target: str,
    digest: str | None,
    direction: str,
    remote_digest: str | None = None,
) -> dict[str, Any]:
    state = {
        "schema_version": "opentraces.bucket.sync_state.v1",
        "provider": provider,
        "target": target,
        "last_sync_digest": digest,
        "last_remote_digest": remote_digest or digest,
        "last_direction": direction,
        "synced_at": utc_now_str(),
    }
    _atomic_write_json(bucket_sync_state_path(), state)
    return state


def classify_bucket_remote_state(
    *,
    provider: str,
    target: str,
    local_digest: str | None,
    remote_digest: str | None,
) -> dict[str, Any]:
    """Classify local/remote relation using the last successful sync point."""

    if remote_digest is None:
        return {"state": "missing", "last_sync_digest": None}
    if remote_digest == local_digest:
        return {"state": "current", "last_sync_digest": remote_digest}
    sync_state = read_bucket_sync_state()
    last = (
        sync_state.get("last_sync_digest")
        if sync_state.get("provider") == provider and sync_state.get("target") == target
        else None
    )
    if not last:
        return {"state": "different", "last_sync_digest": None}
    local_at_last = local_digest == last
    remote_at_last = remote_digest == last
    if local_at_last and not remote_at_last:
        state = "remote_ahead"
    elif remote_at_last and not local_at_last:
        state = "local_ahead"
    elif not local_at_last and not remote_at_last:
        state = "diverged"
    else:
        state = "different"
    return {"state": state, "last_sync_digest": last}


def _load_manifest(path: Path | None = None) -> dict[str, Any] | None:
    """Read ``bucket/manifest.json`` and enforce the v2 schema (Resolution E).

    Returns ``None`` when the manifest does not exist. Raises
    :class:`BucketLayoutError` on schema mismatch — there is no v1 reader
    on this branch.
    """

    manifest_path = path or bucket_manifest_path()
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BucketLayoutError(
            f"bucket manifest is unreadable: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise BucketLayoutError(
            f"bucket manifest is not an object: {manifest_path}"
        )
    found = payload.get("schema_version")
    if found != BUCKET_MANIFEST_SCHEMA:
        raise BucketLayoutError(
            f"bucket manifest schema {found!r} incompatible with local "
            f"{BUCKET_MANIFEST_SCHEMA!r}; run 'opentraces setup bucket --migrate'"
        )
    return payload


DEFAULT_BUCKET_MANIFEST_MAX_BYTES = 16 * 1024 * 1024


def bucket_manifest_max_bytes() -> int:
    """Byte cap for cheap persisted-manifest reads (shared by doctor + gate).

    Reads ``OPENTRACES_DOCTOR_BUCKET_MANIFEST_MAX_BYTES`` (the same env knob
    doctor uses) so doctor's bucket panel and ``bucket remote status``'s
    security gate degrade on an oversized manifest at the SAME threshold.
    """

    raw = os.environ.get("OPENTRACES_DOCTOR_BUCKET_MANIFEST_MAX_BYTES")
    if raw:
        try:
            return max(1024, int(raw))
        except ValueError:
            return DEFAULT_BUCKET_MANIFEST_MAX_BYTES
    return DEFAULT_BUCKET_MANIFEST_MAX_BYTES


def read_persisted_manifest_capped(
    max_bytes: int | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Read-only, byte-capped read of the persisted ``bucket/manifest.json``.

    Returns ``(state, manifest)`` where ``state`` is one of ``"ok"`` /
    ``"absent"`` / ``"too_large"`` / ``"error"``. NEVER scans the bucket and
    NEVER writes ``manifest.json`` — it is the cheap, side-effect-free read that
    both doctor's bucket panel and the ``bucket remote status`` security gate use
    so a huge manifest degrades identically (matching doctor's ``too-large``
    behaviour) instead of stalling / blowing memory on ``read_text``.
    """

    manifest_path = bucket_manifest_path()
    if not manifest_path.exists():
        return ("absent", None)
    cap = bucket_manifest_max_bytes() if max_bytes is None else max_bytes
    try:
        if manifest_path.stat().st_size > cap:
            return ("too_large", None)
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return ("error", None)
    if not isinstance(payload, dict):
        return ("error", None)
    if payload.get("schema_version") != BUCKET_MANIFEST_SCHEMA:
        return ("error", None)
    return ("ok", payload)


def bucket_manifest(
    *,
    write: bool = False,
    heal: bool = True,
    include_objects: bool = False,
    progress=None,
) -> dict[str, Any]:
    """Return the local bucket manifest used by future remote sync.

    The manifest is intentionally transport-neutral: it summarizes the
    canonical bucket substrate and the local projections that remote sync must
    treat as derived state.

    Issue #55 — ``heal`` controls the #31 read-side reconcile's DISK side
    effects. Default ``heal=True`` keeps the materializing behavior for every
    internal caller (``bucket repair``, ``bucket_remote``, ``doctor``, ...).
    The two CLI read verbs pass ``heal=False`` (and ``write=False``) so they
    are byte-level side-effect-free: the reconcile loop still appends each
    orphan's record-derived summary row IN MEMORY (so counts + ``bucket_digest``
    stay identical to the healed state), but writes NO per-trace envelope and
    NO ``manifest.json``. The summary is record-derived in both branches, so
    the digest invariant holds (the read-only digest equals the digest a
    later ``--heal`` / ``bucket repair`` persists to disk).

    #87 — ``progress`` (a shared ``ProgressReporter`` or ``None``) labels each
    O(N) scan phase so a long read on a large bucket is heartbeat-observable
    rather than a silent multi-minute hang. ``None`` → a no-op ``NullProgress``,
    keeping every non-CLI caller byte-identical.
    """

    if progress is None:
        from .progress import NullProgress

        progress = NullProgress()

    # Plan 087 — capture the manifest read-model dirty token BEFORE the O(N)
    # reconcile reads the envelopes. A full ``write=True`` sweep refreshes the
    # rows from those envelopes, so it may clear the marker afterward — but only
    # if no out-of-band writer re-marked DURING the sweep (token-unchanged), so a
    # concurrent security re-scan is never lost.
    _dirty_token_at_start: str | None = None
    if write:
        try:
            from .bucket_manifest_state import current_bucket_manifest_dirty_token

            _dirty_token_at_start = current_bucket_manifest_dirty_token()
        except Exception:
            _dirty_token_at_start = None

    progress.stage("scan_trace_records")
    trace_snapshot = trace_record_snapshot(include_objects=include_objects)
    objects = trace_snapshot.get("objects") or []
    # Issue #31 — iterate the TraceRecord object store ONCE; reuse the parsed
    # ``BucketTraceRecord`` objects for both the (compat) objects block and the
    # read-side self-heal of the v2 ``traces[]`` rows below.
    record_objects = iter_trace_record_objects()
    if not include_objects:
        objects = [
            {
                "privacy_tier": obj.envelope.get("security", {}).get("privacy_tier"),
                "security_version": obj.envelope.get("security", {}).get("security_version"),
                "syncable": obj.envelope.get("security", {}).get("syncable"),
                "security_stale": obj.envelope.get("security", {}).get("stale"),
                "written_at": obj.envelope.get("written_at"),
            }
            for obj in record_objects
        ]
    syncable_count = sum(1 for obj in objects if obj.get("syncable") is True)
    stale_security_count = sum(1 for obj in objects if obj.get("security_stale") is True)
    privacy_off_count = sum(1 for obj in objects if obj.get("privacy_tier") == "off")
    unfiltered_count = sum(1 for obj in objects if obj.get("syncable") is False)
    last_trace_record_write_at = max(
        (str(obj.get("written_at") or "") for obj in objects),
        default="",
    ) or None

    progress.stage("scan_raw_sources")
    raw_snapshot = raw_source_snapshot(include_objects=include_objects)
    progress.stage("scan_trail_events")
    trail_event_exports = trail_event_snapshot(include_objects=include_objects)
    progress.stage("scan_context_trees")
    context_trees_snapshot = context_tree_snapshot(include_objects=include_objects)

    progress.stage("trail_freshness")
    trail_freshness: list[dict[str, Any]] = []
    try:
        from .trace_index import default_index_path, trail_freshness_warnings

        trail_freshness = trail_freshness_warnings(
            index_path=default_index_path(),
            include_current=True,
        )
    except Exception:
        trail_freshness = []
    stale_trail_count = sum(
        1 for item in trail_freshness if item.get("severity") == "warning"
    )
    last_trail_projection_sync_at = max(
        (str(item.get("last_synced_at") or "") for item in trail_freshness),
        default="",
    ) or None

    # Plan 080 §4 — new ``traces[]`` block + ``events_v1`` index. Sorted by
    # (project_slug, trace_id) for deterministic digests.
    # Plan 087 — reuse the already-parsed object envelopes (read once at
    # ``record_objects`` above) so each row's ``status`` block is sourced without
    # a second per-trace object-store read on this O(N) sweep.
    _record_envelopes = {
        (obj.project_slug, obj.trace_id): obj.envelope for obj in record_objects
    }
    # #172 GAP 1 — build the canonical per-trace anchor maps ONCE (one scoped
    # ``git_anchor_created`` read per backing repo, NOT per trace) so every row's
    # ``anchored_count`` single-sources from the canonical events rather than the
    # possibly-phantom on-disk trace.json. Bounded to the slugs actually present
    # in the object store. Building it here (not per-row) avoids the
    # O(traces x full-log-walk) trap.
    _present_slugs = {obj.project_slug for obj in record_objects}
    try:
        _, _distinct_anchored_by_trace, _resolved_slugs = canonical_anchor_maps(
            _present_slugs
        )
    except Exception:
        _distinct_anchored_by_trace, _resolved_slugs = {}, set()
    _anchor_maps = (_distinct_anchored_by_trace, _resolved_slugs)
    traces_v2_rows = iter_traces_v2(
        record_envelopes=_record_envelopes, anchor_maps=_anchor_maps
    )

    # Issue #31 — read-side reconcile. On a restored / cross-machine world the
    # TraceRecord object store can hold traces that have no per-trace v2
    # envelope yet (ingest used to write the envelope only on the live-project
    # hot path, and ``bucket repair`` only walked live opted-in projects). The
    # manifest-only readers (``bucket manifest`` / ``ctx list``) then reported 0
    # traces while ``bucket status`` / the index / ``ctx tree`` saw them. Heal:
    # for every (project_slug, trace_id) present in the object store but missing
    # from ``traces[]``, materialize the per-trace envelope from canonical data
    # (live event log if the project resolves, else the bucket events mirror,
    # else a degraded envelope from the TraceRecord alone) and add its summary
    # row. Materializing on disk in both write modes keeps ``bucket verify``
    # check 3 green and keeps ``bucket repair``'s write=False candidate-digest
    # comparison consistent. Atomic same-bytes writers preserve idempotency.
    progress.stage("reconcile_traces")
    _existing_pairs = {(row["project_slug"], row["trace_id"]) for row in traces_v2_rows}
    _project_paths: dict[str, Path] | None = None
    for obj in record_objects:
        pair = (obj.project_slug, obj.trace_id)
        if pair in _existing_pairs:
            continue
        # Plan 085 S5 — read-in-place. Legacy-store mirrors (no capture-time
        # raw-source link) are a query substrate, not bucket content; never
        # auto-adopt them. Record-only staged traces (PR #63) carry the link
        # and self-heal here — their deferred projection.
        if _is_legacy_read_in_place_mirror(*pair):
            continue
        if not heal:
            # Issue #55 read-only path: report the orphan in-memory WITHOUT
            # touching disk. ``assume_envelope_present`` makes the summary
            # reflect the POST-heal disk state (heal always writes all three
            # framed companions, so has_trail/has_context/has_sources are True;
            # node_count is counted from canonical context events read-only).
            # Keeps counts + bucket_digest byte-identical to the healed state
            # so the digest invariant (BKT-3/BKT-6) holds.
            traces_v2_rows.append(
                _per_trace_v2_summary(
                    obj.project_slug,
                    obj.trace_id,
                    obj.record,
                    assume_envelope_present=True,
                    record_envelope=obj.envelope,
                    distinct_anchored=_distinct_anchored_by_trace.get(obj.trace_id),
                    anchors_resolved=obj.project_slug in _resolved_slugs,
                )
            )
            _existing_pairs.add(pair)
            continue
        try:
            if _project_paths is None:
                _project_paths = {
                    slug: path for path, slug in _iter_opted_in_projects()
                }
            repo = _project_paths.get(obj.project_slug)
            project_per_trace_exports(
                repo,
                project_slug=obj.project_slug,
                trace_id=obj.trace_id,
                record=obj.record,
            )
            traces_v2_rows.append(
                _per_trace_v2_summary(
                    obj.project_slug,
                    obj.trace_id,
                    obj.record,
                    record_envelope=obj.envelope,
                    distinct_anchored=_distinct_anchored_by_trace.get(obj.trace_id),
                    anchors_resolved=obj.project_slug in _resolved_slugs,
                )
            )
            _existing_pairs.add(pair)
        except Exception:
            # Degraded fallback: write an envelope with empty event companions
            # straight from the TraceRecord so the trace is never dropped.
            try:
                _write_per_trace_envelope(
                    obj.project_slug, obj.trace_id, obj.record, [], []
                )
                traces_v2_rows.append(
                    _per_trace_v2_summary(
                        obj.project_slug,
                        obj.trace_id,
                        obj.record,
                        record_envelope=obj.envelope,
                        distinct_anchored=_distinct_anchored_by_trace.get(
                            obj.trace_id
                        ),
                        anchors_resolved=obj.project_slug in _resolved_slugs,
                    )
                )
                _existing_pairs.add(pair)
            except Exception:
                pass
    traces_v2_rows.sort(key=lambda item: (item["project_slug"], item["trace_id"]))

    # ``events_v1`` mirror summary (single-repo today; multi-repo when ingest
    # writes multiple mirror trees).
    events_v1_index: dict[str, Any] = {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "batch_count": 0,
        "last_batch_id": None,
        "latest_event_sequence": 0,
    }
    idx_path = events_v1_index_path()
    if idx_path.exists():
        try:
            raw_idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if (
                isinstance(raw_idx, dict)
                and raw_idx.get("schema_version") == BUCKET_EVENTS_INDEX_SCHEMA
            ):
                events_v1_index = {
                    "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
                    "batch_count": int(raw_idx.get("batch_count") or 0),
                    "last_batch_id": raw_idx.get("last_batch_id"),
                    "latest_event_sequence": int(
                        raw_idx.get("latest_event_sequence") or 0
                    ),
                }
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # Determine ``bucket_root`` — the project slug if exactly one project's
    # traces are present; otherwise an empty string (multi-project bucket).
    project_slugs = sorted({row["project_slug"] for row in traces_v2_rows})
    bucket_root_slug = project_slugs[0] if len(project_slugs) == 1 else ""

    manifest: dict[str, Any] = {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "bucket_root": bucket_root_slug,
        "root": str(paths.bucket_dir()),
        "generated_at": utc_now_str(),
        "updated_at": utc_now_str(),
        "security_version": SECURITY_VERSION,
        # Plan 080 v2 fields (canonical):
        "traces": traces_v2_rows,
        "events_v1": events_v1_index,
        # Compat fields (v1 manifest substructure, retained for current callers):
        "trace_records": {
            "snapshot": trace_snapshot,
            "object_count": trace_snapshot.get("object_count", 0),
            "syncable_count": syncable_count,
            "unfiltered_count": unfiltered_count,
            "privacy_off_count": privacy_off_count,
            "security_stale_count": stale_security_count,
            "last_write_at": last_trace_record_write_at,
        },
        "trail": {
            "freshness": trail_freshness,
            "stale_count": stale_trail_count,
            "last_projection_sync_at": last_trail_projection_sync_at,
        },
        "sync": {
            "eligible": unfiltered_count == 0 and stale_security_count == 0,
            "blocked_reasons": _bucket_sync_blockers(
                unfiltered_count=unfiltered_count,
                stale_security_count=stale_security_count,
            ),
        },
        "raw_sources": raw_snapshot,
        "trail_events": trail_event_exports,
        "context_trees": context_trees_snapshot,
    }

    # Plan 080 Resolution H — ``bucket_digest`` is deterministic across
    # machines: excludes ``generated_at``/``updated_at``; sorts traces[] by
    # (project_slug, trace_id).
    # Digest material covers exactly the SYNCED bucket unit (plan 080
    # Resolution H: deterministic across machines):
    # * machine-local absolute paths ("root", "repo_path") are excluded —
    #   the same bucket restored at a different path must hash identically
    #   (issue #25 PR-B finding #2, confirmed live by the live-hf-* journeys:
    #   pull on another machine was permanently "remote_ahead").
    # * ``context_trees`` is excluded entirely: plan-079 R8 hardcodes
    #   ``remote_sync.eligible = False`` for every context-tree head, so push
    #   deliberately skips that substrate — counting it in the digest made a
    #   pulled bucket unable to ever reproduce the pushed digest. If R8 is
    #   ever lifted (or ``--unsafe-push`` grows a synced-context contract),
    #   the eligible subset must re-enter this material.
    bucket_digest_material = _machine_neutral_digest_view(
        {
            "schema_version": manifest["schema_version"],
            "bucket_root": manifest["bucket_root"],
            "security_version": manifest["security_version"],
            "traces": _digest_safe_trace_rows(
                sorted(
                    traces_v2_rows, key=lambda r: (r["project_slug"], r["trace_id"])
                )
            ),
            "events_v1": manifest["events_v1"],
            "trace_records": manifest["trace_records"],
            "trail": manifest["trail"],
            "raw_sources": manifest["raw_sources"],
            "trail_events": manifest["trail_events"],
            "sync": manifest["sync"],
        }
    )
    bucket_digest = _digest_payload(bucket_digest_material)
    manifest["bucket_digest"] = bucket_digest
    # Retain ``digest`` as a compat alias for callers that still consume the
    # v1 field name (bucket_remote, datasets).
    manifest["digest"] = bucket_digest
    if write:
        # Write-only-on-change discipline (matches bucket_repair §5): persist
        # manifest.json only when the content differs from the on-disk
        # manifest. ``_atomic_write_json``'s same-bytes skip is not enough
        # here because ``generated_at``/``updated_at`` advance every call —
        # so byte-comparison would ALWAYS rewrite. The compare is the FULL
        # manifest minus only those volatile timestamps (NOT the Resolution-H
        # digest alone): the digest excludes machine-local keys and any field
        # outside its material, so a digest-only skip could leave a
        # stale-SHAPED manifest.json on disk after a code upgrade — bytes
        # bucket_remote would then push. Content-compare keeps idempotent
        # re-projections byte-stable (``bucket manifest --heal`` twice =
        # no-op; issue #55) while any real shape/content change still writes.
        manifest_path = bucket_manifest_path()
        existing_view: Any = None
        if manifest_path.exists():
            try:
                existing_doc = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                if isinstance(existing_doc, dict):
                    existing_view = _manifest_change_view(existing_doc)
            except (OSError, ValueError, json.JSONDecodeError):
                existing_view = None
        if existing_view is None or existing_view != _manifest_change_view(
            manifest
        ):
            _atomic_write_json(manifest_path, manifest)
        # Plan 087 — the persisted rows now reflect a full envelope reconcile, so
        # the read-model is fresh. Clear the dirty marker iff no out-of-band
        # writer re-marked while we scanned (token-safe; a concurrent re-mark
        # keeps the marker so the next ``bucket status`` still serves stale).
        try:
            from .bucket_manifest_state import (
                clear_bucket_manifest_dirty_if_unchanged,
            )

            clear_bucket_manifest_dirty_if_unchanged(_dirty_token_at_start)
        except Exception:
            pass
    return manifest


_MANIFEST_VOLATILE_KEYS = frozenset({"generated_at", "updated_at"})


def _manifest_change_view(doc: dict[str, Any]) -> dict[str, Any]:
    """Manifest content minus the per-call volatile timestamps.

    The write-only-on-change compare in :func:`bucket_manifest` keys on this
    view: two manifests that differ only in ``generated_at``/``updated_at``
    are the SAME content (skip the rewrite, keep bytes stable); any other
    delta — including shape changes invisible to ``bucket_digest`` — writes.
    """

    return {k: v for k, v in doc.items() if k not in _MANIFEST_VOLATILE_KEYS}


def _minimal_manifest_doc() -> dict[str, Any]:
    """A schema-valid empty ``opentraces.bucket.manifest.v2`` document.

    Issue #54 — the seed the bounded capture-time upsert writes when no
    ``manifest.json`` exists yet. It carries only the canonical v2 skeleton
    (``schema_version`` + an empty ``traces[]`` + an empty ``events_v1``
    index) plus empty compat blocks every ``.get()``-guarded consumer
    (``bucket_remote``, ``datasets``) already tolerates. A later full
    :func:`bucket_manifest` regeneration (run by ``bucket status`` / ``bucket
    remote push`` / ``bucket repair``) overwrites it with the populated
    blocks; the upsert never sweeps the object store to fill them in (that is
    the #44 latency class this seed exists to avoid).
    """

    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "bucket_root": "",
        "root": str(paths.bucket_dir()),
        "generated_at": utc_now_str(),
        "updated_at": utc_now_str(),
        "security_version": SECURITY_VERSION,
        "traces": [],
        "events_v1": {
            "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
            "batch_count": 0,
            "last_batch_id": None,
            "latest_event_sequence": 0,
        },
        "trace_records": {},
        "trail": {},
        "sync": {},
        "raw_sources": {},
        "trail_events": {},
        "context_trees": {},
    }


@contextmanager
def _manifest_upsert_lock() -> Iterator[None]:
    """Bucket-level exclusive flock serializing the manifest upsert.

    Issue #54 adversary finding — ingest's flock is keyed per ``session_id``,
    so two concurrent ingests of DIFFERENT sessions could both read the same
    ``manifest.json``, each append its own row, and the later atomic replace
    would drop the earlier trace's row (lost update; the exact invisibility
    class the upsert exists to close). Blocking is correct here because the
    hold time is one read + one write of a small JSON doc (mirrors
    ``ingest._FileLock``'s rationale).
    """

    lock_path = bucket_manifest_path().with_name("manifest.json.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def upsert_manifest_trace_row(
    repo: Path | None,
    *,
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None = None,
) -> dict[str, Any] | None:
    """Insert/replace one trace's ``traces[]`` row in ``bucket/manifest.json``.

    Issue #54 — capture materializes the manifest row so the manifest-only
    readers (``ctx list`` / ``ctx info``) see a freshly captured trace
    immediately, WITHOUT a ``bucket manifest`` / ``bucket repair`` heal verb.
    Called from :func:`opentraces.core.ingest.ingest_one_session` right after
    :func:`project_per_trace_exports`, inside the ``if not trace_record_only``
    guard.

    Bounded to O(one trace): the summary row is computed via
    :func:`_per_trace_v2_summary` from the per-trace envelope this ingest just
    wrote (byte-identical to the row a full ``bucket_manifest`` regeneration
    produces). It NEVER calls :func:`iter_trace_record_objects` /
    :func:`trace_record_snapshot` or regenerates the whole manifest (the #44
    post-commit latency class). When ``manifest.json`` is absent a minimal
    schema-valid v2 doc is seeded; the row is upserted (replace if the
    ``(project_slug, trace_id)`` pair already exists, else append) and
    ``traces[]`` re-sorted; ``bucket_root`` + ``bucket_digest`` are recomputed
    over the in-memory rows; the doc is written atomically with the same
    write-only-on-change discipline as :func:`bucket_manifest` (compare the
    full content minus volatile timestamps so idempotent re-captures stay
    byte-stable). Returns the upserted row (``None`` only if the row could not
    be computed — best-effort, never fatal to capture).

    A subsequent full ``bucket_manifest(write=True)`` regeneration overwrites
    the minimal compat blocks with the swept-store content; the per-trace row
    is unchanged because both paths derive it from the same envelope.
    """

    # Plan 087 — source the row's digest-excluded ``status`` block from the
    # authoritative trace-record OBJECT envelope (the same source every other
    # row-producing path uses), so an upsert-maintained row matches a full
    # sweep and re-heals stay idempotent.
    #
    # #172 GAP 1 — single-source ``anchored_count`` from the canonical
    # ``git_anchor_created`` events for this ONE trace (bounded to its own slug,
    # one scoped read) so the upsert-maintained row matches the full-sweep row
    # byte-for-byte. At capture time anchors are pre-commit (0 == 0); after
    # post-commit re-projection the events exist and this catches the row up. When
    # the project is not resolvable, fall back to record-derived (== today).
    try:
        _, _distinct_by_trace, _resolved = canonical_anchor_maps({project_slug})
    except Exception:
        _distinct_by_trace, _resolved = {}, set()
    row = _per_trace_v2_summary(
        project_slug,
        trace_id,
        record,
        record_envelope=_resolve_record_envelope(project_slug, trace_id, record),
        distinct_anchored=_distinct_by_trace.get(trace_id),
        anchors_resolved=project_slug in _resolved,
    )

    manifest_path = bucket_manifest_path()
    with _manifest_upsert_lock():
        doc: dict[str, Any] | None = None
        existing_view: Any = None
        if manifest_path.exists():
            try:
                raw_text = manifest_path.read_text(encoding="utf-8")
                loaded = json.loads(raw_text)
                if isinstance(loaded, dict):
                    # Independent parse: ``doc`` is mutated below, so the
                    # change-view compare must hold its own object graph.
                    existing_view = _manifest_change_view(json.loads(raw_text))
                    if loaded.get("schema_version") == BUCKET_MANIFEST_SCHEMA:
                        doc = loaded
            except (OSError, ValueError, json.JSONDecodeError):
                doc = None
                existing_view = None
        if doc is None:
            doc = _minimal_manifest_doc()

        traces = [r for r in (doc.get("traces") or []) if isinstance(r, dict)]
        traces = [
            r
            for r in traces
            if (r.get("project_slug"), r.get("trace_id"))
            != (project_slug, trace_id)
        ]
        traces.append(row)
        traces.sort(key=lambda item: (item["project_slug"], item["trace_id"]))
        doc["traces"] = traces

        project_slugs = sorted({r["project_slug"] for r in traces})
        doc["bucket_root"] = project_slugs[0] if len(project_slugs) == 1 else ""
        doc["updated_at"] = utc_now_str()

        # The digest covers the doc AS WRITTEN (self-consistent), not the
        # digest a full swept regeneration would produce — the compat blocks
        # stay as-loaded until heal. That transient delta is consumer-safe:
        # every top-level-digest consumer regenerates first
        # (``bucket_remote`` calls ``bucket_manifest(write=True)`` before
        # every ``.get("digest")``; ``datasets`` recomputes ``write=False``);
        # ``ctx info``'s ``digest`` is the per-trace ROW digest, which is
        # byte-identical across both paths.
        bucket_digest_material = _machine_neutral_digest_view(
            {
                "schema_version": doc["schema_version"],
                "bucket_root": doc["bucket_root"],
                "security_version": doc.get("security_version", SECURITY_VERSION),
                "traces": _digest_safe_trace_rows(
                    sorted(traces, key=lambda r: (r["project_slug"], r["trace_id"]))
                ),
                "events_v1": doc.get("events_v1", {}),
                "trace_records": doc.get("trace_records", {}),
                "trail": doc.get("trail", {}),
                "raw_sources": doc.get("raw_sources", {}),
                "trail_events": doc.get("trail_events", {}),
                "sync": doc.get("sync", {}),
            }
        )
        bucket_digest = _digest_payload(bucket_digest_material)
        doc["bucket_digest"] = bucket_digest
        doc["digest"] = bucket_digest

        if existing_view is None or existing_view != _manifest_change_view(doc):
            _atomic_write_json(manifest_path, doc)
    return row


def _digest_safe_trace_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip the plan-087 ``status`` accelerator from each ``traces[]`` row.

    The per-row ``status`` block (syncable/privacy_off/security_stale/written_at)
    is a LOCAL read-model accelerator for ``bucket status`` — it is NOT
    cross-machine sync-protocol material. Excluding it here keeps
    ``bucket_digest`` byte-identical to the pre-plan-087 digest (guarded by
    ``test_cross_machine_bucket_digest_byte_identical``), so a bucket pulled on
    another machine still reproduces the pushed digest. The row's own ``digest``
    field is already status-free (``status`` is not in ``digest_material``).
    """

    return [{k: v for k, v in row.items() if k != "status"} for row in rows]


_MACHINE_LOCAL_DIGEST_KEYS = frozenset({"root", "repo_path"})


def _machine_neutral_digest_view(value: Any) -> Any:
    """Strip machine-local path keys from digest material, recursively.

    The manifest keeps the absolute paths for display; only the digest
    roll-up must be invariant under bucket relocation.
    """

    if isinstance(value, dict):
        return {
            k: _machine_neutral_digest_view(v)
            for k, v in value.items()
            if k not in _MACHINE_LOCAL_DIGEST_KEYS
        }
    if isinstance(value, list):
        return [_machine_neutral_digest_view(v) for v in value]
    return value


def _bounded_status_view(manifest: dict[str, Any]) -> dict[str, Any]:
    """Project the bucket manifest into a bounded, summary-only status view.

    Issue #97 — ``bucket status --json`` must answer sync/security/event/context
    counts WITHOUT dumping every trace. The full v2 ``traces[]`` enumeration (the
    dominant O(N) size term), the per-record ``trace_records.snapshot`` block, and
    the per-projection ``trail.freshness`` array are stripped here; the full
    per-trace listing stays on ``bucket manifest --json`` (the unchanged frozen
    ``opentraces.bucket.manifest.v2`` envelope). This is a non-mutating projection
    over a shallow copy so the manifest the caller computed is never aliased.

    NOTE (honest size-vs-latency split): this bounds OUTPUT size only. The
    manifest is still computed by the O(N) object-store scan, so ``bucket status``
    remains O(N) in WALL TIME — the latency cure (persisting scalar counters) is a
    tracked follow-up, out of this change's locked scope.
    """

    view = dict(manifest)
    # Drop the full per-trace enumeration; keep a scalar count. (Agrees with
    # ``trace_records.object_count`` by construction — one v2 row per record.)
    view["trace_count"] = len(manifest.get("traces") or [])
    view.pop("traces", None)
    view["traces_omitted"] = True  # self-documenting: full list via `bucket manifest --json`

    # Drop the non-scalar ``snapshot`` block; keep the sibling scalar counters.
    trace_records = manifest.get("trace_records")
    if isinstance(trace_records, dict):
        tr = dict(trace_records)
        tr.pop("snapshot", None)
        view["trace_records"] = tr

    # Drop the per-projection ``freshness`` array; keep ``stale_count`` +
    # ``last_projection_sync_at``.
    trail = manifest.get("trail")
    if isinstance(trail, dict):
        tr = dict(trail)
        tr.pop("freshness", None)
        view["trail"] = tr

    return view


def bucket_status(
    *, write_manifest: bool = True, heal: bool = True, progress=None
) -> dict[str, Any]:
    # Issue #55 — ``heal`` mirrors :func:`bucket_manifest`. Internal callers
    # keep the materializing default; the CLI ``bucket status`` read verb passes
    # ``write_manifest=False, heal=False`` for a byte-level side-effect-free read.
    # #87 — ``progress`` (a shared ``ProgressReporter`` or ``None``) is threaded
    # into the O(N) object-store scan so a long read is observable, not silent.
    manifest = bucket_manifest(
        write=write_manifest, heal=heal, include_objects=False, progress=progress
    )
    # Issue #97 — status is summary-only: bound the payload (drop traces[],
    # trace_records.snapshot, trail.freshness). Full listing via `bucket manifest`.
    return {
        "status": "ok",
        "bucket": _bounded_status_view(manifest),
        "config": _bucket_config_payload(),
    }


def _aggregate_status_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate the per-row ``status`` accelerator into bucket-status counts.

    Plan 087 — O(rows-in-memory), no per-trace file I/O. ``unknown_count`` is the
    number of rows whose ``status`` was never populated (a pre-087 manifest, or a
    row written before its object envelope existed); callers fall back to the
    persisted scalar block when EVERY row is unknown.
    """

    syncable = unfiltered = security_stale = privacy_off = unknown = 0
    last_write_at = ""
    for row in rows:
        status = row.get("status") if isinstance(row, dict) else None
        if not isinstance(status, dict) or not status.get("known"):
            unknown += 1
            continue
        if status.get("syncable") is True:
            syncable += 1
        elif status.get("syncable") is False:
            unfiltered += 1
        if status.get("security_stale"):
            security_stale += 1
        if status.get("privacy_off"):
            privacy_off += 1
        written = status.get("written_at")
        if written and str(written) > last_write_at:
            last_write_at = str(written)
    return {
        "syncable_count": syncable,
        "unfiltered_count": unfiltered,
        "security_stale_count": security_stale,
        "privacy_off_count": privacy_off,
        "unknown_count": unknown,
        "last_write_at": last_write_at or None,
    }


def _bucket_status_from_persisted(manifest: dict[str, Any]) -> dict[str, Any]:
    """Build the bucket-status payload from a persisted manifest (plan 087).

    O(1) in the bucket size: reads sync/security counts from the per-row
    ``status`` accelerator (falling back to the persisted ``trace_records``
    scalar block for a pre-087 manifest whose rows carry no status), and stamps
    a ``freshness`` block from three cheap signals — the out-of-band dirty
    marker, a ``security_version`` skew, and a row-count vs ``object_count`` gap.
    """

    from .bucket_manifest_state import current_bucket_manifest_dirty_token

    rows = [r for r in (manifest.get("traces") or []) if isinstance(r, dict)]
    scalar = manifest.get("trace_records") or {}
    agg = _aggregate_status_from_rows(rows)
    known_rows = len(rows) - agg["unknown_count"]

    reasons: list[str] = []
    try:
        if current_bucket_manifest_dirty_token() is not None:
            reasons.append("out_of_band_security_change")
    except Exception:
        pass
    if manifest.get("security_version") != SECURITY_VERSION:
        reasons.append("security_version_skew")
    object_count = scalar.get("object_count")
    if isinstance(object_count, int) and object_count != len(rows):
        reasons.append("incomplete_rows")

    if rows and known_rows == 0:
        # Pre-087 manifest: rows carry no status accelerator. Use the persisted
        # (stale-but-present) scalar block so counts are sensible, not all-zero.
        counts = {
            "object_count": object_count if isinstance(object_count, int) else len(rows),
            "syncable_count": scalar.get("syncable_count", 0),
            "unfiltered_count": scalar.get("unfiltered_count", 0),
            "security_stale_count": scalar.get("security_stale_count", 0),
            "privacy_off_count": scalar.get("privacy_off_count", 0),
            "last_write_at": scalar.get("last_write_at"),
        }
        source = "scalar_block"
        reasons.append("status_not_populated")
    else:
        counts = {
            "object_count": len(rows),
            "syncable_count": agg["syncable_count"],
            "unfiltered_count": agg["unfiltered_count"],
            "security_stale_count": agg["security_stale_count"],
            "privacy_off_count": agg["privacy_off_count"],
            "last_write_at": agg["last_write_at"],
        }
        source = "row_status"
        if agg["unknown_count"]:
            reasons.append("status_partially_populated")

    stale = bool(reasons)

    # Reuse the existing bounded projection (drops traces[]/snapshot/freshness),
    # then override the count + sync blocks with the freshly aggregated numbers
    # so the rendered eligibility matches the rendered counts.
    view = _bounded_status_view(manifest)
    view["trace_count"] = len(rows)
    view["trace_records"] = {
        **(view.get("trace_records") or {}),
        **counts,
    }
    # Eligibility is CONSERVATIVE: a stale read-model (out-of-band rescan,
    # version skew, incomplete or partially-populated rows) cannot confirm every
    # trace's security state, so it must never report eligible=True off counts it
    # knows are incomplete — that would claim remote-syncable when an unscanned
    # trace is hiding in the unknown rows. ``bucket remote push`` recomputes the
    # authoritative manifest anyway, so a conservative status never blocks a real
    # push; it only avoids a false "ready" on a stale read.
    blocked = _bucket_sync_blockers(
        unfiltered_count=counts["unfiltered_count"],
        stale_security_count=counts["security_stale_count"],
    )
    if stale and "read_model_stale" not in blocked:
        blocked = [*blocked, "read_model_stale"]
    view["sync"] = {
        "eligible": (not stale)
        and counts["unfiltered_count"] == 0
        and counts["security_stale_count"] == 0,
        "blocked_reasons": blocked,
    }

    return {
        "status": "ok",
        "bucket": view,
        "freshness": {
            "stale": stale,
            "reasons": reasons,
            "source": source,
            "rebuild_recommended": (
                "opentraces bucket manifest --heal" if reasons else None
            ),
            "last_full_sweep_at": manifest.get("generated_at"),
        },
        "config": _bucket_config_payload(),
    }


def bucket_status_fast(*, progress=None) -> dict[str, Any]:
    """Size-independent ``bucket status`` read (plan 087).

    Reads the persisted ``manifest.json`` O(1) and serves sync/security counts
    from the per-row ``status`` accelerator with a ``freshness`` stamp — instead
    of the O(N) full-TraceRecord + security-recompute scan that times out
    (>400s) on a large bucket. Falls back to the side-effect-free in-memory scan
    ONLY when no usable persisted manifest exists (absent/error — rare, since
    capture upserts the manifest); degrades (no scan) when the manifest exceeds
    the byte cap, recommending ``bucket repair`` rather than hanging.
    """

    state, manifest = read_persisted_manifest_capped()
    if state == "ok" and isinstance(manifest, dict):
        return _bucket_status_from_persisted(manifest)
    if state == "too_large":
        return {
            "status": "ok",
            "bucket": {
                "root": str(paths.bucket_dir()),
                "state": "too-large",
                "trace_count": None,
                "trace_records": {},
                "trail": {},
                "raw_sources": {},
                "trail_events": {},
                # Keep the eligible-present contract every other path satisfies.
                "sync": {
                    "eligible": False,
                    "blocked_reasons": ["manifest_too_large"],
                },
            },
            "freshness": {
                "stale": True,
                "reasons": ["manifest_too_large"],
                "source": "degraded",
                "rebuild_recommended": "opentraces bucket repair",
                "last_full_sweep_at": None,
            },
            "config": _bucket_config_payload(),
        }
    # absent / error: the persisted read-model is unavailable. Fall back to the
    # side-effect-free in-memory O(N) scan (preserves correctness + the read-only
    # contract on a not-yet-built bucket).
    payload = bucket_status(write_manifest=False, heal=False, progress=progress)
    payload["freshness"] = {
        "stale": False,
        "reasons": [],
        "source": "full_scan",
        "rebuild_recommended": None,
        "last_full_sweep_at": payload.get("bucket", {}).get("generated_at"),
    }
    return payload


def sync_trace_records_from_local_stores(
    *,
    prune: bool = True,
) -> BucketSyncSummary:
    """Mirror legacy project/staging TraceRecord stores into the local bucket.

    This is the local migration bridge. It keeps existing project/staging stores
    working while making the bucket the query substrate.
    """

    root = trace_records_root()
    written = 0
    unchanged = 0
    skipped = 0
    expected_paths: set[Path] = set()
    cfg = None
    for record, project_slug, source_layer in _iter_legacy_trace_records():
        explicit_tier = record_privacy_tier(record)
        if _needs_legacy_security_refresh(record) and explicit_tier != "off":
            if cfg is None:
                from .config import load_config

                cfg = load_config()
            try:
                from .pipeline import process_imported_trace

                record = process_imported_trace(record, cfg).record
            except Exception:
                skipped += 1
                continue
        normalized = _normalized_record(record)
        record_hash = _digest_payload(normalized.model_dump(mode="json"))
        object_path = trace_record_object_path(project_slug, record.trace_id, record_hash)
        expected_paths.add(object_path)
        expected_paths.add(trace_record_path(project_slug, record.trace_id))
        existing = read_trace_record_object(trace_record_path(project_slug, record.trace_id))
        if existing and existing.record_hash == record_hash:
            unchanged += 1
            continue
        try:
            write_trace_record(
                normalized,
                project_slug=project_slug,
                source_layer=source_layer,
                legacy_mirror=True,
                privacy_tier=explicit_tier,
            )
            written += 1
        except OSError:
            skipped += 1

    removed = 0
    if prune and root.exists():
        for obj in iter_trace_record_objects():
            if obj.path in expected_paths:
                continue
            if obj.envelope.get("legacy_mirror") is True:
                try:
                    obj.path.unlink()
                    pointer = trace_record_path(obj.project_slug, obj.trace_id)
                    if pointer.exists():
                        pointer.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
    if removed:
        try:
            from .trace_search_state import mark_search_snapshot_dirty

            mark_search_snapshot_dirty("trace_record_remove")
        except Exception:
            pass
    if written or removed:
        # Plan 087 — this mirror rewrote/removed object envelopes out of band of
        # the manifest rows (security re-derive on enabled-tool change, or a
        # legacy-mirror prune). Flag the manifest read-model stale so
        # ``bucket status`` serves the cached row counts marked stale until a heal.
        try:
            from .bucket_manifest_state import mark_bucket_manifest_dirty

            mark_bucket_manifest_dirty("sync_trace_records_from_local_stores")
        except Exception:
            pass
    return BucketSyncSummary(
        root=root,
        written=written,
        unchanged=unchanged,
        removed=removed,
        skipped=skipped,
    )


def _iter_legacy_trace_records() -> list[tuple[TraceRecord, str, str]]:
    records: list[tuple[TraceRecord, str, str]] = []
    projects_root = paths.PROJECTS_DIR
    if projects_root.exists():
        for project_home in sorted(path for path in projects_root.iterdir() if path.is_dir()):
            traces_dir = project_home / "traces"
            if not traces_dir.exists():
                continue
            for trace_path in sorted(traces_dir.glob("*.jsonl")):
                for record in _read_jsonl_trace_records(trace_path):
                    records.append((record, project_home.name, "canonical"))
    staging_root = getattr(paths, "STAGING_DIR", None)
    if staging_root and staging_root.exists() and staging_root.is_dir():
        for trace_path in sorted(staging_root.glob("*.jsonl")):
            for record in _read_jsonl_trace_records(trace_path):
                records.append((record, TRACE_RECORD_PROJECT_STAGING, "staging"))
    return records


def _needs_legacy_security_refresh(record: TraceRecord) -> bool:
    state = bucket_security_state(record)
    return not bool(state.get("syncable"))


def _bucket_sync_blockers(
    *,
    unfiltered_count: int,
    stale_security_count: int,
) -> list[str]:
    blockers: list[str] = []
    if unfiltered_count:
        blockers.append("unfiltered_records")
    if stale_security_count:
        blockers.append("security_version_stale")
    return blockers


def _bucket_config_payload() -> dict[str, Any]:
    try:
        from .config import load_config

        return load_config().bucket.model_dump(mode="json")
    except Exception:
        return {
            "storage": "local",
            "local_cache": True,
            "remote": {
                "enabled": False,
                "provider": "huggingface",
                "url": None,
                "visibility": "private",
                "sync_policy": "daemon",
            },
        }


def _copy_bucket_tree(
    source: Path,
    destination: Path,
    *,
    skip_names: set[str] | None = None,
) -> int:
    skipped = skip_names or set()
    copied = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        if path.name in skipped:
            continue
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() == path.read_bytes():
            continue
        tmp = target.with_name(f".{target.name}.tmp")
        shutil.copy2(path, tmp)
        tmp.replace(target)
        copied += 1
    return copied


# ---------------------------------------------------------------------------
# Re-export the bucket maintenance cluster (repair / verify / prune / prefetch /
# rebuild_* / migrate), moved to ``bucket_maintenance`` in the god-module
# decomposition. The re-export is LAZY (PEP 562 module ``__getattr__``) on
# purpose: ``bucket_maintenance`` imports its dependencies FROM this module, so
# an eager ``from .bucket_maintenance import ...`` here would form a load-time
# import cycle (and break whenever ``bucket_maintenance`` is imported first).
# Deferring to attribute-access time means both modules are fully loaded before
# the lookup resolves. This keeps ``from ...bucket_store import bucket_repair``
# (and the other six verbs) working as part of bucket_store's established
# bucket-subsystem facade, while the implementation lives in its own module.
# ---------------------------------------------------------------------------
_MAINTENANCE_REEXPORTS = frozenset(
    {
        "bucket_repair",
        "bucket_verify",
        "bucket_prune",
        "bucket_prefetch",
        "rebuild_bucket_trail",
        "rebuild_bucket_traces",
        "migrate_bucket_to_v2",
    }
)


def __getattr__(name: str):
    if name in _MAINTENANCE_REEXPORTS:
        from . import bucket_maintenance

        return getattr(bucket_maintenance, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
