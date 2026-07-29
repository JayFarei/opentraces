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
locations into the canonical event log. It is **digest-excluded** (it lives under
the repo's git dir, never inside a ref, the working tree, or the bucket, so
neither the event-ref OID nor the cross-machine bucket digest changes) and
**rebuildable** (any time it is missing, stale, or unreadable a read rebuilds it
or falls back to the full scan and is slow-but-correct; ``rebuild_event_index`` /
``bucket repair`` can discard and regenerate it). It is therefore an accelerator,
never a source of truth.

Persistence is a SINGLE atomic pickle (``base.pkl``) carrying ``{head, postings}``
together, written via ``os.replace`` (atomic). There is deliberately no separate
head file and no append-only delta sidecar: head and postings can never diverge,
so a reader either sees a self-consistent index at some head or extends/rebuilds
it — there is no window in which the recorded head outruns the postings. Two
concurrent appenders simply last-writer-win on the atomic replace; a base left
lagging the ref is detected by head-equality and caught up by reading only the
``base.head..ref_head`` delta (O(delta)). The tradeoff is that an append
re-persists the whole base (O(total) bytes, like the existing event-log snapshot
with its refresh threshold); an O(K) append-only sidecar was prototyped but
reintroduced a fold-vs-append race (a recorded head outrunning its postings), so
correctness was chosen over append-write size — the O(K) sidecar is a deferred
hardening that needs a proper cross-process lock.
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

from .ids import normalize_id
from .models import TrailEvent

# Bump when the on-disk index format changes so a stale sidecar is discarded
# rather than mis-read.
_INDEX_FORMAT = 2

# In-process memo of the loaded index keyed by (repo, head). A per-trace loop
# caller (e.g. context-tree fan-in) reads many keys against one head; this lets
# them share a single load instead of re-unpickling the base per key. Invalidates
# automatically when the ref head advances (the new head misses the old key).
_INDEX_MEMO: dict[tuple[str, str], "EventIndex"] = {}


# --------------------------------------------------------------------------- #
# Posting extraction (pure; shared by the append path and the full rebuild)
# --------------------------------------------------------------------------- #


def _collect_hexes(value: Any, out: set[str]) -> None:
    """Collect every nested ``hex`` string under a dict that carries one.

    Deliberately matches the read_events_scoped post-parse filter EXACTLY, which
    accepts ``payload[key].get("hex")`` for ANY shape — including a bare
    ``{"hex": sha}`` with no ``algo`` (e.g. a v2 ``search_head`` built without the
    typed GitObjectID, see tests/capture/test_watcher_sweep.py). Requiring
    ``algo`` here would make ``by_commit`` MISS such events while the post-parse
    filter still accepts them, so the index path would return fewer events than
    the full scan. Over-inclusive by design: a commit-scoped read intersects this
    with the wanted shas and post-parse-filters on the exact payload key, so a
    stray tree/blob oid here only ever widens candidates, never misses one."""
    if isinstance(value, dict):
        h = value.get("hex")
        if isinstance(h, str) and h:
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

    ``patches`` likewise has to reproduce EXACTLY the trace_patch_id membership
    ``iter_search_records`` (search_records.py) surfaces a record for — a v3
    summary's ``unanchored_trace_patch_ids`` (the compaction stage's rewrite
    output and maturation's live slim flush, #358/#359) each name an unknown
    outcome's patch EXACTLY, same as a ``results[]`` entry's ``trace_patch_id``,
    just outside that list — so a by-patch lookup must index them too, or an
    unanchored patch searched only via that shape is invisible to it even
    though the tri-shape reader yields a (minimal) record for it.
    """
    traces: set[str] = set()
    commits: set[str] = set()
    patches: set[str] = set()

    def _add_patch_id(value: Any) -> None:
        if not isinstance(value, str) or not value:
            return
        # Preserve the historical wire spelling AND index its canonical
        # normalized alias. Readers use normalized ids from typed refs, while
        # old events commonly carry ``tracepatch-sha256:<digest>`` in the flat
        # field; both keys must resolve the same posting.
        patches.add(value)
        normalized = normalize_id(value)
        if normalized:
            patches.add(normalized)

    tid = doc.get("trace_id")
    if isinstance(tid, str) and tid:
        traces.add(tid)

    payload = doc.get("payload")
    if isinstance(payload, dict):
        ptid = payload.get("trace_id")
        if isinstance(ptid, str) and ptid:
            traces.add(ptid)
        pid = payload.get("trace_patch_id")
        _add_patch_id(pid)
        for entry in payload.get("results") or []:
            if not isinstance(entry, dict):
                continue
            rtid = entry.get("trace_id")
            if isinstance(rtid, str) and rtid:
                traces.add(rtid)
            rpid = entry.get("trace_patch_id")
            _add_patch_id(rpid)
        for upid in payload.get("unanchored_trace_patch_ids") or []:
            _add_patch_id(upid)
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
        re-applying an already-folded sequence never double-counts."""
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

    def entries_for_patches(
        self,
        trace_patch_ids: Iterable[str],
        *,
        event_types: set[str] | None = None,
    ) -> list[tuple[str, str]]:
        """Return the union of postings for a finite patch-id set.

        Format-2 indexes written before normalized aliases were added may only
        contain the historical ``tracepatch-sha256:<digest>`` key. Probe the
        finite, known aliases for each requested id rather than walking every
        ``by_patch`` key; the index format remains backward-compatible and the
        query stays O(number of requested ids). When event types are supplied,
        intersect their postings before resolving blobs so a same-patch fat
        summary is never loaded for an unrelated typed query.
        """

        seqs: set[int] = set()
        for patch_id in trace_patch_ids:
            normalized = normalize_id(patch_id)
            aliases = {
                patch_id,
                normalized,
                f"tracepatch-sha256:{normalized}",
                f"sha256:{normalized}",
                f"ot://trace-patch/sha256/{normalized}",
            }
            for alias in aliases:
                if alias:
                    seqs.update(self.by_patch.get(alias, ()))
        if event_types is not None:
            type_seqs: set[int] = set()
            for event_type in event_types:
                type_seqs.update(self.by_type.get(event_type, ()))
            seqs.intersection_update(type_seqs)
        return self.entries_for_seqs(seqs)

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
# Persistence — a single atomic base.pkl ({head, postings})
# --------------------------------------------------------------------------- #


def _index_dir(cwd: Path) -> Path | None:
    """``$GIT_COMMON_DIR/opentraces/event_index`` — repo-local, never inside a
    ref, the working tree, or the bucket (so the ref OID and bucket digest are
    untouched). Uses ``--git-common-dir`` (not ``--git-dir``) so N linked
    worktrees share the ONE index under the common git dir instead of a
    per-worktree copy each (#169 C). Returns None when the git dir can't be
    resolved (caller full-scans)."""
    proc = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
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


def _base_path(cwd: Path) -> Path | None:
    base = _index_dir(cwd)
    return None if base is None else base / "base.pkl"


def _load_persisted(cwd: Path) -> EventIndex | None:
    """Load the persisted index, or None if missing/corrupt. Head and postings
    are loaded together from one atomic file, so they are always consistent."""
    base_path = _base_path(cwd)
    if base_path is None or not base_path.is_file():
        return None
    try:
        blob = pickle.loads(base_path.read_bytes())
        if not isinstance(blob, dict) or blob.get("format") != _INDEX_FORMAT:
            return None
        idx = EventIndex(head=blob.get("head"))
        idx.seq_to_oid = blob["seq_to_oid"]
        idx.by_trace = blob["by_trace"]
        idx.by_commit = blob["by_commit"]
        idx.by_type = blob["by_type"]
        idx.by_patch = blob["by_patch"]
        return idx
    except Exception:  # noqa: BLE001 — any corruption ⇒ no usable index
        return None


def _persist(cwd: Path, idx: EventIndex) -> None:
    """Atomically write the index. Best-effort; an accelerator never fatal."""
    base_path = _base_path(cwd)
    if base_path is None:
        return
    try:
        base_path.parent.mkdir(parents=True, exist_ok=True)
        # FIXED reusable tmp name + streaming dump (see event_log._save_event_snapshot):
        # a hard kill cannot strand a fresh random orphan each save (#169 C).
        tmp = base_path.with_name(base_path.name + ".tmp")
        try:
            with open(tmp, "wb") as fh:
                pickle.dump(
                    {
                        "format": _INDEX_FORMAT,
                        "head": idx.head,
                        "seq_to_oid": idx.seq_to_oid,
                        "by_trace": idx.by_trace,
                        "by_commit": idx.by_commit,
                        "by_type": idx.by_type,
                        "by_patch": idx.by_patch,
                    },
                    fh,
                    protocol=pickle.HIGHEST_PROTOCOL,
                )
            os.replace(tmp, base_path)  # atomic: head + postings swap together
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception:  # noqa: BLE001 — cache is an optimization, never fatal
        return


# --------------------------------------------------------------------------- #
# Public API — read fast-path, append maintenance, rebuild
# --------------------------------------------------------------------------- #


def _apply_delta(cwd: Path, idx: EventIndex, since: str | None, head: str) -> bool:
    """Apply the event postings in ``since..head`` (or all of ``head`` when
    ``since`` is None) into ``idx``. Returns False on a git failure so the caller
    can fall back to a rebuild. Reads only the delta commits' event blobs."""
    from .event_log import (  # lazy import breaks the cycle with event_log
        _event_blob_entries,
        _iter_blobs_batch,
    )

    spec = f"{since}..{head}" if since else head
    proc = subprocess.run(
        ["git", "rev-list", "--reverse", spec],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
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
    return True


def _is_ancestor(cwd: Path, ancestor: str, descendant: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=cwd,
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


def fresh_index_for_read(cwd: Path, ref_head: str) -> EventIndex | None:
    """Return an index current at ``ref_head``, or None if none is available.

    Loads the persisted index; if it lags ``ref_head`` but its head is an
    ancestor, it is caught up by reading only the ``base.head..ref_head`` delta
    (O(delta)) and re-persisted. Because head and postings are persisted together
    atomically, a loaded index is always self-consistent — there is no
    "recorded head outran its postings" window to guard against. None means the
    caller must rebuild or full-scan (slow-but-correct) — the index is an
    accelerator, never required for correctness. The (repo, head) memo lets a
    per-trace loop in one process share a single load.
    """
    key = (str(Path(cwd).resolve()), ref_head)
    memo = _INDEX_MEMO.get(key)
    if memo is not None:
        return memo
    idx = _load_persisted(cwd)
    if idx is None or idx.head is None:
        return None
    if idx.head != ref_head:
        if not _is_ancestor(cwd, idx.head, ref_head):
            return None  # history rewrite / import / supersede → rebuild
        if not _apply_delta(cwd, idx, idx.head, ref_head):
            return None
        idx.head = ref_head
        _persist(cwd, idx)
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
    """Maintain the index for K just-appended events — best-effort.

    Called by the sole appender AFTER the ref advanced ``previous_head ->
    new_head``. Extends the persisted index with the K new events (using the new
    commit's own tree OIDs) and atomically re-persists it, but ONLY when the
    persisted index sits exactly at the pre-append head (this includes the
    fresh-repo case: persisted None and previous_head None). Any other state
    (missing on a pre-existing log, lagging by more than one batch, diverged) is
    left for a read / ``rebuild_event_index`` to regenerate. Never raises: a
    failed index update must never fail an append.
    """
    try:
        idx = _load_persisted(cwd)
        persisted_head = idx.head if idx is not None else None
        # Extend only from exactly the pre-append head; else leave for rebuild.
        if persisted_head != previous_head:
            return
        if idx is None:
            idx = EventIndex(head=None)

        oid_by_seq: dict[int, str] = {}
        for path, oid in new_entries:
            seq = _seq_from_path(path)
            if seq is not None:
                oid_by_seq[seq] = oid

        for event in events:
            oid = oid_by_seq.get(event.event_sequence)
            if oid is None:
                # The new commit's tree did not carry this event blob — the index
                # would be incomplete, so bail to a rebuild rather than persist a
                # gap.
                return
            idx.apply(
                _posting_from_doc(
                    event.event_sequence, oid, event.model_dump(mode="json")
                )
            )
        idx.head = new_head
        _persist(cwd, idx)
        _INDEX_MEMO.clear()
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
    from .event_log import _ref_head  # lazy import breaks the cycle

    cwd = cwd.resolve()
    if head is None:
        head = _ref_head(cwd)
    if head is None:
        return None
    try:
        idx = EventIndex(head=head)
        if not _apply_delta(cwd, idx, None, head):
            return None
        _persist(cwd, idx)
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
