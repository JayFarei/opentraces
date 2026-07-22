"""Anchor-search compaction reclaim pass (issue #358).

``search_compaction.py`` is a capability, not a live path — it can turn a
mixed legacy/v2-fat/v3 event stream into a v3-compact one, but nothing calls
it. This module is the wiring: it walks every project the bucket knows about,
finds the ones actually carrying legacy per-patch or v2-fat
``git_anchor_search_completed`` events, and for each one:

1. Compacts the project's canonical event chain and swaps the Git ref in
   place (``_swap_candidate_ref`` — build the new chain STREAMING, then ONE
   compare-and-swap ``update-ref``; a no-op when the chain is already
   compact, per the caller's own ``ref_would_change`` check). The swap's CAS
   base is the head the chain was actually snapshotted from, not a fresh
   self-read at swap time — see the "Crash safety" section below.
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

Memory-bounded streaming (issue #358, the OOM repair): a REAL dry-run
against the motivating 27 GB bucket was SIGKILLed three times with peak RSS
37.2 GB. The read-all-then-compact-all shape of the ORIGINAL implementation
is gone; the pipeline below is genuinely streaming end to end:

* ``event_log.iter_events`` reads the canonical chain ONE ``TrailEvent`` at
  a time (never ``read_events``'s full materialized list, never the shared
  ``_READ_EVENTS_CACHE`` — see ``_stream_compact_chain``).
* ``search_compaction.stream_compact_events`` transforms that stream with
  O(1) running chain state (next sequence, previous_event_id) plus AT MOST
  ONE buffered batch (bounded by one reconcile run's own patch count, never
  the whole corpus — see that function's own docstring for why grouping
  needs a whole-batch buffer, not O(1) event-at-a-time) instead of planning
  the whole output up front.
* ``event_log.StreamingChainWriter`` stages each finalized event into a
  scratch git index and derives the tree on demand — the candidate commit is
  built WITHOUT ever holding the compacted stream as a Python list.
* Per-trace companion content is read back from that candidate via a
  bounded, single streaming pass (``_bucket_events_for_traces``) scoped to
  exactly the affected traces — but issue #358 repair round 3 found that,
  on the motivating fan-out shape (a summary's ``results[]`` touching
  nearly every trace, so ``affected`` is effectively "every trace"),
  retaining every matched event for every affected trace in one in-RAM
  dict was O(corpus) again. That pass now appends matched events to a
  per-trace ON-DISK scratch (:class:`_TraceEventScratch`) instead, read
  back ONE trace at a time — bounded to O(one trace's own footprint), never
  O(sum of every affected trace).
* ``bucket_dir``-scanning callers walk every project in one process; each
  project's own ``invalidate_read_events_cache``/``event_index.invalidate_
  event_index_memo`` call at the end of ``_process_reachable_project``
  (belt-and-suspenders alongside never populating the cache in the first
  place) makes cross-project accumulation structurally impossible.

Honest boundary this rewrite accepts: a CAS-retry (a concurrent writer
appended during the O(corpus) compaction window — rare, bounded to
``_SWAP_MAX_RETRIES`` attempts) re-STAGES the already-built base candidate's
own content via a fresh streaming read (``_stream_compact_delta``) rather
than re-planning it — bounded MEMORY throughout, but O(base) TIME per retry
instead of the pre-streaming implementation's O(delta) (which needed the
whole base as a Python list to concatenate onto; keeping THAT list around
across possible retries is exactly the unbounded-memory shape this rewrite
removes). Retries only ever fire under an active concurrent-writer race, so
this trades a bounded, one-time worst-case time cost — never memory — for
eliminating the far larger and by-default risk.

Crash safety (issue #358 repair): a compaction pass rewrites history — unlike
the normal append-only flow every other mirror writer assumes — so two
invariants the rest of the system leans on need explicit protection here,
not just "write-new-then-remove-stale" ordering:

* Which mirror event_ids are now STALE is only readable from the canonical
  ref BEFORE the ref swap; once the swap moves the ref, a killed run's
  resume reads the ALREADY-compacted chain, and — because compaction is
  idempotent on already-compact input — would recompute ``old_ids ==
  target_ids`` and see NO stale ids at all, silently abandoning the actual
  stale mirror content from before the crash. A per-project journal
  (``_write_journal`` / ``_read_journal`` / ``_clear_journal``, under
  ``bucket/reclaim/anchor_search/``) is written durably BEFORE the ref swap
  and consumed on resume instead of recomputing, so the removal target
  survives however far the interrupted run got. On resume the journal's id
  set is UNIONED with, not replaced by, this run's fresh pre-compaction read
  (issue #358 repair v3, finding 3): a writer can append a new event between
  the journal write and the crash, invisible to the journal by construction,
  then a ROUTINE (reclaim-unrelated) ``sync_events_mirror`` tick mirrors it
  into its own batch file before resume runs; the resumed compaction's
  whole-stream re-chain gives that event a fresh id/sequence too, but a
  removal scope built from the journal alone can never see its
  pre-compaction batch file, leaving the superseded and rewritten copies to
  coexist in the mirror forever.
* This project's mirror batch files must be assigned the EXACT (seq,
  batch_id) pairs a standalone ``sync_events_mirror`` full rebuild of this
  project's OWN canonical events would independently derive
  (``_project_batch_layout`` — sorted by ``event_sequence``, grouped by
  ``batch_id`` in first-encounter order, seq from 1; a streaming compacted
  chain always shares ONE ``batch_id`` by construction — see
  ``search_compaction._COMPACTION_BATCH_ID`` — so ``_reconcile_mirror_for_
  project``'s streaming branch derives that layout directly, without a
  generic multi-group scan). Anything else — e.g. renumbering against a
  list that also contains OTHER projects' mirror events — produces
  filenames a LATER, independent sync of this project would never
  reproduce; since ``sync_events_mirror`` only adds files and never
  deletes, the old and new names would both persist forever (duplicate
  events, broken replay contiguity). The rewritten index also stamps the
  REAL new ``event_log_head`` (not ``None``) so that later sync takes its
  incremental/no-op path instead of a full rebuild.
* The chain snapshot this pass compacts (``event_log.iter_events``) is taken
  BEFORE the minutes-long O(corpus) compaction work below it; a writer
  elsewhere (a hot post-commit hook reconcile, a watcher maturation tick, a
  trail attach) can append events into that window. ``iter_events`` reads an
  EXPLICIT, already-resolved commit sha — not a live ref it re-derives
  internally — so unlike the pre-streaming ``read_events`` it is structurally
  immune to a "what I read" vs "what I CAS against" divergence: whatever
  ``head`` is passed in is exactly, and only, what gets read, regardless of
  what the ref does concurrently. ``_swap_candidate_ref`` CASes the freshly-
  built candidate against that SAME snapshot head and, on a CAS failure,
  folds the append-only delta onto the compacted chain's tail
  (``_stream_compact_delta``) before retrying — bounded, so a hot writer
  cannot livelock the pass; exhaustion reports ``action="error"`` with the
  mirror and companions untouched, never a partial swap.
* A confirmed-missing ref (a re-clone or damaged repo racing the O(corpus)
  compaction window) is checked EXPLICITLY, immediately before the swap
  attempt (``_swap_candidate_ref``'s own ``_head_sha`` check) — the same
  hazard ``_process_reachable_project``'s own entry guard refuses one call
  stack frame higher. Looping with the newly-``None`` head instead of
  raising would eventually derive an EMPTY compaction target and, with a
  pending journal, hand ``_reconcile_mirror_for_project`` a removal scope
  covering the WHOLE mirror (its sole surviving copy once the ref is gone).
  This raises instead, matching the entry guard, so the loss is contained
  the same way no matter which read notices it first.
* A CAS-retry delta event keeps its OWN pre-fold ``event_id`` on the
  canonical ref right up until it is re-chained by ``_stream_compact_
  delta``'s pass over it — every finalized slot, even a plain passthrough
  event, derives a fresh id from its new ``previous_event_id`` link. A
  routine, reclaim-unrelated ``sync_events_mirror`` tick can mirror that
  delta under its original id before this swap ever lands; because that id
  was never part of the pre-append ``old_ids``/``journaled_old_ids``
  snapshot, a removal scope built from those alone can never see it (issue
  #358 repair v3 round 2, major). The retry loop below returns every delta
  event's original id explicitly, and the caller unions it into
  ``journaled_old_ids`` before deriving ``stale_ids`` — the superseded
  original-id copy and the rewritten copy are the SAME logical event, and
  only one of them should survive the mirror. That union alone only widens
  an in-PROCESS-MEMORY variable, though (issue #358 repair v3 round 2
  follow-up): the durable journal was written, if at all, BEFORE this swap
  was even attempted, from the pre-swap belief, so a kill between the swap
  landing and ``_reconcile_mirror_for_project`` running would resume from a
  journal that never learned about the delta — the same permanent-orphan
  outcome this finding already fixed for the in-memory case, just one crash
  window later. The caller now re-writes the journal with the widened
  ``journaled_old_ids``/``affected`` immediately after the swap returns and
  before that reconcile call, so a kill in that narrow window still resumes
  from what this run actually decided.

Honest boundary: a project whose Git repo is unreachable can only be
compacted from the bucket's own mirror, and — because the mirror carries no
per-project attribution — that is only sound when this project is the ONLY
one the bucket knows about (so the whole mirror is unambiguously its own).
A multi-project bucket with an unreachable project's repo is reported
``skipped`` with that reason; its bytes are left untouched rather than risk
touching a different project's data. Blobs, snapshots, and non-search events
are never touched by this pass, matching ``search_compaction``'s own scope.
This path is deliberately UNCHANGED by the streaming rewrite (issue #358's
motivating 27GB case is a REACHABLE project's canonical log — the unreachable
path's list-based ``compact_search_events`` over the bucket's OWN mirror stays
as-is; a bucket small enough to be the sole project's mirror is not the scale
this repair targets).

Reported bytes are honest about what the ref swap does NOT free: it writes a
whole new chain and moves the ref, but the OLD chain's objects stay in the
Git object database (unreachable, retained — this module never runs ``git
gc``/``prune``). The headline ``bytes_reclaimed`` therefore counts only the
companion-file shrink that is REALLY on disk after ``apply``; the ref-side
delta is reported separately as ``ref_bytes_reclaimable_after_gc``. A
dry-run's freshly-built (but never swapped-in) candidate commit is the exact
same kind of retained-but-unreachable object — the streaming rewrite writes
it either way (dry-run or apply) so the preview can read real per-trace
byte counts back off it, rather than approximating.

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
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote

from . import paths
from ._bucket_io import (
    _atomic_write_bytes,
    _atomic_write_json,
    _canonical_json,
    _gzip_deterministic,
    _read_gzip_bytes,
    _write_streaming_gzip,
)
from ._time import utc_now_str
from .bucket_envelope import _events_for_trace_from_iter, _iter_opted_in_projects, project_per_trace_exports
from .bucket_events import BUCKET_EVENTS_INDEX_SCHEMA, read_events_mirror_batches
from .bucket_layout import _path_part, events_v1_batches_dir, events_v1_index_path, traces_v1_root, trace_v1_trail_path
from .trails import EVENT_LOG_REF, ANCHOR_SEARCH_EVENT_TYPE, TrailEvent
from .trails import event_index
from .trails.event_log import (
    EventLogHeadMovedError,
    StreamingChainWriter,
    _commit_batch,
    _update_event_log_ref,
    invalidate_read_events_cache,
    iter_events,
    read_events_scoped,
    read_events_since,
)
from .trails.search_compaction import CompactionStats, compact_search_events, stream_compact_events

# Below this many raw bytes in a project's canonical ``events/`` tree, a full
# read+compact is not worth the O(corpus) cost — this only needs to catch the
# "no anchor-search history at all" case cheaply (a v2-fat summary is the
# reported 26 GB driver; even a single such blob dwarfs this).
#
# 64 KiB sits comfortably between the two shapes this prefilter has to tell
# apart: a slim, ordinary event (a plain ``trace_patch_created`` or an
# already-v3-compact search summary) serializes to roughly 1.5 KB, so a
# healthy or already-compacted project's WHOLE history can hold dozens of
# them and still clear this threshold; a fat shape this pass exists to catch
# is >100 KB in practice for a single unknowns-bearing v2 summary, and the
# issue #358 motivating case is ~4.3 MB — both land an order of magnitude (or
# three) above 64 KiB on their very first oversized blob. The old value (64
# raw BYTES, not KiB) was smaller than a single slim event's own JSON
# encoding, so every project with any history at all failed the prefilter —
# the fast no-op path it exists for never fired, and a zero-fat bucket paid
# the full O(corpus) read + compact on every ``bucket reclaim`` run anyway.
# Kept low enough that a project with a genuinely small, already-mostly-
# compact search history still gets read (companions/mirror reconciliation
# can be needed even when there is nothing left to compact — see the resume
# note on ``_process_reachable_project``).
_FAT_PREFILTER_MIN_BYTES = 65536

_RECLAIM_WRITER = "bucket-reclaim-anchor-search"

_JOURNAL_SCHEMA = "opentraces.bucket.reclaim.anchor_search_journal.v1"

# Bounded retry count for the ref-swap CAS-on-concurrent-append path (issue
# #358 repair, finding 1). Each retry re-stages the already-built base
# candidate's own content (a fresh, streaming ``iter_events`` read — never
# a materialized list) plus re-finalizes only the newly appended suffix via
# ``_stream_compact_delta`` — never re-PLANS the whole, possibly huge, base
# chain — so a handful of attempts is enough to outrun a hot writer
# (a post-commit hook reconcile, a watcher maturation tick, a trail attach)
# without risking a livelock. See the module docstring's "Honest boundary"
# paragraph for the O(base) TIME (not memory) cost this pays per retry.
_SWAP_MAX_RETRIES = 5


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


def _touch_ids_for_event(event: Any, patch_to_trace: dict[str, str]) -> set[str]:
    """Trace ids ONE fat/legacy, v2-fat, or v3(-compact) search event touches
    (see ``search_records.summary_search_touches_trace`` for the read-side
    analog over a single event). Over-inclusive is safe here (an
    already-current companion costs nothing extra to regenerate);
    under-inclusive is not.

    The single-event body of the old ``_search_touch_ids`` (still used, list-
    based, by the unreachable-project path below) — pulled out so the
    streaming reachable path can track old-side and new-side touches
    INCREMENTALLY as each event streams through, instead of scanning two
    full materialized lists after the fact (issue #358).

    A v3-compact ``unanchored_trace_patch_ids`` entry carries no trace_id of
    its own (the documented delta in ``search_compaction``'s module
    docstring), so this also resolves each one via ``patch_to_trace`` — the
    trace_patch_id -> trace_id map every caller here builds ONCE, up front,
    from the same project's ``trace_patch_created`` events (see
    ``_patch_to_trace_map``)."""

    ids: set[str] = set()
    if event.event_type != ANCHOR_SEARCH_EVENT_TYPE:
        return ids
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


def _search_touch_ids(events: list[Any]) -> set[str]:
    """List-based ``_touch_ids_for_event`` over a whole event list — the
    unreachable-project path's own compaction (over the bucket's mirror,
    never the O(corpus)-scale canonical log this rewrite targets — see the
    module docstring's honest boundary) still needs the full-list form."""

    patch_to_trace: dict[str, str] = {}
    for event in events:
        if event.event_type == "trace_patch_created" and event.trace_id:
            patch_id = (event.payload or {}).get("trace_patch_id")
            if patch_id:
                patch_to_trace[patch_id] = event.trace_id

    ids: set[str] = set()
    for event in events:
        ids |= _touch_ids_for_event(event, patch_to_trace)
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
    (mirrors ``event_log.StreamingChainWriter.stage``'s blob content exactly),
    used to report a real projected ref size without writing anything extra."""

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

    List-based; used by the unreachable-project path's own (list-scale)
    compaction. The reachable path's streaming mirror write
    (``_reconcile_mirror_for_project``'s streaming branch) derives the SAME
    layout directly from the fact that a compacted stream always shares
    exactly ONE ``batch_id`` — see that branch's own docstring.
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
    compacted: "list[Any] | Iterable[Any]", *, old_ids: set[str], event_log_head: str | None, repo_id: str
) -> tuple[int, int]:
    """Reconcile ONLY this project's slice of the shared events mirror.

    Writes this project's own deterministic batch layout FIRST, then removes
    any on-disk batch file that (a) belongs entirely to this project (every
    event_id it holds is in ``old_ids``, this project's FULL pre-compaction
    id set) and (b) is not one of the files just written as current.
    ``old_ids`` — not just the ids that actually changed — is the right
    removal scope because a compacted stream always emits a SINGLE unified
    batch for the whole output stream (every ``_finalize_slot`` call stamps
    every slot, passthrough or rewritten, with the same ``batch_id`` —
    ``search_compaction._COMPACTION_BATCH_ID``): even an UNCHANGED event
    (same event_id, since ``batch_id`` sits outside the content hash) moves
    out of whatever old batch file it used to live in and into that one new
    file, so the old file holding it is now redundant even though none of
    its events are individually "stale". New files are written BEFORE any
    old file is removed (write-new-then-remove-stale), so a kill mid-call
    leaves a strict superset on disk. Issue #358 repair: compaction re-chains
    the WHOLE stream (reassigns ``event_sequence``/``previous_event_id``
    from the first touched slot onward), so only the untouched PREFIX before
    that slot keeps its original ``event_id`` -- everything from there on
    gets a fresh one. A reader mid-window therefore sees two disjoint kinds
    of leftover: true duplicates (the untouched prefix, same ``event_id`` in
    both the old and new file -- ``read_events_mirror_batches`` in
    ``bucket_events.py`` collapses these on read, so they alone never break
    replay) and genuinely SUPERSEDED events (the old shape of whatever got
    rewritten, a DIFFERENT ``event_id`` with no counterpart to collapse
    against -- no generic reader can safely tell these apart from real
    content without this project's own canonical order). Full consistency
    for a reader mid-window therefore depends on this project's OWN
    reconcile finishing, not on read-side tolerance alone -- see
    ``resume_pending_anchor_search_journals``, which ``bucket_repair`` runs
    before its own per-project mirror sync specifically so nothing routine
    has to wait for a human to re-run ``bucket reclaim --apply``.

    ``event_log_head`` is stamped into the index exactly (the real,
    just-swapped ref head, or the still-valid preserved head for a
    mirror-only compaction) so the NEXT ``sync_events_mirror`` for this
    project takes its cheap incremental/no-op path instead of a full
    rebuild — a full rebuild after a HISTORY REWRITE (not a normal append)
    is exactly the case the incremental fast path's ancestor check cannot
    shortcut, and a wrong (``None``) head is what forces the duplicate-
    producing full-rebuild-without-cleanup path in the first place.

    ``compacted`` accepts EITHER a materialized ``list`` (the unreachable
    path's own list-based compaction) OR any other one-pass ``Iterable``
    (issue #358: the reachable path's streaming compacted chain, read back
    via ``event_log.iter_events`` off the already-built candidate commit
    rather than ever materialized as a Python list here). The two branches
    below produce byte-identical output for the same logical content — the
    list branch's ``_project_batch_layout`` groups generically by whatever
    ``batch_id``s it finds; the streaming branch skips that scan because a
    compacted stream is KNOWN, by construction, to share exactly one.

    Returns ``(batch_files_removed, batch_files_written)`` — batch-FILE
    counts, not event counts (a compacted stream lands in ONE unified batch
    per :func:`_project_batch_layout`, so ``batch_files_written`` is 0 or 1
    regardless of how many events it holds, and a removed batch file's event
    count includes untouched-but-relocated events alongside genuinely stale
    ones — see the docstring paragraph above). Callers report these under
    their own honestly-named fields; never as ``mirror_events_removed``/
    ``mirror_events_added`` (issue #358 repair, finding 2: that mismatch is
    exactly what let dry-run and apply report different UNITS for the same
    quiescent world).
    """

    batches_dir = events_v1_batches_dir()
    batches_dir.mkdir(parents=True, exist_ok=True)

    if isinstance(compacted, list):
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
        batch_count = len(layout)
        state = "ok" if compacted else "missing"
    else:
        # Streaming branch (issue #358): a compacted stream shares exactly
        # ONE `batch_id` by construction (`search_compaction._finalize_
        # slot` always stamps `_COMPACTION_BATCH_ID`), so this never needs
        # `_project_batch_layout`'s generic multi-group scan — seq=1,
        # directly. `_write_streaming_gzip` compresses line-by-line (see its
        # own docstring for why chunk boundaries cannot affect the
        # deterministic output), so peak memory here is ONE event, never
        # the whole batch body.
        from .trails.search_compaction import _COMPACTION_BATCH_ID

        latest_seq = 0
        last_batch_id: str | None = None
        seen_any = False

        def _lines() -> Any:
            nonlocal latest_seq, last_batch_id, seen_any
            for event in compacted:
                seen_any = True
                latest_seq = event.event_sequence
                last_batch_id = event.batch_id
                yield _canonical_json(event.model_dump(mode="json"))

        path = batches_dir / f"{1:012d}-{_path_part(_COMPACTION_BATCH_ID)}.jsonl.gz"
        changed = _write_streaming_gzip(path, _lines())
        if seen_any:
            new_paths = {path}
            added = 1 if changed else 0
            batch_count = 1
            state = "ok"
        else:
            new_paths = set()
            added = 0
            batch_count = 0
            state = "missing"
            last_batch_id = None

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
        "batch_count": batch_count,
        "last_batch_id": last_batch_id,
        "latest_event_sequence": latest_seq,
        "state": state,
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
    # Same preview semantics in dry-run and apply (issue #358 repair v3
    # round 2, major): dry-run used to leave this at its ``False`` default
    # even when ``ref_would_change`` was already known True, so a cautious
    # operator auditing what ``--apply`` will touch read a preview that
    # under-reported the single most sensitive mutation this pass performs.
    ref_rewritten: bool = False
    ref_bytes_before: int = 0
    ref_bytes_after: int = 0
    mirror_rewritten: bool = False
    # Event counts, NOT batch-file counts — the same preview semantics in
    # dry-run and apply (issue #358 repair, finding 2: apply used to
    # overwrite these with ``_reconcile_mirror_for_project``'s file-level
    # return, a different unit that silently disagreed with the dry-run
    # preview). See ``mirror_batch_files_removed``/``_written`` for the
    # file-level numbers.
    mirror_events_removed: int = 0
    mirror_events_added: int = 0
    mirror_batch_files_removed: int = 0
    mirror_batch_files_written: int = 0
    companion_bytes_before: int = 0
    companion_bytes_after: int = 0
    companions_regenerated: list[str] = field(default_factory=list)
    # Count of CAS failures the ref swap absorbed (issue #358 repair, finding
    # 1) — 0 on every quiescent run; >0 means a concurrent writer's append
    # was detected and folded onto the compacted chain's tail before the
    # swap landed. See the reachable path's swap-retry loop.
    swap_retries: int = 0

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
        """The ref-side delta is NOT yet freed disk space: swapping the ref
        writes a whole new chain and moves it, but the OLD chain's objects
        stay in the Git object database (unreachable, retained — this
        module never runs ``git gc``/``prune``). Reported separately and
        honestly, so ``bytes_reclaimed`` never claims disk space that is
        still sitting in ``.git`` until gc actually runs."""
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
            "mirror_batch_files_removed": self.mirror_batch_files_removed,
            "mirror_batch_files_written": self.mirror_batch_files_written,
            "companions_regenerated": list(self.companions_regenerated),
            "companion_count": len(self.companions_regenerated),
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "bytes_reclaimed": self.bytes_reclaimed,
            "swap_retries": self.swap_retries,
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
    """List-based per-trace real-on-disk-vs-would-be-projected comparison —
    the unreachable-project path's own (list-scale) form. See
    ``_companion_deltas_from_buckets`` for the reachable path's streaming
    analog. Summed before/after for reporting, plus a per-trace
    ``any_changed`` flag — a resumed run (ref already compacted, so this
    pass finds nothing new) still needs to know whether a companion is
    stale from an EARLIER, interrupted pass; summed bytes alone could
    coincidentally match even when individual traces differ, so this
    compares per trace."""

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


class _TraceEventScratch:
    """On-disk per-trace scratch built by :func:`_bucket_events_for_traces`
    (issue #358 repair round 3, major): one small file per (trace,
    trail-or-context) pair under a private ``tempfile.TemporaryDirectory``,
    instead of a materialized ``dict[trace_id, (list, list)]``. On the
    #358-motivating fan-out shape (a search summary's ``results[]`` can
    reference nearly every trace the project ever searched, so ``affected``
    is effectively "every trace") the old dict retained EVERY matched event
    for EVERY affected trace simultaneously — O(corpus) again, the exact
    hazard this whole rewrite exists to remove. ``get(trace_id)`` reads back
    ONLY that one trace's own file, so peak memory across the companion-
    regen pass this backs is O(one trace's own footprint), never O(sum of
    every affected trace's footprint) — the TIME cost of the extra
    open/write/close per matched event is a deliberate trade for that bound,
    the same trade the CAS-retry path makes (see the module docstring's
    "Honest boundary").

    Mirrors ``StreamingChainWriter``'s ``tempfile.TemporaryDirectory`` +
    explicit ``close()``/context-manager shape (same module family: scratch
    state lives on disk, not in a Python list, and the caller owns closing
    it — see ``event_log.py``).
    """

    def __init__(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="opentraces-reclaim-scratch-")

    def _path(self, trace_id: str, kind: str) -> Path:
        return Path(self._tmpdir.name) / f"{_path_part(trace_id)}.{kind}.jsonl"

    def _append(self, trace_id: str, kind: str, line: str) -> None:
        with self._path(trace_id, kind).open("a", encoding="utf-8") as fh:
            fh.write(line)

    def get(self, trace_id: str, default: Any = None) -> tuple[list[Any], list[Any]]:
        # ``default`` is accepted, not used -- every caller already treats a
        # trace with no matched events as ``([], [])``, which is exactly
        # what an absent scratch file reads back as; matching ``dict.get``'s
        # signature is what lets every existing call site stay unchanged.
        return self._read(self._path(trace_id, "trail")), self._read(self._path(trace_id, "context"))

    @staticmethod
    def _read(path: Path) -> list[Any]:
        if not path.exists():
            return []
        events: list[Any] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    events.append(TrailEvent.model_validate(json.loads(line)))
        return events

    def close(self) -> None:
        self._tmpdir.cleanup()

    def __enter__(self) -> "_TraceEventScratch":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _bucket_events_for_traces(repo: Path, head: str, trace_ids: set[str]) -> _TraceEventScratch:
    """ONE streaming pass over ``head``'s full event chain, appending each
    event into every ``trace_ids`` member's own ON-DISK scratch file (issue
    #358 part C; issue #358 repair round 3 replaced the in-RAM ``dict`` this
    used to build with :class:`_TraceEventScratch` — see its docstring for
    why) per the SAME predicate ``bucket_envelope._events_for_trace_from_
    iter`` applies per-trace: top-level/payload ``trace_id`` match, or — for
    a search-summary event — a ``results[]`` entry referencing the trace. A
    summary event touching MULTIPLE wanted traces is correctly appended to
    EACH of their files (the same event legitimately belongs in every
    trace's companion it searched). This pass itself still costs O(1) event
    in flight — the memory saving is in never RETAINING what it reads; the
    caller pays O(corpus) TIME either way (an ordinary Git object read per
    event), same class as ``bucket repair``.

    Returns a :class:`_TraceEventScratch` the caller MUST close (context
    manager or explicit ``.close()``) once done reading it back.

    ``head`` is typically the streaming candidate's own (still unreferenced,
    for a dry-run or a not-yet-swapped preview) commit sha — ``iter_events``
    reads an explicit commit, never a live ref, so this works identically
    whether or not anything currently points at it.
    """
    from .context_tree.contract import (
        CONTEXT_COMPACTION_OBSERVED,
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from .trails.search_records import is_summary_search_event

    context_types = {
        CONTEXT_LAYER_CAPTURED, CONTEXT_NODE_OBSERVED, CONTEXT_COMPACTION_OBSERVED, CONTEXT_TREE_RECONCILED,
    }

    scratch = _TraceEventScratch()
    if not trace_ids:
        return scratch

    for event in iter_events(repo, head):
        touched: set[str] = set()
        ev_trace_id = event.trace_id
        if not ev_trace_id and isinstance(event.payload, dict):
            ev_trace_id = event.payload.get("trace_id")
        if ev_trace_id in trace_ids:
            touched.add(ev_trace_id)
        if is_summary_search_event(event):
            payload = event.payload or {}
            for entry in payload.get("results") or []:
                tid = entry.get("trace_id") if isinstance(entry, dict) else None
                if tid in trace_ids:
                    touched.add(tid)
        if not touched:
            continue
        kind = "context" if event.event_type in context_types else "trail"
        line = _canonical_json(event.model_dump(mode="json")) + "\n"
        for tid in touched:
            scratch._append(tid, kind, line)
    return scratch


def _companion_deltas_from_buckets(
    slug: str, affected_trace_ids: set[str], buckets: "_TraceEventScratch"
) -> tuple[int, int, bool]:
    """Streaming analog of ``_companion_deltas``: ``buckets`` reads back ONE
    trace's own on-disk scratch at a time (see ``_bucket_events_for_traces``
    / :class:`_TraceEventScratch`), never the whole affected set in RAM at
    once."""

    before = 0
    after = 0
    any_changed = False
    for trace_id in sorted(affected_trace_ids):
        path = trace_v1_trail_path(slug, trace_id)
        b = path.stat().st_size if path.exists() else 0
        trail_events, _context_events = buckets.get(trace_id, ([], []))
        a = _trail_gzip_size(trail_events)
        before += b
        after += a
        if b != a:
            any_changed = True
    return before, after, any_changed


# ---------------------------------------------------------------------------
# Streaming chain compaction (issue #358) — the reachable-project path.
# ---------------------------------------------------------------------------


@dataclass
class _CompactedChain:
    """Result of a streaming compaction pass — a candidate git commit
    ALREADY WRITTEN (blobs/tree/commit objects exist) but not yet
    referenced by anything, plus the bookkeeping the caller needs to decide
    whether/how to swap it in. Mirrors what the pre-streaming implementation
    read off a materialized ``(compacted, stats)`` pair, but every field
    here was accumulated incrementally as the stream passed through — see
    ``_stream_compact_chain``.
    """

    commit_sha: str
    events_before: int
    events_after: int
    legacy_events_collapsed: int
    fat_summaries_rewritten: int
    old_ids: set[str]
    target_ids: set[str]
    affected: set[str]
    projected_ref_bytes: int
    tail_sequence: int
    tail_event_id: str | None


def _patch_to_trace_map(repo: Path) -> dict[str, str]:
    """Bounded (O(patches), not O(corpus)) pre-pass resolving
    ``trace_patch_id -> trace_id`` from every ``trace_patch_created`` event
    in ``repo``'s canonical log — needed by ``_touch_ids_for_event`` to
    resolve a v3-compact search event's ``unanchored_trace_patch_ids``
    entries back to their trace, regardless of whether the search event or
    its patch's own ``trace_patch_created`` event comes first in sequence
    order (a real log always creates a patch before searching it, but
    building this map UP FRONT, once, makes the streaming compaction pass
    below correct without relying on that ordering assumption at all).

    Uses ``read_events_scoped``'s ``sink`` form (issue #358): each
    ``trace_patch_created`` event — payload included — is handed to the
    sink and discarded immediately, so peak memory here is the resulting
    dict (patch id -> trace id, short strings), never the events
    themselves."""

    mapping: dict[str, str] = {}

    def _record(event: Any) -> None:
        if not event.trace_id:
            return
        patch_id = (event.payload or {}).get("trace_patch_id")
        if patch_id:
            mapping[patch_id] = event.trace_id

    read_events_scoped(repo, event_types={"trace_patch_created"}, sink=_record)
    return mapping


def _stream_compact_chain(repo: Path, head: str, patch_to_trace: dict[str, str]) -> _CompactedChain:
    """Read ``repo``'s canonical chain at ``head``, compact it, and write the
    result to a NEW, UNREFERENCED git commit — all in ONE streaming pass
    (issue #358: the analog of ``compact_search_events(old_events)``, but
    O(1) ``TrailEvent`` in flight instead of O(corpus) — see
    ``event_log.iter_events`` and ``search_compaction.stream_compact_
    events``). Never touches ``_READ_EVENTS_CACHE`` or the #137 event-index
    memo — ``iter_events`` bypasses both by design (see its own docstring).

    Returns a :class:`_CompactedChain` describing the candidate WITHOUT
    swapping any ref — the caller decides whether ``old_ids != target_ids``
    (a real change) before ever attempting the CAS (``_swap_candidate_
    ref``); when it does not, the candidate commit this wrote is simply
    never referenced by anything and becomes an ordinary orphaned git
    object (the SAME honesty trade-off the module docstring already makes
    for the ref swap's retained-but-unreachable old chain — this module
    never runs ``git gc``).

    This is the ONE place ``stream_compact_events`` is called for a fresh
    (non-retry) pass — ``_stream_compact_delta`` (the CAS-retry fold)
    deliberately does NOT call back into this function, so a concurrent-
    append fault-injection wrapped around THIS function fires exactly once
    per attempt, mirroring the pre-streaming split between
    ``compact_search_events`` (base) and ``compact_and_append`` (retry
    fold).
    """

    old_ids: set[str] = set()
    affected: set[str] = set()
    stats = CompactionStats()

    def _source() -> Any:
        for event in iter_events(repo, head):
            old_ids.add(event.event_id)
            affected.update(_touch_ids_for_event(event, patch_to_trace))
            yield event

    target_ids: set[str] = set()
    projected_ref_bytes = 0
    tail_sequence = 0
    tail_event_id: str | None = None

    with StreamingChainWriter(repo) as writer:
        for event in stream_compact_events(_source(), stats):
            target_ids.add(event.event_id)
            affected.update(_touch_ids_for_event(event, patch_to_trace))
            projected_ref_bytes += _event_blob_bytes(event)
            tail_sequence = event.event_sequence
            tail_event_id = event.event_id
            writer.stage(event)
        batch_id = f"bucket-reclaim-{uuid.uuid4().hex}"
        batch = {
            "batch_id": batch_id,
            "writer": _RECLAIM_WRITER,
            "previous_event_log_head": None,
            "event_count": writer.event_count,
            "imported": True,
        }
        tree_sha = writer.finalize(batch)
    commit_sha = _commit_batch(repo, tree_sha, None, batch_id)

    return _CompactedChain(
        commit_sha=commit_sha,
        events_before=stats.events_in,
        events_after=stats.events_out,
        legacy_events_collapsed=stats.legacy_search_events_in,
        fat_summaries_rewritten=stats.fat_summaries_rewritten,
        old_ids=old_ids,
        target_ids=target_ids,
        affected=affected,
        projected_ref_bytes=projected_ref_bytes,
        tail_sequence=tail_sequence,
        tail_event_id=tail_event_id,
    )


def _stream_compact_delta(
    repo: Path, base: _CompactedChain, delta_events: list[Any], patch_to_trace: dict[str, str],
) -> _CompactedChain:
    """CAS-retry fold (issue #358): extend an ALREADY-BUILT candidate
    (``base.commit_sha``, still unreferenced — the CAS against it just
    failed) with a freshly-appended, real delta — the streaming analog of
    ``compact_and_append``, but re-staging ``base``'s own already-compacted
    content VERBATIM (re-read via ``iter_events``, never re-run through
    ``stream_compact_events`` a second time) instead of concatenating a
    materialized list. Peak memory stays O(1) ``TrailEvent`` regardless of
    how large ``base`` is; ``delta_events`` is the append-only tail since
    the base snapshot and expected to be small (bounded by
    ``_SWAP_MAX_RETRIES`` attempts, each only firing under an active
    concurrent-writer race), so running IT through ``stream_compact_events``
    is cheap. See the module docstring's "Honest boundary" paragraph for
    why re-staging the base costs O(base) TIME (not memory) per retry —
    the trade this rewrite makes instead of keeping the base as a Python
    list around across possible retries.

    ``patch_to_trace`` is extended IN PLACE with any ``trace_patch_created``
    events in ``delta_events`` before resolving touches — a hot concurrent
    writer can create a patch and search it in the SAME delta window (the
    module docstring's own named concurrent writers).

    Mirrors the pre-streaming ``compact_and_append`` caller's field
    semantics exactly: ``events_before``/``legacy_events_collapsed``/
    ``fat_summaries_rewritten`` stay FROZEN at the base pass's values (the
    delta's own stats were always discarded there too — a retry fold only
    ever updates ``events_after``/``target_ids``/``affected``).
    """

    for event in delta_events:
        if event.event_type == "trace_patch_created" and event.trace_id:
            patch_id = (event.payload or {}).get("trace_patch_id")
            if patch_id:
                patch_to_trace[patch_id] = event.trace_id

    affected = set(base.affected)
    for event in delta_events:
        affected.update(_touch_ids_for_event(event, patch_to_trace))

    target_ids = set(base.target_ids)
    projected_ref_bytes = base.projected_ref_bytes
    tail_sequence = base.tail_sequence
    tail_event_id = base.tail_event_id
    stats = CompactionStats()

    with StreamingChainWriter(repo) as writer:
        for event in iter_events(repo, base.commit_sha):
            writer.stage(event)
        for event in stream_compact_events(
            iter(delta_events), stats,
            start_sequence=base.tail_sequence + 1, start_previous_event_id=base.tail_event_id,
        ):
            target_ids.add(event.event_id)
            affected.update(_touch_ids_for_event(event, patch_to_trace))
            projected_ref_bytes += _event_blob_bytes(event)
            tail_sequence = event.event_sequence
            tail_event_id = event.event_id
            writer.stage(event)
        batch_id = f"bucket-reclaim-{uuid.uuid4().hex}"
        batch = {
            "batch_id": batch_id,
            "writer": _RECLAIM_WRITER,
            "previous_event_log_head": None,
            "event_count": writer.event_count,
            "imported": True,
        }
        tree_sha = writer.finalize(batch)
    commit_sha = _commit_batch(repo, tree_sha, None, batch_id)

    return _CompactedChain(
        commit_sha=commit_sha,
        events_before=base.events_before,
        events_after=tail_sequence,
        legacy_events_collapsed=base.legacy_events_collapsed,
        fat_summaries_rewritten=base.fat_summaries_rewritten,
        old_ids=set(base.old_ids) | {e.event_id for e in delta_events},
        target_ids=target_ids,
        affected=affected,
        projected_ref_bytes=projected_ref_bytes,
        tail_sequence=tail_sequence,
        tail_event_id=tail_event_id,
    )


def _swap_candidate_ref(repo: Path, *, commit_sha: str, expected_head: str | None) -> dict[str, Any]:
    """Attempt the CAS ref-update for an ALREADY-BUILT candidate commit
    (issue #358: ``_stream_compact_chain``/``_stream_compact_delta`` already
    landed the commit object itself via ``StreamingChainWriter`` — this is
    ONLY the swap, mirroring the pre-streaming ``import_event_log``'s own
    CAS (``_update_event_log_ref``) plus cache-invalidation contract
    exactly, without re-validating or re-writing a tree from a materialized
    list — the whole point of building it via ``StreamingChainWriter`` in
    the first place).

    Raises :class:`EventLogHeadMovedError` on a lost race — the SAME
    exception the caller's retry loop already handles — and a distinct,
    clearly-worded ``RuntimeError`` when the ref has DISAPPEARED entirely
    (mirrors ``_process_reachable_project``'s own entry guard one call
    stack frame up: a vanished ref must never be silently treated as
    "compacted to nothing", see the module docstring's "Crash safety"
    section).
    """

    current = _head_sha(repo)
    if current is None:
        raise RuntimeError(
            f"{EVENT_LOG_REF} disappeared while reclaim was building the "
            "compacted chain for a concurrently-written project -- refusing "
            "to swap against a lost ref"
        )
    if not _update_event_log_ref(repo, commit_sha, expected_head):
        raise EventLogHeadMovedError(f"{EVENT_LOG_REF} moved during the reclaim ref swap")
    invalidate_read_events_cache(repo)
    event_index.invalidate_event_index_memo(repo)
    return {"head": commit_sha}


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
        # from an empty source and, via the journal's own removal scope,
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

    # `head` is guaranteed non-None here: the `if head is None:` block above
    # always returns or raises, so execution only reaches this line when it
    # did not.
    tree_bytes = _events_tree_bytes(repo, head)
    report.ref_bytes_before = report.ref_bytes_after = tree_bytes
    if journal is None and tree_bytes < _FAT_PREFILTER_MIN_BYTES:
        report.reason = "below fat-detection size prefilter"
        return report

    try:
        # Streaming chain compaction (issue #358): reads `head`'s canonical
        # log ONE event at a time (`event_log.iter_events`), compacts it
        # with O(1) running chain state (`search_compaction.stream_compact_
        # events`), and stages the output into a scratch git index
        # (`event_log.StreamingChainWriter`) -- never materializing the
        # chain as a Python list, never touching `_READ_EVENTS_CACHE`. The
        # resulting candidate commit is written to the git object database
        # but not yet referenced by anything; the caller below decides
        # whether to CAS it in.
        patch_to_trace = _patch_to_trace_map(repo)
        candidate = _stream_compact_chain(repo, head, patch_to_trace)
    finally:
        # Belt-and-suspenders (issue #358 part B): `iter_events`/`stream_
        # compact_events` never populate `_READ_EVENTS_CACHE` or the #137
        # index memo in the first place, but this project's OWN prior
        # activity (an interactive `read_events_for_trace` call earlier in
        # this same process, say) could have already warmed either --
        # invalidating here makes cross-project accumulation structurally
        # impossible regardless of how this project got read, not just for
        # the streaming path this function itself takes.
        invalidate_read_events_cache(repo)
        event_index.invalidate_event_index_memo(repo)

    report.events_before = candidate.events_before
    report.events_after = candidate.events_after
    report.legacy_events_collapsed = candidate.legacy_events_collapsed
    report.fat_summaries_rewritten = candidate.fat_summaries_rewritten

    ref_would_change = candidate.old_ids != candidate.target_ids

    if journal is not None:
        # UNION with the fresh pre-compaction read, not a replacement (issue
        # #358 repair v3, finding 3): the journal only knows what was old at
        # the ORIGINAL run's read time, but a writer can append a new event
        # between that journal write and the crash -- invisible to the
        # journal by construction, yet still present (and, after this run's
        # whole-stream re-chain, re-id'd) in `candidate.old_ids`. A batch
        # file holding ONLY such an event is a subset of neither set alone,
        # so replacing rather than unioning left it permanently unreachable
        # by ``_reconcile_mirror_for_project``'s removal scope. Safe to
        # widen: removal already skips files matching the just-written
        # layout, and any file wholly inside the union is by construction
        # redundant (either superseded by THIS run's compaction or already
        # stale from the interrupted one). `affected` unions the same way
        # one field below -- this was the one asymmetric field, not a new
        # pattern.
        journaled_old_ids = set(journal["old_event_ids"]) | candidate.old_ids
        affected = set(journal["affected_trace_ids"]) | candidate.affected
    else:
        journaled_old_ids = candidate.old_ids
        affected = candidate.affected
    stale_ids = journaled_old_ids - candidate.target_ids

    # Per-trace bucketed read off the CANDIDATE (issue #358 part C): ONE
    # streaming pass scoped to exactly `affected`, appended into per-trace
    # on-disk scratch files rather than a materialized dict (issue #358
    # repair round 3, major -- see `_bucket_events_for_traces` /
    # `_TraceEventScratch`: on the motivating fan-out shape, `affected` is
    # effectively "every trace", so a dict retaining every one of their
    # events at once was O(corpus) again). Used for BOTH the preview
    # (dry-run and the "anything to do" decision below) and, on `apply`,
    # the actual companion write further down; a CAS retry re-derives it
    # from the folded candidate (see below), matching the pre-streaming
    # implementation's own "re-derive only on an actual retry" gate.
    # `buckets` owns a scratch directory that MUST be closed on every exit
    # path (including the swap-exhaustion raise below) -- the `finally`
    # closes whatever `buckets` currently refers to, so the reassignment on
    # a CAS retry (below) explicitly closes the stale one first rather than
    # leaking it.
    buckets = _bucket_events_for_traces(repo, candidate.commit_sha, affected)
    try:
        comp_before, comp_after, companions_stale = _companion_deltas_from_buckets(slug, affected, buckets)

        # Mirror preview — read-only, safe in both modes. Computed (and, below,
        # ALWAYS acted on) regardless of ``ref_would_change`` so a resume — the
        # ref already swapped by an earlier, interrupted run, compaction
        # finding nothing new THIS pass — still finishes a mirror or companion
        # step that pass never reached.
        mirror_present = events_v1_index_path().exists()
        existing_ids = {e.event_id for e in read_events_mirror_batches()} if mirror_present else set()
        to_add_count = len(candidate.target_ids - existing_ids)
        would_touch_mirror = mirror_present and (bool(stale_ids & existing_ids) or bool(to_add_count))
        report.mirror_events_removed = len(stale_ids & existing_ids)
        report.mirror_events_added = to_add_count

        if journal is None and not (ref_would_change or would_touch_mirror or companions_stale):
            report.reason = "no legacy or v2-fat anchor-search events found"
            return report

        report.companion_bytes_before = comp_before
        report.companion_bytes_after = comp_after
        report.companions_regenerated = sorted(affected)
        report.action = "compacted"

        if not apply:
            report.ref_bytes_after = candidate.projected_ref_bytes
            report.mirror_rewritten = would_touch_mirror
            # `ref_would_change` is already the exact predicate `apply` checks
            # before it ever attempts the CAS -- previewing it here too, not
            # just the mirror fields above, is what keeps a cautious operator's
            # dry-run read honest about the single most sensitive mutation
            # `--apply` performs (issue #358 repair v3 round 2, major).
            report.ref_rewritten = ref_would_change
            return report

        if journal is None and (ref_would_change or would_touch_mirror):
            # Durable BEFORE the ref swap below — see the module docstring.
            _write_journal(slug, old_event_ids=candidate.old_ids, affected_trace_ids=affected)

        # CAS the already-built candidate against the snapshot it was actually
        # derived from — never a fresh self-read at swap time (issue #358
        # repair, finding 1). On a lost race, fold JUST the appended delta onto
        # the candidate's own tail (`_stream_compact_delta`) and retry — bounded
        # to `_SWAP_MAX_RETRIES`, each attempt only re-planning the delta (see
        # the module docstring's "Honest boundary" for the streaming rewrite's
        # O(base) TIME, still O(1) MEMORY, per-retry cost).
        final = candidate
        delta_original_ids: set[str] = set()
        expected: str | None = head
        new_head: str | None = None
        swap_retries = 0

        # A KNOWN no-op (this run's own candidate content is id-for-id
        # identical to what `head` already holds) AND nobody moved the ref
        # while we were building it: skip the CAS entirely (issue #358). Unlike
        # the pre-streaming `import_event_log`, which detected this via its own
        # `existing_ids == incoming_ids` probe over a materialized list and
        # returned WITHOUT writing, a streaming candidate is always built up
        # front (its own memory bound never depends on knowing the answer in
        # advance) — attempting the CAS anyway would move the ref to a
        # content-identical but SHA-different commit (git commit-tree stamps a
        # fresh author/committer timestamp every call) on EVERY idempotent
        # re-run, defeating "a second apply reports zero deltas" by
        # construction. When the ref HAS moved despite `ref_would_change` being
        # False (a concurrent writer raced in without changing what THIS
        # project's compaction itself would produce), the normal swap loop
        # below still runs and correctly folds whatever real delta exists.
        if not ref_would_change and _head_sha(repo) == head:
            new_head = head
        else:
            for attempt in range(1, _SWAP_MAX_RETRIES + 1):
                try:
                    result = _swap_candidate_ref(repo, commit_sha=final.commit_sha, expected_head=expected)
                    new_head = result["head"]
                    swap_retries = attempt - 1
                    break
                except EventLogHeadMovedError:
                    current_head, delta_events = read_events_since(repo, expected)
                    if current_head is None or delta_events is None:
                        # Not append-only relative to `expected` after all
                        # (missing ref, or a history rewrite by some other
                        # process) — refuse rather than guess; falls through to
                        # the exhaustion raise.
                        break
                    if delta_events:
                        delta_original_ids |= {e.event_id for e in delta_events}
                        # Patch-id -> trace_id attribution for a v3-compact
                        # delta entry can reach back into the base chain (its
                        # own ``trace_patch_created`` may predate this crash
                        # window), so this reads the base's own affected set
                        # (already resident) -- not just `delta_events` --
                        # before extending it.
                        final = _stream_compact_delta(repo, final, delta_events, patch_to_trace)
                        affected = affected | final.affected
                    expected = current_head

            if new_head is None:
                raise RuntimeError(
                    f"{EVENT_LOG_REF} kept moving during the reclaim ref swap for a "
                    f"concurrently-written project after {_SWAP_MAX_RETRIES} retries — "
                    "journal left in place for the next attempt, mirror/companions untouched"
                )

        report.swap_retries = swap_retries
        if delta_original_ids:
            # A CAS-retry delta event keeps its OWN pre-fold id right up
            # until `_stream_compact_delta` re-chains it, and a routine,
            # reclaim-unrelated `sync_events_mirror` tick can mirror it under
            # that original id before this swap ever lands -- an id that was
            # never in `candidate.old_ids`/`journaled_old_ids` (both pre-append
            # snapshots) to begin with. Union it into the removal scope now
            # so `stale_ids` below actually targets that superseded copy
            # instead of leaving it to coexist with the rewritten one forever
            # (issue #358 repair v3 round 2, major).
            journaled_old_ids = journaled_old_ids | delta_original_ids
            # Re-write the journal NOW, durably, before `_reconcile_mirror_
            # for_project` below ever runs (issue #358 repair v3 round 2
            # follow-up, blocker): the journal written above -- if any --
            # predates this swap entirely and was gated on the PRE-swap
            # belief (`ref_would_change`/`would_touch_mirror` computed before
            # the swap loop was even entered), so it cannot already contain
            # `delta_original_ids`. Widening only the in-process
            # `journaled_old_ids` variable is invisible to a killed run's
            # resume, which reads the journal back off disk -- `_write_
            # journal` is the same atomic write-new-then-rename
            # `_atomic_write_json` uses everywhere else in this module, so a
            # kill mid-rewrite still leaves either the old or the new
            # journal intact, never a half-written one.
            _write_journal(slug, old_event_ids=journaled_old_ids, affected_trace_ids=affected)
        report.ref_rewritten = ref_would_change or new_head != head
        report.events_after = final.tail_sequence
        report.ref_bytes_after = _events_tree_bytes(repo, new_head) if new_head else report.ref_bytes_before

        if report.swap_retries:
            # A concurrent append landed mid-run and got folded onto the
            # compacted tail -- the preview computed above now describes a
            # world that no longer matches what is about to be written, so it
            # must be re-derived here rather than reported stale (issue #358
            # repair, finding 1's honesty requirement). Gated on an actual
            # retry so the quiescent (overwhelmingly common) case never pays a
            # second bucketed pass.
            target_ids = final.target_ids
            stale_ids = journaled_old_ids - target_ids
            to_add_count = len(target_ids - existing_ids)
            would_touch_mirror = mirror_present and (bool(stale_ids & existing_ids) or bool(to_add_count))
            report.mirror_events_removed = len(stale_ids & existing_ids)
            report.mirror_events_added = to_add_count
            buckets.close()
            buckets = _bucket_events_for_traces(repo, final.commit_sha, affected)
            comp_before, comp_after, _companions_stale = _companion_deltas_from_buckets(slug, affected, buckets)
            report.companion_bytes_before = comp_before
            report.companion_bytes_after = comp_after
            report.companions_regenerated = sorted(affected)

        if would_touch_mirror or stale_ids:
            # (#358 repair finding 2): the returned counts are batch-FILE
            # counts, a different unit from the event counts above — kept
            # under their own honestly-named fields (see ``_reconcile_mirror_
            # for_project``'s docstring). Streaming (issue #358): reads the
            # FINAL candidate back off its own commit rather than passing a
            # materialized list.
            files_removed, files_written = _reconcile_mirror_for_project(
                iter_events(repo, final.commit_sha), old_ids=journaled_old_ids, event_log_head=new_head, repo_id=slug,
            )
            report.mirror_batch_files_removed = files_removed
            report.mirror_batch_files_written = files_written
            report.mirror_rewritten = True

        # Cleared only AFTER the companion loop below finishes (issue #358
        # repair v3 round 2 follow-up): clearing it here, before that loop ran,
        # meant a kill mid-loop left NO journal for a killed run's resume to
        # bypass the prefilter with — and the ref/mirror are already compacted
        # by this point, so their tree can legitimately fall back under the
        # prefilter threshold, silently short-circuiting the resume before it
        # ever reaches the companion-delta check and re-running the
        # interrupted regen.
        for trace_id in sorted(affected):
            trail_events, context_events = buckets.get(trace_id, ([], []))
            project_per_trace_exports(
                repo, project_slug=slug, trace_id=trace_id,
                events=trail_events + context_events, events_authoritative=True,
            )

        _clear_journal(slug)

        return report
    finally:
        buckets.close()


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
        # (#358 repair finding 2): file-level counts, kept under their own
        # honestly-named fields — never used to overwrite the event-count
        # preview set above (see ``_reconcile_mirror_for_project``'s
        # docstring and ``_process_reachable_project``'s matching fix).
        files_removed, files_written = _reconcile_mirror_for_project(
            compacted, old_ids=journaled_old_ids, event_log_head=preserved_head, repo_id=slug,
        )
        report.mirror_batch_files_removed = files_removed
        report.mirror_batch_files_written = files_written
        report.mirror_rewritten = True

    # Cleared only AFTER the companion loop below finishes (issue #358
    # repair v3 round 2 follow-up, second pass — mirrors the reachable
    # path's matching fix for consistency: the journal represents pending
    # work until EVERYTHING, including companions, is done, not just until
    # the mirror is). This path in particular serves projects whose repos
    # are unreachable, so it deserves the same guarantee even though —
    # unlike the reachable path — a kill here does not actually strand a
    # resume today: `affected` is re-derived fresh from `read_events_mirror_
    # batches()` every call, and an already-compacted mirror's
    # `unanchored_trace_patch_ids` still resolves back to this trace via the
    # SAME `trace_patch_created` events `_search_touch_ids` always consults
    # (see its own docstring), so `_companion_deltas` still correctly finds
    # the on-disk company stale and this loop still runs on resume even with
    # the journal already gone. There is no byte-size prefilter on this path
    # to short-circuit before that check the way there is on the reachable
    # path, which is what made THAT path's early clear actually lose work.
    for trace_id in sorted(affected):
        project_per_trace_exports(
            None, project_slug=slug, trace_id=trace_id, events=compacted,
            events_authoritative=True, mirror_fallback=False,
        )

    _clear_journal(slug)

    return report


def reclaim_anchor_search(*, apply: bool = False) -> AnchorSearchReclaimReport:
    """Compact legacy/v2-fat anchor-search events across every project the
    bucket knows about, per the module docstring's three-step contract.

    Dry-run by default: detection (the streaming read + compact pass) always
    runs — it is pure/read-only from the REF's perspective (a candidate
    commit is written to the git object database either way, but never
    referenced — see the module docstring) — but the ref swap, mirror
    rewrite, and companion regeneration only happen when ``apply=True``. A
    second ``apply=True`` run over an already-compacted bucket reports zero
    deltas for every project (the CAS-skip-when-unchanged check, the
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
                project_report = _process_reachable_project(slug, repo, apply=apply)
            else:
                project_report = _process_unreachable_project(
                    slug, apply=apply, single_project_bucket=single_project_bucket
                )
            report.projects.append(project_report)
            outcome = project_report.action
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
            outcome = "error"
        # Progress visibility (issue #358): the O(corpus) streaming pass over
        # even ONE fat project can run for minutes with nothing else
        # observable — one stderr line per project completed, never stdout
        # (which under `--json` is reserved for the single JSON report
        # object; see bucket_remote.py's own push-pass progress line for the
        # same stderr-only convention).
        print(f"bucket reclaim: {slug}: {outcome}", file=sys.stderr, flush=True)

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
