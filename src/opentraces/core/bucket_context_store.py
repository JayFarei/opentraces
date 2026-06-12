"""Bucket Context Tree projection cluster (plan 079 / 080).

Extracted verbatim from opentraces.core.bucket_store. The facade module
(bucket_store.py) re-exports every public symbol from here so all existing
call sites continue to work unchanged.

Public functions:
    project_context_tree_to_bucket
    read_context_tree_head
    iter_context_tree_traces
    verify_context_tree_layer_refs
    compute_context_tree_status
    context_tree_snapshot

Private helpers (also re-exported from the facade for internal callers):
    _context_blob_scope
    _build_context_layer_blob
    _build_context_head
    _iter_context_tree_head_payloads
    _head_payload_to_row
    _layer_id_refs_for_trace
    _layer_id_refs_from_events_mirror
    _iter_context_blob_files
    _hash_for_blob_path
    _blob_content_matches_path
"""

from __future__ import annotations

import gzip
import json
import zlib
from pathlib import Path
from typing import Any, Iterator

from opentraces.core._time import utc_now_str

from .bucket_layout import (
    _path_part,
    blobs_v1_context_path,
    blobs_v1_root,
    context_layer_blob_path,
    context_tree_dir,
    context_tree_head_path,
    context_tree_nodes_path,
    context_tree_reconciliation_path,
    contexts_root,
    events_v1_batches_dir,
    trace_v1_context_path,
)
from ._bucket_io import (
    _atomic_write_gzip,
    _atomic_write_json,
    _atomic_write_text,
    _canonical_json,
    _digest_payload,
    _read_gzip_bytes,
)

# Schema constants — string literals that must stay byte-identical with the
# declarations in bucket_store.py (the single public source of truth for
# downstream consumers). The test suite enforces equivalence via manifest
# digests.
CONTEXT_TREE_BUCKET_SCHEMA = "opentraces.bucket.context_tree.v1"
CONTEXT_LAYER_BLOB_SCHEMA = "opentraces.bucket.context_layer_blob.v1"
CONTEXT_TREE_SNAPSHOT_SCHEMA = "opentraces.bucket.context_trees_snapshot.v1"
CONTEXT_TREE_REMOTE_SYNC_BLOCKER = "context_tree_unfiltered_layers"

# Branch types ordered for deterministic on-disk nodes.jsonl sort.
_BRANCH_TYPE_ORDINAL: dict[str, int] = {
    "root": 0,
    "linear": 1,
    "compaction_fork": 2,
    "subagent_fork": 3,
    "rewind_branch": 4,
    "manual_branch": 5,
}


def _context_blob_scope() -> str:
    """Resolve the active layer blob scope from config, with a safe default."""

    try:
        from .config import load_config

        return load_config().bucket.contexts.layer_blob_scope
    except Exception:
        return "project"


def _build_context_layer_blob(layer: Any) -> dict[str, Any]:
    """Wrap a ContextLayer into the bucket-shaped blob envelope.

    Plan 080 Resolution H: ``written_at`` is dropped from the blob payload —
    layer blobs are pure content (``{layer_id, layer_type, capture_method,
    completeness, content}``). Provenance lives in the event log.
    """

    return {
        "schema_version": CONTEXT_LAYER_BLOB_SCHEMA,
        "layer_id": layer.layer_id,
        "layer_type": layer.layer_type,
        "capture_method": layer.capture_method,
        "completeness": layer.completeness,
        "content": layer.content,
    }


def _build_context_head(
    *,
    project_slug: str,
    trace_id: str,
    node_ids: list[str],
    layer_refs: list[str],
    capture_methods: list[str],
    active_leaf_node_id: str | None,
    subagent_session_ids: list[str],
    capture_limitations: list[str],
    blob_scope: str,
    event_log_head: str | None,
    events_processed_through_sequence: int,
) -> dict[str, Any]:
    """Assemble the head.json envelope and compute its self-digest."""

    payload = {
        "schema_version": CONTEXT_TREE_BUCKET_SCHEMA,
        "project_slug": project_slug,
        "trace_id": trace_id,
        "node_count": len(node_ids),
        "layer_count": len(layer_refs),
        "layer_refs": layer_refs,
        "capture_methods": capture_methods,
        "active_leaf_node_id": active_leaf_node_id,
        "subagent_session_ids": subagent_session_ids,
        "capture_limitations": capture_limitations,
        "remote_sync": {
            "eligible": False,
            "scope": "private_bucket_only",
            "publishable": False,
            "blocked_reasons": [CONTEXT_TREE_REMOTE_SYNC_BLOCKER],
        },
        "blob_scope": blob_scope,
        "last_projection_at": utc_now_str(),
        "event_log_head": event_log_head,
        "events_processed_through_sequence": events_processed_through_sequence,
    }
    payload["digest"] = _digest_payload(payload)
    return payload


def project_context_tree_to_bucket(
    repo: Path,
    *,
    project_slug: str,
    trace_id: str | None = None,
    events: list | None = None,
    seq_suffix: list | None = None,
) -> dict[str, Any]:
    """Project Context Tree events from the canonical event log into the bucket.

    Reads ``read_events(repo)`` through ``build_context_tree_projection``.
    For each trace (or just ``trace_id`` if given) writes layer blobs,
    nodes.jsonl, optional reconciliation.json, then atomically writes
    head.json LAST so a partial run never points at missing blobs.

    See plan 079 §"Writer" for the full contract.

    #65 bounded path: when ``events`` is supplied (a SCOPED full-history read
    of the four ``context_*`` types — small by construction), it feeds both
    the projection build and the reconciled-payload scan, and the two full
    ``read_events`` walks this function previously performed per changed
    watcher tick (~872K pydantic events + the 2GB snapshot pickle, observed
    live) never happen. Per-trace ``events_processed_through_sequence`` is
    then maintained INCREMENTALLY: prior head value ⊔ max sequence seen in
    ``events`` and ``seq_suffix`` (the all-type suffix since the daemon's
    projection watermark). That value may transiently under-report trail-only
    activity for traces whose context didn't change — the bucket
    repair/status verbs keep the exact full-fidelity accounting. Legacy
    behavior (``events=None``) is unchanged.
    """

    from .context_tree.contract import CONTEXT_TREE_RECONCILED
    from .context_tree.query import build_context_tree_projection
    from .trails import event_log_status, read_events

    # When a single trace is targeted (the per-session ingest path), build
    # only that trace's projection instead of re-walking the whole history.
    projection = build_context_tree_projection(
        repo, trace_id=trace_id, events=events
    )
    status = event_log_status(repo)
    event_log_head = status.get("head")
    blob_scope = _context_blob_scope()

    if trace_id is not None:
        target_trace_ids = [trace_id] if trace_id in projection.nodes_by_trace else []
    else:
        target_trace_ids = sorted(projection.nodes_by_trace.keys())

    # Precompute reconciled payload + max event sequence per trace.
    # NOTE: when ``events`` is None, ``build_context_tree_projection`` above
    # already called ``read_events(repo)`` with the default ``verify=True``.
    # We share that cache key here to avoid a second full event-log walk per
    # projection.
    reconciled_by_trace: dict[str, dict[str, Any]] = {}
    max_seq_by_trace: dict[str, int] = {tid: 0 for tid in target_trace_ids}
    target_set = set(target_trace_ids)
    if events is not None:
        for tid in target_trace_ids:
            prior = read_context_tree_head(project_slug, tid) or {}
            max_seq_by_trace[tid] = int(
                prior.get("events_processed_through_sequence") or 0
            )
        scan_sources: list = [events]
        if seq_suffix:
            scan_sources.append(seq_suffix)
        for source in scan_sources:
            for event in source:
                ev_trace_id = event.trace_id or (
                    event.payload.get("trace_id")
                    if isinstance(event.payload, dict) else None
                )
                if not ev_trace_id or ev_trace_id not in target_set:
                    continue
                if event.event_sequence > max_seq_by_trace.get(ev_trace_id, 0):
                    max_seq_by_trace[ev_trace_id] = event.event_sequence
                if event.event_type == CONTEXT_TREE_RECONCILED:
                    reconciled_by_trace[ev_trace_id] = dict(event.payload or {})
    else:
        for event in read_events(repo):
            ev_trace_id = event.trace_id or (event.payload.get("trace_id") if isinstance(event.payload, dict) else None)
            if not ev_trace_id or ev_trace_id not in target_set:
                continue
            if event.event_sequence > max_seq_by_trace.get(ev_trace_id, 0):
                max_seq_by_trace[ev_trace_id] = event.event_sequence
            if event.event_type == CONTEXT_TREE_RECONCILED:
                reconciled_by_trace[ev_trace_id] = dict(event.payload or {})

    blobs_written = 0
    blobs_unchanged = 0
    heads_written = 0
    heads_unchanged = 0

    for tid in target_trace_ids:
        node_ids = list(projection.nodes_by_trace.get(tid, []))
        nodes = [projection.nodes_by_id[nid] for nid in node_ids if nid in projection.nodes_by_id]
        if not nodes:
            continue

        # Collect referenced layer ids (sorted dedup).
        ref_set: set[str] = set()
        for n in nodes:
            ref_set.update({
                n.system_layer_id,
                n.messages_layer_id,
                n.tool_registry_layer_id,
                n.runtime_state_layer_id,
            })
        layer_refs = sorted(ref_set)

        # Per-layer capture_methods (sorted dedup across all referenced layers).
        capture_methods_set: set[str] = set()
        for lid in layer_refs:
            layer = projection.layers_by_id.get(lid)
            if layer is None:
                continue
            capture_methods_set.add(layer.capture_method)
        capture_methods = sorted(capture_methods_set)

        # 1. Write layer blobs first (content-addressed; no-op when present).
        for lid in layer_refs:
            layer = projection.layers_by_id.get(lid)
            if layer is None:
                continue
            blob_path = context_layer_blob_path(project_slug, lid, scope=blob_scope)
            blob_payload = _build_context_layer_blob(layer)
            blob_bytes = _canonical_json(blob_payload, pretty=True).encode("utf-8")
            if blob_path.exists():
                try:
                    existing = json.loads(_read_gzip_bytes(blob_path).decode("utf-8"))
                except (OSError, ValueError, gzip.BadGzipFile, json.JSONDecodeError):
                    existing = None
                if isinstance(existing, dict) and existing == blob_payload:
                    blobs_unchanged += 1
                    continue
            _atomic_write_gzip(blob_path, blob_bytes)
            blobs_written += 1

        # 2. nodes.jsonl — full deterministic rewrite, sorted.
        sorted_nodes = sorted(
            nodes,
            key=lambda n: (
                _BRANCH_TYPE_ORDINAL.get(n.branch_type, 99),
                n.step_index if n.step_index is not None else 1_000_000_000,
                n.node_id,
            ),
        )
        nodes_jsonl_text = "\n".join(
            _canonical_json(n.model_dump(mode="json")) for n in sorted_nodes
        )
        if nodes_jsonl_text:
            nodes_jsonl_text += "\n"
        _atomic_write_text(
            context_tree_nodes_path(project_slug, tid),
            nodes_jsonl_text,
        )

        # 3. reconciliation.json (only when a reconciled event exists).
        reconciled = reconciled_by_trace.get(tid)
        recon_path = context_tree_reconciliation_path(project_slug, tid)
        if reconciled is not None:
            reconciliation_payload = {
                "schema_version": CONTEXT_TREE_BUCKET_SCHEMA,
                "trace_id": tid,
                **reconciled,
            }
            _atomic_write_json(recon_path, reconciliation_payload)

        # 4. Determine head + active leaf metadata for the head.json envelope.
        active_leaf_uuid = projection.active_leaves_by_trace.get(tid)
        active_leaf_node_id: str | None = None
        if active_leaf_uuid:
            leaf_node = projection.node_for_transcript_uuid(tid, active_leaf_uuid)
            if leaf_node is not None:
                active_leaf_node_id = leaf_node.node_id
        if active_leaf_node_id is None and reconciled and reconciled.get("active_path_leaf_id"):
            active_leaf_node_id = reconciled.get("active_path_leaf_id")

        subagent_session_ids = sorted(set(
            projection.subagent_session_ids_by_trace.get(tid, []) or []
        ))
        capture_limitations = sorted(set(
            projection.capture_limitations_by_trace.get(tid, []) or []
        ))

        head_payload = _build_context_head(
            project_slug=project_slug,
            trace_id=tid,
            node_ids=node_ids,
            layer_refs=layer_refs,
            capture_methods=capture_methods,
            active_leaf_node_id=active_leaf_node_id,
            subagent_session_ids=subagent_session_ids,
            capture_limitations=capture_limitations,
            blob_scope=blob_scope,
            event_log_head=event_log_head,
            events_processed_through_sequence=int(max_seq_by_trace.get(tid, 0)),
        )

        # 5. head.json LAST. Detect byte-identical (ignoring volatile fields).
        head_path = context_tree_head_path(project_slug, tid)
        existing_head = read_context_tree_head(project_slug, tid)
        unchanged = False
        if existing_head is not None:
            volatile = {"last_projection_at", "digest", "event_log_head"}
            stable_existing = {k: v for k, v in existing_head.items() if k not in volatile}
            stable_new = {k: v for k, v in head_payload.items() if k not in volatile}
            unchanged = stable_existing == stable_new
        if unchanged:
            heads_unchanged += 1
        else:
            _atomic_write_json(head_path, head_payload)
            heads_written += 1

    return {
        "schema_version": CONTEXT_TREE_BUCKET_SCHEMA,
        "substrate": "context-tree",
        "traces_projected": len(target_trace_ids),
        "blobs_written": blobs_written,
        "blobs_unchanged": blobs_unchanged,
        "heads_written": heads_written,
        "heads_unchanged": heads_unchanged,
        "idempotent_noop": blobs_written == 0 and heads_written == 0,
        "event_log_head": event_log_head,
        "blob_scope": blob_scope,
        "projected_at": utc_now_str(),
    }


def read_context_tree_head(project_slug: str, trace_id: str) -> dict[str, Any] | None:
    """Return the per-trace head envelope (raw dict), or None if missing."""

    path = context_tree_head_path(project_slug, trace_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != CONTEXT_TREE_BUCKET_SCHEMA:
        return None
    return payload


def _iter_context_tree_head_payloads(
    project_slug: str | None = None,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Walk every projected head.json once; return ``(slug, tid, payload)``.

    Single source of disk I/O for ``iter_context_tree_traces``,
    ``verify_context_tree_layer_refs``, and ``context_tree_snapshot`` so a
    status / manifest pass reads each head exactly once. Sorted by
    ``(project_slug, trace_id)``.
    """

    root = contexts_root()
    out: list[tuple[str, str, dict[str, Any]]] = []
    if not root.exists():
        return out
    project_pattern = _path_part(project_slug) if project_slug else "*"
    for head_path in sorted(root.glob(f"{project_pattern}/*/head.json")):
        try:
            payload = json.loads(head_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema_version") != CONTEXT_TREE_BUCKET_SCHEMA:
            continue
        proj_slug = str(payload.get("project_slug") or head_path.parent.parent.name)
        tid = str(payload.get("trace_id") or head_path.parent.name)
        if project_slug is not None and proj_slug != project_slug:
            continue
        out.append((proj_slug, tid, payload))
    out.sort(key=lambda item: (item[0], item[1]))
    return out


def _head_payload_to_row(
    proj_slug: str, tid: str, payload: dict[str, Any]
) -> dict[str, Any]:
    sync = payload.get("remote_sync") or {}
    return {
        "project_slug": proj_slug,
        "trace_id": tid,
        "node_count": int(payload.get("node_count") or 0),
        "layer_count": int(payload.get("layer_count") or 0),
        "capture_methods": list(payload.get("capture_methods") or []),
        "blob_scope": payload.get("blob_scope") or "project",
        "last_projection_at": payload.get("last_projection_at"),
        "remote_sync_eligible": bool(sync.get("eligible")),
        "events_processed_through_sequence": int(
            payload.get("events_processed_through_sequence") or 0
        ),
    }


def iter_context_tree_traces(
    project_slug: str | None = None,
) -> list[dict[str, Any]]:
    """Return one summary row per projected trace.

    Sorted deterministically by ``(project_slug, trace_id)``.
    """

    return [
        _head_payload_to_row(slug, tid, payload)
        for slug, tid, payload in _iter_context_tree_head_payloads(project_slug)
    ]


def verify_context_tree_layer_refs(
    project_slug: str | None = None,
    *,
    _heads: list[tuple[str, str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Walk every projected head and confirm referenced layer_ids resolve to blobs.

    ``_heads`` is an internal optimisation knob used by aggregators that
    have already walked the heads (e.g. ``compute_context_tree_status``);
    when supplied the helper skips its own disk walk. Pass-through only,
    not part of the public contract.
    """

    from . import paths

    if _heads is None:
        head_triples = _iter_context_tree_head_payloads(project_slug=project_slug)
    else:
        if project_slug is None:
            head_triples = list(_heads)
        else:
            head_triples = [item for item in _heads if item[0] == project_slug]
    dangling: list[dict[str, Any]] = []
    for slug, tid, head in head_triples:
        scope = head.get("blob_scope") or "project"
        for layer_id in head.get("layer_refs") or []:
            expected_path = context_layer_blob_path(
                slug, str(layer_id), scope=str(scope)
            )
            if not expected_path.exists():
                try:
                    rel = expected_path.relative_to(paths.bucket_dir()).as_posix()
                except ValueError:
                    rel = str(expected_path)
                dangling.append(
                    {
                        "project_slug": slug,
                        "trace_id": tid,
                        "missing_layer_id": layer_id,
                        "expected_blob_path": rel,
                    }
                )
    return {
        "state": "ok" if not dangling else "dangling",
        "trace_count": len(head_triples),
        "dangling_layer_refs_count": len(dangling),
        "dangling": dangling,
    }


def compute_context_tree_status() -> dict[str, Any]:
    """Aggregate snapshot + freshness + integrity for plan 079 status surfaces.

    Single source of truth shared by ``opentraces bucket context-tree status``
    and the doctor ``context_tree`` panel so the two surfaces never drift.
    Pure read-only aggregator over ``context_tree_snapshot()`` (dedup
    metrics), per-project event log status (catch-up metrics), and
    ``verify_context_tree_layer_refs`` (integrity).
    """

    from .config import get_project_dir, load_config, opted_in_projects
    from .trails import event_log_status, read_events

    # Walk every head.json exactly once and feed the three aggregators
    # via internal ``_heads`` kwargs so they never re-read from disk.
    # Without this, an interactive ``bucket context-tree status`` on a
    # registry with N traces does 3*N head reads.
    head_triples = _iter_context_tree_head_payloads()
    snapshot = context_tree_snapshot(include_objects=False, _heads=head_triples)
    verify = verify_context_tree_layer_refs(_heads=head_triples)

    # Aggregate per-project max processed sequence + latest projection
    # timestamp in the same pass; rows list is unused beyond this loop.
    max_processed_by_project: dict[str, int] = {}
    last_projection_at: str | None = None
    for slug, _tid, payload in head_triples:
        processed = int(payload.get("events_processed_through_sequence") or 0)
        if processed > max_processed_by_project.get(slug, 0):
            max_processed_by_project[slug] = processed
        ts = payload.get("last_projection_at")
        if ts and (last_projection_at is None or ts > last_projection_at):
            last_projection_at = ts

    cfg = load_config()
    project_paths = [Path(path) for path in opted_in_projects(cfg)]
    events_behind = 0
    oldest_unprojected_event_time: str | None = None
    event_log_head: str | None = None
    for project_path in project_paths:
        if not project_path.exists():
            continue
        try:
            project_slug = get_project_dir(project_path).name
            status = event_log_status(project_path)
        except Exception:
            continue
        if status.get("state") in {"missing", "error"}:
            continue
        current_count = int(status.get("event_count", 0) or 0)
        if event_log_head is None:
            event_log_head = status.get("head")
        max_processed = max_processed_by_project.get(project_slug, 0)
        delta = max(0, current_count - max_processed)
        events_behind += delta
        if delta > 0:
            try:
                events = read_events(project_path, verify=False)
            except Exception:
                events = []
            for event in sorted(events, key=lambda e: e.event_sequence):
                if event.event_sequence <= max_processed:
                    continue
                ts = getattr(event, "created_at", None)
                if ts is not None:
                    ts_str = ts if isinstance(ts, str) else ts.isoformat()
                    if (
                        oldest_unprojected_event_time is None
                        or ts_str < oldest_unprojected_event_time
                    ):
                        oldest_unprojected_event_time = ts_str
                break

    return {
        "schema_version": snapshot.get("schema_version"),
        "root": snapshot.get("root"),
        "trace_count": snapshot.get("trace_count", 0),
        "unique_layer_blob_count": snapshot.get("unique_layer_blob_count", 0),
        "sum_layer_refs_count": snapshot.get("sum_layer_refs_count", 0),
        "dedup_hits": snapshot.get("dedup_hits", 0),
        "global_shared_blob_count": snapshot.get("global_shared_blob_count", 0),
        "digest": snapshot.get("digest"),
        "last_projection_at": last_projection_at,
        "events_since_last_projection": events_behind,
        "oldest_unprojected_event_time": oldest_unprojected_event_time,
        "event_log_head": event_log_head,
        "dangling_layer_refs_count": int(
            verify.get("dangling_layer_refs_count", 0) or 0
        ),
    }


def context_tree_snapshot(
    *,
    include_objects: bool = False,
    _heads: list[tuple[str, str, dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Deterministic snapshot for bucket manifest digest contribution.

    ``_heads`` is an internal optimisation knob — see
    ``verify_context_tree_layer_refs`` for the same pattern.
    """

    root = contexts_root()
    head_triples = (
        _iter_context_tree_head_payloads() if _heads is None else list(_heads)
    )
    head_payloads: list[dict[str, Any]] = [payload for _, _, payload in head_triples]
    unique_blob_ids: set[str] = set()
    sum_layer_refs = 0
    for head in head_payloads:
        for lid in head.get("layer_refs") or []:
            unique_blob_ids.add(str(lid))
        sum_layer_refs += int(head.get("layer_count") or 0)

    # Count blobs physically present under _shared/ (global scope).
    # Plan 080: global-scope blobs now live at
    # ``bucket/blobs/v1/_shared/context/<hh>/<hash>.json.gz``.
    global_shared_blob_count = 0
    shared_dir = blobs_v1_root() / "_shared" / "context"
    if shared_dir.exists():
        for blob_path in shared_dir.rglob("*.json.gz"):
            if blob_path.is_file():
                global_shared_blob_count += 1

    digest_material = {
        "schema_version": CONTEXT_TREE_SNAPSHOT_SCHEMA,
        "trace_count": len(head_payloads),
        "heads": sorted(
            [
                {
                    "project_slug": h.get("project_slug"),
                    "trace_id": h.get("trace_id"),
                    "digest": h.get("digest"),
                }
                for h in head_payloads
            ],
            key=lambda item: (str(item.get("project_slug")), str(item.get("trace_id"))),
        ),
        "unique_layer_blob_ids": sorted(unique_blob_ids),
        "global_shared_blob_count": global_shared_blob_count,
    }
    snapshot: dict[str, Any] = {
        "schema_version": CONTEXT_TREE_SNAPSHOT_SCHEMA,
        "root": str(root),
        "trace_count": len(head_payloads),
        "unique_layer_blob_count": len(unique_blob_ids),
        "sum_layer_refs_count": sum_layer_refs,
        "dedup_hits": sum_layer_refs - len(unique_blob_ids),
        "global_shared_blob_count": global_shared_blob_count,
        "digest": _digest_payload(digest_material),
    }
    if include_objects:
        snapshot["objects"] = head_payloads
    return snapshot


# ---------------------------------------------------------------------------
# Private helpers used by bucket_verify / bucket_prune in bucket_store.py
# ---------------------------------------------------------------------------


def _layer_id_refs_for_trace(
    project_slug: str, trace_id: str
) -> set[str]:
    """Collect every layer_id referenced by one per-trace ``context.jsonl.gz``.

    A layer_id appears either at the top of a ``context_layer_captured``
    payload (``layer_id`` / ``payload.layer_id``) or as one of the four
    ``*_layer_id`` slots on a ``context_node_observed`` payload. Both shapes
    are walked; missing files yield an empty set.
    """

    ctx_path = trace_v1_context_path(project_slug, trace_id)
    if not ctx_path.exists():
        return set()
    try:
        raw = _read_gzip_bytes(ctx_path).decode("utf-8")
    except (OSError, gzip.BadGzipFile):
        return set()
    refs: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        payload = obj.get("payload") if isinstance(obj, dict) else None
        if not isinstance(payload, dict):
            continue
        lid = payload.get("layer_id")
        if isinstance(lid, str) and lid:
            refs.add(lid)
        for key in (
            "system_layer_id",
            "messages_layer_id",
            "tool_registry_layer_id",
            "runtime_state_layer_id",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value:
                refs.add(value)
    return refs


def _layer_id_refs_from_events_mirror() -> set[str]:
    """Collect layer_ids referenced by the events mirror (``bucket/events/v1/``).

    Walks ``bucket/events/v1/batches/*.jsonl.gz`` directly so we never touch
    the canonical Git ref. Empty if the mirror does not exist.
    """

    refs: set[str] = set()
    batches_dir = events_v1_batches_dir()
    if not batches_dir.exists():
        return refs
    for batch_path in sorted(batches_dir.glob("*.jsonl.gz")):
        try:
            raw = _read_gzip_bytes(batch_path).decode("utf-8")
        except (OSError, gzip.BadGzipFile):
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, json.JSONDecodeError):
                continue
            payload = obj.get("payload") if isinstance(obj, dict) else None
            if not isinstance(payload, dict):
                continue
            lid = payload.get("layer_id")
            if isinstance(lid, str) and lid:
                refs.add(lid)
            for key in (
                "system_layer_id",
                "messages_layer_id",
                "tool_registry_layer_id",
                "runtime_state_layer_id",
            ):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    refs.add(value)
    return refs


def _iter_context_blob_files() -> Iterator[Path]:
    """Yield every existing context blob file under ``bucket/blobs/v1/.../context/``.

    Includes both per-project (``<project>/context/<hh>/<hash>.json.gz``)
    and globally-shared (``_shared/context/...``) layouts. Sorted for
    deterministic prune output.
    """

    root = blobs_v1_root()
    if not root.exists():
        return
    for blob in sorted(root.glob("*/context/*/*.json.gz")):
        if blob.is_file():
            yield blob


def _hash_for_blob_path(path: Path) -> str | None:
    """Return the ``sha256:<hex>`` value encoded in ``<hh>/<hash>.json.gz``.

    Returns ``None`` if the path does not match the expected shape — the
    caller treats those as orphans worth a log line but never deletes them.
    """

    if path.suffix != ".gz":
        return None
    stem = path.stem  # "<hash>.json" once gzip suffix is dropped
    if not stem.endswith(".json"):
        return None
    digest_hex = stem[: -len(".json")]
    if not digest_hex or len(digest_hex) < 4:
        return None
    return f"sha256:{digest_hex}"


def _blob_content_matches_path(path: Path) -> tuple[bool, str | None]:
    """Recompute the content hash of one context blob and compare to its path.

    The canonical form is the JSON the writer fed through
    :func:`_canonical_json` in :func:`project_context_tree_to_bucket`: it
    contains ``layer_id`` / ``layer_type`` / ``capture_method`` /
    ``completeness`` / ``content`` (plus the ``schema_version`` envelope).
    The recomputed hash matches the path-encoded hash IFF the writer stored
    a hash of the layer content matching the file location.

    For plan 080's verify primitive we treat the *layer_id* itself as the
    truth: blobs are content-addressed by the layer_id encoded in the path,
    and the blob payload must echo that same layer_id (otherwise it is
    corrupted). This avoids re-deriving the layer_id hashing rules (those
    live in :mod:`context_tree.models`) and instead asserts: "the path's
    hash equals the blob payload's layer_id".

    Returns ``(ok, detail)`` where ``ok`` is True on a clean match and
    ``detail`` is a short failure reason when False.
    """

    expected = _hash_for_blob_path(path)
    if expected is None:
        return False, "unexpected filename shape"
    try:
        raw = _read_gzip_bytes(path).decode("utf-8")
        payload = json.loads(raw)
    except (OSError, EOFError, zlib.error, ValueError, json.JSONDecodeError) as exc:
        return False, f"unreadable blob: {exc}"
    if not isinstance(payload, dict):
        return False, "blob payload is not an object"
    lid = payload.get("layer_id")
    if not isinstance(lid, str) or not lid:
        return False, "blob missing layer_id"
    if lid != expected:
        return False, f"layer_id {lid!r} does not match path hash {expected!r}"
    return True, None
