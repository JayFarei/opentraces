"""Append-only Git event log for Trace Trails."""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    GitObjectID,
    TrailEvent,
    TrailEventDraft,
    expected_event_id,
    finalize_event,
    payload_content_hash,
)

EVENT_LOG_REF = "refs/opentraces/local/events/v1"
EVENT_LOG_APPEND_MAX_RETRIES = 5

# Bump when the on-disk snapshot format or the TrailEvent shape changes so a
# stale cache is ignored rather than mis-parsed.
_EVENT_CACHE_FORMAT = 1

# Only re-pickle the full event snapshot once the unsaved delta reaches this
# many events — small per-ingest deltas read from the existing snapshot + a
# cheap range read without rewriting the whole (potentially 100s of MB) file.
_SNAPSHOT_REFRESH_MIN_DELTA = 2000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(
    cwd: Path,
    args: list[str],
    *,
    input: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc


def _git_bytes(
    cwd: Path,
    args: list[str],
    *,
    input: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        input=input,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        stderr = proc.stderr.decode(errors="replace").strip()
        stdout = proc.stdout.decode(errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr or stdout}")
    return proc


def _ref_head(cwd: Path) -> str | None:
    proc = _git(cwd, ["rev-parse", "--verify", EVENT_LOG_REF], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _zero_object_id(cwd: Path) -> str:
    proc = _git(cwd, ["rev-parse", "--show-object-format"], check=False)
    return "0" * 64 if proc.stdout.strip() == "sha256" else "0" * 40


def _ref_update_raced(
    cwd: Path,
    proc: subprocess.CompletedProcess[str],
    expected_head: str | None,
) -> bool:
    text = f"{proc.stderr}\n{proc.stdout}".lower()
    if not any(
        marker in text
        for marker in (
            "cannot lock ref",
            "reference already exists",
            "but expected",
            "is at",
        )
    ):
        return False
    current_head = _ref_head(cwd)
    return current_head != expected_head


def _update_event_log_ref(cwd: Path, commit_sha: str, expected_head: str | None) -> bool:
    old_value = expected_head or _zero_object_id(cwd)
    proc = _git(
        cwd,
        ["update-ref", EVENT_LOG_REF, commit_sha, old_value],
        check=False,
    )
    if proc.returncode == 0:
        return True
    if _ref_update_raced(cwd, proc, expected_head):
        return False
    raise RuntimeError(
        f"git update-ref {EVENT_LOG_REF} failed: "
        f"{proc.stderr.strip() or proc.stdout.strip()}"
    )


def _hash_blob(cwd: Path, text: str) -> str:
    return _git(cwd, ["hash-object", "-w", "--stdin"], input=text).stdout.strip()


def _object_type(cwd: Path, oid: GitObjectID) -> str | None:
    proc = _git(cwd, ["cat-file", "-t", oid.hex], check=False)
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _collect_object_ids(value: Any) -> list[GitObjectID]:
    found: list[GitObjectID] = []
    if isinstance(value, dict):
        if set(value.keys()) >= {"algo", "hex"}:
            try:
                found.append(GitObjectID.model_validate(value))
            except Exception:
                pass
        for child in value.values():
            found.extend(_collect_object_ids(child))
    elif isinstance(value, list):
        for item in value:
            found.extend(_collect_object_ids(item))
    return found


def _safe_tree_entry_name(value: str) -> str:
    return value.replace(":", "-").replace("/", "-")


def _tree_oid_from_payload(cwd: Path, event: TrailEvent) -> GitObjectID | None:
    tree_id = event.payload.get("tree_id")
    if not tree_id:
        return None
    try:
        oid = GitObjectID.model_validate(tree_id)
    except Exception:
        return None
    if _object_type(cwd, oid) != "tree":
        return None
    return oid


def _write_batch_tree(cwd: Path, events: list[TrailEvent], batch: dict[str, Any]) -> str:
    snapshot_tree_entries: list[tuple[str, str]] = []
    boundary_tree_entries: list[tuple[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="opentraces-trails-index-") as td:
        index_path = str(Path(td) / "index")
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = index_path
        _git(cwd, ["read-tree", "--empty"], env=env)

        batch_blob = _hash_blob(cwd, json.dumps(batch, sort_keys=True, indent=2) + "\n")
        _git(
            cwd,
            ["update-index", "--add", "--cacheinfo", f"100644,{batch_blob},batch.json"],
            env=env,
        )

        retained_blobs: set[str] = set()
        retained_trees: set[str] = set()
        for event in events:
            payload = event.model_dump(mode="json")
            event_blob = _hash_blob(cwd, json.dumps(payload, sort_keys=True, indent=2) + "\n")
            _git(
                cwd,
                [
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{event_blob},events/{event.event_sequence:012d}.json",
                ],
                env=env,
            )
            for oid in _collect_object_ids(event.payload):
                if oid.hex in retained_blobs:
                    continue
                if _object_type(cwd, oid) != "blob":
                    continue
                retained_blobs.add(oid.hex)
                _git(
                    cwd,
                    [
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        f"100644,{oid.hex},objects/blobs/{oid.hex}",
                    ],
                    env=env,
                )
            snapshot_id = event.payload.get("snapshot_id")
            oid = _tree_oid_from_payload(cwd, event)
            if oid:
                safe_event_id = _safe_tree_entry_name(event.event_id)
                boundary_tree_entries.append(
                    (f"{event.event_sequence:012d}-{safe_event_id}", oid.hex)
                )
            if event.event_type == "trace_snapshot_created" and oid and snapshot_id:
                if oid.hex not in retained_trees:
                    retained_trees.add(oid.hex)
                    safe_snapshot_id = _safe_tree_entry_name(str(snapshot_id))
                    snapshot_tree_entries.append((safe_snapshot_id, oid.hex))

        base_tree = _git(cwd, ["write-tree"], env=env).stdout.strip()

    if not snapshot_tree_entries and not boundary_tree_entries:
        return base_tree

    root_entries = _git(cwd, ["ls-tree", base_tree]).stdout
    if snapshot_tree_entries:
        snapshots_tree_input = "".join(
            f"040000 tree {tree_hex}\t{name}\n"
            for name, tree_hex in sorted(snapshot_tree_entries)
        )
        snapshots_tree = _git(cwd, ["mktree"], input=snapshots_tree_input).stdout.strip()
        root_entries += f"040000 tree {snapshots_tree}\tsnapshots\n"
    if boundary_tree_entries:
        trees_tree_input = "".join(
            f"040000 tree {tree_hex}\t{name}\n"
            for name, tree_hex in sorted(boundary_tree_entries)
        )
        trees_tree = _git(cwd, ["mktree"], input=trees_tree_input).stdout.strip()
        root_entries += f"040000 tree {trees_tree}\ttrees\n"
    return _git(cwd, ["mktree"], input=root_entries).stdout.strip()


def _commit_batch(cwd: Path, tree_sha: str, head: str | None, batch_id: str) -> str:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "opentraces")
    env.setdefault("GIT_AUTHOR_EMAIL", "opentraces@local")
    env.setdefault("GIT_COMMITTER_NAME", "opentraces")
    env.setdefault("GIT_COMMITTER_EMAIL", "opentraces@local")
    args = ["commit-tree", tree_sha, "-m", f"opentraces trail event batch {batch_id}"]
    if head:
        args[2:2] = ["-p", head]
    return _git(cwd, args, env=env).stdout.strip()


def append_event_batch(
    cwd: Path,
    drafts: list[TrailEventDraft | dict[str, Any]],
    *,
    writer: str,
) -> list[TrailEvent]:
    """Append a linear batch of TrailEvents to the local Git event log."""
    cwd = cwd.resolve()
    if not drafts:
        return []
    raw_drafts = [
        raw if isinstance(raw, TrailEventDraft) else TrailEventDraft.model_validate(raw)
        for raw in drafts
    ]

    for attempt in range(1, EVENT_LOG_APPEND_MAX_RETRIES + 1):
        head = _ref_head(cwd)
        # Bug B: derive (next_sequence, previous_event_id) from the HEAD batch's
        # tail blob, not by materialising the whole ~N-event history. Re-read
        # inside the retry loop so a concurrent writer's new HEAD is observed.
        tail = _read_head_batch_tail(cwd, head) if head else None
        next_sequence = (tail[0] + 1) if tail else 1
        previous_event_id = tail[1] if tail else None
        batch_id = f"batch-{uuid.uuid4().hex}"

        events: list[TrailEvent] = []
        for draft in raw_drafts:
            data = {
                "event_sequence": next_sequence,
                "event_time": draft.event_time or _utc_now(),
                "previous_event_id": previous_event_id,
                "trace_id": draft.trace_id,
                "generation_index": draft.generation_index,
                "step_index": draft.step_index,
                "batch_id": batch_id,
                "writer": writer,
                "capture_method": draft.capture_method,
                "event_type": draft.event_type,
                "payload": draft.payload,
                "SCHEMA_VERSION": draft.SCHEMA_VERSION,
                "SECURITY_VERSION": draft.SECURITY_VERSION,
                "ATTRIBUTION_VERSION": draft.ATTRIBUTION_VERSION,
            }
            # Let the model defaults set version fields by omitting explicit None.
            data = {k: v for k, v in data.items() if v is not None}
            event = finalize_event(data)
            events.append(event)
            next_sequence += 1
            previous_event_id = event.event_id

        batch = {
            "batch_id": batch_id,
            "writer": writer,
            "previous_event_log_head": head,
            "event_count": len(events),
        }
        tree_sha = _write_batch_tree(cwd, events, batch)
        commit_sha = _commit_batch(cwd, tree_sha, head, batch_id)

        if _update_event_log_ref(cwd, commit_sha, head):
            return events
        if attempt == EVENT_LOG_APPEND_MAX_RETRIES:
            break

    raise RuntimeError(
        f"{EVENT_LOG_REF} moved during append after "
        f"{EVENT_LOG_APPEND_MAX_RETRIES} retries"
    )


def _validate_import_events(events: list[TrailEvent]) -> list[TrailEvent]:
    ordered = sorted(events, key=lambda event: event.event_sequence)
    previous_event_id: str | None = None
    for index, event in enumerate(ordered, start=1):
        if event.event_sequence != index:
            raise ValueError(
                "imported TrailEvents must have contiguous event_sequence values "
                f"starting at 1; saw {event.event_sequence} at position {index}"
            )
        if event.previous_event_id != previous_event_id:
            raise ValueError(
                "imported TrailEvents have an invalid previous_event_id chain "
                f"at sequence {event.event_sequence}"
            )
        expected = expected_event_id(event)
        if event.event_id != expected:
            raise ValueError(
                "imported TrailEvents have an invalid event_id "
                f"at sequence {event.event_sequence}"
            )
        if event.content_hash != payload_content_hash(event.payload):
            raise ValueError(
                "imported TrailEvents have an invalid content_hash "
                f"at sequence {event.event_sequence}"
            )
        previous_event_id = event.event_id
    return ordered


def import_event_log(
    cwd: Path,
    events: list[TrailEvent | dict[str, Any]],
    *,
    writer: str = "bucket-restore",
    force: bool = False,
) -> dict[str, Any]:
    """Materialize a complete TrailEvent stream into ``EVENT_LOG_REF``.

    Bucket sync exports Trace Trails as file-shaped JSONL segments. This helper
    rebuilds the local Git ref from that stream so normal trail commands can run
    against a repo supplied by the user.
    """

    cwd = cwd.resolve()
    parsed = [
        event if isinstance(event, TrailEvent) else TrailEvent.model_validate(event)
        for event in events
    ]
    ordered = _validate_import_events(parsed)
    head = _ref_head(cwd)
    if head is not None:
        existing = read_events(cwd, verify=False)
        existing_ids = [event.event_id for event in existing]
        incoming_ids = [event.event_id for event in ordered]
        if existing_ids == incoming_ids:
            return {
                "state": "current",
                "ref": EVENT_LOG_REF,
                "head": head,
                "event_count": len(existing),
                "events_imported": 0,
            }
        if not force:
            raise ValueError(
                f"{EVENT_LOG_REF} already exists and differs; pass --force to replace it"
            )

    batch_id = f"bucket-restore-{uuid.uuid4().hex}"
    batch = {
        "batch_id": batch_id,
        "writer": writer,
        "previous_event_log_head": None,
        "event_count": len(ordered),
        "imported": True,
    }
    tree_sha = _write_batch_tree(cwd, ordered, batch)
    commit_sha = _commit_batch(cwd, tree_sha, None, batch_id)
    if not _update_event_log_ref(cwd, commit_sha, head):
        raise RuntimeError(f"{EVENT_LOG_REF} moved during import")
    invalidate_read_events_cache(cwd)
    return {
        "state": "imported",
        "ref": EVENT_LOG_REF,
        "head": commit_sha,
        "event_count": len(ordered),
        "events_imported": len(ordered),
        "replaced_head": head,
    }


def _event_blob_entries(cwd: Path, commits: list[str]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for commit in commits:
        proc = _git_bytes(cwd, ["ls-tree", "-r", "-z", commit, "events"])
        commit_entries: list[tuple[str, str]] = []
        for raw_entry in proc.stdout.split(b"\0"):
            if not raw_entry:
                continue
            try:
                meta, raw_path = raw_entry.split(b"\t", 1)
            except ValueError:
                continue
            parts = meta.split()
            if len(parts) < 3 or parts[1] != b"blob":
                continue
            path = raw_path.decode(errors="surrogateescape")
            if path.startswith("events/") and path.endswith(".json"):
                commit_entries.append((path, parts[2].decode()))
        entries.extend((path, oid) for path, oid in sorted(commit_entries))
    return entries


def _read_blobs_batch(cwd: Path, entries: list[tuple[str, str]]) -> list[bytes]:
    if not entries:
        return []
    input_data = ("".join(f"{oid}\n" for _, oid in entries)).encode()
    proc = _git_bytes(cwd, ["cat-file", "--batch"], input=input_data)
    data = proc.stdout
    offset = 0
    blobs: list[bytes] = []
    for _path, expected_oid in entries:
        newline = data.find(b"\n", offset)
        if newline < 0:
            raise RuntimeError("git cat-file --batch returned truncated header")
        header = data[offset:newline].split()
        if len(header) != 3:
            raise RuntimeError("git cat-file --batch returned malformed header")
        oid, object_type, raw_size = header
        if oid.decode() != expected_oid or object_type != b"blob":
            raise RuntimeError("git cat-file --batch returned unexpected object")
        size = int(raw_size)
        start = newline + 1
        end = start + size
        if end > len(data):
            raise RuntimeError("git cat-file --batch returned truncated blob")
        blobs.append(data[start:end])
        offset = end + 1
    return blobs


def _read_events_in_range(cwd: Path, since: str | None, head: str) -> list[TrailEvent]:
    """Parse events from commits in ``since..head`` (or all of ``head``).

    ``since`` is excluded; passing None reads the full history reachable
    from ``head``. Result is NOT sorted — callers merge + sort.
    """
    spec = f"{since}..{head}" if since else head
    commits = _git(cwd, ["rev-list", "--reverse", spec]).stdout.splitlines()
    entries = _event_blob_entries(cwd, commits)
    return [
        TrailEvent.model_validate_json(raw)
        for raw in _read_blobs_batch(cwd, entries)
    ]


def _read_events_from_head(cwd: Path, head: str) -> list[TrailEvent]:
    events = _read_events_in_range(cwd, None, head)
    events.sort(key=lambda event: event.event_sequence)
    return events


def _read_head_batch_tail(cwd: Path, head: str) -> tuple[int, str] | None:
    """Return ``(max_event_sequence, last_event_id)`` from the HEAD commit's
    batch WITHOUT materialising history (Bug B append fast-path).

    The log is non-cumulative, globally monotonic, gap-free, and never commits
    an empty batch, so the global tail event (max sequence) always lives in the
    HEAD commit's own tree. The zero-padded ``events/<seq:012d>.json`` names sort
    lexicographically by sequence, so the last entry is the tail. Returns None
    when there is no head / no events (caller treats it as an empty log).
    """
    entries = _event_blob_entries(cwd, [head])
    if not entries:
        return None
    blobs = _read_blobs_batch(cwd, [entries[-1]])
    if not blobs:
        return None
    event = TrailEvent.model_validate_json(blobs[0])
    return event.event_sequence, event.event_id


def read_events_scoped(
    cwd: Path,
    *,
    event_types: set[str],
    commit_filter: dict[str, str] | None = None,
    commit_sha: str | None = None,
) -> list[TrailEvent]:
    """Read only events of ``event_types``, streaming per-commit so the whole
    history is never materialised at once (Bug B hot-path reader).

    For the post-commit reconciler this bounds RSS to the wanted event types
    (a tiny fraction of the full log) instead of every TrailEvent. Each commit's
    tree is its own batch, so this walks ``rev-list`` and reads one batch at a
    time, discarding non-matching events before retaining them.

    ``commit_filter`` maps an event_type to a payload key whose nested ``hex``
    must equal ``commit_sha`` (used to keep only anchor/search events that
    reference the commit being reconciled). Types absent from ``commit_filter``
    are kept in full.

    NOTE: this never populates the shared ``_READ_EVENTS_CACHE`` (keyed
    ``(repo, head, verify)`` and shared with full-history callers) — a partial
    list there would silently truncate them — and never runs verification.

    Performance: a single ``git rev-list --objects`` lists every event blob in
    one pass, then blobs are read in chunks via ``git cat-file --batch`` with a
    cheap raw-bytes prefilter so only the wanted-type blobs (typically <1% of
    the log) are JSON-parsed. This keeps both subprocess count and peak RSS
    bounded even on a ~600k-event log (vs. a per-commit walk that spawns one
    ``ls-tree`` per commit and parses every event).
    """
    cwd = cwd.resolve()
    head = _ref_head(cwd)
    if head is None:
        return []
    # One pass: every (path, oid) reachable from head. `rev-list --objects`
    # emits "<oid> <path>" for blobs/trees and "<oid>" for commits.
    entries: list[tuple[str, str]] = []
    for line in _git(cwd, ["rev-list", "--objects", head]).stdout.splitlines():
        oid, sep, path = line.partition(" ")
        if not sep:
            continue
        if path.startswith("events/") and path.endswith(".json"):
            entries.append((path, oid))
    if not entries:
        return []
    entries.sort(key=lambda entry: entry[0])  # zero-padded name == event_sequence

    # Cheap raw-bytes prefilter so only wanted blobs are JSON-parsed. Types that
    # carry a commit_filter (anchor/search events) are additionally gated on the
    # reconciled commit's sha appearing in the blob — on a real log these
    # commit-keyed events dominate (e.g. ~500k git_anchor_search_completed), and
    # all but this commit's handful would otherwise be parsed only to be dropped
    # by commit_filter below. The post-parse checks still enforce exact matching,
    # so the commit-sha prefilter can only over-include, never miss.
    commit_filter = commit_filter or {}
    plain_tokens = [
        f'"{t}"'.encode() for t in event_types if t not in commit_filter
    ]
    filtered_tokens = [
        f'"{t}"'.encode() for t in event_types if t in commit_filter
    ]
    commit_token = commit_sha.encode() if commit_sha else None

    def _wanted(raw: bytes) -> bool:
        if any(token in raw for token in plain_tokens):
            return True
        if not filtered_tokens:
            return False
        if commit_token is not None and commit_token not in raw:
            return False
        return any(token in raw for token in filtered_tokens)

    matched: list[TrailEvent] = []
    chunk = 8192
    for start in range(0, len(entries), chunk):
        for raw in _read_blobs_batch(cwd, entries[start:start + chunk]):
            if not _wanted(raw):
                continue
            event = TrailEvent.model_validate_json(raw)
            if event.event_type not in event_types:
                continue
            if event.event_type in commit_filter:
                ref = (event.payload.get(commit_filter[event.event_type]) or {}).get("hex")
                if ref != commit_sha:
                    continue
            matched.append(event)
    matched.sort(key=lambda event: event.event_sequence)
    return matched


def _event_cache_path(cwd: Path) -> Path | None:
    """Per-repo snapshot path inside the git dir (cross-process, repo-local).

    Returns None when the git dir can't be resolved (callers fall back to a
    full read).
    """
    proc = _git(cwd, ["rev-parse", "--git-dir"], check=False)
    if proc.returncode != 0:
        return None
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (Path(cwd) / git_dir).resolve()
    return git_dir / "opentraces" / "event_log_snapshot.pkl"


def _verify_watermark_path(cwd: Path) -> Path | None:
    snap = _event_cache_path(cwd)
    return None if snap is None else snap.with_name("event_log_verified.json")


def _load_verify_watermark(cwd: Path) -> dict[str, Any] | None:
    """Load the persisted clean-verification watermark, or None."""
    path = _verify_watermark_path(cwd)
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("format") != _EVENT_CACHE_FORMAT
        or not isinstance(data.get("head"), str)
        or not isinstance(data.get("last_event_sequence"), int)
    ):
        return None
    return data


def _save_verify_watermark(cwd: Path, data: dict[str, Any]) -> None:
    """Persist the clean-verification watermark atomically. Best-effort."""
    path = _verify_watermark_path(cwd)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — watermark is an optimization, never fatal
        return


def _load_event_snapshot(cwd: Path) -> tuple[str, list[TrailEvent]] | None:
    """Load (head, events) from the on-disk snapshot, or None if unusable."""
    path = _event_cache_path(cwd)
    if path is None or not path.is_file():
        return None
    try:
        blob = pickle.loads(path.read_bytes())
        if (
            not isinstance(blob, dict)
            or blob.get("format") != _EVENT_CACHE_FORMAT
            or not isinstance(blob.get("head"), str)
            or not isinstance(blob.get("events"), list)
        ):
            return None
        return blob["head"], blob["events"]
    except Exception:  # noqa: BLE001 — any corruption ⇒ fall back to full read
        return None


def _save_event_snapshot(cwd: Path, head: str, events: list[TrailEvent]) -> None:
    """Atomically persist the parsed events keyed by ``head``. Best-effort."""
    path = _event_cache_path(cwd)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = pickle.dumps(
            {"format": _EVENT_CACHE_FORMAT, "head": head, "events": events},
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:  # noqa: BLE001 — cache is an optimization, never fatal
        return


def _events_contiguous(events: list[TrailEvent]) -> bool:
    """O(1) sanity check that a sorted event list is the gap-free 1..N chain.

    The log is monotonic + gap-free from sequence 1, so a correct sorted stream
    has ``events[0].event_sequence == 1`` and ``events[-1].event_sequence ==
    len(events)``. A dropped/duplicated event breaks the length/last-sequence
    identity. Used to self-heal a corrupt snapshot rather than serve it.
    """
    if not events:
        return True
    return (
        events[0].event_sequence == 1
        and events[-1].event_sequence == len(events)
    )


def _read_events_incremental(cwd: Path, head: str) -> list[TrailEvent]:
    """Read all events at ``head`` reusing a cross-process snapshot.

    Reads + validates only the commits appended since the snapshot's head;
    falls back to a full read whenever the snapshot is missing, stale, or the
    snapshot head is not an ancestor of ``head`` (history rewrite / supersede).
    """
    snapshot = _load_event_snapshot(cwd)
    if snapshot is not None:
        snap_head, snap_events = snapshot
        if snap_head == head:
            # Self-heal: a subtly-corrupt (non-contiguous) snapshot must not be
            # served verbatim — fall through to a clean full read instead of
            # silently propagating a broken stream.
            if _events_contiguous(snap_events):
                return snap_events
        else:
            is_ancestor = _git(
                cwd, ["merge-base", "--is-ancestor", snap_head, head], check=False
            ).returncode == 0
            if is_ancestor:
                try:
                    new_events = _read_events_in_range(cwd, snap_head, head)
                    merged = snap_events + new_events
                    merged.sort(key=lambda event: event.event_sequence)
                    if _events_contiguous(merged):
                        # Re-persisting the full snapshot costs O(total events)
                        # of pickle I/O, so only refresh it once the delta is
                        # large enough to amortize that write. Small deltas read
                        # from the existing snapshot + a cheap range read and
                        # skip the rewrite entirely.
                        if len(new_events) >= _SNAPSHOT_REFRESH_MIN_DELTA:
                            _save_event_snapshot(cwd, head, merged)
                        return merged
                    # corrupt merge -> fall through to a clean full read
                except Exception:  # noqa: BLE001 — fall back to a clean full read
                    pass
    events = _read_events_from_head(cwd, head)
    _save_event_snapshot(cwd, head, events)
    return events


# Cluster G — process-level memo for ``read_events``.
#
# The CLI's batch path calls ``read_events`` more than once per process
# (once to enumerate patch ids, once to thread into ``sync_patch``). On a
# project with 100k+ events the read costs ~6 seconds — paying that twice
# is half the warm-cache budget. We memo the result keyed by
# ``(repo, event_log_ref_head, verify)`` so callers within one process
# share one read. The cache invalidates automatically when the ref head
# advances.
_READ_EVENTS_CACHE: dict[
    tuple[str, str, bool], list[TrailEvent]
] = {}

# Per-(repo, head) memo for the expensive whole-log verification. The event
# log is append-only, so a given head's verification result is stable; this
# stops sync/status callers from re-validating every event on every call
# within a process.
_VERIFY_STATUS_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def read_events(cwd: Path, *, verify: bool = True) -> list[TrailEvent]:
    head = _ref_head(cwd)
    if head is None:
        return []
    cache_key = (str(Path(cwd).resolve()), head, verify)
    cached = _READ_EVENTS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    events = _read_events_incremental(cwd, head)
    if verify:
        errors = verify_event_log(cwd, events=events)["errors"]
        if errors:
            raise ValueError("; ".join(errors))
    # Bound the cache: keep only the most recent entry per repo so we do
    # not retain stale events across long-lived processes whose HEAD
    # advances. Enough for the per-CLI-call use case.
    repo_key = cache_key[0]
    for key in list(_READ_EVENTS_CACHE.keys()):
        if key[0] == repo_key and key != cache_key:
            _READ_EVENTS_CACHE.pop(key, None)
    _READ_EVENTS_CACHE[cache_key] = events
    return events


def invalidate_read_events_cache(cwd: Path | None = None) -> None:
    """Drop cached events for ``cwd`` (or globally when ``cwd`` is None).

    Tests and writers that mutate the event log mid-process should call
    this to force the next ``read_events`` to re-read from disk. The
    in-process append paths already invalidate when they advance the
    ref head implicitly via the per-(repo, head) key.
    """
    if cwd is None:
        _READ_EVENTS_CACHE.clear()
        _VERIFY_STATUS_CACHE.clear()
        return
    repo_key = str(Path(cwd).resolve())
    for key in list(_READ_EVENTS_CACHE.keys()):
        if key[0] == repo_key:
            _READ_EVENTS_CACHE.pop(key, None)
    for key in list(_VERIFY_STATUS_CACHE.keys()):
        if key[0] == repo_key:
            _VERIFY_STATUS_CACHE.pop(key, None)


def _parents_are_linear(cwd: Path) -> tuple[bool, list[str], int]:
    if _ref_head(cwd) is None:
        return False, [], 0
    lines = _git(cwd, ["rev-list", "--reverse", "--parents", EVENT_LOG_REF]).stdout.splitlines()
    previous: str | None = None
    errors: list[str] = []
    for index, line in enumerate(lines):
        parts = line.split()
        commit, parents = parts[0], parts[1:]
        if index == 0:
            if len(parents) > 1:
                errors.append(f"{commit[:12]} has multiple parents")
        elif parents != [previous]:
            errors.append(f"{commit[:12]} does not parent previous batch {previous[:12] if previous else '?'}")
        previous = commit
    return not errors, errors, len(lines)


def _is_ancestor(cwd: Path, ancestor: str, descendant: str) -> bool:
    return _git(
        cwd, ["merge-base", "--is-ancestor", ancestor, descendant], check=False
    ).returncode == 0


def _check_event_chain(
    events_sorted: list[TrailEvent],
    *,
    start_sequence: int,
    start_previous_event_id: str | None,
) -> tuple[bool, bool, list[str], str | None]:
    """Content-hash + chain checks over an ordered run of events.

    ``start_sequence`` / ``start_previous_event_id`` seed the expected values
    so a partial (incremental) run continues the chain from a prior point.
    Returns (content_ok, chain_ok, errors, last_event_id).
    """
    content_ok = True
    chain_ok = True
    errors: list[str] = []
    previous_event_id = start_previous_event_id
    expected_sequence = start_sequence
    for event in events_sorted:
        if event.content_hash != payload_content_hash(event.payload):
            content_ok = False
            errors.append(f"event {event.event_sequence}: content_hash mismatch")
        if event.event_id != expected_event_id(event):
            content_ok = False
            errors.append(f"event {event.event_sequence}: event_id mismatch")
        if event.event_sequence != expected_sequence:
            chain_ok = False
            errors.append(f"event {event.event_sequence}: non-contiguous event_sequence")
        if event.previous_event_id != previous_event_id:
            chain_ok = False
            errors.append(f"event {event.event_sequence}: previous_event_id mismatch")
        previous_event_id = event.event_id
        expected_sequence += 1
    return content_ok, chain_ok, errors, previous_event_id


def _verify_result_from_watermark(wm: dict[str, Any]) -> dict[str, Any]:
    return {
        "ref": EVENT_LOG_REF,
        "exists": True,
        "head": wm["head"],
        "batch_count": int(wm.get("batch_count", 0)),
        "event_count": int(wm.get("event_count", 0)),
        "batch_parents_linear": True,
        "content_hashes_valid": True,
        "event_chain_valid": True,
        "errors": [],
    }


def _try_incremental_verify(
    cwd: Path, head: str, wm: dict[str, Any]
) -> dict[str, Any] | None:
    """Verify only events appended since a clean watermark.

    The log is append-only and content-addressed (git-immutable), so events
    already verified at ``wm['head']`` cannot change. We re-check parent
    linearity (cheap) and only run content/chain checks on events beyond the
    watermark sequence. Returns the result on success, or None to signal the
    caller should fall back to a full verify (so it can report all errors).
    """
    linear, _parent_errors, batch_count = _parents_are_linear(cwd)
    if not linear:
        return None
    try:
        events = read_events(cwd, verify=False)
    except Exception:  # noqa: BLE001
        return None
    wm_seq = int(wm["last_event_sequence"])
    new = sorted(
        (e for e in events if e.event_sequence > wm_seq),
        key=lambda e: e.event_sequence,
    )
    # Contiguity sanity: the count of trusted (≤ wm_seq) events must be exactly
    # wm_seq. Any mismatch means the prefix changed — bail to a full verify.
    if len(events) != wm_seq + len(new):
        return None
    content_ok, chain_ok, errs, last_eid = _check_event_chain(
        new,
        start_sequence=wm_seq + 1,
        start_previous_event_id=wm.get("last_event_id"),
    )
    if errs or not content_ok or not chain_ok:
        return None
    last_seq = new[-1].event_sequence if new else wm_seq
    last_id = last_eid if new else wm.get("last_event_id")
    result = {
        "ref": EVENT_LOG_REF,
        "exists": True,
        "head": head,
        "batch_count": batch_count,
        "event_count": len(events),
        "batch_parents_linear": True,
        "content_hashes_valid": True,
        "event_chain_valid": True,
        "errors": [],
    }
    _save_verify_watermark(cwd, {
        "format": _EVENT_CACHE_FORMAT,
        "head": head,
        "last_event_sequence": last_seq,
        "last_event_id": last_id,
        "event_count": len(events),
        "batch_count": batch_count,
    })
    return result


def verify_event_log(
    cwd: Path,
    *,
    events: list[TrailEvent] | None = None,
) -> dict[str, Any]:
    """Verify parent linearity and TrailEvent content addresses."""
    errors: list[str] = []
    head = _ref_head(cwd)
    if head is None:
        return {
            "ref": EVENT_LOG_REF,
            "exists": False,
            "head": None,
            "batch_count": 0,
            "event_count": 0,
            "batch_parents_linear": False,
            "content_hashes_valid": False,
            "event_chain_valid": False,
            "errors": [],
        }

    # Whole-log verification (events is None) is keyed by head and memoized:
    # the log is append-only so the result can't change for a fixed head.
    cache_key = (str(Path(cwd).resolve()), head)
    if events is None and cache_key in _VERIFY_STATUS_CACHE:
        return _VERIFY_STATUS_CACHE[cache_key]

    # Incremental fast-path: if a clean verification was persisted at an
    # ancestor head, re-verify only the events appended since — every event is
    # still verified exactly once over the life of the log, but no single call
    # re-hashes the whole history.
    if events is None:
        wm = _load_verify_watermark(cwd)
        if wm is not None and wm.get("head") == head:
            result = _verify_result_from_watermark(wm)
            _VERIFY_STATUS_CACHE[cache_key] = result
            return result
        if wm is not None and _is_ancestor(cwd, wm["head"], head):
            inc = _try_incremental_verify(cwd, head, wm)
            if inc is not None:
                _VERIFY_STATUS_CACHE[cache_key] = inc
                return inc

    linear, parent_errors, batch_count = _parents_are_linear(cwd)
    errors.extend(parent_errors)

    cache_result = events is None
    if events is None:
        # Reuse the snapshot-backed batched reader instead of a ``git show``
        # per event — the per-blob form forked a subprocess for each of (here)
        # hundreds of thousands of events, pegging a core for minutes every
        # time sync called event_log_status.
        try:
            events = read_events(cwd, verify=False)
        except Exception as exc:  # noqa: BLE001 — corrupt blob ⇒ report, don't crash
            result = {
                "ref": EVENT_LOG_REF,
                "exists": True,
                "head": head,
                "batch_count": batch_count,
                "event_count": 0,
                "batch_parents_linear": linear,
                "content_hashes_valid": False,
                "event_chain_valid": False,
                "errors": errors + [f"event log read failed: {exc}"],
            }
            _VERIFY_STATUS_CACHE[cache_key] = result
            return result

    events_sorted = sorted(events, key=lambda e: e.event_sequence)
    content_ok, chain_ok, chain_errors, _last_eid = _check_event_chain(
        events_sorted, start_sequence=1, start_previous_event_id=None
    )
    errors.extend(chain_errors)

    result = {
        "ref": EVENT_LOG_REF,
        "exists": True,
        "head": head,
        "batch_count": batch_count,
        "event_count": len(events),
        "batch_parents_linear": linear,
        "content_hashes_valid": content_ok,
        "event_chain_valid": chain_ok,
        "errors": errors,
    }
    if cache_result:
        # Persist a clean watermark so future calls (this process or a fresh
        # cold one) can verify only the delta.
        if (
            not errors and content_ok and chain_ok and linear and events_sorted
        ):
            last = events_sorted[-1]
            _save_verify_watermark(cwd, {
                "format": _EVENT_CACHE_FORMAT,
                "head": head,
                "last_event_sequence": last.event_sequence,
                "last_event_id": last.event_id,
                "event_count": len(events),
                "batch_count": batch_count,
            })
        repo_key = cache_key[0]
        for key in list(_VERIFY_STATUS_CACHE.keys()):
            if key[0] == repo_key and key != cache_key:
                _VERIFY_STATUS_CACHE.pop(key, None)
        _VERIFY_STATUS_CACHE[cache_key] = result
    return result


def event_log_status(cwd: Path) -> dict[str, Any]:
    status = verify_event_log(cwd)
    if not status["exists"]:
        status["state"] = "missing"
    elif status["errors"]:
        status["state"] = "invalid"
    else:
        status["state"] = "ok"
    return status


# ---------------------------------------------------------------------------
# Cluster G — P2: ``patch_survival_cached`` event-keyed cache
# ---------------------------------------------------------------------------
#
# Survival rows are expensive to compute (6+ git invocations per patch).
# Most batch ``trail track`` callers ask the same question repeatedly while
# HEAD does not move. We persist each computed row as a TrailEvent of type
# ``patch_survival_cached`` keyed by ``(trace_patch_id, observed_head_sha)``.
# Because the key includes the HEAD sha the cache invalidates automatically
# when HEAD moves: the new HEAD's lookup misses the old key.
#
# The cache lives in the same append-only event log everything else does, so
# it survives between sessions and is correct under concurrent writers.
#
# Read path: ``build_survival_cache_index(events)`` collapses cache events
# into a ``{(patch_id, head_sha): payload}`` dict — last-write-wins via
# ``event_sequence``.

CACHED_SURVIVAL_EVENT_TYPE = "patch_survival_cached"


def build_survival_cache_index(
    events: list[TrailEvent],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Project the event log down to a survival-cache lookup table.

    Returns a ``{(trace_patch_id, observed_head_hex): payload}`` dict where
    the payload is the cached ``current_survival`` row at the time the
    cache event was written. Last-write-wins via ``event_sequence``.

    The cache key uses the *hex* portion of ``observed_head_id`` so consumers
    can match against ``git rev-parse HEAD`` output directly without
    unwrapping a ``GitObjectID``.

    The function is O(n) over the events list and pure Python — there is no
    Git I/O. Callers that already pass ``events`` to ``sync_patch`` can use
    it without re-reading the log.
    """
    cache: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    for event in events:
        if event.event_type != CACHED_SURVIVAL_EVENT_TYPE:
            continue
        payload = event.payload or {}
        patch_id = payload.get("trace_patch_id")
        observed_head_id = payload.get("observed_head_id")
        survival = payload.get("survival")
        head_hex: str | None = None
        if isinstance(observed_head_id, dict):
            candidate = observed_head_id.get("hex")
            if isinstance(candidate, str):
                head_hex = candidate
        if (
            not isinstance(patch_id, str)
            or head_hex is None
            or not isinstance(survival, dict)
        ):
            continue
        key = (patch_id, head_hex)
        existing = cache.get(key)
        if existing is None or existing[0] < event.event_sequence:
            cache[key] = (event.event_sequence, _survival_from_storage(survival))
    return {key: payload for key, (_seq, payload) in cache.items()}


def get_cached_survival(
    events: list[TrailEvent],
    *,
    trace_patch_id: str,
    observed_head_sha: str,
) -> dict[str, Any] | None:
    """Return the most recent cached survival row for ``(patch, head)``.

    ``observed_head_sha`` is the bare 40-char hex returned by
    ``git rev-parse HEAD``. The cache normalises against the hex of the
    stored ``GitObjectID`` so callers do not need to wrap/unwrap.

    Returns ``None`` when no cache event matches. Callers should fall back
    to a fresh ``sync_patch`` and emit a new cache event with the result.
    """
    if not trace_patch_id or not observed_head_sha:
        return None
    index = build_survival_cache_index(events)
    return index.get((trace_patch_id, observed_head_sha))


# Survival rows include a few bare-hex ``*_sha`` keys (``commit_sha``,
# ``lost_at_commit_sha``) that TrailEvent payload validation rejects under
# the typed-Git-object-identity rule. We wrap them at write-time and unwrap
# them at read-time so the on-disk shape is schema-valid while the
# in-process sync API keeps the historical ``_sha`` keys.
_SURVIVAL_SHA_KEYS_TO_WRAP = ("commit_sha", "lost_at_commit_sha")


def _survival_for_storage(survival: dict[str, Any]) -> dict[str, Any]:
    """Wrap bare ``*_sha`` hex strings so the payload validator accepts it."""
    out: dict[str, Any] = {}
    for key, value in survival.items():
        if key in _SURVIVAL_SHA_KEYS_TO_WRAP and isinstance(value, str) and value:
            wrap_key = key[:-len("_sha")] + "_id"
            try:
                out[wrap_key] = GitObjectID(hex=value).model_dump(mode="json")
                continue
            except Exception:
                # Bad hex — drop the field rather than poisoning the row.
                continue
        if key in _SURVIVAL_SHA_KEYS_TO_WRAP and value is None:
            # Null sha — emit as null wrap-key so consumers see the slot.
            wrap_key = key[:-len("_sha")] + "_id"
            out[wrap_key] = None
            continue
        out[key] = value
    return out


def _survival_from_storage(stored: dict[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`_survival_for_storage`: restore bare ``*_sha`` keys."""
    out: dict[str, Any] = {}
    for key, value in stored.items():
        if key in {"commit_id", "lost_at_commit_id"}:
            sha_key = key[:-len("_id")] + "_sha"
            if isinstance(value, dict) and "hex" in value:
                out[sha_key] = value.get("hex")
            else:
                out[sha_key] = value if isinstance(value, str) else None
            continue
        out[key] = value
    return out


def make_survival_cache_draft(
    *,
    trace_patch_id: str,
    observed_head_sha: str,
    survival: dict[str, Any],
    trace_id: str | None = None,
) -> TrailEventDraft:
    """Build a ``patch_survival_cached`` event draft for one survival row.

    The payload uses ``observed_head_id`` (a ``{algo, hex}`` GitObjectID)
    rather than a bare ``observed_head_sha`` because TrailEvent payloads
    forbid bare 40/64-char hex SHAs under ``_sha``-suffixed keys (the
    schema enforces typed Git object identity everywhere). Bare ``*_sha``
    fields inside the survival dict get wrapped into ``*_id`` form via
    :func:`_survival_for_storage`; readers reverse the wrap.

    The cache key fields (``trace_patch_id``, ``observed_head_id``) are
    duplicated at the top level of the payload so a downstream replay can
    rebuild the index without inspecting the nested survival dict.
    """
    payload: dict[str, Any] = {
        "trace_patch_id": trace_patch_id,
        "observed_head_id": GitObjectID(hex=observed_head_sha).model_dump(mode="json"),
        "survival": _survival_for_storage(survival),
    }
    return TrailEventDraft(
        event_type=CACHED_SURVIVAL_EVENT_TYPE,
        trace_id=trace_id,
        capture_method=["survival_cache"],
        payload=payload,
    )


def append_survival_cache_events(
    cwd: Path,
    drafts: list[TrailEventDraft],
    *,
    writer: str = "survival-cache",
) -> list[TrailEvent]:
    """Append a batch of cache events. Best-effort: returns ``[]`` on failure.

    A failed cache append must never cause survival queries to error. The
    cache is a perf hint; correctness is owned by the fresh-compute path.
    """
    if not drafts:
        return []
    try:
        return append_event_batch(cwd, drafts, writer=writer)
    except Exception:
        return []
