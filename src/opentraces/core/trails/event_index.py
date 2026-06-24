"""Digest-excluded, rebuildable OID index over the canonical TrailEvent log (#137).

The canonical event log (``refs/opentraces/local/events/v1``) is an append-only
Git ref. A per-key read (``read_events_for_trace`` / ``read_events_scoped``)
historically streamed EVERY event blob's bytes through one ``git cat-file
--batch`` to find the handful it wanted — so a zero-match per-trace read cost
~12s on a 919k-event ref purely because of the byte-stream, not the result size
(#136 diagnosis, #137 fix).

This module is the accelerator that removes that floor. It maps the keys reads
are issued by to event sequences, and event sequences to the Git blob OID:

* ``by_trace``   : ``trace_id`` -> [event_sequence ...]
* ``by_commit``  : commit/object ``hex`` -> [event_sequence ...]
* ``by_type``    : ``event_type`` -> [event_sequence ...]
* ``by_patch``   : ``trace_patch_id`` -> [event_sequence ...]
* ``seq_to_oid`` : ``event_sequence`` -> blob OID  (the load-bearing map)

A reader resolves a key to its sequences, maps those to blob OIDs, and reads
ONLY those blobs via a targeted ``cat-file --batch`` — O(result), not O(log).

Discipline (the #89 lesson): this index carries NO authoritative data, only
locations into the canonical event log. It is **digest-excluded** (it lives
under the repo's git dir, never inside a ref, the working tree, or the bucket,
so neither the event-ref OID nor the cross-machine bucket digest changes) and
**rebuildable** (any time it is missing, stale, or unreadable a read falls back
to the full scan and is slow-but-correct; ``rebuild_event_index`` /
``bucket repair`` can discard and regenerate it). It is therefore an accelerator,
never a source of truth.

Maintenance is incremental and append-friendly. The sole appender
(``append_event_batch``) calls :func:`extend_after_append` with the K new
events; that appends K postings to a ``pending.jsonl`` sidecar (O(K)) and stamps
the new head — no whole-index rewrite. The pending sidecar is folded into the
pickled base only once it crosses :data:`_FOLD_THRESHOLD`, amortizing the
base rewrite to O(1) per event. ``apply`` is idempotent (a sequence already
present is ignored), so a crash mid-fold can only ever replay, never double-count.
"""
from __future__ import annotations

import json
import os
import pickle
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import TrailEvent

# Bump when the on-disk index format changes so a stale sidecar is discarded
# rather than mis-read.
_INDEX_FORMAT = 1

# Fold ``pending.jsonl`` into the pickled base once it reaches this many events.
# Keeps per-read pending replay bounded and the base rewrite amortized O(1)/event.
_FOLD_THRESHOLD = 4000

# In-process memo of the loaded index keyed by (repo, head). A per-trace loop
# caller (e.g. context-tree fan-in) reads many keys against one head; this lets
# them share a single load instead of re-unpickling the base per key. Invalidates
# automatically when the ref head advances (the new head misses the old key).
_INDEX_MEMO: dict[tuple[str, str], "EventIndex"] = {}


# --------------------------------------------------------------------------- #
# Posting extraction (pure; shared by the append path and the full rebuild)
# --------------------------------------------------------------------------- #


def _collect_hexes(value: Any, out: set[str]) -> None:
    """Collect every typed Git object ``hex`` (a ``{algo, hex}`` shape) nested in
    ``value``. Over-inclusive by design: a commit-scoped read intersects this
    with the wanted shas and post-parse-filters on the exact payload key, so a
    stray tree/blob oid here only ever widens candidates, never misses one."""
    if isinstance(value, dict):
        h = value.get("hex")
        a = value.get("algo")
        if isinstance(h, str) and isinstance(a, str):
            out.add(h)
        for child in value.values():
            _collect_hexes(child, out)
    elif isinstance(value, list):
        for item in value:
            _collect_hexes(item, out)


def _posting_from_doc(seq: int, oid: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Build one event's posting from its JSON document (``event.model_dump`` or
    a parsed blob — they are byte-identical).

    The ``trace`` postings reproduce EXACTLY the per-trace membership every
    consumer post-filters to: top-level ``trace_id``, a payload ``trace_id``, or
    a v2 anchor-search summary whose ``results[]`` reference the trace
    (``summary_search_touches_trace``). So an index-served per-trace read and the
    full-scan fallback yield the identical event set.
    """
    traces: set[str] = set()
    commits: set[str] = set()
    patches: set[str] = set()

    tid = doc.get("trace_id")
    if isinstance(tid, str) and tid:
        traces.add(tid)

    payload = doc.get("payload")
    if isinstance(payload, dict):
        ptid = payload.get("trace_id")
        if isinstance(ptid, str) and ptid:
            traces.add(ptid)
        pid = payload.get("trace_patch_id")
        if isinstance(pid, str) and pid:
            patches.add(pid)
        for entry in payload.get("results") or []:
            if not isinstance(entry, dict):
                continue
            rtid = entry.get("trace_id")
            if isinstance(rtid, str) and rtid:
                traces.add(rtid)
            rpid = entry.get("trace_patch_id")
            if isinstance(rpid, str) and rpid:
                patches.add(rpid)
        _collect_hexes(payload, commits)

    return {
        "s": seq,
        "o": oid,
        "t": doc.get("event_type"),
        "tr": sorted(traces),
        "c": sorted(commits),
        "p": sorted(patches),
    }


def _seq_from_path(path: str) -> int | None:
    if not (path.startswith("events/") and path.endswith(".json")):
        return None
    try:
        return int(path[len("events/"):-len(".json")])
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# In-memory index
# --------------------------------------------------------------------------- #


@dataclass
class EventIndex:
    head: str | None = None
    seq_to_oid: dict[int, str] = field(default_factory=dict)
    by_trace: dict[str, list[int]] = field(default_factory=dict)
    by_commit: dict[str, list[int]] = field(default_factory=dict)
    by_type: dict[str, list[int]] = field(default_factory=dict)
    by_patch: dict[str, list[int]] = field(default_factory=dict)

    def apply(self, posting: dict[str, Any]) -> None:
        """Add one posting. Idempotent: a sequence already indexed is ignored, so
        replaying a pending line that was already folded never double-counts."""
        seq = posting["s"]
        if seq in self.seq_to_oid:
            return
        self.seq_to_oid[seq] = posting["o"]
        et = posting.get("t")
        if et:
            self.by_type.setdefault(et, []).append(seq)
        for tr in posting.get("tr") or []:
            self.by_trace.setdefault(tr, []).append(seq)
        for c in posting.get("c") or []:
            self.by_commit.setdefault(c, []).append(seq)
        for p in posting.get("p") or []:
            self.by_patch.setdefault(p, []).append(seq)

    # -- lookups ----------------------------------------------------------- #

    def entries_for_seqs(self, seqs: Iterable[int]) -> list[tuple[str, str]]:
        """Map sequences to ``(path, oid)`` entries in ascending sequence order
        (the canonical event order ``_iter_blobs_batch`` consumes)."""
        out: list[tuple[str, str]] = []
        for seq in sorted(set(seqs)):
            oid = self.seq_to_oid.get(seq)
            if oid is not None:
                out.append((f"events/{seq:012d}.json", oid))
        return out

    def entries_for_trace(self, trace_id: str) -> list[tuple[str, str]]:
        return self.entries_for_seqs(self.by_trace.get(trace_id, ()))

    def entries_for_scoped(
        self,
        *,
        event_types: set[str],
        commit_filter: dict[str, str] | None,
        wanted_shas: set[str],
    ) -> list[tuple[str, str]]:
        """Candidate ``(path, oid)`` entries for a scoped read.

        Plain types (no ``commit_filter``) contribute all their events; filtered
        types (anchor/search keyed on a commit) contribute only events that also
        reference a wanted sha. This is a SUPERSET of the events that pass the
        caller's exact post-parse filter, so applying that same filter to these
        candidates yields the identical result as the full scan — just bounded.
        """
        commit_filter = commit_filter or {}
        seqs: set[int] = set()
        filtered_types = {t for t in event_types if t in commit_filter}
        for t in event_types - filtered_types:
            seqs.update(self.by_type.get(t, ()))
        if filtered_types and wanted_shas:
            commit_seqs: set[int] = set()
            for sha in wanted_shas:
                commit_seqs.update(self.by_commit.get(sha, ()))
            for t in filtered_types:
                seqs.update(set(self.by_type.get(t, ())) & commit_seqs)
        # filtered types with no wanted sha contribute nothing — matching the
        # full reader, which drops a commit-keyed event when no commit matches.
        return self.entries_for_seqs(seqs)


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #


def _index_dir(cwd: Path) -> Path | None:
    """``$GIT_DIR/opentraces/event_index`` — repo-local, never inside a ref, the
    working tree, or the bucket (so the ref OID and bucket digest are untouched).
    Returns None when the git dir can't be resolved (caller full-scans)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (Path(cwd) / git_dir).resolve()
    return git_dir / "opentraces" / "event_index"


def _paths(cwd: Path) -> tuple[Path, Path, Path] | None:
    base = _index_dir(cwd)
    if base is None:
        return None
    return base / "base.pkl", base / "pending.jsonl", base / "head"


def _persisted_head(cwd: Path) -> str | None:
    paths = _paths(cwd)
    if paths is None:
        return None
    _base, _pending, head_path = paths
    try:
        return head_path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _load_base(base_path: Path) -> EventIndex:
    blob = pickle.loads(base_path.read_bytes())
    if not isinstance(blob, dict) or blob.get("format") != _INDEX_FORMAT:
        raise ValueError("incompatible event-index base format")
    idx = EventIndex(head=blob.get("head"))
    idx.seq_to_oid = blob["seq_to_oid"]
    idx.by_trace = blob["by_trace"]
    idx.by_commit = blob["by_commit"]
    idx.by_type = blob["by_type"]
    idx.by_patch = blob["by_patch"]
    return idx


def _replay_pending(idx: EventIndex, pending_path: Path) -> None:
    try:
        raw = pending_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            idx.apply(json.loads(line))
        except (ValueError, KeyError, TypeError):
            # A torn final line (crash mid-append) — stop; the head gate already
            # forces a rebuild whenever the persisted head != the ref head.
            break


def _load_persisted(cwd: Path) -> EventIndex | None:
    """Load base + replay pending into one in-memory index, or None if there is
    no usable persisted index."""
    paths = _paths(cwd)
    if paths is None:
        return None
    base_path, pending_path, head_path = paths
    head = _persisted_head(cwd)
    if head is None:
        return None
    try:
        idx = _load_base(base_path) if base_path.exists() else EventIndex(head=head)
    except Exception:  # noqa: BLE001 — any corruption ⇒ no usable index
        return None
    _replay_pending(idx, pending_path)
    idx.head = head
    return idx


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_head(head_path: Path, head: str) -> None:
    _atomic_write_bytes(head_path, (head + "\n").encode())


def _persist_base(cwd: Path, idx: EventIndex) -> None:
    paths = _paths(cwd)
    if paths is None:
        return
    base_path, pending_path, head_path = paths
    payload = pickle.dumps(
        {
            "format": _INDEX_FORMAT,
            "head": idx.head,
            "seq_to_oid": idx.seq_to_oid,
            "by_trace": idx.by_trace,
            "by_commit": idx.by_commit,
            "by_type": idx.by_type,
            "by_patch": idx.by_patch,
        },
        protocol=pickle.HIGHEST_PROTOCOL,
    )
    _atomic_write_bytes(base_path, payload)
    # The base now covers every folded sequence; truncate pending. ``apply``'s
    # idempotency makes a crash between these two writes a harmless replay.
    try:
        pending_path.unlink()
    except OSError:
        pass
    if idx.head:
        _write_head(head_path, idx.head)


def _pending_line_count(pending_path: Path) -> int:
    try:
        with pending_path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


# --------------------------------------------------------------------------- #
# Public API — read fast-path, append maintenance, rebuild
# --------------------------------------------------------------------------- #


def _extend_to_head(cwd: Path, idx: EventIndex, ref_head: str) -> EventIndex | None:
    """Catch a lagging index up to ``ref_head`` by reading ONLY the delta commits
    (``idx.head..ref_head``), O(delta). Returns the fresh index, or None when the
    persisted head is not an ancestor of ``ref_head`` (history rewrite / import /
    supersede) so the caller can full-rebuild.

    This makes a read self-healing under an advancing ref (another worktree or a
    capture append moved the shared ref) without re-walking the whole log."""
    from .event_log import (  # lazy import breaks the cycle
        _event_blob_entries,
        _is_ancestor,
        _iter_blobs_batch,
    )

    if idx.head is None or not _is_ancestor(cwd, idx.head, ref_head):
        return None
    proc = subprocess.run(
        ["git", "rev-list", "--reverse", f"{idx.head}..{ref_head}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    commits = proc.stdout.split()
    entries = _event_blob_entries(cwd, commits)
    for (path, oid), raw in zip(entries, _iter_blobs_batch(cwd, entries)):
        seq = _seq_from_path(path)
        if seq is None:
            continue
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        idx.apply(_posting_from_doc(seq, oid, doc))
    idx.head = ref_head
    _persist_base(cwd, idx)
    return idx


def fresh_index_for_read(cwd: Path, ref_head: str) -> EventIndex | None:
    """Return an index current at ``ref_head``, or None if none is available.

    Loads the persisted index; if it lags ``ref_head`` but its head is an
    ancestor, it is extended by the delta only (O(delta)) and re-persisted. None
    means the caller must rebuild or full-scan (slow-but-correct) — the index is
    an accelerator, never required for correctness. The (repo, head) memo lets a
    per-trace loop in one process share a single load.
    """
    key = (str(Path(cwd).resolve()), ref_head)
    memo = _INDEX_MEMO.get(key)
    if memo is not None:
        return memo
    persisted_head = _persisted_head(cwd)
    if persisted_head is None:
        return None
    idx = _load_persisted(cwd)
    if idx is None:
        return None
    if idx.head != ref_head:
        idx = _extend_to_head(cwd, idx, ref_head)
        if idx is None:
            return None
    _INDEX_MEMO.clear()  # only ever cache one head at a time
    _INDEX_MEMO[key] = idx
    return idx


def extend_after_append(
    cwd: Path,
    *,
    previous_head: str | None,
    new_head: str,
    new_entries: list[tuple[str, str]],
    events: list[TrailEvent],
) -> None:
    """Maintain the index for K just-appended events — O(K), best-effort.

    Called by the sole appender AFTER the ref advanced ``previous_head ->
    new_head``. Appends K postings to ``pending.jsonl`` and stamps ``new_head``
    when the persisted index is exactly one batch behind (its head ==
    ``previous_head``); otherwise it leaves the index stale (a later read /
    ``rebuild_event_index`` regenerates it). Never raises: a failed index update
    must never fail an append.
    """
    try:
        paths = _paths(cwd)
        if paths is None:
            return
        base_path, pending_path, head_path = paths
        persisted = _persisted_head(cwd)
        # Extend only when the index sits exactly at the pre-append head (this
        # includes the fresh-repo case: persisted is None and previous_head is
        # None). Any other state (missing on a pre-existing log, lagging by more
        # than one batch, diverged) is left for a rebuild — keeps append O(K).
        if persisted != previous_head:
            return

        oid_by_seq: dict[int, str] = {}
        for path, oid in new_entries:
            seq = _seq_from_path(path)
            if seq is not None:
                oid_by_seq[seq] = oid

        lines: list[str] = []
        for event in events:
            oid = oid_by_seq.get(event.event_sequence)
            if oid is None:
                # The new commit's tree did not carry this event blob — the index
                # would be incomplete, so bail to a rebuild rather than persist a
                # gap.
                return
            posting = _posting_from_doc(
                event.event_sequence, oid, event.model_dump(mode="json")
            )
            lines.append(json.dumps(posting, separators=(",", ":"), sort_keys=True))

        base_path.parent.mkdir(parents=True, exist_ok=True)
        if base_path.exists() is False and persisted is None:
            # First append on a fresh repo: seed an empty base so future reads
            # have a base to load even before the first fold.
            _persist_base(cwd, EventIndex(head=None))
        with pending_path.open("a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
        _write_head(head_path, new_head)
        _INDEX_MEMO.clear()

        if _pending_line_count(pending_path) >= _FOLD_THRESHOLD:
            idx = _load_persisted(cwd)
            if idx is not None:
                idx.head = new_head
                _persist_base(cwd, idx)
    except Exception:  # noqa: BLE001 — index maintenance never breaks an append
        return


def rebuild_event_index(cwd: Path, head: str | None = None) -> EventIndex | None:
    """Build (or rebuild) the full index from the canonical log via one scan, and
    persist it. Returns the in-memory index (so a triggering read can use it
    directly), or None when there is no log / the scan fails.

    This is the bootstrap path for a pre-existing log (no index yet) and the
    ``bucket repair`` regenerate path. It is the one O(log) operation here; every
    subsequent read is O(result).
    """
    # Lazy import avoids an import cycle with event_log (which imports this).
    from .event_log import _iter_blobs_batch, _list_event_blob_entries, _ref_head

    cwd = cwd.resolve()
    if head is None:
        head = _ref_head(cwd)
    if head is None:
        return None
    try:
        entries = _list_event_blob_entries(cwd, head)
        idx = EventIndex(head=head)
        for (path, oid), raw in zip(entries, _iter_blobs_batch(cwd, entries)):
            seq = _seq_from_path(path)
            if seq is None:
                continue
            try:
                doc = json.loads(raw)
            except ValueError:
                continue
            idx.apply(_posting_from_doc(seq, oid, doc))
        _persist_base(cwd, idx)
        _INDEX_MEMO.clear()
        _INDEX_MEMO[(str(cwd), head)] = idx
        return idx
    except Exception:  # noqa: BLE001 — a failed rebuild ⇒ caller full-scans
        return None


def invalidate_event_index_memo(cwd: Path | None = None) -> None:
    """Drop the in-process loaded-index memo (tests / mid-process mutation)."""
    if cwd is None:
        _INDEX_MEMO.clear()
        return
    repo_key = str(Path(cwd).resolve())
    for key in list(_INDEX_MEMO.keys()):
        if key[0] == repo_key:
            _INDEX_MEMO.pop(key, None)
