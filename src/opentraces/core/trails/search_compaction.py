"""Anchor-search rollup + fat-summary compaction (issues #116 B, #358).

Plan 090 replaced the per-patch ``git_anchor_search_completed`` event (one per
searched Trace Patch, ~N/commit, ~505K already on live logs) with ONE v2
``opentraces.trail.anchor_search.v2`` summary event per (commit, reconcile-run)
carrying a per-patch ``results[]`` list. Plan 090 changed only the WRITE path;
it never rewrote the legacy events already on disk. Issue #358 then found that
v2's ``results[]`` itself was the real weight: it carried one dict per
SEARCHED patch, unknown outcomes included, which fanned ~26GB of mostly
never-anchored dicts into every trace companion a summary touched, and
repointed the WRITE path onto the v3 anchored-only shape (see contract.py's
``ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION``) — again without rewriting anything
already on disk. This module is the offline rewrite for BOTH gaps: it rolls
legacy per-patch events AND v2 fat summaries into the v3-compact shape
(anchored-only ``results[]`` plus the exact ``unanchored_trace_patch_ids`` of
what it drops) so a mature log sheds the volume either shape occupies.

This is a CAPABILITY, intentionally NOT wired into any live path. It operates
on an event STREAM and produces a NEW, self-consistent canonical chain.

What compaction touches and what it preserves
---------------------------------------------
* Legacy per-patch ``git_anchor_search_completed`` events sharing a
  ``(batch_id, search_head.hex)`` are collapsed into ONE v3-compact summary
  placed at the position of the group's first member. ``batch_id`` is the
  reconcile-run identity: a reconcile run appends exactly one batch.
* A v2 fat summary (no ``coverage`` and no ``unanchored_trace_patch_ids`` key
  — a full-mixed ``results[]``) is rewritten IN PLACE, at its own position,
  into the same v3-compact shape: its own results split into anchored-only
  plus the exact ids of what it drops. It is already one event per
  reconcile-run, so unlike the legacy shape it never needs grouping with
  anything else.
* A v3 event — EITHER variant, coverage-claim or already-compact — carries
  one of those two keys already and passes through unchanged. This is what
  makes compaction idempotent: an already-compacted (or always-v3) log is a
  no-op second pass.
* Every non-search event passes through unchanged in content.
* The event log is a tamper-evident, content-addressed chain (``event_id``
  binds ``event_sequence`` + ``previous_event_id`` + payload). Removing or
  rewriting events re-sequences everything downstream, so EVERY event after
  the first touched one gets a fresh ``event_id`` — exactly like a history
  rewrite. The compacted log is therefore a NEW valid chain, not a patched
  old one.

Two guarantees this rewrite must keep (both re-observed in the demo script):
  1. ``iter_search_records`` over the compacted log yields the SAME dedup-key
     and query/explain functional record stream as over the original, WITH
     ONE DOCUMENTED DELTA: a per-patch record for an outcome collapsed into
     ``unanchored_trace_patch_ids`` (either a legacy/v2-fat unknown this
     rewrite demoted, or one already carried that way by a live v3-compact
     event) loses its ``trace_id`` / ``step_index`` / ``generation_index`` —
     those identify the SEARCH ATTEMPT, not the outcome, and the exact-id list
     never carried them (see ``iter_search_records``'s minimal-record branch).
     Every dedup key (``trace_patch_id`` / ``search_head_sha`` /
     ``attribution_version`` / ``result``) and every anchored entry's full
     record — the only rows ``query.py``/``explain.py`` actually read per
     patch — are unaffected. ``source_event`` provenance (``event_id`` /
     ``event_sequence`` / ``event_time``) is unaffected by this delta too;
     plan 090 already documented it as SHARED across a summary's patches.
  2. ``opentraces bucket replay --repo`` reconstructs the compacted ref
     byte-identically from the rewritten ``bucket/events/v1/batches/`` mirror.

The dual-shape reader (:func:`search_records.iter_search_records`) is KEPT, not
retired: live logs are not compacted, so reads must still handle every shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .._time import utc_now_str
from .contract import ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION
from .models import TrailEvent, finalize_event
from .search_records import (
    ANCHOR_SEARCH_EVENT_TYPE,
    build_anchor_search_summary_payload,
    is_summary_search_event,
)

_COMPACTION_BATCH_ID = "anchor-search-compaction"
_COMPACTION_WRITER = "anchor-search-compaction"


@dataclass
class CompactionStats:
    events_in: int = 0
    events_out: int = 0
    legacy_search_events_in: int = 0
    summary_events_in: int = 0
    summary_events_out: int = 0
    groups_collapsed: int = 0
    # #358: v2 fat summaries rewritten IN PLACE to v3-compact — a subset of
    # summary_events_in disjoint from groups_collapsed (that counts legacy
    # multi-event GROUPS; this counts single already-summary events rewritten).
    fat_summaries_rewritten: int = 0
    non_search_events: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "events_in": self.events_in,
            "events_out": self.events_out,
            "legacy_search_events_in": self.legacy_search_events_in,
            "summary_events_in": self.summary_events_in,
            "summary_events_out": self.summary_events_out,
            "groups_collapsed": self.groups_collapsed,
            "fat_summaries_rewritten": self.fat_summaries_rewritten,
            "non_search_events": self.non_search_events,
            "events_removed": self.events_in - self.events_out,
        }


@dataclass
class _Slot:
    """One output position. Either a passthrough event or a summary-in-progress."""

    kind: str  # "passthrough" | "summary"
    # passthrough
    event: TrailEvent | None = None
    # summary
    search_head: Any = None
    algorithms_attempted: list[str] = field(default_factory=list)
    capture_method: list[str] = field(default_factory=list)
    schema_version: str | None = None
    security_version: str | None = None
    attribution_version: str | None = None
    event_time: str | None = None
    results: list[dict[str, Any]] = field(default_factory=list)


def _is_legacy_per_patch_search(event: TrailEvent) -> bool:
    return (
        event.event_type == ANCHOR_SEARCH_EVENT_TYPE
        and not is_summary_search_event(event)
    )


def _is_already_v3_shaped(payload: dict[str, Any]) -> bool:
    """True when ``payload`` already carries one of v3's two alternate keys.

    Both are opt-in on ``build_anchor_search_summary_payload`` and mutually
    exclusive with each other (asserted there) and with the v2 full-mixed
    shape — a payload carrying neither is v2 fat and needs rewriting; a
    payload carrying either is ALREADY anchored-only regardless of which
    caller minted it (anchors.py's coverage claim, maturation.py's slim
    flush, or this module's own prior rewrite), so it is passed through
    unchanged rather than re-detected by a caller-specific schema_version
    check — that is what makes a second compaction pass a no-op.
    """
    return "coverage" in payload or "unanchored_trace_patch_ids" in payload


def _result_entry_from_legacy(event: TrailEvent) -> dict[str, Any]:
    """Project a legacy per-patch search event into a ``results[]`` entry.

    The entry shape itself is unversioned — v2 and v3 differ only in whether
    unknown-outcome entries stay in ``results[]`` (v2) or get demoted to
    ``unanchored_trace_patch_ids`` (v3, see ``_refinalize``'s summary branch)
    — so this mirrors the entry shape ``reconcile_commit_anchors`` builds
    today (anchors.py) regardless of which shape the group collapses into.
    """
    payload = event.payload or {}
    return {
        "trace_patch_id": payload.get("trace_patch_id"),
        "trace_id": event.trace_id,
        "step_index": event.step_index,
        "generation_index": event.generation_index,
        "result": payload.get("result"),
        "created_anchor_ids": payload.get("created_anchor_ids") or [],
    }


def plan_compacted_stream(events: list[TrailEvent]) -> tuple[list[_Slot], CompactionStats]:
    """Walk ``events`` in sequence order and produce ordered output slots.

    Groups legacy per-patch search events by ``(batch_id, search_head.hex)``.
    The first member of a group opens a summary slot at its position; later
    members fold their result entry into that slot and emit nothing of their
    own. A v2 fat summary is already one event per reconcile-run, so it opens
    (and immediately closes) its OWN summary slot at its own position instead
    — never merged with a legacy group even if they happen to share a
    ``(batch_id, search_head.hex)``, since a real log never mixes the two
    write paths for the same reconcile run. A v3 event (either variant)
    passes through untouched.
    """
    stats = CompactionStats(events_in=len(events))
    ordered = sorted(events, key=lambda e: e.event_sequence)
    slots: list[_Slot] = []
    open_summaries: dict[tuple[str, str | None], _Slot] = {}

    for event in ordered:
        if is_summary_search_event(event):
            stats.summary_events_in += 1
            payload = event.payload or {}
            if _is_already_v3_shaped(payload):
                slots.append(_Slot(kind="passthrough", event=event))
                continue
            stats.fat_summaries_rewritten += 1
            slot = _Slot(
                kind="summary",
                search_head=payload.get("search_head"),
                algorithms_attempted=list(payload.get("algorithms_attempted") or []),
                capture_method=list(event.capture_method),
                schema_version=event.SCHEMA_VERSION,
                security_version=event.SECURITY_VERSION,
                attribution_version=event.ATTRIBUTION_VERSION,
                event_time=event.event_time,
            )
            slot.results.extend(
                entry for entry in payload.get("results") or [] if isinstance(entry, dict)
            )
            slots.append(slot)
            continue
        if _is_legacy_per_patch_search(event):
            stats.legacy_search_events_in += 1
            payload = event.payload or {}
            search_head = payload.get("search_head")
            head_hex = (
                search_head.get("hex") if isinstance(search_head, dict) else None
            )
            group_key = (event.batch_id, head_hex)
            slot = open_summaries.get(group_key)
            if slot is None:
                slot = _Slot(
                    kind="summary",
                    search_head=search_head,
                    algorithms_attempted=list(payload.get("algorithms_attempted") or []),
                    capture_method=list(event.capture_method),
                    schema_version=event.SCHEMA_VERSION,
                    security_version=event.SECURITY_VERSION,
                    attribution_version=event.ATTRIBUTION_VERSION,
                    event_time=event.event_time,
                )
                open_summaries[group_key] = slot
                slots.append(slot)
            slot.results.append(_result_entry_from_legacy(event))
            continue
        stats.non_search_events += 1
        slots.append(_Slot(kind="passthrough", event=event))

    # ``open_summaries`` holds exactly one slot per distinct legacy
    # (batch_id, search_head.hex) group — a fat-summary rewrite slot (above)
    # is never registered there, so this counts legacy GROUPS only, disjoint
    # from ``fat_summaries_rewritten``.
    stats.groups_collapsed = len(open_summaries)
    return slots, stats


def _refinalize(slots: list[_Slot]) -> list[TrailEvent]:
    """Assign a fresh contiguous sequence + chain and finalize every slot.

    Produces a self-consistent canonical chain (``import_event_log`` validates
    it). Passthrough events keep their content (type / payload / versions /
    capture_method / trace fields / event_time); only their sequence,
    previous_event_id and the derived event_id change.
    """
    finalized: list[TrailEvent] = []
    previous_event_id: str | None = None
    for index, slot in enumerate(slots, start=1):
        if slot.kind == "passthrough":
            assert slot.event is not None
            src = slot.event
            data = {
                "event_sequence": index,
                "event_time": src.event_time,
                "previous_event_id": previous_event_id,
                "trace_id": src.trace_id,
                "generation_index": src.generation_index,
                "step_index": src.step_index,
                "batch_id": _COMPACTION_BATCH_ID,
                "writer": _COMPACTION_WRITER,
                "capture_method": list(src.capture_method),
                "event_type": src.event_type,
                "payload": src.payload,
                "SCHEMA_VERSION": src.SCHEMA_VERSION,
                "SECURITY_VERSION": src.SECURITY_VERSION,
                "ATTRIBUTION_VERSION": src.ATTRIBUTION_VERSION,
            }
        else:
            # #358: the exact ids of every entry this rewrite drops from
            # ``results[]`` — the v3-compact shape's alternate-key contract
            # (build_anchor_search_summary_payload filters ``results`` to
            # anchored-only whenever this is not None). Matches maturation.py's
            # flush exactly: ``str(...)`` because a hand-built legacy/v2-fat
            # fixture id is not guaranteed to already be a str.
            unanchored_trace_patch_ids = [
                str(entry["trace_patch_id"])
                for entry in slot.results
                if entry.get("result") != "anchored" and entry.get("trace_patch_id")
            ]
            payload = build_anchor_search_summary_payload(
                schema_version=ANCHOR_SEARCH_COVERAGE_SCHEMA_VERSION,
                search_head=slot.search_head,
                algorithms_attempted=slot.algorithms_attempted,
                results=slot.results,
                unanchored_trace_patch_ids=unanchored_trace_patch_ids,
            )
            data = {
                "event_sequence": index,
                "event_time": slot.event_time or utc_now_str(),
                "previous_event_id": previous_event_id,
                # Match the live v3-compact draft: trace_id/step_index None, gen 0.
                "trace_id": None,
                "generation_index": 0,
                "step_index": None,
                "batch_id": _COMPACTION_BATCH_ID,
                "writer": _COMPACTION_WRITER,
                "capture_method": slot.capture_method,
                "event_type": ANCHOR_SEARCH_EVENT_TYPE,
                "payload": payload,
                "SCHEMA_VERSION": slot.schema_version,
                "SECURITY_VERSION": slot.security_version,
                "ATTRIBUTION_VERSION": slot.attribution_version,
            }
        data = {k: v for k, v in data.items() if v is not None}
        event = finalize_event(data)
        finalized.append(event)
        previous_event_id = event.event_id
    return finalized


def compact_search_events(
    events: list[TrailEvent],
) -> tuple[list[TrailEvent], CompactionStats]:
    """Return ``(compacted_stream, stats)`` for an in-memory event list."""
    slots, stats = plan_compacted_stream(events)
    compacted = _refinalize(slots)
    stats.events_out = len(compacted)
    stats.summary_events_out = sum(
        1
        for e in compacted
        if e.event_type == ANCHOR_SEARCH_EVENT_TYPE and is_summary_search_event(e)
    )
    return compacted, stats


def compact_repo(
    source_repo: Path,
    target_repo: Path,
    *,
    force: bool = True,
) -> dict[str, Any]:
    """Read the canonical log from ``source_repo``, compact, write to ``target_repo``.

    ``target_repo`` MUST be a different repo (or a scratch clone) — this never
    rewrites a repo in place to keep the live ref untouched by accident.
    """
    from .event_log import import_event_log, read_events

    source_repo = Path(source_repo).resolve()
    target_repo = Path(target_repo).resolve()
    if source_repo == target_repo:
        raise ValueError(
            "compact_repo writes a NEW chain; target_repo must differ from source_repo"
        )
    events = read_events(source_repo, verify=False)
    compacted, stats = compact_search_events(events)
    imported = import_event_log(
        target_repo, compacted, writer=_COMPACTION_WRITER, force=force
    )
    return {"stats": stats.as_dict(), "import": imported}


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        prog="python -m opentraces.core.trails.search_compaction",
        description="Roll legacy per-patch and v2 fat anchor-search events into v3-compact summaries.",
    )
    parser.add_argument("source_repo", type=Path, help="Repo to read the canonical log from.")
    parser.add_argument("target_repo", type=Path, help="Repo to write the compacted chain to.")
    parser.add_argument("--force", action="store_true", help="Replace a differing target ref.")
    args = parser.parse_args(argv)
    result = compact_repo(args.source_repo, args.target_repo, force=args.force)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
