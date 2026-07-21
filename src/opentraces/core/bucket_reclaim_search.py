"""Anchor-search compaction reclaim pass (issue #358).

``search_compaction.py`` is a capability, not a live path — it can turn a
mixed legacy/v2-fat/v3 event stream into a v3-compact one, but nothing calls
it. This module is the wiring: it walks every project the bucket knows about,
finds the ones actually carrying legacy per-patch or v2-fat
``git_anchor_search_completed`` events, and for each one:

1. Compacts the project's canonical event chain and swaps the Git ref in
   place (``import_event_log`` — build the new chain, then ONE
   compare-and-swap ``update-ref``; a no-op when the chain is already
   compact, per its own idempotency check).
2. Reconciles the bucket's events mirror (``bucket/events/v1/``) so it stops
   carrying byte-identical copies of the same fat content. The mirror is a
   single flat batch stream shared across every synced project (no
   per-project namespacing exists in the bucket layout today — the SAME
   limitation ``bucket_repair`` already documents for its own mirror sync
   step), so reconciliation touches ONLY this project's own batch files
   (see ``_project_batch_layout`` / ``_reconcile_mirror_for_project``) —
   never another project's.
3. Regenerates ONLY the trail companions a fat/legacy search event actually
   touched (``project_per_trace_exports``, atomic same-bytes-skip writers —
   already-current companions cost nothing to "regenerate").

Crash safety (issue #358 repair): a compaction pass rewrites history — unlike
the normal append-only flow every other mirror writer assumes — so two
invariants the rest of the system leans on need explicit protection here,
not just "write-new-then-remove-stale" ordering:

* Which mirror event_ids are now STALE is only readable from the canonical
  ref BEFORE the ref swap; once ``import_event_log`` moves the ref, a killed
  run's resume reads the ALREADY-compacted chain, and — because
  ``compact_search_events`` is idempotent on already-compact input — would
  recompute ``old_ids == target_ids`` and see NO stale ids at all, silently
  abandoning the actual stale mirror content from before the crash. A
  per-project journal (``_write_journal`` / ``_read_journal`` /
  ``_clear_journal``, under ``bucket/reclaim/anchor_search/``) is written
  durably BEFORE the ref swap and consumed on resume instead of recomputing,
  so the removal target survives however far the interrupted run got.
* This project's mirror batch files must be assigned the EXACT (seq,
  batch_id) pairs a standalone ``sync_events_mirror`` full rebuild of this
  project's OWN canonical events would independently derive
  (``_project_batch_layout`` — sorted by ``event_sequence``, grouped by
  ``batch_id`` in first-encounter order, seq from 1). Anything else — e.g.
  renumbering against a list that also contains OTHER projects' mirror
  events — produces filenames a LATER, independent sync of this project
  would never reproduce; since ``sync_events_mirror`` only adds files and
  never deletes, the old and new names would both persist forever
  (duplicate events, broken replay contiguity). The rewritten index also
  stamps the REAL new ``event_log_head`` (not ``None``) so that later sync
  takes its incremental/no-op path instead of a full rebuild.

Honest boundary: a project whose Git repo is unreachable can only be
compacted from the bucket's own mirror, and — because the mirror carries no
per-project attribution — that is only sound when this project is the ONLY
one the bucket knows about (so the whole mirror is unambiguously its own).
A multi-project bucket with an unreachable project's repo is reported
``skipped`` with that reason; its bytes are left untouched rather than risk
touching a different project's data. Blobs, snapshots, and non-search events
are never touched by this pass, matching ``search_compaction``'s own scope.

Reported bytes are honest about what ``import_event_log`` does NOT free: it
writes a whole new chain and moves the ref, but the OLD chain's objects stay
in the Git object database (unreachable, retained — this module never runs
``git gc``/``prune``). The headline ``bytes_reclaimed`` therefore counts only
the companion-file shrink that is REALLY on disk after ``apply``; the ref-side
delta is reported separately as ``ref_bytes_reclaimable_after_gc``.

Same O(corpus) class as ``bucket repair`` — a cheap byte-size prefilter (see
``_events_tree_bytes``; no JSON parsing) skips the full read+compact for a
project whose canonical event history is too small to hold anything worth
reclaiming (bypassed when a resume journal is present, since a just-compacted
ref's tree can legitimately be smaller than the prefilter threshold).
"""

from __future__ import annotations

import gzip
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from . import paths
from ._bucket_io import (
    _atomic_write_bytes,
    _atomic_write_json,
    _canonical_json,
    _gzip_deterministic,
    _read_gzip_bytes,
)
from ._time import utc_now_str
from .bucket_envelope import _events_for_trace_from_iter, _iter_opted_in_projects, project_per_trace_exports
from .bucket_events import BUCKET_EVENTS_INDEX_SCHEMA, read_events_mirror_batches
from .bucket_layout import _path_part, events_v1_batches_dir, events_v1_index_path, traces_v1_root, trace_v1_trail_path
from .trails import EVENT_LOG_REF, ANCHOR_SEARCH_EVENT_TYPE, TrailEvent, import_event_log, read_events
from .trails.search_compaction import compact_search_events

# Below this many raw bytes in a project's canonical ``events/`` tree, a full
# read+compact is not worth the O(corpus) cost — this only needs to catch the
# "no anchor-search history at all" case cheaply (a v2-fat summary is the
# reported 26 GB driver; even a single such blob dwarfs this). Kept low so a
# project with a genuinely small, already-mostly-compact search history still
# gets read (companions/mirror reconciliation can be needed even when there
# is nothing left to compact — see the resume note on ``_process_reachable_
# project``).
_FAT_PREFILTER_MIN_BYTES = 64

_RECLAIM_WRITER = "bucket-reclaim-anchor-search"

_JOURNAL_SCHEMA = "opentraces.bucket.reclaim.anchor_search_journal.v1"


def _events_tree_bytes(repo: Path, head: str) -> int:
    """Sum blob sizes for every historical ``events/*.json`` object reachable
    from ``head`` — no object CONTENT is fetched, only sizes.

    Each batch commit's tree is non-cumulative (``event_log._write_batch_tree``
    starts every call from an empty index — see its own ``read-tree --empty``),
    so a single ``git ls-tree -l <head> events`` only ever sees the LATEST
    batch. Walking the full object graph (``git rev-list --objects`` restricted
    to the ``events`` pathspec, then ``cat-file --batch-check`` for sizes) is
    what actually finds the historical fat this prefilter exists to catch —
    two subprocess calls total, regardless of how many batches the log has.
    """

    proc = subprocess.run(
        ["git", "rev-list", "--objects", head, "--", "events"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return 0
    oids: list[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == 2 and parts[1].startswith("events/") and parts[1].endswith(".json"):
            oids.append(parts[0])
    if not oids:
        return 0
    proc2 = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectsize)"],
        cwd=repo, input="\n".join(oids) + "\n", capture_output=True, text=True, check=False,
    )
    if proc2.returncode != 0:
        return 0
    total = 0
    for line in proc2.stdout.splitlines():
        try:
            total += int(line.strip())
        except ValueError:
            continue
    return total


def _head_sha(repo: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", EVENT_LOG_REF],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def _search_touch_ids(events: list[Any]) -> set[str]:
    """Trace ids a fat/legacy, v2-fat, OR v3(-compact) search event touches
    (see ``search_records.summary_search_touches_trace`` for the same
    ``results[]`` scan over a single event). Over-inclusive is safe here (an
    already-current companion costs nothing extra to regenerate);
    under-inclusive is not.

    A v3-compact ``unanchored_trace_patch_ids`` entry carries no trace_id of
    its own (the documented delta in ``search_compaction``'s module
    docstring), so this also resolves each one via the trace_patch_id ->
    trace_id a ``trace_patch_created`` event in the SAME stream carries.
    Those are never touched by compaction, so on a resumed run -- the ref
    already compacted, the ORIGINAL fat/legacy event (which used to carry the
    trace_id directly) already gone -- this is the only surviving way to
    learn that a now fully-untouched trace's companion still needs its stale
    search event dropped."""

    patch_to_trace: dict[str, str] = {}
    for event in events:
        if event.event_type == "trace_patch_created" and event.trace_id:
            patch_id = (event.payload or {}).get("trace_patch_id")
            if patch_id:
                patch_to_trace[patch_id] = event.trace_id

    ids: set[str] = set()
    for event in events:
        if event.event_type != ANCHOR_SEARCH_EVENT_TYPE:
            continue
        if event.trace_id:
            ids.add(event.trace_id)
        payload = event.payload or {}
        for entry in payload.get("results") or []:
            tid = entry.get("trace_id") if isinstance(entry, dict) else None
            if tid:
                ids.add(tid)
        for patch_id in payload.get("unanchored_trace_patch_ids") or []:
            tid = patch_to_trace.get(patch_id)
            if tid:
                ids.add(tid)
    return ids


def _trail_gzip_size(trail_events: list[Any]) -> int:
    """Exact ``trail.jsonl.gz`` byte size for ``trail_events`` — the same
    construction ``bucket_envelope._write_per_trace_envelope`` writes, so
    this is a real measurement (not an estimate) whether or not it is ever
    written to disk."""

    lines = [_canonical_json(event.model_dump(mode="json")) for event in trail_events]
    body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    return len(_gzip_deterministic(body))


def _event_blob_bytes(event: Any) -> int:
    """Exact per-event Git blob size the writer would produce for ``event``
    (mirrors ``event_log._write_batch_tree``'s blob content exactly), used
    to report a real projected ref size without writing anything."""

    text = json.dumps(event.model_dump(mode="json"), sort_keys=True, indent=2) + "\n"
    return len(text.encode("utf-8"))


def _bucket_known_slugs() -> set[str]:
    root = traces_v1_root()
    if not root.exists():
        return set()
    return {unquote(child.name) for child in root.iterdir() if child.is_dir()}


# ---------------------------------------------------------------------------
# Per-project resume journal — durable memory of a stale-id removal decision
# and (for the mirror-only path) the compacted target itself, so a killed run
# resumes from what it originally decided rather than re-deriving from a
# source that may itself now be partially mutated. See the module docstring's
# "Crash safety" section for why this is load-bearing, not belt-and-suspenders.
# ---------------------------------------------------------------------------


def _journal_path(slug: str) -> Path:
    return paths.bucket_dir() / "reclaim" / "anchor_search" / f"{_path_part(slug)}.json"


def _read_journal(slug: str) -> dict[str, Any] | None:
    path = _journal_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema_version") != _JOURNAL_SCHEMA:
        return None
    return data


def _write_journal(
    slug: str,
    *,
    old_event_ids: set[str],
    affected_trace_ids: set[str],
    compacted_events: list[Any] | None = None,
    events_before: int | None = None,
    events_after: int | None = None,
    legacy_events_collapsed: int | None = None,
    fat_summaries_rewritten: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "schema_version": _JOURNAL_SCHEMA,
        "project_slug": slug,
        # This project's FULL pre-compaction id set (not just the ones that
        # changed) — see ``_reconcile_mirror_for_project`` for why the wider
        # scope is what safe old-file removal actually needs.
        "old_event_ids": sorted(old_event_ids),
        "affected_trace_ids": sorted(affected_trace_ids),
        "written_at": utc_now_str(),
    }
    if compacted_events is not None:
        payload["compacted_events"] = [e.model_dump(mode="json") for e in compacted_events]
    if events_before is not None:
        payload["events_before"] = events_before
    if events_after is not None:
        payload["events_after"] = events_after
    if legacy_events_collapsed is not None:
        payload["legacy_events_collapsed"] = legacy_events_collapsed
    if fat_summaries_rewritten is not None:
        payload["fat_summaries_rewritten"] = fat_summaries_rewritten
    _atomic_write_json(_journal_path(slug), payload)


def _clear_journal(slug: str) -> None:
    _journal_path(slug).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Mirror reconciliation — scoped to exactly one project's own batch layout.
# ---------------------------------------------------------------------------


def _project_batch_layout(events: list[Any]) -> list[tuple[int, str, list[Any]]]:
    """Assign ``(seq, batch_id, group)`` exactly as ``sync_events_mirror``'s
    own full-rebuild algorithm would for ``events`` read in isolation
    (sorted by ``event_sequence``, grouped by ``batch_id`` in first-encounter
    order, seq starting at 1 — see ``bucket_events.py``'s full-rebuild path).

    Matching that algorithm exactly is what keeps a LATER, independent
    ``sync_events_mirror`` call idempotent against what reclaim already wrote:
    if reclaim invented its own numbering (e.g. from a globally-renumbered
    list spanning every project sharing the mirror), a subsequent real
    rebuild of just this project's events would derive DIFFERENT filenames
    for the SAME content, and since ``sync_events_mirror`` only adds files —
    it never deletes — the old and new names would both persist forever.
    """

    ordered = sorted(events, key=lambda e: e.event_sequence)
    order: list[str] = []
    by_batch: dict[str, list[Any]] = {}
    for event in ordered:
        if event.batch_id not in by_batch:
            by_batch[event.batch_id] = []
            order.append(event.batch_id)
        by_batch[event.batch_id].append(event)
    return [(seq, bid, by_batch[bid]) for seq, bid in enumerate(order, start=1)]


def _reconcile_mirror_for_project(
    compacted: list[Any], *, old_ids: set[str], event_log_head: str | None, repo_id: str
) -> tuple[int, int]:
    """Reconcile ONLY this project's slice of the shared events mirror.

    Writes this project's own deterministic batch layout (``_project_batch_
    layout``) FIRST, then removes any on-disk batch file that (a) belongs
    entirely to this project (every event_id it holds is in ``old_ids``,
    this project's FULL pre-compaction id set) and (b) is not one of the
    files just written as current. ``old_ids`` — not just the ids that
    actually changed — is the right removal scope because ``compact_search_
    events`` always emits a SINGLE unified batch for the whole output
    stream (``search_compaction._refinalize`` stamps every slot, passthrough
    or rewritten, with the same ``batch_id``): even an UNCHANGED event (same
    event_id, since ``batch_id`` sits outside the content hash) moves out of
    whatever old batch file it used to live in and into that one new file,
    so the old file holding it is now redundant even though none of its
    events are individually "stale". New files are written BEFORE any old
    file is removed (write-new-then-remove-stale), so a kill mid-call leaves
    a strict superset on disk. Issue #358 repair: compaction re-chains the
    WHOLE stream (``search_compaction._refinalize`` reassigns ``event_
    sequence``/``previous_event_id`` from the first touched slot onward), so
    only the untouched PREFIX before that slot keeps its original
    ``event_id`` -- everything from there on gets a fresh one. A reader mid-
    window therefore sees two disjoint kinds of leftover: true duplicates
    (the untouched prefix, same ``event_id`` in both the old and new file --
    ``read_events_mirror_batches`` in ``bucket_events.py`` collapses these on
    read, so they alone never break replay) and genuinely SUPERSEDED events
    (the old shape of whatever got rewritten, a DIFFERENT ``event_id`` with
    no counterpart to collapse against -- no generic reader can safely tell
    these apart from real content without this project's own canonical
    order). Full consistency for a reader mid-window therefore depends on
    this project's OWN reconcile finishing, not on read-side tolerance alone
    -- see ``resume_pending_anchor_search_journals``, which ``bucket_repair``
    runs before its own per-project mirror sync specifically so nothing
    routine has to wait for a human to re-run ``bucket reclaim --apply``.

    ``event_log_head`` is stamped into the index exactly (the real,
    just-swapped ref head, or the still-valid preserved head for a
    mirror-only compaction) so the NEXT ``sync_events_mirror`` for this
    project takes its cheap incremental/no-op path instead of a full
    rebuild — a full rebuild after a HISTORY REWRITE (not a normal append)
    is exactly the case the incremental fast path's ancestor check cannot
    shortcut, and a wrong (``None``) head is what forces the duplicate-
    producing full-rebuild-without-cleanup path in the first place.

    Returns ``(events_removed, events_added)``.
    """

    batches_dir = events_v1_batches_dir()
    batches_dir.mkdir(parents=True, exist_ok=True)

    layout = _project_batch_layout(compacted)
    new_paths: set[Path] = set()
    added = 0
    latest_seq = 0
    last_batch_id: str | None = None
    for seq, bid, group in layout:
        lines = [_canonical_json(e.model_dump(mode="json")) for e in group]
        body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
        compressed = _gzip_deterministic(body)
        path = batches_dir / f"{seq:012d}-{_path_part(bid)}.jsonl.gz"
        new_paths.add(path)
        if not (path.exists() and path.read_bytes() == compressed):
            _atomic_write_bytes(path, compressed)
            added += 1
        latest_seq = max(latest_seq, group[-1].event_sequence)
        last_batch_id = bid

    removed = 0
    if old_ids:
        for old_path in sorted(batches_dir.glob("*.jsonl.gz")):
            if old_path in new_paths:
                continue
            try:
                raw = _read_gzip_bytes(old_path).decode("utf-8")
            except (OSError, gzip.BadGzipFile):
                continue
            file_ids = {
                json.loads(line)["event_id"] for line in raw.splitlines() if line.strip()
            }
            if file_ids and file_ids <= old_ids:
                old_path.unlink(missing_ok=True)
                removed += len(file_ids)

    index = {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "repo_id": repo_id,
        "event_log_ref": EVENT_LOG_REF,
        "event_log_head": event_log_head,
        "batch_count": len(layout),
        "last_batch_id": last_batch_id,
        "latest_event_sequence": latest_seq,
        "state": "ok" if compacted else "missing",
        "updated_at": utc_now_str(),
        "batches_written": added,
    }
    _atomic_write_json(events_v1_index_path(), index)
    return removed, added


@dataclass
class ProjectAnchorSearchReport:
    project_slug: str
    repo_reachable: bool
    action: str = "clean"
    reason: str | None = None
    events_before: int = 0
    events_after: int = 0
    legacy_events_collapsed: int = 0
    fat_summaries_rewritten: int = 0
    ref_rewritten: bool = False
    ref_bytes_before: int = 0
    ref_bytes_after: int = 0
    mirror_rewritten: bool = False
    mirror_events_removed: int = 0
    mirror_events_added: int = 0
    companion_bytes_before: int = 0
    companion_bytes_after: int = 0
    companions_regenerated: list[str] = field(default_factory=list)

    @property
    def bytes_before(self) -> int:
        # Companion-only: the ref side is never counted here — see
        # ``ref_bytes_reclaimable_after_gc``.
        return self.companion_bytes_before

    @property
    def bytes_after(self) -> int:
        return self.companion_bytes_after

    @property
    def bytes_reclaimed(self) -> int:
        return self.bytes_before - self.bytes_after

    @property
    def ref_bytes_reclaimable_after_gc(self) -> int:
        """The ref-side delta is NOT yet freed disk space: ``import_event_
        log`` writes a whole new chain and moves the ref, but the OLD
        chain's objects stay in the Git object database (unreachable,
        retained — this module never runs ``git gc``/``prune``). Reported
        separately and honestly, so ``bytes_reclaimed`` never claims disk
        space that is still sitting in ``.git`` until gc actually runs."""
        return max(0, self.ref_bytes_before - self.ref_bytes_after)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_reachable": self.repo_reachable,
            "action": self.action,
            "reason": self.reason,
            "events_before": self.events_before,
            "events_after": self.events_after,
            "legacy_events_collapsed": self.legacy_events_collapsed,
            "fat_summaries_rewritten": self.fat_summaries_rewritten,
            "ref_rewritten": self.ref_rewritten,
            "ref_bytes_before": self.ref_bytes_before,
            "ref_bytes_after": self.ref_bytes_after,
            "ref_bytes_reclaimable_after_gc": self.ref_bytes_reclaimable_after_gc,
            "mirror_rewritten": self.mirror_rewritten,
            "mirror_events_removed": self.mirror_events_removed,
            "mirror_events_added": self.mirror_events_added,
            "companions_regenerated": list(self.companions_regenerated),
            "companion_count": len(self.companions_regenerated),
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "bytes_reclaimed": self.bytes_reclaimed,
        }


@dataclass
class AnchorSearchReclaimReport:
    apply: bool
    projects: list[ProjectAnchorSearchReport] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def bytes_reclaimed(self) -> int:
        return sum(p.bytes_before - p.bytes_after for p in self.projects)

    def as_dict(self) -> dict[str, Any]:
        return {
            "apply": self.apply,
            "projects": [p.as_dict() for p in self.projects],
            "project_count": len(self.projects),
            "bytes_reclaimed": self.bytes_reclaimed,
            "errors": list(self.errors),
        }


def _companion_deltas(
    slug: str, affected_trace_ids: set[str], compacted: list[Any]
) -> tuple[int, int, bool]:
    """Per-trace real-on-disk-vs-would-be-projected comparison. Summed
    before/after for reporting, plus a per-trace ``any_changed`` flag — a
    resumed run (ref already compacted, so ``compact_search_events`` finds
    nothing new THIS pass) still needs to know whether a companion is stale
    from an EARLIER, interrupted pass; summed bytes alone could coincidentally
    match even when individual traces differ, so this compares per trace."""

    before = 0
    after = 0
    any_changed = False
    for trace_id in sorted(affected_trace_ids):
        path = trace_v1_trail_path(slug, trace_id)
        b = path.stat().st_size if path.exists() else 0
        trail_events, _context_events = _events_for_trace_from_iter(compacted, trace_id)
        a = _trail_gzip_size(trail_events)
        before += b
        after += a
        if b != a:
            any_changed = True
    return before, after, any_changed


def _process_reachable_project(
    slug: str, repo: Path, *, apply: bool
) -> ProjectAnchorSearchReport:
    report = ProjectAnchorSearchReport(project_slug=slug, repo_reachable=True)

    journal = _read_journal(slug)

    head = _head_sha(repo)
    if head is None:
        if journal is None:
            report.action = "clean"
            report.reason = "no canonical event log for this project"
            return report
        # A journal for this project is only ever written (below) after a
        # run found a real ``head`` here, so its existence is proof this ref
        # held real events at some point. Its absence now is NOT "compacted
        # to nothing" -- treating it that way would derive an empty target
        # from ``old_events = []`` and, via the journal's own removal scope,
        # let ``_reconcile_mirror_for_project`` delete every mirror batch
        # file for this project (the sole surviving copy once the ref is
        # gone) and regenerate every affected companion to empty (issue
        # #358 repair finding: a re-clone or damaged repo during crash
        # recovery loses the ref between the journal write and resume).
        # Refuse instead -- the per-project try/except in
        # ``reclaim_anchor_search`` turns this into an ``action="error"``
        # row without touching the mirror or companions, and the journal is
        # left in place so a resume once the ref is restored still recovers.
        raise RuntimeError(
            f"project {slug!r} has a pending reclaim journal but its "
            f"canonical event ref ({EVENT_LOG_REF}) is missing; refusing to "
            f"compact to an empty chain -- restore the ref before retrying, "
            f"or remove {_journal_path(slug)} manually if the loss is "
            f"intentional"
        )

    if head is not None:
        tree_bytes = _events_tree_bytes(repo, head)
        report.ref_bytes_before = report.ref_bytes_after = tree_bytes
        if journal is None and tree_bytes < _FAT_PREFILTER_MIN_BYTES:
            report.reason = "below fat-detection size prefilter"
            return report

    old_events = read_events(repo, verify=False) if head is not None else []
    compacted, stats = compact_search_events(old_events)
    report.events_before = len(old_events)
    report.events_after = len(compacted)
    report.legacy_events_collapsed = stats.legacy_search_events_in
    report.fat_summaries_rewritten = stats.fat_summaries_rewritten

    old_ids = {e.event_id for e in old_events}
    target_ids = {e.event_id for e in compacted}
    ref_would_change = old_ids != target_ids

    fresh_affected = _search_touch_ids(old_events) | _search_touch_ids(compacted)
    if journal is not None:
        # Trust the ORIGINAL run's decision over whatever this run's fresh
        # read shows — see the module docstring's "Crash safety" section for
        # why the fresh read can no longer be trusted once the ref has moved.
        journaled_old_ids = set(journal["old_event_ids"])
        affected = set(journal["affected_trace_ids"]) | fresh_affected
    else:
        journaled_old_ids = old_ids
        affected = fresh_affected
    stale_ids = journaled_old_ids - target_ids

    comp_before, comp_after, companions_stale = _companion_deltas(slug, affected, compacted)

    # Mirror preview — read-only, safe in both modes. Computed (and, below,
    # ALWAYS acted on) regardless of ``ref_would_change`` so a resume — the
    # ref already swapped by an earlier, interrupted run, ``compact_search_
    # events`` finding nothing new THIS pass — still finishes a mirror or
    # companion step that pass never reached.
    mirror_present = events_v1_index_path().exists()
    mirror_events: list[Any] = list(read_events_mirror_batches()) if mirror_present else []
    existing_ids = {e.event_id for e in mirror_events}
    to_add = [e for e in compacted if e.event_id not in existing_ids]
    would_touch_mirror = mirror_present and (bool(stale_ids & existing_ids) or bool(to_add))
    report.mirror_events_removed = len(stale_ids & existing_ids)
    report.mirror_events_added = len(to_add)

    if journal is None and not (ref_would_change or would_touch_mirror or companions_stale):
        report.reason = "no legacy or v2-fat anchor-search events found"
        return report

    report.companion_bytes_before = comp_before
    report.companion_bytes_after = comp_after
    report.companions_regenerated = sorted(affected)
    report.action = "compacted"

    if not apply:
        report.ref_bytes_after = sum(_event_blob_bytes(e) for e in compacted) if compacted else 0
        report.mirror_rewritten = would_touch_mirror
        return report

    if journal is None and (ref_would_change or would_touch_mirror):
        # Durable BEFORE the ref swap below — see the module docstring.
        _write_journal(slug, old_event_ids=old_ids, affected_trace_ids=affected)

    if head is not None:
        import_event_log(repo, compacted, writer=_RECLAIM_WRITER, force=True)
    report.ref_rewritten = ref_would_change
    new_head = _head_sha(repo) if head is not None else None
    report.ref_bytes_after = _events_tree_bytes(repo, new_head) if new_head else report.ref_bytes_before

    if would_touch_mirror or stale_ids:
        removed, added = _reconcile_mirror_for_project(
            compacted, old_ids=journaled_old_ids, event_log_head=new_head, repo_id=slug,
        )
        report.mirror_events_removed = removed
        report.mirror_events_added = added
        report.mirror_rewritten = True

    _clear_journal(slug)

    for trace_id in sorted(affected):
        project_per_trace_exports(
            repo, project_slug=slug, trace_id=trace_id, events=compacted, events_authoritative=True,
        )

    return report


def _process_unreachable_project(slug: str, *, apply: bool, single_project_bucket: bool) -> ProjectAnchorSearchReport:
    report = ProjectAnchorSearchReport(project_slug=slug, repo_reachable=False)

    if not single_project_bucket:
        report.action = "skipped"
        report.reason = (
            "repo unreachable and the bucket holds more than one project; "
            "the events mirror carries no per-project attribution, so this "
            "project's chain cannot be safely isolated for mirror-only compaction"
        )
        return report

    if not events_v1_index_path().exists():
        report.action = "clean"
        report.reason = "no events mirror present"
        return report

    # The pre-existing head: the canonical ref is NEVER rewritten on this
    # path (repo unreachable), so this value stays accurate no matter what
    # this pass does to the mirror — preserving it (instead of stamping
    # ``None``) is what lets a LATER sync, once the project is reachable
    # again, take its cheap incremental/no-op path rather than a full
    # rebuild that never cleans up the stale files it leaves alongside the
    # ones this pass wrote (issue #358 repair finding).
    try:
        prior_index = json.loads(events_v1_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior_index = {}
    preserved_head = prior_index.get("event_log_head") if isinstance(prior_index, dict) else None

    journal = _read_journal(slug)
    old_events: list[Any] = []
    if journal is not None:
        compacted = [TrailEvent.model_validate(e) for e in journal["compacted_events"]]
        journaled_old_ids = set(journal["old_event_ids"])
        affected = set(journal["affected_trace_ids"])
        report.events_before = journal.get("events_before", 0)
        report.events_after = journal.get("events_after", 0)
        report.legacy_events_collapsed = journal.get("legacy_events_collapsed", 0)
        report.fat_summaries_rewritten = journal.get("fat_summaries_rewritten", 0)
    else:
        old_events = list(read_events_mirror_batches())
        if not old_events:
            report.action = "clean"
            report.reason = "events mirror is empty"
            return report
        compacted, stats = compact_search_events(old_events)
        report.events_before = len(old_events)
        report.events_after = len(compacted)
        report.legacy_events_collapsed = stats.legacy_search_events_in
        report.fat_summaries_rewritten = stats.fat_summaries_rewritten
        journaled_old_ids = {e.event_id for e in old_events}
        affected = _search_touch_ids(old_events) | _search_touch_ids(compacted)

    target_ids = {e.event_id for e in compacted}
    stale_ids = journaled_old_ids - target_ids
    existing_ids = {e.event_id for e in read_events_mirror_batches()}
    to_add = [e for e in compacted if e.event_id not in existing_ids]
    mirror_would_change = bool(stale_ids & existing_ids) or bool(to_add)

    comp_before, comp_after, companions_stale = _companion_deltas(slug, affected, compacted)

    if journal is None and not (mirror_would_change or companions_stale):
        report.reason = "no legacy or v2-fat anchor-search events found"
        return report

    report.companion_bytes_before = comp_before
    report.companion_bytes_after = comp_after
    report.companions_regenerated = sorted(affected)
    report.action = "mirror_only_compacted"
    report.reason = "repo unreachable; canonical ref not rewritten, mirror-only"
    report.mirror_events_removed = len(stale_ids & existing_ids)
    report.mirror_events_added = len(to_add)
    report.mirror_rewritten = mirror_would_change

    if not apply:
        return report

    if journal is None and mirror_would_change:
        # Durable BEFORE this pass mutates the mirror — the mirror is the
        # ONLY source of truth on this path (no canonical ref to fall back
        # on), so a killed run must not re-derive its target from whatever
        # partial mix of old/new content it left behind; it replays the
        # ORIGINAL decision instead. See the module docstring.
        _write_journal(
            slug,
            old_event_ids=journaled_old_ids,
            affected_trace_ids=affected,
            compacted_events=compacted,
            events_before=report.events_before,
            events_after=report.events_after,
            legacy_events_collapsed=report.legacy_events_collapsed,
            fat_summaries_rewritten=report.fat_summaries_rewritten,
        )

    if mirror_would_change or stale_ids:
        removed, added = _reconcile_mirror_for_project(
            compacted, old_ids=journaled_old_ids, event_log_head=preserved_head, repo_id=slug,
        )
        report.mirror_events_removed = removed
        report.mirror_events_added = added
        report.mirror_rewritten = True

    _clear_journal(slug)

    for trace_id in sorted(affected):
        project_per_trace_exports(
            None, project_slug=slug, trace_id=trace_id, events=compacted,
            events_authoritative=True, mirror_fallback=False,
        )

    return report


def reclaim_anchor_search(*, apply: bool = False) -> AnchorSearchReclaimReport:
    """Compact legacy/v2-fat anchor-search events across every project the
    bucket knows about, per the module docstring's three-step contract.

    Dry-run by default: detection (the read + ``compact_search_events`` call)
    always runs — it is pure/read-only — but the ref swap, mirror rewrite,
    and companion regeneration only happen when ``apply=True``. A second
    ``apply=True`` run over an already-compacted bucket reports zero deltas
    for every project (``import_event_log``'s own idempotency check, the
    stale/to-add set-diff, and the atomic same-bytes-skip companion writers
    all independently no-op).

    One project's processing failure (a corrupt event, a git error mid-swap,
    an unreadable journal) is caught and recorded in ``errors`` / that
    project's own ``action="error"`` row rather than aborting the whole pass
    — every OTHER project still gets processed and reported.
    """

    report = AnchorSearchReclaimReport(apply=apply)

    slug_to_repo = {slug: path for path, slug in _iter_opted_in_projects()}
    candidate_slugs = sorted(set(slug_to_repo) | _bucket_known_slugs())
    single_project_bucket = len(candidate_slugs) == 1

    for slug in candidate_slugs:
        repo = slug_to_repo.get(slug)
        try:
            if repo is not None:
                report.projects.append(_process_reachable_project(slug, repo, apply=apply))
            else:
                report.projects.append(
                    _process_unreachable_project(slug, apply=apply, single_project_bucket=single_project_bucket)
                )
        except Exception as exc:  # noqa: BLE001 - one bad project must never sink the whole reclaim pass
            report.errors.append(f"{slug}: {exc}")
            report.projects.append(
                ProjectAnchorSearchReport(
                    project_slug=slug,
                    repo_reachable=repo is not None,
                    action="error",
                    reason=str(exc),
                )
            )

    return report


def _pending_journal_slugs() -> list[str]:
    """Project slugs with a durable, not-yet-cleared anchor-search reclaim
    journal on disk — proof a prior ``bucket reclaim --apply`` run committed
    to a removal target (wrote the journal) but was killed before finishing
    it (see the module docstring's "Crash safety" section)."""

    journal_dir = paths.bucket_dir() / "reclaim" / "anchor_search"
    if not journal_dir.exists():
        return []
    return sorted(p.stem for p in journal_dir.glob("*.json"))


def resume_pending_anchor_search_journals() -> list[ProjectAnchorSearchReport]:
    """Finish any anchor-search reclaim run a prior process left interrupted
    — WITHOUT scanning or touching any project that has no pending journal.

    Unlike :func:`reclaim_anchor_search`, this never opens a project's
    canonical log looking for NEW fat/legacy content to compact; it exists
    only to close the read-time gap a killed run's own crash window leaves
    open (issue #358 repair finding): a mirror superset a reader can be
    caught mid-window on (see ``bucket_events.read_events_mirror_batches``'s
    duplicate-collapsing) is reliably cleaned up only by that project's OWN
    reconcile finishing, and that must not wait for someone to remember to
    re-run ``bucket reclaim --apply``. ``bucket_repair`` runs this BEFORE
    its own per-project mirror sync so a healed project's normal sync sees
    consistent state and — per the journal-driven resume path stamping the
    real ``event_log_head`` — takes its cheap incremental/no-op route.

    One project's failure here is skipped (its journal, and therefore its
    stale mirror content, is left exactly as it was for the next attempt)
    rather than raised — a corrupt journal for project A must not block an
    otherwise-unrelated repair pass over the rest of the bucket.
    """

    slugs = _pending_journal_slugs()
    if not slugs:
        return []

    slug_to_repo = {slug: path for path, slug in _iter_opted_in_projects()}
    single_project_bucket = len(set(slug_to_repo) | _bucket_known_slugs()) == 1

    reports: list[ProjectAnchorSearchReport] = []
    for slug in slugs:
        repo = slug_to_repo.get(slug)
        try:
            if repo is not None:
                reports.append(_process_reachable_project(slug, repo, apply=True))
            else:
                reports.append(
                    _process_unreachable_project(slug, apply=True, single_project_bucket=single_project_bucket)
                )
        except Exception:  # noqa: BLE001 - see docstring: one bad journal must not block the rest
            continue
    return reports
