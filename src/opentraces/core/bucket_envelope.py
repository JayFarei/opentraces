"""Bucket per-trace envelope projection and summary rows.

Extracted from ``bucket_store`` (god-module decomposition) as the LOWER layer of
the bucket core. TraceRecord object I/O and raw-source artifact I/O now live in
``bucket_trace_records`` and ``bucket_raw_sources``; this module owns the v2
per-trace envelope, companion projection, and ``traces[]`` summary rows. The
manifest/sync layer that stays in ``bucket_store`` imports these from here, and
``bucket_store`` re-exports them so existing call sites stay unchanged.
"""

from __future__ import annotations

import gzip
import json
from opentraces.core._time import utc_now_str
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from pydantic import ValidationError

from opentraces_schema import TraceRecord

from ..security.privacy import (
    bucket_security_state,
)
from . import paths
from .bucket_layout import (
    _path_part,
    raw_sources_root,
    trace_v1_context_path,
    trace_v1_json_path,
    trace_v1_sources_path,
    trace_v1_trail_path,
    traces_v1_dir,
    traces_v1_root,
)

# ---------------------------------------------------------------------------
# Re-export I/O utilities from the extracted _bucket_io module.
# All internal callers within this file still work; external callers that
# do ``from opentraces.core.bucket_store import _atomic_write_json`` etc.
# continue to resolve.
# ---------------------------------------------------------------------------
from ._bucket_io import (
    _atomic_write_gzip,
    _atomic_write_json,
    _canonical_json,
    _digest_payload,
    _read_gzip_bytes,
)

# ---------------------------------------------------------------------------
# Re-export trail/events-mirror cluster from bucket_events.
# ---------------------------------------------------------------------------
from .bucket_events import (
    read_events_mirror_batches,
)

# ---------------------------------------------------------------------------
# Re-export Context Tree bucket projection cluster from bucket_context_store.
# ---------------------------------------------------------------------------


# Bucket data models + schema-version constants live in the dependency-free base
# module ``bucket_models``; re-exported here so ``from ...bucket_store import
# BucketTraceRecord`` / ``BUCKET_MANIFEST_SCHEMA`` (and the rest) keep working.
# Imported eagerly: ``bucket_models`` imports nothing from this package, so there
# is no cycle, and these names are used throughout the manifest code below.
from .bucket_models import (
    BUCKET_PER_TRACE_SCHEMA,
    BucketLayoutError,
    TRACE_RECORD_PROJECT_STAGING,
)
from .bucket_raw_sources import (
    raw_source_snapshot as raw_source_snapshot,
    write_raw_source_artifact as write_raw_source_artifact,
)
from .bucket_trace_records import (
    _normalized_record as _normalized_record,
    _project_store_bucket_record as _project_store_bucket_record,
    _read_jsonl_trace_records as _read_jsonl_trace_records,
    _resolve_trace_record_pointer as _resolve_trace_record_pointer,
    iter_trace_record_objects as iter_trace_record_objects,
    iter_trace_record_pointers as iter_trace_record_pointers,
    project_store_record_from_path as project_store_record_from_path,
    read_bucket_record_for_trace as read_bucket_record_for_trace,
    read_trace_record_object,
    trace_record_object_path as trace_record_object_path,
    trace_record_path,
    trace_record_snapshot as trace_record_snapshot,
    write_trace_record as write_trace_record,
)


# ---------------------------------------------------------------------------
# Plan 080 — Per-trace envelope projector (Writer 2 per plan §9)
# ---------------------------------------------------------------------------


def _events_for_trace_from_iter(
    events_iter: Any, trace_id: str
) -> tuple[list[Any], list[Any]]:
    """Split an event iterable into (trail_events, context_events) for one trace.

    Shared by :func:`project_per_trace_exports` (Git event log) and the
    events-mirror fallback path (issue #28) so both sources filter identically.
    """

    from .context_tree.contract import (
        CONTEXT_COMPACTION_OBSERVED,
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )

    _CONTEXT_EVENT_TYPES = {
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_COMPACTION_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    }

    # plan 090: a v2 anchor-search summary event has top-level trace_id=None (it
    # spans the traces it searched). The shared helper fans it into this trace's
    # companion when one of its per-patch results belongs here.
    from .trails.search_records import summary_search_touches_trace

    trail_events: list[Any] = []
    context_events: list[Any] = []
    for event in sorted(events_iter, key=lambda e: e.event_sequence):
        ev_trace_id = event.trace_id
        if not ev_trace_id and isinstance(event.payload, dict):
            ev_trace_id = event.payload.get("trace_id")
        if ev_trace_id != trace_id and not summary_search_touches_trace(event, trace_id):
            continue
        if event.event_type in _CONTEXT_EVENT_TYPES:
            context_events.append(event)
        else:
            trail_events.append(event)
    return trail_events, context_events


def _context_events_for_trace_readonly(
    project_slug: str, trace_id: str
) -> list[Any]:
    """Return this trace's context events from canonical data WITHOUT writing.

    Issue #55 read-only summary helper. Mirrors :func:`project_per_trace_exports`'s
    event source order — the live Git event log for the matching opted-in
    project first, then the bucket's own events mirror — so the in-memory
    ``node_count`` equals the one the healed companion would carry. Never
    touches disk under the bucket root.

    The mirror fallback condition must match the writer's EXACTLY
    (``not trail_events and not context_events``, never ``not
    context_events`` alone): a trace whose live log holds trail events but
    zero context events gets an EMPTY context companion on heal, so the
    read-only count must not borrow the mirror's context events for it —
    that would break the read-digest == post-heal-digest invariant.
    """

    from .trails import read_events

    repo: Path | None = None
    try:
        for path, slug in _iter_opted_in_projects():
            if slug == project_slug:
                repo = path
                break
    except Exception:
        repo = None

    events_iter: list[Any] = []
    if repo is not None:
        try:
            # Deliberately the FULL read, not the #65 trace-scoped one: this
            # read-only helper is called in per-trace loops (doctor / bucket
            # status over ~1K traces), where the process-level read_events
            # memo amortises ONE full read across every trace. A trace-scoped
            # read per call defeats the memo and turns the loop into
            # O(traces × full-log-walk) — observed as a wedged doctor on a
            # 874K-event repo during #65 verification.
            events_iter = list(read_events(repo, verify=False))
        except Exception:
            events_iter = []
    trail_events, context_events = _events_for_trace_from_iter(
        events_iter, trace_id
    )

    if not trail_events and not context_events:
        try:
            mirror_events = list(read_events_mirror_batches())
        except (FileNotFoundError, ValueError, BucketLayoutError):
            mirror_events = []
        except Exception:
            mirror_events = []
        if mirror_events:
            _, context_events = _events_for_trace_from_iter(mirror_events, trace_id)
    return context_events


# ---------------------------------------------------------------------------
# Epic #169 / #172 GAP 1 — canonical single-source anchor re-derive.
#
# These three helpers are the SINGLE implementation the LIVE per-trace
# projection (:func:`_write_per_trace_envelope`) and the ``--bucket-root`` COPY
# repair (:func:`bucket_maintenance.rederive_bucket_anchors`) both call, so the
# shipped surface and the copy-repair surface are single-source-by-construction
# and can never drift from the canonical ``git_anchor_created`` oracle the
# grader probes read.
# ---------------------------------------------------------------------------


def canonical_anchor_maps(
    slugs: set[str],
) -> tuple[
    dict[str, dict[str, list[tuple]]],
    dict[str, set[str]],
    set[str],
]:
    """Read canonical ``git_anchor_created`` events for the opted-in repos
    backing ``slugs`` and project them into per-trace anchor maps.

    Returns ``(anchors_by_trace_patch, distinct_anchored_by_trace,
    resolved_slugs)`` where:

    * ``anchors_by_trace_patch[trace_id][patch_id]`` is the list of
      ``(event_sequence, commit_hex, evidence_tier, evidence_firmness, path,
      event_time)`` tuples (one per ``git_anchor_created`` event for that patch);
    * ``distinct_anchored_by_trace[trace_id]`` is the set of patch ids the trace
      has ANY anchor event for (the single-source anchored count);
    * ``resolved_slugs`` is the subset of ``slugs`` whose project repo was found
      on this machine (only those traces can be re-derived from canonical data).

    The map is keyed by ``id_from_payload(payload, 'trace_patch')`` — the
    NORMALIZED patch id, byte-identical to the shipped ``Patch.patch_id`` and the
    B3/B4 probe oracle. Keying by the raw ``payload['trace_patch_id']`` instead
    would under-project the fraction of events carrying a ``tracepatch-sha256:``
    prefix (raw != normalized), fabricating false de-attributions.

    The canonical event log is the per-repo Git ref ``refs/opentraces/local/
    events/v1`` — shared across worktrees — so this reads truth, never the
    lagging bucket companions. Read-only over Git.
    """

    from .trails.event_log import read_events_scoped
    from .trails.ids import id_from_payload

    slug_repo = {slug: path for path, slug in _iter_opted_in_projects()}
    anchors_by_trace_patch: dict[str, dict[str, list[tuple]]] = {}
    distinct_anchored_by_trace: dict[str, set[str]] = {}
    resolved_slugs: set[str] = set()
    seen_repos: set[Path] = set()

    for slug in slugs:
        repo = slug_repo.get(slug)
        if repo is None:
            continue
        resolved_slugs.add(slug)
        # One scoped read per distinct repo (a repo may back several slugs).
        if repo in seen_repos:
            continue
        seen_repos.add(repo)
        try:
            events = read_events_scoped(repo, event_types={"git_anchor_created"})
        except Exception:
            continue
        for event in events:
            payload = event.payload or {}
            trace_id = event.trace_id
            patch_id = id_from_payload(payload, "trace_patch")
            commit_hex = (payload.get("commit_id") or {}).get("hex")
            if not (trace_id and patch_id and commit_hex):
                continue
            anchors_by_trace_patch.setdefault(trace_id, {}).setdefault(
                patch_id, []
            ).append(
                (
                    event.event_sequence,
                    commit_hex,
                    payload.get("evidence_tier"),
                    payload.get("evidence_firmness"),
                    payload.get("path"),
                    event.event_time,
                )
            )
            distinct_anchored_by_trace.setdefault(trace_id, set()).add(patch_id)

    return anchors_by_trace_patch, distinct_anchored_by_trace, resolved_slugs


def anchor_map_from_trail_events(
    trail_events: list[Any],
) -> dict[str, list[tuple]]:
    """Build the per-patch anchor map for a SINGLE trace from its own trail
    events (no extra event read).

    Hot per-trace projection path (#172 GAP 1): the trace's ``git_anchor_created``
    events are ALREADY inside the ``trail_events`` computed by
    :func:`_events_for_trace_from_iter` (they are trail-type and already
    trace-filtered), so this reuses them instead of re-scanning the log. Mirrors
    the inner map of :func:`canonical_anchor_maps` exactly — same tuple shape,
    same ``id_from_payload`` key form — so a trace healed on the live path and the
    same trace re-derived by ``--bucket-root`` repair produce identical rows.
    """

    from .trails.ids import id_from_payload

    patch_anchors: dict[str, list[tuple]] = {}
    for event in trail_events:
        if getattr(event, "event_type", None) != "git_anchor_created":
            continue
        payload = getattr(event, "payload", None) or {}
        patch_id = id_from_payload(payload, "trace_patch")
        commit_hex = (payload.get("commit_id") or {}).get("hex")
        if not (patch_id and commit_hex):
            continue
        patch_anchors.setdefault(patch_id, []).append(
            (
                event.event_sequence,
                commit_hex,
                payload.get("evidence_tier"),
                payload.get("evidence_firmness"),
                payload.get("path"),
                event.event_time,
            )
        )
    return patch_anchors


def _rederive_patch_rows(
    base_patches: list[dict[str, Any]],
    patch_anchors: dict[str, list[tuple]],
) -> tuple[list[dict[str, Any]], int, int]:
    """Single-source a trace's patch rows from its canonical per-patch anchor map.

    Epic #169 / #172 GAP 1 — the shared re-derive body used by BOTH the live
    per-trace projection (:func:`_write_per_trace_envelope`) and the
    ``--bucket-root`` COPY repair (:func:`bucket_maintenance.rederive_bucket_anchors`).
    Given a trace's base patch dicts and its ``{patch_id: [(seq, commit_hex,
    tier, firm, path, etime), ...]}`` map:

    * a patch WITH backing events is surfaced as ONE row per distinct anchor
      commit (``found=True``, ``commit_sha`` from the event — file-membership
      correct by construction; the newest commit is the primary row and older
      commits ride ``superseded_by`` newest-first on every row);
    * a patch with NO backing event is DE-ATTRIBUTED (``found=False`` preserving
      any prior ``last_searched_at`` for idempotency, else ``anchor=None``).

    Returns ``(new_patches, n_anchored, n_deattributed)`` where ``n_anchored`` is
    the DISTINCT anchored patch count. Idempotent: an already-expanded surface is
    first collapsed to one base row per patch_id (dropping the expansion-only
    ``superseded_by``) before re-expanding, so rows never compound.
    """

    # Collapse any prior one-row-per-anchor expansion back to one base patch per
    # patch_id (first occurrence wins) BEFORE re-expanding.
    collapsed: list[dict[str, Any]] = []
    seen_patch_ids: set[str] = set()
    for patch in base_patches:
        pid = patch.get("patch_id")
        if pid in seen_patch_ids:
            continue
        seen_patch_ids.add(pid)
        base = dict(patch)
        base.pop("superseded_by", None)
        collapsed.append(base)

    new_patches: list[dict[str, Any]] = []
    n_anchored = 0
    n_deattributed = 0
    for patch in collapsed:
        patch_id = patch.get("patch_id")
        events_for_patch = patch_anchors.get(patch_id)
        if events_for_patch:
            # Distinct anchor commits, oldest-first by event sequence.
            ordered: list[tuple] = []
            seen_commits: set[str] = set()
            for seq, commit_hex, tier, firm, path, etime in sorted(
                events_for_patch
            ):
                if commit_hex in seen_commits:
                    continue
                seen_commits.add(commit_hex)
                ordered.append((commit_hex, tier, firm, path, etime))
            all_commits = [commit_hex for commit_hex, *_ in ordered]
            n_anchored += 1
            # One surface row per distinct anchor commit; every row carries the
            # OTHER commits (newest-first) on superseded_by.
            for commit_hex, tier, firm, path, etime in ordered:
                row = dict(patch)
                row["anchor"] = {
                    "last_searched_at": etime,
                    "found": True,
                    "commit_sha": commit_hex,
                    "path": path or patch.get("file_path"),
                    "blob_sha": None,
                    "git_patch_id": None,
                    "evidence_tier": tier or "exact_range_hash",
                    "evidence_firmness": firm or "firm_observed",
                }
                row["superseded_by"] = [
                    c for c in reversed(all_commits) if c != commit_hex
                ]
                new_patches.append(row)
        else:
            # No backing event -> de-attribute. Preserve a prior search
            # timestamp (idempotent); leave never-anchored patches None.
            prev = patch.get("anchor")
            row = dict(patch)
            if isinstance(prev, dict) and prev.get("found"):
                n_deattributed += 1
            if isinstance(prev, dict) and prev.get("last_searched_at"):
                row["anchor"] = {
                    "last_searched_at": prev.get("last_searched_at"),
                    "found": False,
                    "commit_sha": None,
                    "path": None,
                    "blob_sha": None,
                    "git_patch_id": None,
                    "evidence_tier": None,
                    "evidence_firmness": None,
                }
            else:
                row["anchor"] = None
            row["superseded_by"] = []
            new_patches.append(row)
    return new_patches, n_anchored, n_deattributed


def _write_per_trace_envelope(
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None,
    trail_events: list[Any],
    context_events: list[Any],
    patch_anchors: dict[str, list[tuple]] | None = None,
) -> dict[str, Any]:
    """File-writing tail of :func:`project_per_trace_exports` (issue #31 step B).

    Writes the four companion files + ``trace.json`` in the load-bearing order
    (companions first, ``trace.json`` LAST as the manifest consumer signal).
    All gzipped files use ``mtime=0`` (Resolution H — deterministic). Atomic
    same-bytes writers keep this idempotent.
    """

    trace_dir = traces_v1_dir(project_slug, trace_id)
    trace_dir.mkdir(parents=True, exist_ok=True)

    # 2. trail.jsonl.gz
    trail_lines = [
        _canonical_json(event.model_dump(mode="json")) for event in trail_events
    ]
    trail_body = ("\n".join(trail_lines) + "\n").encode("utf-8") if trail_lines else b""
    _atomic_write_gzip(trace_v1_trail_path(project_slug, trace_id), trail_body)

    # 3. context.jsonl.gz
    context_lines = [
        _canonical_json(event.model_dump(mode="json")) for event in context_events
    ]
    context_body = ("\n".join(context_lines) + "\n").encode("utf-8") if context_lines else b""
    _atomic_write_gzip(trace_v1_context_path(project_slug, trace_id), context_body)

    # 4. sources.jsonl.gz — placeholder empty file when no raw source refs.
    sources_path = trace_v1_sources_path(project_slug, trace_id)
    if not sources_path.exists():
        _atomic_write_gzip(sources_path, b"")

    # 5. trace.json LAST. Look up the canonical record if not passed in.
    if record is None:
        existing = read_trace_record_object(trace_record_path(project_slug, trace_id))
        record = existing.record if existing is not None else None
    if record is not None:
        payload = record.model_dump(mode="json")
        # #172 GAP 1 — single-source the persisted patch anchors from the
        # canonical ``git_anchor_created`` events (``patch_anchors``) instead of
        # echoing the stored ``Patch.anchor`` verbatim. When ``patch_anchors`` is
        # None the project was not resolvable at projection time (cross-machine
        # restore): the on-disk ``record`` IS the already-healed upstream
        # artifact, so it is trusted verbatim — the honest fallback that keeps
        # the cross-machine digest identical. Healing BOTH directions: a phantom
        # over-attributed patch is de-attributed, an under-projected patch is
        # anchored to its backing commit.
        if patch_anchors is not None:
            payload["patches"], _n_anchored, _n_deattr = _rederive_patch_rows(
                payload.get("patches") or [], patch_anchors
            )
        _atomic_write_json(trace_v1_json_path(project_slug, trace_id), payload)

    return {
        "schema_version": BUCKET_PER_TRACE_SCHEMA,
        "project_slug": project_slug,
        "trace_id": trace_id,
        "trace_path": trace_v1_json_path(project_slug, trace_id)
        .relative_to(paths.bucket_dir())
        .as_posix(),
        "trail_event_count": len(trail_events),
        "context_event_count": len(context_events),
        "has_trace_record": record is not None,
        "projected_at": utc_now_str(),
    }


def project_per_trace_exports(
    repo: Path | None = None,
    *,
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None = None,
    events: list[Any] | None = None,
) -> dict[str, Any]:
    """Write the per-trace envelope under ``bucket/traces/v1/<proj>/<trace>/``.

    Plan 080 §9 Writer 2 contract — order is load-bearing for partial-failure
    recovery:

    1. Filter events for this trace from the canonical Git event log.
    2. Write ``trail.jsonl.gz``  (atomic).
    3. Write ``context.jsonl.gz`` (atomic).
    4. Write ``sources.jsonl.gz`` (atomic).
    5. Write ``trace.json``       LAST (the spine; manifest consumer signal).

    The manifest at ``bucket/manifest.json`` is updated separately by
    :func:`bucket_manifest`. Callers that need a consistent snapshot should
    invoke this function for every trace BEFORE calling ``bucket_manifest``.

    Issue #28 — ``repo`` is optional. When ``repo`` is ``None`` (no live
    project on this machine — the cross-machine restore shape) OR the live
    Git event log yields no events for this trace, the bucket's OWN events
    mirror (``bucket/events/v1/``) is used as the event source so the
    envelope is still written from canonical data. This makes the bucket
    self-sufficient: ``bucket repair`` / manifest rebuild no longer drop a
    trace that exists in the bucket but has no live opted-in project.

    All gzipped files use ``mtime=0`` (Resolution H — deterministic).
    """

    from .trails.event_log import read_events_for_trace

    # 1. Filter events by trace_id (sequence order preserved). Prefer the live
    # Git event log; fall back to the bucket's own events mirror when there is
    # no repo, or the live log yields nothing for this trace.
    #
    # #65: trace-scoped read — the previous full ``read_events`` here ran per
    # ingested trace per watcher tick, materialising the whole log (~872K
    # pydantic events) plus the 2GB snapshot pickle. The raw prefilter
    # over-includes; _events_for_trace_from_iter post-filters exactly.
    # Loop callers (bucket repair / manifest rebuild over ~1K traces) MUST
    # pass ``events`` (one shared full read) instead — a trace-scoped walk
    # per loop iteration is O(traces × full-log-walk).
    events_iter: list[Any] = []
    if events is not None:
        events_iter = events
    elif repo is not None:
        try:
            events_iter = read_events_for_trace(repo, trace_id)
        except Exception:
            events_iter = []
    trail_events, context_events = _events_for_trace_from_iter(events_iter, trace_id)

    if not trail_events and not context_events:
        try:
            mirror_events = list(read_events_mirror_batches())
        except (FileNotFoundError, ValueError, BucketLayoutError):
            mirror_events = []
        except Exception:
            mirror_events = []
        if mirror_events:
            trail_events, context_events = _events_for_trace_from_iter(
                mirror_events, trace_id
            )

    # #172 GAP 1 — build the trace's per-patch anchor map from its own trail
    # events (already in hand — no second read) so the persisted trace.json
    # single-sources ``Patch.anchor`` from the canonical ``git_anchor_created``
    # events. Honest fallback: when the project is not resolvable (``repo is
    # None`` — cross-machine restore), pass None so the already-healed on-disk
    # record is written verbatim rather than fabricated / stripped.
    patch_anchors = (
        anchor_map_from_trail_events(trail_events) if repo is not None else None
    )

    return _write_per_trace_envelope(
        project_slug,
        trace_id,
        record,
        trail_events,
        context_events,
        patch_anchors=patch_anchors,
    )


def _row_status_block(record_envelope: dict[str, Any] | None) -> dict[str, Any]:
    """Per-trace security/freshness state for a ``traces[]`` row.

    Plan 087 (size-independent bucket reads) — the row carries the same
    per-trace security facts the O(N) ``trace_records`` scalar block aggregates
    (``syncable`` / ``privacy_off`` / ``security_stale`` / ``written_at``), so
    ``bucket status`` can re-aggregate the counts straight from the persisted
    manifest rows in O(rows-in-memory) instead of re-parsing every TraceRecord
    envelope. Sourced verbatim from the trace-record OBJECT envelope (the same
    authoritative source the scalar block reads), so the row count and the
    scalar count never diverge.

    This block is DELIBERATELY excluded from ``bucket_digest`` (see
    ``_digest_safe_trace_rows`` in bucket_store) — it is a local read-model
    accelerator, not sync-protocol material, so adding it does not perturb the
    cross-machine digest. ``known=False`` when no object envelope was available
    (the count is then treated as not-yet-known and drives a serve-stale stamp).
    """

    security = record_envelope.get("security") if isinstance(record_envelope, dict) else None
    if not isinstance(security, dict):
        return {
            "known": False,
            "syncable": None,
            "privacy_off": None,
            "security_stale": None,
            "written_at": None,
        }
    written_at = record_envelope.get("written_at") if isinstance(record_envelope, dict) else None
    return {
        "known": True,
        "syncable": bool(security.get("syncable")),
        "privacy_off": security.get("privacy_tier") == "off",
        "security_stale": bool(security.get("stale")),
        "written_at": str(written_at) if written_at else None,
    }


def _resolve_record_envelope(
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None,
) -> dict[str, Any] | None:
    """Best-effort AUTHORITATIVE object-store envelope for a row's status block.

    Plan 087 — every ``traces[]`` row-producing path (full-sweep ``iter_traces_v2``,
    the orphan reconcile loop, ``upsert_manifest_trace_row``, the ``ctx info``
    fallback) must derive ``status`` from the SAME source, or two heals of the
    same trace produce different rows and break manifest idempotency. The
    canonical source is the trace-record OBJECT envelope (what the O(N) scalar
    block reads); fall back to a record-derived security state only when the
    object is not on disk yet. Returns ``None`` when neither is available
    (status then reports ``known=False`` → drives a serve-stale stamp).
    """

    try:
        obj = read_trace_record_object(trace_record_path(project_slug, trace_id))
        if obj is not None:
            return obj.envelope
    except Exception:
        pass
    if record is not None:
        try:
            return {"security": bucket_security_state(record)}
        except Exception:
            pass
    return None


def _per_trace_v2_summary(
    project_slug: str,
    trace_id: str,
    record: TraceRecord | None,
    *,
    assume_envelope_present: bool = False,
    record_envelope: dict[str, Any] | None = None,
    distinct_anchored: set[str] | None = None,
    anchors_resolved: bool = False,
) -> dict[str, Any]:
    """Compute the manifest summary block for one per-trace envelope.

    Plan 080 §4 — drives the ``traces[]`` entries in ``manifest.json``.

    Issue #55 — ``assume_envelope_present`` is the read-only reconcile mode.
    When set, the summary reflects the state the per-trace envelope WOULD have
    after a self-heal, WITHOUT touching disk: :func:`_write_per_trace_envelope`
    always writes all three companion files (framed gzip, non-zero size even
    when logically empty), so ``has_trail`` / ``has_context`` / ``has_sources``
    are all ``True`` post-heal, and ``node_count`` is read from the canonical
    context events (read-only) instead of the not-yet-written companion. This
    makes the read-only digest byte-identical to the digest a subsequent
    ``bucket repair`` / ``bucket manifest --heal`` persists.

    Plan 087 — ``record_envelope`` is the trace-record OBJECT envelope (carrying
    the authoritative ``security`` block + ``written_at``). When provided, the
    row gains a digest-excluded ``status`` block so ``bucket status`` can read
    sync/security counts from the manifest rows without an O(N) envelope scan.
    """

    trail_path = trace_v1_trail_path(project_slug, trace_id)
    context_path = trace_v1_context_path(project_slug, trace_id)
    sources_path = trace_v1_sources_path(project_slug, trace_id)
    trace_json = trace_v1_json_path(project_slug, trace_id)

    if assume_envelope_present:
        # Heal always materializes all three framed companions (size > 0), so
        # the post-heal disk view reports True for each regardless of content.
        has_trail = True
        has_context = True
        has_sources = True
    else:
        has_trail = trail_path.exists() and trail_path.stat().st_size > 0
        has_context = context_path.exists() and context_path.stat().st_size > 0
        has_sources = sources_path.exists() and sources_path.stat().st_size > 0

    # Summary counters from TraceRecord when available.
    step_count = 0
    patch_count = 0
    anchored_count = 0
    title: str | None = None
    lifecycle: str | None = None
    capture_methods: list[str] = []
    agent_name: str | None = None
    agent_version: str | None = None
    agent_model: str | None = None
    if record is not None:
        step_count = len(record.steps or [])
        patches = record.patches or []
        patch_count = len(patches)
        # Count DISTINCT anchored patch ids, not raw found stamps (epic #169
        # B-attr): the canonical anchor set is keyed by trace_patch_id, so a
        # patch surfaced once per anchor commit (the re-derived lineage surface
        # for amend / multi-commit patches) must still contribute one to the
        # anchored count. Digest-safe: for every record whose patches carry
        # distinct patch_ids (the normal shape) this equals the prior
        # ``sum(... found)``, so ``bucket_digest`` is unchanged for already-
        # correct traces.
        #
        # #172 GAP 1 — when the caller resolved the trace's project and threaded
        # the canonical ``distinct_anchored`` set (from ``canonical_anchor_maps``),
        # single-source ``anchored_count`` straight from the canonical
        # ``git_anchor_created`` events. This heals a manifest row whose on-disk
        # trace.json still carries phantom over-attributed (or under-projected)
        # anchors, independent of a trace.json re-projection. When the project is
        # NOT resolvable (``anchors_resolved`` False) fall back to the
        # record-derived count — the honest fallback == today's behaviour.
        if anchors_resolved:
            anchored_count = len(distinct_anchored or set())
        else:
            anchored_count = len(
                {
                    p.patch_id
                    for p in patches
                    if p.anchor is not None and p.anchor.found
                }
            )
        title = (record.task.description if record.task else None) or None
        lifecycle = record.lifecycle
        if record.agent is not None:
            agent_name = record.agent.name
            agent_version = record.agent.version
            agent_model = record.agent.model
        # Capture methods from context_tree_summary if present.
        if isinstance(record.context_tree_summary, dict):
            methods = record.context_tree_summary.get("capture_methods")
            if isinstance(methods, list):
                capture_methods = sorted(str(m) for m in methods if m)

    # node_count: count distinct node payloads via context_node_observed
    # events. In the default (materialized) path read the per-trace
    # context.jsonl.gz (cheap — small file). In the read-only #55 path the
    # companion is not on disk yet, so count the SAME events from canonical
    # data (read-only) — what the healed companion would contain.
    from .context_tree.contract import CONTEXT_NODE_OBSERVED

    node_count = 0
    if assume_envelope_present:
        for event in _context_events_for_trace_readonly(project_slug, trace_id):
            if getattr(event, "event_type", None) == CONTEXT_NODE_OBSERVED:
                node_count += 1
    elif has_context:
        try:
            raw = _read_gzip_bytes(context_path).decode("utf-8")
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except (ValueError, json.JSONDecodeError):
                    continue
                if payload.get("event_type") == CONTEXT_NODE_OBSERVED:
                    node_count += 1
        except (OSError, gzip.BadGzipFile):
            pass

    summary = {
        "step_count": step_count,
        "patch_count": patch_count,
        "anchored_count": anchored_count,
        "node_count": node_count,
        "capture_methods": capture_methods,
    }

    digest_material = {
        "project_slug": project_slug,
        "trace_id": trace_id,
        "step_count": step_count,
        "patch_count": patch_count,
        "anchored_count": anchored_count,
        "node_count": node_count,
        "capture_methods": capture_methods,
        "lifecycle": lifecycle,
        "agent_name": agent_name,
        "agent_version": agent_version,
        "agent_model": agent_model,
        "has_trail": has_trail,
        "has_context": has_context,
        "has_sources": has_sources,
    }
    return {
        "project_slug": project_slug,
        "trace_id": trace_id,
        "title": title,
        "agent_name": agent_name,
        "agent_version": agent_version,
        "agent_model": agent_model,
        "trace_path": trace_json.relative_to(paths.bucket_dir()).as_posix(),
        "lifecycle": lifecycle or "provisional",
        "summary": summary,
        "files": {
            "has_trail": has_trail,
            "has_context": has_context,
            "has_sources": has_sources,
        },
        "remote_sync_eligible": False,
        # Plan 087 — digest-EXCLUDED local read-model accelerator (stripped in
        # bucket_store._digest_safe_trace_rows before hashing). NOT in
        # ``digest_material`` above, so the per-row ``digest`` is also unchanged.
        "status": _row_status_block(record_envelope),
        "digest": _digest_payload(digest_material),
    }


def iter_traces_v2(
    project_slug: str | None = None,
    *,
    record_envelopes: dict[tuple[str, str], dict[str, Any]] | None = None,
    anchor_maps: tuple[dict[str, set[str]], set[str]] | None = None,
) -> list[dict[str, Any]]:
    """Yield per-trace summary rows from the v2 layout (plan 080 §4).

    Sorted by ``(project_slug, trace_id)``. Used by the manifest full sweep.

    Plan 087 — ``record_envelopes`` is an OPTIONAL ``{(project_slug, trace_id):
    object_envelope}`` map of already-parsed trace-record object envelopes. The
    full sweep passes the ``iter_trace_record_objects()`` result it ALREADY read
    so each row's ``status`` block is sourced without a second object-store read
    (avoids doubling the sweep's per-trace I/O). When a pair is absent (or no map
    is given), fall back to :func:`_resolve_record_envelope` (one read).

    #172 GAP 1 — ``anchor_maps`` is an OPTIONAL ``(distinct_anchored_by_trace,
    resolved_slugs)`` pair (built ONCE by :func:`canonical_anchor_maps` in the
    manifest sweep — never a per-trace event read). When given, a row whose
    project is resolved single-sources ``anchored_count`` from the canonical
    ``git_anchor_created`` events; an unresolved-project row keeps the
    record-derived count (the honest fallback).
    """

    root = traces_v1_root()
    rows: list[dict[str, Any]] = []
    if not root.exists():
        return rows
    glob_prefix = (
        f"{_path_part(project_slug)}/*" if project_slug else "*/*"
    )
    for trace_json in sorted(root.glob(f"{glob_prefix}/trace.json")):
        proj_dir = trace_json.parent.parent
        proj_slug = unquote(proj_dir.name)
        tid = unquote(trace_json.parent.name)
        record: TraceRecord | None = None
        try:
            raw = json.loads(trace_json.read_text(encoding="utf-8"))
            record = TraceRecord.model_validate(raw)
        except (OSError, ValueError, json.JSONDecodeError, ValidationError):
            record = None
        envelope: dict[str, Any] | None = None
        if record_envelopes is not None:
            envelope = record_envelopes.get((proj_slug, tid))
        if envelope is None:
            envelope = _resolve_record_envelope(proj_slug, tid, record)
        anchors_resolved = anchor_maps is not None and proj_slug in anchor_maps[1]
        distinct_anchored = (
            anchor_maps[0].get(tid) if anchor_maps is not None else None
        )
        rows.append(
            _per_trace_v2_summary(
                proj_slug,
                tid,
                record,
                record_envelope=envelope,
                distinct_anchored=distinct_anchored,
                anchors_resolved=anchors_resolved,
            )
        )
    rows.sort(key=lambda item: (item["project_slug"], item["trace_id"]))
    return rows


def trace_v2_summary_by_id(trace_id: str) -> dict[str, Any] | None:
    """Resolve ONE trace's v2 summary row by ``trace_id`` (no manifest read).

    Issue #54 — backs ``ctx info``'s documented ``trace.json`` fallback. When
    the manifest has no row for ``trace_id`` but the per-trace envelope exists
    on disk, this globs ``traces/v1/*/<trace_id>/trace.json`` and derives the
    summary via :func:`_per_trace_v2_summary`, so the fallback's info block is
    byte-identical to the block ``ctx info`` reads from a manifest row.
    Returns ``None`` when no envelope exists. Bounded to one trace — NOT the
    ``ctx list`` 10k-trace perf gate (a single-id ``ctx info`` lookup).
    """

    root = traces_v1_root()
    if not root.exists():
        return None
    for trace_json in sorted(root.glob(f"*/{_path_part(trace_id)}/trace.json")):
        proj_slug = unquote(trace_json.parent.parent.name)
        record: TraceRecord | None = None
        try:
            raw = json.loads(trace_json.read_text(encoding="utf-8"))
            record = TraceRecord.model_validate(raw)
        except (OSError, ValueError, json.JSONDecodeError, ValidationError):
            record = None
        return _per_trace_v2_summary(
            proj_slug,
            trace_id,
            record,
            record_envelope=_resolve_record_envelope(proj_slug, trace_id, record),
        )
    return None


def _iter_opted_in_projects() -> list[tuple[Path, str]]:
    """Return ``(project_path, project_slug)`` pairs for every opted-in project.

    Helper used by :func:`bucket_repair` / :func:`rebuild_bucket_trail` /
    :func:`rebuild_bucket_traces` to walk the full registry deterministically.
    Skips projects whose on-disk path is missing.
    """

    try:
        from .config import get_project_dir, load_config, opted_in_projects
    except Exception:
        return []
    try:
        cfg = load_config()
    except Exception:
        return []
    out: list[tuple[Path, str]] = []
    for raw_path in opted_in_projects(cfg):
        project_path = Path(raw_path)
        if not project_path.exists():
            continue
        try:
            slug = get_project_dir(project_path).name
        except Exception:
            continue
        out.append((project_path, slug))
    # Deterministic ordering: by slug (matches downstream sort orders).
    out.sort(key=lambda item: item[1])
    return out


def _events_for_export_loop(repo: Path) -> list[Any]:
    """One full event read shared across a per-trace export loop (#65).

    ``read_events`` memoises per (repo, head), so repair/rebuild loops pay one
    full read instead of one per trace. Returns [] on any failure — the
    per-trace export then falls back to its own (mirror) sources.
    """

    try:
        from .trails import read_events
    except Exception:
        return []
    try:
        return list(read_events(repo, verify=False))
    except Exception:
        return []


def _trace_ids_for_project(repo: Path) -> list[str]:
    """Return distinct ``trace_id`` values present in ``repo``'s event log.

    Pulled from the canonical Git event log (the source of truth). Returns a
    sorted, deduplicated list so the projection order is deterministic.

    plan 090: the v2 anchor-search summary event carries top-level trace_id=None,
    so it contributes no id here. That is safe and intentional: a search only
    ever runs for an existing patch, so every trace_id inside a summary's
    results[] necessarily also appears on that patch's ``trace_patch_created``
    event, which IS counted. No trace is ever missed by skipping the summary.
    """

    try:
        from .trails import read_events
    except Exception:
        return []
    try:
        events = read_events(repo, verify=False)
    except Exception:
        return []
    seen: set[str] = set()
    for event in events:
        tid = event.trace_id
        if not tid and isinstance(event.payload, dict):
            tid = event.payload.get("trace_id")
        if tid:
            seen.add(str(tid))
    return sorted(seen)


def _is_legacy_read_in_place_mirror(project_slug: str, trace_id: str) -> bool:
    """True when ``trace_id``'s object-store entry is a plan-085-S5 legacy
    read-in-place mirror, i.e. it must NOT be auto-adopted into a per-trace
    v2 envelope / ``manifest.traces[]``.

    Plan 085 S5 (read-in-place): legacy ``traces/*.jsonl`` records are
    mirrored into the TraceRecord object store as a query substrate
    (:func:`sync_trace_records_from_local_stores`, run by ``trace index
    rebuild``), but they must never be auto-adopted into per-trace v2
    envelopes / ``manifest.traces[]``. Two facts must hold for a pair to be
    classified legacy:

    1. **The in-place JSONL still exists** (``~/.opentraces/projects/<slug>/
       traces/<trace_id>.jsonl`` or staging). Both the 0.3.3 and 0.4 writers
       name the file ``<trace_id>.jsonl``. When it is gone the trace is a
       restored bucket (cross-machine pull) and the auto-materialization
       passes (#28 / #31) must heal it regardless of provenance.
    2. **No capture-time raw-source link exists** for the pair
       (``bucket/objects/raw/v1/sources/<slug>/<trace_id>.json``). The link
       is written exclusively by capture-time ingest
       (:func:`write_raw_source_artifact`'s sole caller is
       ``core/ingest.py``, on BOTH the full and the ``--trace-record-only``
       paths), never by the legacy mirror bridge — and 0.3.3 had no bucket
       at all. It is therefore the per-trace provenance discriminator
       (PR #63): a record-only staged trace deliberately defers
       ``project_per_trace_exports`` at ingest ("projection deferred") and
       MUST be materialized by manifest/repair later, while a true legacy
       mirror is read in place forever. Adoption of a legacy trace remains
       reserved for genuine re-capture through ingest, which writes the
       link.
    """

    if project_slug == TRACE_RECORD_PROJECT_STAGING:
        staging_root = getattr(paths, "STAGING_DIR", None)
        if not staging_root:
            return False
        in_place = (Path(staging_root) / f"{trace_id}.jsonl").exists()
    else:
        in_place = (
            paths.PROJECTS_DIR / project_slug / "traces" / f"{trace_id}.jsonl"
        ).exists()
    if not in_place:
        return False
    capture_link = (
        raw_sources_root()
        / "sources"
        / _path_part(project_slug)
        / f"{_path_part(trace_id)}.json"
    )
    return not capture_link.exists()
