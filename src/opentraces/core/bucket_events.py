"""Bucket trail/events-mirror cluster (plan 080 §4).

Extracted verbatim from opentraces.core.bucket_store. The facade module
(bucket_store.py) re-exports every public symbol from here so all existing
call sites continue to work unchanged.

Functions:
    sync_events_mirror
    sync_trail_events_from_repo  (compat alias)
    read_events_mirror_batches
    read_trail_event_export      (compat alias)
    restore_trail_events_to_repo
    trail_event_snapshot
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Iterator

from opentraces.core._time import utc_now_str

from .bucket_layout import (
    _path_part,
    events_v1_batches_dir,
    events_v1_index_path,
    events_v1_root,
)
from ._bucket_io import (
    _atomic_write_bytes,
    _atomic_write_json,
    _canonical_json,
    _read_gzip_bytes,
    _gzip_deterministic,
)

# Schema constants (imported from facade to keep a single source of truth).
# These are re-declared here as string literals that must stay byte-identical
# with the declarations in bucket_store.py. Any change to either copy must be
# mirrored to both; the test suite enforces this via the manifest digests.
BUCKET_EVENTS_INDEX_SCHEMA = "opentraces.bucket.events.v2"
TRAIL_EVENT_SNAPSHOT_SCHEMA = "opentraces.bucket.trail_events_snapshot.v1"


def sync_events_mirror(
    repo: Path,
    *,
    repo_id: str,
) -> dict[str, Any]:
    """Mirror the repo-local TrailEvent Git ref into ``bucket/events/v1/`` (plan 080 §4).

    Per Resolution B, this hard-cuts the legacy ``bucket/events/trail/v1/``
    layout. Each Git batch becomes one ``batches/<seq>-<batch-id>.jsonl.gz``
    file (gzip-deterministic) and the ``index.json`` head records the
    aggregate counters consumed by remote sync and ``bucket repair``.

    Idempotent: a tick that finds no new batches writes nothing (and skips
    rewriting index.json when the counters are unchanged).
    """

    from .trails import EVENT_LOG_REF, event_log_status, read_events

    status = event_log_status(repo)
    if status.get("state") == "missing":
        # Issue #28 — do NOT clobber a restored mirror. On a fresh clone /
        # cross-machine restore the live project's Git event-log ref is missing,
        # but the bucket's own events mirror (``bucket/events/v1/``) may already
        # hold the canonical batches (left by ``bucket remote pull``). Resetting
        # the index to ``state=missing``/``batch_count=0`` here would orphan
        # those batches and drop every trace they carry. So: if a prior
        # non-empty mirror exists on disk, return it UNCHANGED (on-disk bytes
        # untouched). Only write the missing-state index when no prior mirror
        # exists — the normal first-tick behavior is unchanged.
        index_path = events_v1_index_path()
        if index_path.exists():
            try:
                prior = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                prior = None
            if (
                isinstance(prior, dict)
                and int(prior.get("batch_count") or 0) > 0
            ):
                return prior
        index = {
            "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
            "repo_id": repo_id,
            "event_log_ref": EVENT_LOG_REF,
            "event_log_head": None,
            "batch_count": 0,
            "last_batch_id": None,
            "latest_event_sequence": 0,
            "state": "missing",
            "updated_at": utc_now_str(),
        }
        _atomic_write_json(events_v1_index_path(), index)
        return index

    from .trails.event_log import _is_ancestor

    events = sorted(read_events(repo, verify=False), key=lambda e: e.event_sequence)

    # Incremental fast-path: existing batch files are immutable (one batch per
    # append, content-addressed), so when the prior index head is an ancestor
    # of the current head we only group + write batches for events appended
    # since. New batches always sort after existing ones, so their ordinals
    # (and therefore filenames + contents) match a full rebuild byte-for-byte
    # — keeping the replay-equals-git invariant.
    index_path = events_v1_index_path()
    prior_index: dict[str, Any] | None = None
    if index_path.exists():
        try:
            prior_index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            prior_index = None
    prior_head = prior_index.get("event_log_head") if prior_index else None
    prior_seq = int(prior_index.get("latest_event_sequence", 0)) if prior_index else 0
    prior_batch_count = int(prior_index.get("batch_count", 0)) if prior_index else 0
    incremental = (
        prior_index is not None
        and isinstance(prior_head, str)
        and events_v1_batches_dir().exists()
        and _is_ancestor(repo, prior_head, status.get("head") or prior_head)
    )

    if incremental and prior_head == status.get("head"):
        # Nothing new since the last mirror; leave the bucket untouched.
        return prior_index

    work_events = (
        [e for e in events if e.event_sequence > prior_seq]
        if incremental else events
    )
    seq_offset = prior_batch_count if incremental else 0

    # Group the events we need to write by batch_id, in sequence order.
    batch_order: list[str] = []
    by_batch: dict[str, list[Any]] = {}
    for event in work_events:
        bid = event.batch_id
        if bid not in by_batch:
            by_batch[bid] = []
            batch_order.append(bid)
        by_batch[bid].append(event)

    batches_dir = events_v1_batches_dir()
    batches_dir.mkdir(parents=True, exist_ok=True)

    batches_written = 0
    last_batch_id: str | None = (
        prior_index.get("last_batch_id") if incremental else None
    )
    latest_event_sequence = prior_seq if incremental else 0
    for offset, bid in enumerate(batch_order, start=1):
        seq = seq_offset + offset
        batch_events = by_batch[bid]
        # Filename: <seq>-<batch-id>.jsonl.gz; seq is zero-padded for sort.
        safe_bid = _path_part(bid)
        filename = f"{seq:012d}-{safe_bid}.jsonl.gz"
        path = batches_dir / filename
        lines = [
            _canonical_json(event.model_dump(mode="json"))
            for event in sorted(batch_events, key=lambda e: e.event_sequence)
        ]
        body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
        compressed = _gzip_deterministic(body)
        if not (path.exists() and path.read_bytes() == compressed):
            _atomic_write_bytes(path, compressed)
            batches_written += 1
        last_batch_id = bid
        for ev in batch_events:
            if ev.event_sequence > latest_event_sequence:
                latest_event_sequence = ev.event_sequence

    index = {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "repo_id": repo_id,
        "event_log_ref": EVENT_LOG_REF,
        "event_log_head": status.get("head"),
        "batch_count": seq_offset + len(batch_order),
        "last_batch_id": last_batch_id,
        "latest_event_sequence": latest_event_sequence,
        "state": status.get("state"),
        "updated_at": utc_now_str(),
        "verification": {
            "batch_count": status.get("batch_count"),
            "batch_parents_linear": status.get("batch_parents_linear"),
            "content_hashes_valid": status.get("content_hashes_valid"),
            "event_chain_valid": status.get("event_chain_valid"),
        },
        "batches_written": batches_written,
    }
    _atomic_write_json(events_v1_index_path(), index)
    return index


# Compat alias for existing call sites (ingest.py / watcher/daemon.py).
# These will be retired when those tracks merge.
def sync_trail_events_from_repo(repo: Path, *, repo_id: str) -> dict[str, Any]:
    """Deprecated alias for :func:`sync_events_mirror`. Plan 080 Resolution B."""

    return sync_events_mirror(repo, repo_id=repo_id)


def read_events_mirror_batches() -> Iterator[Any]:
    """Yield decompressed ``TrailEvent`` instances from the v2 event-log mirror.

    Walks ``bucket/events/v1/batches/*.jsonl.gz`` in sequence-prefix order,
    decompressing each batch and yielding events in original order. Raises
    ``FileNotFoundError`` if the mirror is missing entirely.
    """

    from .trails import TrailEvent
    from .bucket_store import BucketLayoutError

    index_path = events_v1_index_path()
    if not index_path.exists():
        raise FileNotFoundError(
            f"no v2 events mirror found at {index_path}; run "
            f"'opentraces setup watcher tick' or 'opentraces bucket repair'"
        )
    try:
        index = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable events mirror index: {exc}") from exc
    if index.get("schema_version") != BUCKET_EVENTS_INDEX_SCHEMA:
        raise BucketLayoutError(
            f"events mirror schema {index.get('schema_version')!r} incompatible with "
            f"local {BUCKET_EVENTS_INDEX_SCHEMA!r}; run "
            f"'opentraces bucket repair'"
        )
    batches_dir = events_v1_batches_dir()
    if not batches_dir.exists():
        return
    for batch_path in sorted(batches_dir.glob("*.jsonl.gz")):
        try:
            raw = _read_gzip_bytes(batch_path).decode("utf-8")
        except (OSError, gzip.BadGzipFile) as exc:
            raise ValueError(f"unreadable events mirror batch {batch_path}: {exc}") from exc
        for line in raw.splitlines():
            if not line.strip():
                continue
            yield TrailEvent.model_validate_json(line)


# Compat alias for callers still on the v1 export reader signature. Returns
# (head, events) like the old function. The head shape uses the v2 fields so
# downstream code can dispatch on schema_version.
def read_trail_event_export(repo_id: str | None = None) -> tuple[dict[str, Any], list[Any]]:
    """Deprecated alias for :func:`read_events_mirror_batches`.

    Returns the v2 index head plus a list of decompressed ``TrailEvent``
    instances, matching the legacy ``(head, events)`` tuple shape used by
    ``bucket replay`` callers.
    """

    from .bucket_store import BucketLayoutError

    index_path = events_v1_index_path()
    if not index_path.exists():
        raise FileNotFoundError(
            f"no v2 events mirror found for repo_id={repo_id!r}; run "
            f"'opentraces setup watcher tick' or 'opentraces bucket repair'"
        )
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("schema_version") != BUCKET_EVENTS_INDEX_SCHEMA:
        raise BucketLayoutError(
            f"events mirror schema {index.get('schema_version')!r} incompatible with "
            f"local {BUCKET_EVENTS_INDEX_SCHEMA!r}; run "
            f"'opentraces bucket repair'"
        )
    events = list(read_events_mirror_batches())
    return index, events


def restore_trail_events_to_repo(
    repo: Path,
    *,
    repo_id: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Restore bucket-exported Trace Trails into a supplied Git repository.

    Plan 080: reads from the v2 events mirror (``bucket/events/v1/``).
    """

    from .trails import import_event_log

    head, events = read_trail_event_export(repo_id)
    if not events:
        return {
            "state": "empty",
            "repo": str(repo),
            "repo_id": head.get("repo_id"),
            "event_count": 0,
            "events_imported": 0,
        }
    imported = import_event_log(repo, events, writer="bucket-restore", force=force)
    return {
        **imported,
        "repo": str(repo),
        "repo_id": head.get("repo_id"),
        "export_event_log_head": head.get("event_log_head"),
    }


def trail_event_snapshot(*, include_objects: bool = False) -> dict[str, Any]:
    """Return a deterministic snapshot of the v2 events mirror (plan 080).

    Reads ``bucket/events/v1/index.json``. The legacy
    ``bucket/events/trail/v1`` layout is retired; this snapshot is kept
    only as a manifest contributor for compat callers.
    """

    from ._bucket_io import _digest_payload

    index_path = events_v1_index_path()
    head: dict[str, Any] | None = None
    if index_path.exists():
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("schema_version") == BUCKET_EVENTS_INDEX_SCHEMA
            ):
                head = payload
        except (OSError, ValueError, json.JSONDecodeError):
            head = None

    heads_rows: list[dict[str, Any]] = []
    if head is not None:
        heads_rows.append(
            {
                "path": "events/v1/index.json",
                "repo_id": head.get("repo_id"),
                "event_log_head": head.get("event_log_head"),
                "event_count": int(head.get("latest_event_sequence") or 0),
                "batch_count": int(head.get("batch_count") or 0),
                "state": head.get("state"),
                "updated_at": head.get("updated_at"),
                **({"object": head} if include_objects else {}),
            }
        )

    digest_material = [
        (
            f"{item.get('path')} {item.get('event_log_head')} "
            f"{item.get('event_count')} {item.get('batch_count')} {item.get('state')}"
        )
        for item in heads_rows
    ]
    snapshot: dict[str, Any] = {
        "schema_version": TRAIL_EVENT_SNAPSHOT_SCHEMA,
        "root": str(events_v1_root()),
        "repository_count": len(heads_rows),
        "event_count": sum(int(item.get("event_count") or 0) for item in heads_rows),
        "batch_count": sum(int(item.get("batch_count") or 0) for item in heads_rows),
        "digest": _digest_payload(digest_material),
    }
    if include_objects:
        snapshot["objects"] = heads_rows
    return snapshot
