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
import os
import tempfile
import zlib
from itertools import groupby
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
    _atomic_write_json,
    _canonical_json,
    _read_gzip_bytes,
)

# Schema constants (imported from facade to keep a single source of truth).
# These are re-declared here as string literals that must stay byte-identical
# with the declarations in bucket_store.py. Any change to either copy must be
# mirrored to both; the test suite enforces this via the manifest digests.
BUCKET_EVENTS_INDEX_SCHEMA = "opentraces.bucket.events.v2"
TRAIL_EVENT_SNAPSHOT_SCHEMA = "opentraces.bucket.trail_events_snapshot.v1"


def _same_file_bytes(left: Path, right: Path) -> bool:
    """Compare two files without retaining either file in memory."""

    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as left_handle, right.open("rb") as right_handle:
        while True:
            left_chunk = left_handle.read(1024 * 1024)
            right_chunk = right_handle.read(1024 * 1024)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _write_event_batch_streaming(path: Path, events: Iterator[Any]) -> tuple[bool, int]:
    """Write one event batch with O(one event + codec buffers) peak memory.

    The byte stream matches :func:`_gzip_deterministic` exactly: canonical JSON,
    one newline per event, zlib level 6 with a gzip wrapper, ``mtime=0``, and the
    normalized RFC-1952 OS byte.  Candidate comparison is chunked as well, so a
    pre-existing multi-gigabyte batch never becomes two in-memory ``bytes``
    objects merely to preserve the write-only-on-change contract.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    latest_event_sequence = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            for event in events:
                latest_event_sequence = max(latest_event_sequence, int(event.event_sequence))
                line = _canonical_json(event.model_dump(mode="json"))
                handle.write(compressor.compress(line.encode("utf-8") + b"\n"))
            handle.write(compressor.flush())
        with tmp_path.open("r+b") as handle:
            handle.seek(9)
            handle.write(b"\xff")
        if path.exists() and _same_file_bytes(path, tmp_path):
            return False, latest_event_sequence
        tmp_path.replace(path)
        return True, latest_event_sequence
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


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

    from .trails import EVENT_LOG_REF, event_log_verification_status

    # Watcher ingestion is a hot recovery path, not an explicit integrity
    # audit.  The quick surface resolves the ref and bounded counters without
    # falling back to ``read_events`` when its verification watermark is
    # absent or stale.  Full validation remains available through
    # ``trail verify --mode full`` and the explicit verification APIs.
    status = event_log_verification_status(repo, mode="quick")
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
            if isinstance(prior, dict) and int(prior.get("batch_count") or 0) > 0:
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

    from .trails.event_log import _is_ancestor, read_events_since

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

    # #65: the mirror was already WRITE-incremental but READ-full — every
    # changed tick materialised the entire log (~872K pydantic events observed
    # live, the 2GB snapshot pickle) only to filter it down to the appended
    # suffix. Read just the suffix instead; the full read remains for true
    # rebuilds (no/invalid prior index, rewritten history).
    work_events: Iterator[Any] | None = None
    if incremental:
        _, new_events = read_events_since(repo, prior_head)
        if new_events is None:
            incremental = False
        else:
            work_events = iter(
                [
                    e
                    for e in sorted(new_events, key=lambda e: e.event_sequence)
                    if e.event_sequence > prior_seq
                ]
            )
    if work_events is None:
        # #365: a missing/invalid mirror index is automatic watcher recovery,
        # not an explicit maintenance command.  The old fallback parsed and
        # retained every TrailEvent, then retained them a second time in
        # ``by_batch``.  ``iter_events`` walks one immutable head in sequence
        # order and the groupby below consumes one batch at a time.
        from .trails.event_log import iter_events

        head = status.get("head")
        work_events = iter_events(repo, head) if isinstance(head, str) else iter(())
    seq_offset = prior_batch_count if incremental else 0

    batches_dir = events_v1_batches_dir()
    batches_dir.mkdir(parents=True, exist_ok=True)

    batches_written = 0
    last_batch_id: str | None = prior_index.get("last_batch_id") if incremental else None
    latest_event_sequence = prior_seq if incremental else 0
    batch_count = 0
    for offset, (bid, batch_events) in enumerate(
        groupby(work_events, key=lambda event: event.batch_id), start=1
    ):
        batch_count = offset
        seq = seq_offset + offset
        # Filename: <seq>-<batch-id>.jsonl.gz; seq is zero-padded for sort.
        safe_bid = _path_part(bid)
        filename = f"{seq:012d}-{safe_bid}.jsonl.gz"
        path = batches_dir / filename
        wrote, batch_latest = _write_event_batch_streaming(path, batch_events)
        if wrote:
            batches_written += 1
        last_batch_id = bid
        latest_event_sequence = max(latest_event_sequence, batch_latest)

    index = {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "repo_id": repo_id,
        "event_log_ref": EVENT_LOG_REF,
        "event_log_head": status.get("head"),
        "batch_count": seq_offset + batch_count,
        "last_batch_id": last_batch_id,
        "latest_event_sequence": latest_event_sequence,
        # This is canonical synced mirror state, not the machine-local
        # verification state. A current full-verification watermark changes
        # quick diagnostics from ``unverified_large`` to ``ok`` without
        # changing one byte of the Git ref; allowing that cache state into the
        # index made identical buckets hash differently across machines.
        # Missing refs are handled above, and structural quick-check failures
        # remain honestly invalid. Detailed local verification diagnostics
        # stay available through ``event_log_verification_status``.
        "state": "invalid" if status.get("state") == "invalid" else "ok",
        "updated_at": utc_now_str(),
        "batches_written": batches_written,
    }
    _atomic_write_json(events_v1_index_path(), index)
    return index


# Compat alias for existing call sites (ingest.py / watcher/daemon.py).
# These will be retired when those tracks merge.
def sync_trail_events_from_repo(repo: Path, *, repo_id: str) -> dict[str, Any]:
    """Deprecated alias for :func:`sync_events_mirror`. Plan 080 Resolution B."""

    return sync_events_mirror(repo, repo_id=repo_id)


def _mirror_batch_paths() -> list[Path]:
    """Validate the mirror index and return immutable batches in replay order.

    The aggregate mirror is shared by multiple projects and can legitimately
    contain duplicate ordinal prefixes and files outside the most recently
    written project's index slice. Replay therefore keeps every batch file;
    scoped readers establish non-empty ownership from validated event content,
    never from the shared filename ordinal namespace. The index can also lag
    the immutable files during reclaim crash recovery, so its counters are
    diagnostics rather than a file-selection boundary.
    """

    from .bucket_models import BucketLayoutError

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
    if index.get("state") == "missing":
        raise FileNotFoundError(
            "events mirror index records a missing canonical event log; run "
            "'opentraces setup watcher tick' or 'opentraces bucket repair'"
        )
    try:
        declared_count = int(index.get("batch_count"))
    except (TypeError, ValueError) as exc:
        raise ValueError("events mirror index has an invalid batch_count") from exc
    if declared_count < 0:
        raise ValueError("events mirror index has a negative batch_count")

    batches_dir = events_v1_batches_dir()
    if not batches_dir.exists():
        return []
    return sorted(batches_dir.glob("*.jsonl.gz"))


def _iter_gzip_candidate_lines(
    batch_path: Path,
    token: bytes,
    *,
    chunk_size: int = 64 * 1024,
) -> Iterator[tuple[int, bytes]]:
    """Yield token-bearing JSONL rows without retaining unrelated fat rows.

    The current line is written to a bounded-memory spool and spills to a
    temporary file after one chunk. Only a token-bearing candidate is read
    back as one ``bytes`` value for JSON validation; an unrelated event can be
    arbitrarily large without becoming one Python allocation.
    """

    overlap = max(len(token) - 1, 0)
    line_number = 1
    line_has_token = False
    token_tail = b""
    line = tempfile.SpooledTemporaryFile(max_size=chunk_size, mode="w+b")

    def _consume(part: bytes) -> None:
        nonlocal line_has_token, token_tail
        if part:
            window = token_tail + part
            if token in window:
                line_has_token = True
            token_tail = window[-overlap:] if overlap else b""
            line.write(part)

    def _finish() -> bytes | None:
        nonlocal line, line_has_token, token_tail
        candidate: bytes | None = None
        if line_has_token:
            line.seek(0)
            candidate = line.read()
        line.close()
        line = tempfile.SpooledTemporaryFile(max_size=chunk_size, mode="w+b")
        line_has_token = False
        token_tail = b""
        return candidate

    try:
        with gzip.open(batch_path, "rb") as handle:
            while chunk := handle.read(chunk_size):
                parts = chunk.split(b"\n")
                for part in parts[:-1]:
                    _consume(part)
                    candidate = _finish()
                    if candidate is not None:
                        yield line_number, candidate
                    line_number += 1
                _consume(parts[-1])
        if line.tell():
            candidate = _finish()
            if candidate is not None:
                yield line_number, candidate
    finally:
        line.close()


def read_events_mirror_for_trace(trace_id: str) -> Iterator[Any]:
    """Yield exactly one trace's mirror events without inflating any batch.

    Each gzip file is scanned in fixed-size chunks, with the current row held
    in a bounded spool. A quoted trace-id token is only a raw prefilter;
    candidate lines are still fully validated and checked with the canonical
    ownership rule (top-level id, payload id, or an anchor-search summary
    touching the trace). Malformed unrelated JSON is therefore skippable,
    while malformed candidate bytes or conflicting relevant duplicates remain
    an honest error. Retained memory is O(one chunk + this trace), not O(the
    global mirror or its largest unrelated row).
    """

    from .trails import TrailEvent
    from .trails.event_log import _event_owns_trace

    token = json.dumps(trace_id, ensure_ascii=False).encode("utf-8")
    seen: dict[str, dict[str, Any]] = {}
    for batch_path in _mirror_batch_paths():
        try:
            for line_number, raw_line in _iter_gzip_candidate_lines(batch_path, token):
                try:
                    event = TrailEvent.model_validate_json(raw_line)
                except Exception as exc:
                    raise ValueError(
                        "unreadable relevant event in events mirror batch "
                        f"{batch_path} at line {line_number}: {exc}"
                    ) from exc
                if not _event_owns_trace(event, trace_id):
                    continue
                material = event.canonical_event_material()
                prior = seen.get(event.event_id)
                if prior is not None:
                    if prior != material:
                        raise ValueError(
                            "events mirror carries two conflicting relevant "
                            f"copies of event_id {event.event_id!r}; run "
                            "'opentraces bucket repair'"
                        )
                    continue
                seen[event.event_id] = material
                yield event
        except (OSError, EOFError, gzip.BadGzipFile) as exc:
            raise ValueError(f"unreadable events mirror batch {batch_path}: {exc}") from exc


def read_events_mirror_batches() -> Iterator[Any]:
    """Yield decompressed ``TrailEvent`` instances from the v2 event-log mirror.

    Walks ``bucket/events/v1/batches/*.jsonl.gz`` in sequence-prefix order,
    decompressing each batch and yielding events in original order. Raises
    ``FileNotFoundError`` if the mirror is missing entirely.

    A project mid ``bucket reclaim`` mirror reconcile can, for one
    interrupted window, have BOTH its freshly written consolidated batch
    file and its stale pre-reconcile batch file(s) on disk at once
    (write-new-then-remove-stale --
    see ``bucket_reclaim_search._reconcile_mirror_for_project``). An
    unchanged event's two copies share the same content-addressed
    ``event_id`` (``batch_id``/``writer`` sit outside the hash -- see
    ``TrailEvent.canonical_event_material``) and differ only in those
    transport fields, so a later occurrence of an already-yielded
    ``event_id`` is dropped rather than re-yielded -- callers such as
    ``restore_trail_events_to_repo`` need one contiguous stream, and
    content-addressing guarantees the drop is safe. This only ever collapses
    a TRUE duplicate (same ``event_id`` in both files); a stale event whose
    replacement got a genuinely different ``event_id`` (compaction re-chains
    everything from the first touched slot onward, so most superseded
    content falls in this bucket, not the same-id one) is not something this
    function can safely arbitrate on its own -- see ``bucket_reclaim_
    search.resume_pending_anchor_search_journals`` for what actually closes
    that window. If the replay-relevant fields ever genuinely differ under
    the same ``event_id`` (real corruption, not this crash window), this
    raises instead of silently picking one -- arbitrating between
    conflicting copies is not this function's job.
    """

    from .trails import TrailEvent

    seen: dict[str, dict[str, Any]] = {}
    for batch_path in _mirror_batch_paths():
        try:
            raw = _read_gzip_bytes(batch_path).decode("utf-8")
        except (OSError, gzip.BadGzipFile) as exc:
            raise ValueError(f"unreadable events mirror batch {batch_path}: {exc}") from exc
        for line in raw.splitlines():
            if not line.strip():
                continue
            event = TrailEvent.model_validate_json(line)
            material = event.canonical_event_material()
            prior = seen.get(event.event_id)
            if prior is not None:
                if prior != material:
                    raise ValueError(
                        f"events mirror carries two conflicting copies of event_id "
                        f"{event.event_id!r} (content differs beyond batch_id/writer); "
                        f"run 'opentraces bucket repair'"
                    )
                continue
            seen[event.event_id] = material
            yield event


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
