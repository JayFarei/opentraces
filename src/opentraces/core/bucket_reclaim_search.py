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
   step). Reconciliation therefore removes exactly the pre-compaction
   ``event_id``s this run's own read observed for THIS project (an exact,
   collision-free set — ``event_id`` is a content-addressed hash, so it can
   never mis-identify another project's events) and adds whatever of the
   compacted chain isn't mirrored yet. An event whose id is unchanged by the
   rewrite (anything before the first touched position in the chain) is
   therefore correctly left alone rather than round-tripped.
3. Regenerates ONLY the trail companions a fat/legacy search event actually
   touched (``project_per_trace_exports``, atomic same-bytes-skip writers —
   already-current companions cost nothing to "regenerate").

Honest boundary: a project whose Git repo is unreachable can only be
compacted from the bucket's own mirror, and — because the mirror carries no
per-project attribution — that is only sound when this project is the ONLY
one the bucket knows about (so the whole mirror is unambiguously its own).
A multi-project bucket with an unreachable project's repo is reported
``skipped`` with that reason; its bytes are left untouched rather than risk
touching a different project's data. Blobs, snapshots, and non-search events
are never touched by this pass, matching ``search_compaction``'s own scope.

Same O(corpus) class as ``bucket repair`` — a cheap byte-size prefilter (see
``_events_tree_bytes``; no JSON parsing) skips the full read+compact for a
project whose canonical event history is too small to hold anything worth
reclaiming.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from ._bucket_io import _atomic_write_bytes, _atomic_write_json, _canonical_json, _gzip_deterministic
from ._time import utc_now_str
from .bucket_envelope import _events_for_trace_from_iter, _iter_opted_in_projects, project_per_trace_exports
from .bucket_events import BUCKET_EVENTS_INDEX_SCHEMA, read_events_mirror_batches
from .bucket_layout import _path_part, events_v1_batches_dir, events_v1_index_path, traces_v1_root, trace_v1_trail_path
from .trails import EVENT_LOG_REF, ANCHOR_SEARCH_EVENT_TYPE, import_event_log, read_events
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


def _write_mirror_batches(final_events: list[Any], *, repo_id: str | None) -> None:
    """Full write-new-then-remove-stale rebuild of ``bucket/events/v1/`` from
    ``final_events``. New batch files are written FIRST (additive, each one
    atomically via ``_atomic_write_bytes``); files no longer represented are
    only unlinked AFTER every new one lands. A kill anywhere in between
    leaves a strict superset of ``final_events`` on disk — readable, just not
    yet fully shrunk — and a re-run's fresh diff finishes the job."""

    batches_dir = events_v1_batches_dir()
    existing = sorted(batches_dir.glob("*.jsonl.gz")) if batches_dir.exists() else []

    order: list[str] = []
    by_batch: dict[str, list[Any]] = {}
    for event in final_events:
        if event.batch_id not in by_batch:
            by_batch[event.batch_id] = []
            order.append(event.batch_id)
        by_batch[event.batch_id].append(event)

    new_paths: set[Path] = set()
    latest_seq = 0
    for seq, bid in enumerate(order, start=1):
        group = sorted(by_batch[bid], key=lambda e: e.event_sequence)
        lines = [_canonical_json(event.model_dump(mode="json")) for event in group]
        body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
        compressed = _gzip_deterministic(body)
        path = batches_dir / f"{seq:012d}-{_path_part(bid)}.jsonl.gz"
        _atomic_write_bytes(path, compressed)
        new_paths.add(path)
        latest_seq = max(latest_seq, group[-1].event_sequence)

    for path in existing:
        if path not in new_paths:
            path.unlink(missing_ok=True)

    index = {
        "schema_version": BUCKET_EVENTS_INDEX_SCHEMA,
        "repo_id": repo_id,
        "event_log_ref": EVENT_LOG_REF,
        "event_log_head": None,
        "batch_count": len(new_paths),
        "last_batch_id": order[-1] if order else None,
        "latest_event_sequence": latest_seq,
        "state": "ok" if final_events else "missing",
        "updated_at": utc_now_str(),
        "batches_written": len(new_paths),
    }
    _atomic_write_json(events_v1_index_path(), index)


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
        return self.ref_bytes_before + self.companion_bytes_before

    @property
    def bytes_after(self) -> int:
        return self.ref_bytes_after + self.companion_bytes_after

    @property
    def bytes_reclaimed(self) -> int:
        return self.bytes_before - self.bytes_after

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

    @property
    def bytes_reclaimed(self) -> int:
        return sum(p.bytes_before - p.bytes_after for p in self.projects)

    def as_dict(self) -> dict[str, Any]:
        return {
            "apply": self.apply,
            "projects": [p.as_dict() for p in self.projects],
            "project_count": len(self.projects),
            "bytes_reclaimed": self.bytes_reclaimed,
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

    head = _head_sha(repo)
    if head is None:
        report.action = "clean"
        report.reason = "no canonical event log for this project"
        return report

    tree_bytes = _events_tree_bytes(repo, head)
    report.ref_bytes_before = report.ref_bytes_after = tree_bytes
    if tree_bytes < _FAT_PREFILTER_MIN_BYTES:
        report.reason = "below fat-detection size prefilter"
        return report

    old_events = read_events(repo, verify=False)
    compacted, stats = compact_search_events(old_events)
    report.events_before = len(old_events)
    report.events_after = len(compacted)
    report.legacy_events_collapsed = stats.legacy_search_events_in
    report.fat_summaries_rewritten = stats.fat_summaries_rewritten

    old_ids = {e.event_id for e in old_events}
    target_ids = {e.event_id for e in compacted}
    ref_would_change = old_ids != target_ids

    affected = _search_touch_ids(old_events) | _search_touch_ids(compacted)
    comp_before, comp_after, companions_stale = _companion_deltas(slug, affected, compacted)

    # Mirror preview — read-only, safe in both modes; ``stale_ids`` is exact
    # (event_id is a content-addressed hash, so it can never mis-identify
    # another project's events) and ``to_add`` covers "never mirrored yet".
    # Computed (and, below, ALWAYS acted on) regardless of ``ref_would_change``
    # so a resume — the ref already swapped by an earlier, interrupted run,
    # ``compact_search_events`` finding nothing new THIS pass — still finishes
    # a mirror or companion step that pass never reached.
    mirror_present = events_v1_index_path().exists()
    mirror_events: list[Any] = list(read_events_mirror_batches()) if mirror_present else []
    stale_ids = old_ids - target_ids
    existing_ids = {e.event_id for e in mirror_events}
    survivors = [e for e in mirror_events if e.event_id not in stale_ids]
    to_add = [e for e in compacted if e.event_id not in existing_ids]
    would_touch_mirror = mirror_present and (bool(stale_ids & existing_ids) or bool(to_add))
    report.mirror_events_removed = len(mirror_events) - len(survivors)
    report.mirror_events_added = len(to_add)

    if not (ref_would_change or would_touch_mirror or companions_stale):
        report.reason = "no legacy or v2-fat anchor-search events found"
        return report

    report.companion_bytes_before = comp_before
    report.companion_bytes_after = comp_after
    report.companions_regenerated = sorted(affected)
    report.action = "compacted"

    if not apply:
        report.ref_bytes_after = sum(_event_blob_bytes(e) for e in compacted)
        report.mirror_rewritten = would_touch_mirror
        return report

    import_event_log(repo, compacted, writer=_RECLAIM_WRITER, force=True)
    report.ref_rewritten = ref_would_change
    new_head = _head_sha(repo)
    report.ref_bytes_after = _events_tree_bytes(repo, new_head) if new_head else 0

    if would_touch_mirror:
        _write_mirror_batches(survivors + to_add, repo_id=slug)
        report.mirror_rewritten = True

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

    old_ids = {e.event_id for e in old_events}
    target_ids = {e.event_id for e in compacted}
    mirror_would_change = old_ids != target_ids

    affected = _search_touch_ids(old_events) | _search_touch_ids(compacted)
    comp_before, comp_after, companions_stale = _companion_deltas(slug, affected, compacted)

    # Same resume note as the reachable path: a run interrupted after the
    # mirror write but before companion regen leaves ``old_events`` (the
    # mirror, freshly re-read) already matching ``compacted`` on a re-run —
    # ``mirror_would_change`` alone would miss it, so ``companions_stale``
    # (a real on-disk-vs-projected comparison) is checked too.
    if not (mirror_would_change or companions_stale):
        report.reason = "no legacy or v2-fat anchor-search events found"
        return report

    report.companion_bytes_before = comp_before
    report.companion_bytes_after = comp_after
    report.companions_regenerated = sorted(affected)
    report.action = "mirror_only_compacted"
    report.reason = "repo unreachable; canonical ref not rewritten, mirror-only"
    if mirror_would_change:
        # Whole-mirror replace (single-project bucket, no per-project
        # attribution needed): every old event is superseded by a re-chained
        # one, even the ones whose content is unchanged (their event_id
        # still moves), so the honest count is the full before/after size,
        # not a subset diff.
        report.mirror_events_removed = len(old_events)
        report.mirror_events_added = len(compacted)
        report.mirror_rewritten = True

    if not apply:
        return report

    if mirror_would_change:
        _write_mirror_batches(compacted, repo_id=slug)

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
    ``stale_ids``/``to_add`` set-diff, and the atomic same-bytes-skip
    companion writers all independently no-op).
    """

    report = AnchorSearchReclaimReport(apply=apply)

    slug_to_repo = {slug: path for path, slug in _iter_opted_in_projects()}
    candidate_slugs = sorted(set(slug_to_repo) | _bucket_known_slugs())
    single_project_bucket = len(candidate_slugs) == 1

    for slug in candidate_slugs:
        repo = slug_to_repo.get(slug)
        if repo is not None:
            report.projects.append(_process_reachable_project(slug, repo, apply=apply))
        else:
            report.projects.append(
                _process_unreachable_project(slug, apply=apply, single_project_bucket=single_project_bucket)
            )

    return report
