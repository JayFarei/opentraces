"""Bucket maintenance verbs — repair / verify / prune / prefetch / rebuild / migrate.

Extracted from the ``bucket_store`` god module (plan: god-module decomposition).
These are the operator-facing maintenance operations over the private bucket —
they read and rewrite bucket content from the canonical event log + blob store
(``bucket repair`` / ``verify`` / ``prune`` / ``prefetch``, the two ``rebuild_*``
helpers, and the one-shot ``migrate_bucket_to_v2``). They form a leaf of the
bucket layer: storage primitives come from their true owner modules, while the
few manifest/facade helpers still come from ``bucket_store``. The intra-cluster
call ``migrate_bucket_to_v2`` -> ``bucket_repair`` is intra-module.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote


from . import paths
from ._bucket_io import _atomic_write_bytes
from .bucket_context_store import (
    _blob_content_matches_path,
    _hash_for_blob_path,
    _iter_context_blob_files,
    _layer_id_refs_for_trace,
    _layer_id_refs_from_events_mirror,
    project_context_tree_to_bucket,
)
from .bucket_envelope import (
    _events_for_export_loop,
    _is_legacy_read_in_place_mirror,
    _iter_opted_in_projects,
    _rederive_patch_rows,
    _trace_ids_for_project,
    canonical_anchor_maps,
    project_per_trace_exports,
)
from .bucket_events import BUCKET_EVENTS_INDEX_SCHEMA, sync_events_mirror
from .bucket_layout import (
    blobs_v1_context_path,
    blobs_v1_root,
    bucket_manifest_path,
    context_layer_blob_path,
    events_v1_index_path,
    trace_v1_context_path,
    trace_v1_json_path,
    trace_v1_sources_path,
    trace_v1_trail_path,
    traces_v1_root,
)
from .bucket_models import (
    BUCKET_MANIFEST_SCHEMA,
    BUCKET_PER_TRACE_SCHEMA,
    BucketLayoutError,
)
from .bucket_store import (
    _copy_bucket_tree,
    _load_manifest,
    bucket_manifest,
)
from .bucket_trace_records import iter_trace_record_objects


def rederive_bucket_anchors(
    bucket_root: Path, *, dry_run: bool = False
) -> dict[str, Any]:
    """Rebuild persisted ``trace.json`` patch anchors + manifest
    ``anchored_count`` from the canonical ``git_anchor_created`` events.

    Epic #169 (B-attr) single-source-of-truth: ``Patch.anchor``, the manifest
    ``summary.anchored_count`` and the lineage surface must all DERIVE from the
    canonical per-patch anchor events (written by
    ``core/trails/anchors.reconcile_commit_anchors``), never from standalone
    correlator stamps. For every per-trace envelope under ``bucket_root``:

    * a patch is ``anchor.found = True`` ONLY when a ``git_anchor_created`` event
      exists for its ``patch_id``, with ``commit_sha`` taken from that event
      (file-membership-correct by construction — the canonical writer only
      anchors a patch into a commit that genuinely changed its file); a patch
      with no backing event is DE-ATTRIBUTED (``found = False``, or left
      un-anchored when it never carried an anchor);
    * a patch that genuinely appears in several commits (amend / cherry-pick /
      repeated content) is surfaced as ONE patch row per distinct anchor commit,
      so the per-trace lineage surface set EXACTLY equals the canonical anchor
      set (the latest commit stays the primary ``anchor``; older commits also
      ride ``superseded_by`` newest-first);
    * the manifest row ``summary.anchored_count`` is set to the number of
      DISTINCT anchored patch ids for the trace.

    Idempotent: every written value is drawn from canonical data (commit hex +
    the event's own ``event_time``), so a second run is byte-identical, and a
    trace whose anchors already match the canonical set is left untouched (so
    ``bucket_digest`` is unchanged for already-correct traces).

    Reads events from the live opted-in project that shares each trace's slug;
    traces whose slug has no resolvable project on this machine are skipped
    (their canonical events are not reachable here). NEVER mutates the canonical
    event log — it is read-only over Git and write-only over ``bucket_root``.
    """

    bucket_root = Path(bucket_root)
    traces_root = bucket_root / "traces" / "v1"
    manifest_path = bucket_root / "manifest.json"

    manifest: dict[str, Any] | None = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            manifest = None

    # Slugs whose per-trace envelopes are actually present on disk. We read the
    # canonical anchor events ONLY for these slugs (one scoped read per backing
    # repo), so a bucket COPY holding a handful of fixture traces costs a handful
    # of reads, never one per project in the full registry. Manifest rows for a
    # present slug are still all re-derived (so every row of that project gets a
    # single-source anchored_count); rows for absent slugs are left untouched.
    present_slugs: set[str] = set()
    if traces_root.is_dir():
        for child in traces_root.iterdir():
            if child.is_dir() and any(child.glob("*/trace.json")):
                present_slugs.add(child.name)

    anchors_by_trace_patch, distinct_anchored_by_trace, resolved_slugs = (
        canonical_anchor_maps(present_slugs)
    )

    traces_rewritten = 0
    patches_deattributed = 0
    patches_anchored = 0
    if traces_root.is_dir():
        for trace_json in sorted(traces_root.glob("*/*/trace.json")):
            slug = trace_json.parent.parent.name
            trace_id = trace_json.parent.name
            if slug not in resolved_slugs:
                continue
            try:
                doc = json.loads(trace_json.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            patch_anchors = anchors_by_trace_patch.get(trace_id, {})
            # #172 GAP 1 — the collapse + re-expansion is now the shared
            # ``_rederive_patch_rows`` body (single source with the LIVE per-trace
            # projection). It collapses any prior one-row-per-anchor expansion to
            # one base patch per patch_id, then re-expands from the canonical
            # events, so a second run is byte-identical.
            new_patches, n_anchored, n_deattributed = _rederive_patch_rows(
                doc.get("patches") or [], patch_anchors
            )
            patches_anchored += n_anchored
            patches_deattributed += n_deattributed
            if new_patches != (doc.get("patches") or []):
                doc["patches"] = new_patches
                if not dry_run:
                    _atomic_write_bytes(
                        trace_json,
                        (
                            json.dumps(
                                doc,
                                ensure_ascii=False,
                                indent=2,
                                sort_keys=True,
                            )
                            + "\n"
                        ).encode("utf-8"),
                    )
                traces_rewritten += 1

    manifest_rows_updated = 0
    if isinstance(manifest, dict):
        for row in manifest.get("traces") or []:
            slug = row.get("project_slug")
            trace_id = row.get("trace_id")
            if slug not in resolved_slugs:
                continue
            correct = len(distinct_anchored_by_trace.get(trace_id, set()))
            summary = row.setdefault("summary", {})
            if summary.get("anchored_count") != correct:
                summary["anchored_count"] = correct
                manifest_rows_updated += 1
        if manifest_rows_updated and not dry_run:
            _atomic_write_bytes(
                manifest_path,
                (
                    json.dumps(
                        manifest, ensure_ascii=False, indent=2, sort_keys=True
                    )
                    + "\n"
                ).encode("utf-8"),
            )

    return {
        "bucket_root": str(bucket_root),
        "dry_run": dry_run,
        "resolved_slugs": sorted(resolved_slugs),
        "unresolved_slugs": sorted(present_slugs - resolved_slugs),
        "traces_rewritten": traces_rewritten,
        "patches_anchored": patches_anchored,
        "patches_deattributed": patches_deattributed,
        "manifest_rows_updated": manifest_rows_updated,
    }


def bucket_repair(
    *, dry_run: bool = False, bucket_root: Path | None = None
) -> dict[str, Any]:
    """Full rebuild from canonical (event log + blob store).

    Plan 080 §9 / Resolution G — the documented crash-recovery primitive.
    Walks every opted-in project, re-runs :func:`sync_events_mirror` to
    rebuild the events mirror, re-projects every trace via
    :func:`project_per_trace_exports`, and regenerates the manifest with
    :func:`bucket_manifest`.

    Idempotent: a second invocation on the same canonical state produces
    byte-identical on-disk output (proof: every writer uses
    :func:`_atomic_write_*` helpers that skip same-bytes writes; the
    manifest digest excludes the volatile ``generated_at`` / ``updated_at``
    fields per Resolution H).

    Epic #169 (B-attr): when ``bucket_root`` is given (a bucket COPY), repair
    runs ONLY the canonical anchor re-derive (:func:`rederive_bucket_anchors`) —
    it rebuilds ``trace.json`` patch anchors + manifest ``anchored_count`` from
    the canonical ``git_anchor_created`` events (the single source of truth, so
    no standalone correlator stamp survives without a backing event) and does
    NOT re-project the full project set into a partial copy. This is what makes
    ``opentraces bucket repair --bucket-root <copy>`` the safe way to re-derive a
    bucket COPY without touching the live ``~/.opentraces`` bucket. The default
    (no ``bucket_root``) keeps the documented live-bucket re-projection
    behaviour unchanged.
    """

    # ``bucket_root`` given: focused anchor re-derive over the copy's existing
    # envelopes (no full re-projection — the copy is a partial subset).
    if bucket_root is not None:
        rederive = rederive_bucket_anchors(Path(bucket_root), dry_run=dry_run)
        return {
            "schema_version": BUCKET_MANIFEST_SCHEMA,
            "status": "ok",
            "dry_run": dry_run,
            "mode": "rederive_anchors",
            "traces_projected": rederive["traces_rewritten"],
            "bucket_sourced_traces": 0,
            "events_mirrored": 0,
            "manifest_regenerated": bool(rederive["manifest_rows_updated"]),
            "projects_walked": len(rederive["resolved_slugs"]),
            "rederive": rederive,
            "errors": [],
        }

    errors: list[dict[str, Any]] = []
    traces_projected = 0
    bucket_sourced_traces = 0
    events_mirrored = 0
    manifest_regenerated = False

    projects = _iter_opted_in_projects()
    handled_pairs: set[tuple[str, str]] = set()

    for project_path, project_slug in projects:
        # 1. Events mirror rebuild — required before per-trace projection.
        if not dry_run:
            try:
                sync_events_mirror(project_path, repo_id=project_slug)
            except Exception as exc:
                errors.append(
                    {
                        "kind": "events_mirror",
                        "project_slug": project_slug,
                        "detail": str(exc),
                    }
                )

        # 2. Per-trace envelopes for every trace_id seen in the event log.
        # #65: ONE shared full read for the whole loop (read_events memoises
        # per head); the default per-call trace-scoped read would be
        # O(traces × full-log-walk) here.
        trace_ids = _trace_ids_for_project(project_path)
        shared_events = _events_for_export_loop(project_path) if trace_ids else []
        for trace_id in trace_ids:
            traces_projected += 1
            handled_pairs.add((project_slug, trace_id))
            if dry_run:
                continue
            try:
                project_per_trace_exports(
                    project_path,
                    project_slug=project_slug,
                    trace_id=trace_id,
                    events=shared_events,
                )
            except Exception as exc:
                errors.append(
                    {
                        "kind": "per_trace_export",
                        "project_slug": project_slug,
                        "trace_id": trace_id,
                        "detail": str(exc),
                    }
                )

        # 3. Context-tree projection (idempotent; rebuilds head/nodes/blobs).
        if not dry_run:
            try:
                project_context_tree_to_bucket(
                    project_path, project_slug=project_slug
                )
            except Exception as exc:
                errors.append(
                    {
                        "kind": "context_tree_projection",
                        "project_slug": project_slug,
                        "detail": str(exc),
                    }
                )

    # 3b. Issue #28 — bucket-sourced pass. The live-projects walk above only
    # sees traces whose opted-in project still exists on THIS machine. After a
    # cross-machine restore (``bucket remote pull`` onto a second machine) the
    # bucket holds canonical TraceRecord envelopes with no live project — those
    # must NOT be dropped by repair. For every TraceRecord object NOT already
    # handled by the live walk, project the per-trace envelope from the bucket's
    # OWN events mirror (``project_per_trace_exports(None, ...)`` falls back to
    # ``read_events_mirror_batches``) so the trace survives. Idempotent: a
    # second repair re-projects the same bytes (atomic same-bytes writers).
    # Plan 085 S5 — legacy-store mirrors (in-place JSONL exists AND no
    # capture-time raw-source link) are read in place, never adopted.
    # Record-only staged traces (PR #63) carry the link and ARE projected
    # here — that is their deferred projection.
    for obj in iter_trace_record_objects():
        pair = (obj.project_slug, obj.trace_id)
        if pair in handled_pairs:
            continue
        if _is_legacy_read_in_place_mirror(*pair):
            continue
        handled_pairs.add(pair)
        traces_projected += 1
        bucket_sourced_traces += 1
        if dry_run:
            continue
        try:
            project_per_trace_exports(
                None,
                project_slug=obj.project_slug,
                trace_id=obj.trace_id,
                record=obj.record,
            )
        except Exception as exc:
            errors.append(
                {
                    "kind": "bucket_sourced_export",
                    "project_slug": obj.project_slug,
                    "trace_id": obj.trace_id,
                    "detail": str(exc),
                }
            )

    # 4. events_mirrored count from the events mirror index (set by the
    # last sync_events_mirror call across all projects). When there's only
    # a single project this is exact; for multi-project buckets the index
    # holds the most recently synced project's batch count.
    idx_path = events_v1_index_path()
    if idx_path.exists():
        try:
            raw_idx = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(raw_idx, dict):
                events_mirrored = int(raw_idx.get("batch_count") or 0)
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    # 5. Regenerate manifest. Write only when not in dry-run mode AND
    # when the content has actually changed — compare ``bucket_digest``
    # (Resolution H: excludes volatile generated_at/updated_at fields).
    # This makes ``bucket_repair`` byte-identical-idempotent: a second run
    # against unchanged canonical state leaves ``manifest.json`` untouched
    # on disk so its raw bytes (including the embedded ``generated_at``
    # timestamp) stay stable.
    if not dry_run:
        try:
            candidate = bucket_manifest(write=False, include_objects=False)
            existing_doc = None
            manifest_path = bucket_manifest_path()
            if manifest_path.exists():
                try:
                    existing_doc = json.loads(
                        manifest_path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError, json.JSONDecodeError):
                    existing_doc = None
            existing_digest = (
                existing_doc.get("bucket_digest")
                if isinstance(existing_doc, dict)
                else None
            )
            if candidate.get("bucket_digest") != existing_digest:
                bucket_manifest(write=True, include_objects=False)
            manifest_regenerated = True
        except Exception as exc:
            errors.append(
                {"kind": "manifest", "project_slug": None, "detail": str(exc)}
            )

    # Epic #169 (B-attr): the canonical anchor re-derive runs ONLY in the
    # explicit ``--bucket-root`` mode (above), against a bucket COPY. The default
    # live-bucket repair keeps its documented re-projection behaviour byte-for-
    # byte — re-deriving the whole live bucket from canonical here would both
    # contradict "never repair the live bucket" and risk de-attributing a trace
    # whose canonical events are not reachable on the local machine.
    status = "ok" if not errors else "partial"
    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "status": status,
        "dry_run": dry_run,
        "traces_projected": traces_projected,
        "bucket_sourced_traces": bucket_sourced_traces,
        "events_mirrored": events_mirrored,
        "manifest_regenerated": manifest_regenerated,
        "projects_walked": len(projects),
        "errors": errors,
    }


def bucket_verify(
    *,
    sample: int = 100,
    full: bool = False,
) -> dict[str, Any]:
    """Blob content integrity + dangling-ref detection.

    Plan 080 §7 / §20 Resolution G. Three checks:

    1. **Blob content integrity.** For each context blob, recompute the
       hash encoded in the filename and assert the blob's ``layer_id``
       field echoes the same value. ``sample`` (default 100) bounds the
       fast path; ``full=True`` walks every blob.
    2. **Dangling reference detection.** Every ``layer_id`` referenced
       by a per-trace ``context.jsonl.gz`` and the events mirror must
       resolve to an on-disk blob.
    3. **Manifest consistency.** Each trace listed in ``manifest.json``
       must have ``traces/v1/<proj>/<trace>/trace.json`` on disk.

    Returns ``{ok, sampled, errors: [{kind, path, detail}], full}``.
    Never mutates bucket state.
    """

    import random

    errors: list[dict[str, Any]] = []

    # --- 1. Blob content integrity --------------------------------------
    blob_files = list(_iter_context_blob_files())
    if full or sample <= 0:
        sampled_blobs = blob_files
    else:
        if len(blob_files) <= sample:
            sampled_blobs = blob_files
        else:
            # Deterministic sample for the same input set — seed by the
            # sorted filename list so repeated calls return the same N.
            rng = random.Random(",".join(p.name for p in blob_files))
            sampled_blobs = rng.sample(blob_files, sample)
            sampled_blobs.sort()
    for blob_path in sampled_blobs:
        ok, detail = _blob_content_matches_path(blob_path)
        if ok:
            continue
        try:
            rel = blob_path.relative_to(paths.bucket_dir()).as_posix()
        except ValueError:
            rel = str(blob_path)
        errors.append(
            {"kind": "blob_content", "path": rel, "detail": detail or "mismatch"}
        )

    # --- 2. Dangling reference detection --------------------------------
    referenced_layer_ids: set[str] = _layer_id_refs_from_events_mirror()

    # Walk per-trace context.jsonl.gz under traces/v1/.
    traces_root = traces_v1_root()
    if traces_root.exists():
        for trace_json in sorted(traces_root.glob("*/*/trace.json")):
            try:
                proj_slug = unquote(trace_json.parent.parent.name)
                tid = unquote(trace_json.parent.name)
            except Exception:
                continue
            trace_refs = _layer_id_refs_for_trace(proj_slug, tid)
            referenced_layer_ids.update(trace_refs)
            # Per-trace dangling check: each referenced layer_id must have
            # a blob on disk under the project's namespace (or _shared).
            for layer_id in trace_refs:
                proj_path = blobs_v1_context_path(proj_slug, layer_id)
                shared_path = context_layer_blob_path(
                    proj_slug, layer_id, scope="global"
                )
                if proj_path.exists() or shared_path.exists():
                    continue
                try:
                    rel = proj_path.relative_to(paths.bucket_dir()).as_posix()
                except ValueError:
                    rel = str(proj_path)
                errors.append(
                    {
                        "kind": "dangling_blob",
                        "path": rel,
                        "detail": (
                            f"trace {tid!r} references layer_id "
                            f"{layer_id!r} but no blob exists"
                        ),
                    }
                )

    # Events-mirror dangling check: walk every layer_id seen in mirror and
    # verify *some* project has a blob for it (we don't know the project
    # context from the mirror alone, so we look across the blob root).
    if referenced_layer_ids:
        blob_root = blobs_v1_root()
        # Build a fast lookup of all blob filenames -> hash strings.
        on_disk_hashes: set[str] = set()
        if blob_root.exists():
            for blob in blob_root.glob("*/context/*/*.json.gz"):
                expected = _hash_for_blob_path(blob)
                if expected:
                    on_disk_hashes.add(expected)
        for layer_id in sorted(referenced_layer_ids):
            if layer_id in on_disk_hashes:
                continue
            # Already reported per-trace above; report once for events-only refs.
            already_reported = any(
                err.get("kind") == "dangling_blob"
                and layer_id in (err.get("detail") or "")
                for err in errors
            )
            if already_reported:
                continue
            errors.append(
                {
                    "kind": "dangling_blob_events",
                    "path": layer_id,
                    "detail": "events mirror references layer_id with no blob",
                }
            )

    # --- 3. Manifest consistency ----------------------------------------
    manifest_path = bucket_manifest_path()
    if manifest_path.exists():
        try:
            manifest_doc = _load_manifest()
        except BucketLayoutError as exc:
            errors.append(
                {
                    "kind": "manifest_schema",
                    "path": str(manifest_path),
                    "detail": str(exc),
                }
            )
            manifest_doc = None
        if manifest_doc is not None:
            for row in manifest_doc.get("traces") or []:
                if not isinstance(row, dict):
                    continue
                rel_trace_path = row.get("trace_path")
                if not isinstance(rel_trace_path, str) or not rel_trace_path:
                    continue
                abs_path = paths.bucket_dir() / rel_trace_path
                if abs_path.exists():
                    continue
                errors.append(
                    {
                        "kind": "manifest_missing_trace",
                        "path": rel_trace_path,
                        "detail": (
                            f"manifest lists trace {row.get('trace_id')!r} but "
                            f"trace.json is missing"
                        ),
                    }
                )

    return {
        "ok": not errors,
        "sampled": len(sampled_blobs),
        "errors": errors,
        "full": bool(full),
        # Compat keys that older callers (and Section G test) still inspect.
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "blobs_checked": len(sampled_blobs),
        "dangling_count": sum(
            1 for err in errors if err["kind"].startswith("dangling_blob")
        ),
    }


def bucket_prune(*, dry_run: bool = False) -> dict[str, Any]:
    """Reachability-based orphan-blob cleanup.

    Plan 080 §9 Resolution G — NEVER touches events or ``trace.json``.

    Two passes:

    1. **Reachable blob set.** For every trace listed in ``manifest.json``
       (or, when absent, every trace under ``traces/v1/``), collect every
       layer_id referenced by its ``context.jsonl.gz``. Add every layer_id
       referenced by the events mirror.
    2. **Sweep.** Any context blob whose path-encoded hash is not in the
       reachable set is an orphan. ``.tmp`` files older than 1 hour
       anywhere under ``blobs/v1/`` or ``traces/v1/`` are also removed.

    Returns ``{would_delete, deleted, blobs: [...], tempfiles: [...]}``.
    """

    import time

    deleted_blobs: list[str] = []
    deleted_tempfiles: list[str] = []
    would_delete = 0
    deleted = 0

    # --- 1. Reachable layer set -----------------------------------------
    reachable: set[str] = set()
    reachable.update(_layer_id_refs_from_events_mirror())

    # Pick the trace list from manifest if available; otherwise scan disk.
    manifest_doc: dict[str, Any] | None = None
    manifest_path = bucket_manifest_path()
    if manifest_path.exists():
        try:
            manifest_doc = _load_manifest()
        except BucketLayoutError:
            manifest_doc = None

    if manifest_doc is not None and manifest_doc.get("traces"):
        for row in manifest_doc.get("traces") or []:
            if not isinstance(row, dict):
                continue
            proj_slug = row.get("project_slug")
            tid = row.get("trace_id")
            if not isinstance(proj_slug, str) or not isinstance(tid, str):
                continue
            reachable.update(_layer_id_refs_for_trace(proj_slug, tid))
    else:
        traces_root = traces_v1_root()
        if traces_root.exists():
            for trace_json in sorted(traces_root.glob("*/*/trace.json")):
                proj_slug = unquote(trace_json.parent.parent.name)
                tid = unquote(trace_json.parent.name)
                reachable.update(_layer_id_refs_for_trace(proj_slug, tid))

    # --- 2a. Orphan blob sweep ------------------------------------------
    for blob_path in _iter_context_blob_files():
        hash_str = _hash_for_blob_path(blob_path)
        if hash_str is None:
            # Unknown shape — be conservative; leave alone.
            continue
        if hash_str in reachable:
            continue
        try:
            rel = blob_path.relative_to(paths.bucket_dir()).as_posix()
        except ValueError:
            rel = str(blob_path)
        would_delete += 1
        deleted_blobs.append(rel)
        if dry_run:
            continue
        try:
            blob_path.unlink()
            deleted += 1
        except FileNotFoundError:
            pass

    # --- 2b. Tempfile sweep ---------------------------------------------
    now = time.time()
    one_hour = 60 * 60
    sweep_roots = [blobs_v1_root(), traces_v1_root()]
    for root in sweep_roots:
        if not root.exists():
            continue
        for tmp_path in sorted(root.rglob("*.tmp")):
            if not tmp_path.is_file():
                continue
            # Match the writer's tempfile convention: .{name}.{rand}.tmp .
            if not tmp_path.name.startswith("."):
                continue
            try:
                age = now - tmp_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < one_hour:
                continue
            try:
                rel = tmp_path.relative_to(paths.bucket_dir()).as_posix()
            except ValueError:
                rel = str(tmp_path)
            would_delete += 1
            deleted_tempfiles.append(rel)
            if dry_run:
                continue
            try:
                tmp_path.unlink()
                deleted += 1
            except FileNotFoundError:
                pass

    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "status": "ok",
        "dry_run": dry_run,
        "would_delete": would_delete,
        "deleted": deleted,
        "blobs": deleted_blobs,
        "tempfiles": deleted_tempfiles,
        # Compat keys for existing callers.
        "orphans_removed": len(deleted_blobs) if not dry_run else 0,
        "tempfiles_removed": len(deleted_tempfiles) if not dry_run else 0,
    }


def bucket_prefetch(
    trace_id: str,
    *,
    remote: str | None = None,
    project_slug: str | None = None,
) -> dict[str, Any]:
    """Eager-pull one trace's envelope + blobs from a remote HF bucket.

    Plan 080 §20 Resolution N: writes into the LOCAL bucket directly
    (``bucket/blobs/v1/<project>/context/`` for layer blobs;
    ``bucket/traces/v1/<project>/<trace>/`` for trace.json + companion
    JSONL). Mental model: "warm my bucket from remote." Use before
    ``ctx show`` on a cold cache to avoid per-blob HTTP round-trips.

    Steps:

    1. Resolve target remote repo_id (explicit ``remote`` arg or
       configured ``cfg.bucket.remote.url``).
    2. Resolve target ``project_slug`` (explicit arg or remote manifest
       lookup by ``trace_id``).
    3. Fetch the 4 per-trace envelope files (``trace.json``,
       ``trail.jsonl.gz``, ``context.jsonl.gz``, ``sources.jsonl.gz``).
    4. Walk ``context.jsonl.gz`` for every referenced layer_id, fetch
       each blob into ``bucket/blobs/v1/<project>/context/<hh>/<hash>.json.gz``.
    5. Return ``{trace_id, project_slug, files_fetched, blobs_fetched,
       bytes, status}``.

    Idempotent: re-running on an already-warm cache fetches what's
    missing and is a no-op for files that match the remote.
    """

    from .bucket_remote import BucketRemoteError, _hf_api, _hf_repo_id
    from .config import load_config

    cfg = load_config()
    repo_url = remote if remote is not None else (
        cfg.bucket.remote.url if cfg.bucket.remote and cfg.bucket.remote.enabled else None
    )
    if not repo_url:
        raise BucketRemoteError(
            "no remote configured; pass --remote <hf-repo> or run "
            "'opentraces setup bucket' to configure a remote"
        )
    repo_id = _hf_repo_id(repo_url)
    api = _hf_api(cfg.hf_token)

    # --- 1. Resolve project_slug (via remote manifest if not provided) ---
    resolved_slug: str | None = project_slug
    if resolved_slug is None:
        try:
            manifest_path = Path(
                api.hf_hub_download(
                    repo_id=repo_id,
                    filename="manifest.json",
                    repo_type="dataset",
                )
            )
            manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise BucketRemoteError(
                f"unable to read remote bucket manifest for {repo_id}: {exc}"
            ) from exc
        for row in manifest_doc.get("traces") or []:
            if isinstance(row, dict) and row.get("trace_id") == trace_id:
                resolved_slug = row.get("project_slug")
                break
        if resolved_slug is None:
            raise ValueError(
                f"trace {trace_id!r} not found in remote bucket manifest at {repo_id}"
            )

    # --- 2. Fetch the 4 per-trace envelope files -----------------------
    files_fetched: list[str] = []
    bytes_fetched = 0

    def _fetch_to_local(remote_path: str, local_path: Path) -> int:
        """Download ``remote_path`` and write atomically to ``local_path``.

        Returns the byte count of the downloaded file, or 0 if the
        remote file does not exist.
        """
        try:
            downloaded = Path(
                api.hf_hub_download(
                    repo_id=repo_id,
                    filename=remote_path,
                    repo_type="dataset",
                )
            )
        except Exception:
            return 0
        data = downloaded.read_bytes()
        # Idempotent: if local matches, skip the rewrite.
        if local_path.exists() and local_path.read_bytes() == data:
            return len(data)
        _atomic_write_bytes(local_path, data)
        return len(data)

    proj_slug = resolved_slug
    # Path quoting must match LocalBucketBackend / RemoteHubBackend
    # (safe="-._~" — RFC 3986 unreserved set). Mismatched safe sets
    # produced divergent remote paths for slugs containing -, ., _, ~.
    from .bucket_backend import _hf_layer_blob_path, _hf_traces_path

    envelope_files = [
        (_hf_traces_path(proj_slug, trace_id, "trace.json"), trace_v1_json_path(proj_slug, trace_id)),
        (_hf_traces_path(proj_slug, trace_id, "trail.jsonl.gz"), trace_v1_trail_path(proj_slug, trace_id)),
        (_hf_traces_path(proj_slug, trace_id, "context.jsonl.gz"), trace_v1_context_path(proj_slug, trace_id)),
        (_hf_traces_path(proj_slug, trace_id, "sources.jsonl.gz"), trace_v1_sources_path(proj_slug, trace_id)),
    ]
    for remote_path, local_path in envelope_files:
        n = _fetch_to_local(remote_path, local_path)
        if n > 0:
            files_fetched.append(remote_path)
            bytes_fetched += n

    if not files_fetched:
        raise ValueError(
            f"trace {trace_id!r} (project={proj_slug!r}) has no envelope "
            f"files on remote {repo_id}"
        )

    # --- 3. Walk context.jsonl.gz for layer_id refs --------------------
    layer_ids: set[str] = _layer_id_refs_for_trace(proj_slug, trace_id)

    # --- 4. Fetch each referenced layer blob ---------------------------
    blobs_fetched_count = 0
    for lid in sorted(layer_ids):
        local_blob = blobs_v1_context_path(proj_slug, lid)
        if local_blob.exists():
            # Already warm.
            continue
        remote_blob_path = _hf_layer_blob_path(proj_slug, lid)
        n = _fetch_to_local(remote_blob_path, local_blob)
        if n > 0:
            blobs_fetched_count += 1
            bytes_fetched += n

    return {
        "schema_version": BUCKET_MANIFEST_SCHEMA,
        "status": "ok",
        "trace_id": trace_id,
        "project_slug": proj_slug,
        "repo_id": repo_id,
        "files_fetched": len(files_fetched),
        "blobs_fetched": blobs_fetched_count,
        "bytes": bytes_fetched,
        "layer_refs_seen": len(layer_ids),
    }


def rebuild_bucket_trail() -> dict[str, Any]:
    """Rebuild the events mirror substrate from each opted-in project.

    Plan 080 §7 ``bucket rebuild --substrate trail`` body. Calls
    :func:`sync_events_mirror` once per opted-in project and aggregates the
    per-project envelopes into a single envelope so the CLI can present a
    single ``trail`` block under ``rebuild.per_substrate``.
    """

    projects = _iter_opted_in_projects()
    per_project: list[dict[str, Any]] = []
    events_mirrored = 0
    last_batch_id: str | None = None
    latest_event_sequence = 0
    for project_path, project_slug in projects:
        try:
            index = sync_events_mirror(project_path, repo_id=project_slug)
        except Exception as exc:
            per_project.append(
                {
                    "project_slug": project_slug,
                    "project": str(project_path),
                    "error": str(exc),
                }
            )
            continue
        per_project.append(
            {
                "project_slug": project_slug,
                "project": str(project_path),
                "batch_count": int(index.get("batch_count") or 0),
                "batches_written": int(index.get("batches_written") or 0),
                "latest_event_sequence": int(
                    index.get("latest_event_sequence") or 0
                ),
                "last_batch_id": index.get("last_batch_id"),
                "state": index.get("state"),
            }
        )
        events_mirrored += int(index.get("batch_count") or 0)
        seq = int(index.get("latest_event_sequence") or 0)
        if seq > latest_event_sequence:
            latest_event_sequence = seq
        if index.get("last_batch_id"):
            last_batch_id = index.get("last_batch_id")

    return {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "substrate": "trail",
        "projects_walked": len(projects),
        "events_mirrored": events_mirrored,
        "latest_event_sequence": latest_event_sequence,
        "last_batch_id": last_batch_id,
        "per_project": per_project,
        "idempotent_noop": all(
            int(row.get("batches_written") or 0) == 0 for row in per_project
        ),
    }


def rebuild_bucket_traces() -> dict[str, Any]:
    """Rebuild every per-trace envelope from each opted-in project.

    Plan 080 §7 ``bucket rebuild --substrate traces`` body. Walks each
    project's event log, collects trace_ids, and calls
    :func:`project_per_trace_exports` for each. Idempotent: byte-identical
    output on a second call (atomic-write helpers skip same-bytes writes).
    """

    projects = _iter_opted_in_projects()
    per_project: list[dict[str, Any]] = []
    envelopes_written = 0
    handled_pairs: set[tuple[str, str]] = set()
    for project_path, project_slug in projects:
        trace_ids = _trace_ids_for_project(project_path)
        project_envelopes = 0
        errors: list[dict[str, Any]] = []
        # #65: ONE shared full read for the whole loop (see repair loop).
        shared_events = _events_for_export_loop(project_path) if trace_ids else []
        for tid in trace_ids:
            handled_pairs.add((project_slug, tid))
            try:
                project_per_trace_exports(
                    project_path,
                    project_slug=project_slug,
                    trace_id=tid,
                    events=shared_events,
                )
                project_envelopes += 1
            except Exception as exc:
                errors.append({"trace_id": tid, "detail": str(exc)})
        envelopes_written += project_envelopes
        per_project.append(
            {
                "project_slug": project_slug,
                "project": str(project_path),
                "trace_count": len(trace_ids),
                "envelopes_written": project_envelopes,
                "errors": errors,
            }
        )

    # Issue #28 — bucket-sourced pass (shared with :func:`bucket_repair`). Every
    # TraceRecord object NOT reached by a live opted-in project (cross-machine
    # restore shape) is projected from the bucket's own events mirror so it is
    # never dropped. ``project_per_trace_exports(None, ...)`` falls back to
    # ``read_events_mirror_batches``. Plan 085 S5 — legacy-store mirrors
    # (in-place JSONL exists AND no capture-time raw-source link) are read in
    # place, never adopted; record-only staged traces (PR #63) carry the link
    # and are projected here.
    bucket_sourced_errors: list[dict[str, Any]] = []
    bucket_sourced_written = 0
    for obj in iter_trace_record_objects():
        pair = (obj.project_slug, obj.trace_id)
        if pair in handled_pairs:
            continue
        if _is_legacy_read_in_place_mirror(*pair):
            continue
        handled_pairs.add(pair)
        try:
            project_per_trace_exports(
                None,
                project_slug=obj.project_slug,
                trace_id=obj.trace_id,
                record=obj.record,
            )
            envelopes_written += 1
            bucket_sourced_written += 1
        except Exception as exc:
            bucket_sourced_errors.append(
                {
                    "project_slug": obj.project_slug,
                    "trace_id": obj.trace_id,
                    "detail": str(exc),
                }
            )
    if bucket_sourced_written or bucket_sourced_errors:
        per_project.append(
            {
                "project_slug": None,
                "project": "<bucket-sourced>",
                "trace_count": bucket_sourced_written + len(bucket_sourced_errors),
                "envelopes_written": bucket_sourced_written,
                "errors": bucket_sourced_errors,
            }
        )

    return {
        "schema_version": BUCKET_PER_TRACE_SCHEMA,
        "substrate": "traces",
        "projects_walked": len(projects),
        "envelopes_written": envelopes_written,
        "bucket_sourced_traces": bucket_sourced_written,
        "per_project": per_project,
        "idempotent_noop": envelopes_written == 0,
    }


def migrate_bucket_to_v2(
    *,
    bucket_root: Path,
    bucket_v2_path: Path,
    from_layout: str,
) -> dict[str, Any]:
    """Migrate an existing legacy bucket into the plan-080 v2 layout.

    Plan 080 §15(a) — write-new-and-swap. The legacy bucket at
    ``bucket_root`` is left intact while a fresh v2 layout is written
    under ``bucket_v2_path``; after a consistency check the new directory
    is atomically swapped in (the legacy tree is renamed aside with a
    ``.legacy-<timestamp>`` suffix so it can be removed manually).

    ``from_layout`` is one of:

    - ``"v1_plan79"``  — has ``bucket/contexts/v1/`` (plan 079 layout).
    - ``"v1_pre79"``   — has ``bucket/events/trail/v1/`` or
                          ``bucket/objects/traces/v1/`` (pre-plan-079).

    The reconstruction is canonical-driven: per-project Git event logs are
    the source of truth, so the new bucket is rebuilt the same way
    :func:`bucket_repair` would build it from scratch. Legacy files (raw
    sources, TraceRecord envelopes) are copied verbatim when present.
    Returns ``{traces_migrated, blobs_migrated, status, from_layout,
    to_layout}``.
    """

    if from_layout not in {"v1_plan79", "v1_pre79"}:
        # Empty / already-v2 buckets short-circuit in the CLI layer; this
        # function should never be called for them.
        return {
            "status": "noop",
            "from_layout": from_layout,
            "to_layout": "v2",
            "traces_migrated": 0,
            "blobs_migrated": 0,
            "detail": f"unsupported from_layout {from_layout!r}",
        }

    bucket_root = Path(bucket_root)
    bucket_v2_path = Path(bucket_v2_path)

    # Stage area: build the v2 layout in a sibling directory using the
    # standard bucket writers. We temporarily redirect ``paths.bucket_dir``
    # by writing into ``bucket_v2_path`` directly via a swap.
    if bucket_v2_path.exists():
        shutil.rmtree(bucket_v2_path)
    bucket_v2_path.mkdir(parents=True, exist_ok=True)

    # Copy legacy artifacts that are still meaningful in the v2 layout
    # before we run the rebuilders. Two categories:
    #   - ``objects/traces/v1/`` (legacy TraceRecord envelopes) — the v2
    #     spine lives under ``traces/v1/`` but the legacy mirror is
    #     preserved at the same relative path for compat reads.
    #   - ``objects/raw/v1/`` (raw source artifacts).
    blobs_migrated = 0
    legacy_objects_dir = bucket_root / "objects"
    if legacy_objects_dir.exists():
        blobs_migrated += _copy_bucket_tree(
            legacy_objects_dir,
            bucket_v2_path / "objects",
        )

    # Copy ``contexts/v1/`` (plan 079 projected nodes/heads) verbatim so
    # the bucket retains its plan-079 read paths. The v2 writer will also
    # emit fresh files under ``traces/v1/`` and ``blobs/v1/`` from the
    # canonical event log.
    if (bucket_root / "contexts").exists():
        blobs_migrated += _copy_bucket_tree(
            bucket_root / "contexts",
            bucket_v2_path / "contexts",
        )

    # Atomic swap: rename legacy root aside, move new root into place.
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    legacy_aside = bucket_root.with_name(
        f"{bucket_root.name}.legacy-{timestamp}"
    )
    if bucket_root.exists():
        bucket_root.rename(legacy_aside)
    bucket_v2_path.rename(bucket_root)

    # Now that ``bucket_dir()`` points at the new content, rebuild
    # canonical-driven artifacts (events mirror + per-trace envelopes +
    # manifest) by running ``bucket_repair`` end-to-end. This is the same
    # body the user could run manually post-migration.
    repair = bucket_repair(dry_run=False)
    traces_migrated = int(repair.get("traces_projected") or 0)

    status = "ok" if not repair.get("errors") else "partial"

    return {
        "status": status,
        "from_layout": from_layout,
        "to_layout": "v2",
        "traces_migrated": traces_migrated,
        "blobs_migrated": blobs_migrated,
        "legacy_aside": str(legacy_aside),
        "errors": repair.get("errors") or [],
    }
