"""Context companion backfill (issue #210, seal-family W2).

Reads the independent audit's per-trace results (``context_companions_audit``)
and classifies each defective trace into one of three actionable buckets:

  - ``reproject``: a "stale projection" trace — the backing
    ``context_node_observed`` events already exist in the shared event
    mirror, the companion just hasn't been re-projected since. Fixed by
    re-running :func:`bucket_envelope.project_per_trace_exports` with the
    events already in hand — no live project checkout required.
  - ``codex_recover``: a ``legitimately_empty`` codex-cli trace whose steps
    carry enough structure to deterministically re-derive a Context Tree via
    :func:`build_context_tree_projection_from_record` (record-derived, no
    transcript needed). Requires a LIVE project checkout (the recovered
    events must be appended to that project's canonical Git event log) —
    when the project directory is not resolvable on this machine the action
    is reported but not applied.
  - ``drop_dangling``: an inconsistent trace whose stamped ``context_node_id``
    values resolve to nothing anywhere (not even the event mirror) — a
    genuine append failure with no recoverable data. The dangling ids are
    cleared from ``trace.json`` steps so the record stops lying; the count is
    an accepted loss, never fabricated.

Every write path is idempotent: re-running ``apply_backfill`` over an
already-fixed trace produces byte-identical output (the underlying
``_atomic_write_*`` helpers skip same-bytes writes, and
:func:`build_context_tree_projection_from_record` is a pure function of the
record).

This module NEVER re-implements the audit's read logic — it imports
:mod:`opentraces.core.context_companions_audit` for every observation. The
audit direction stays one-way: the audit never imports this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord

from .context_companions_audit import AuditReport, audit_bucket


@dataclass
class BackfillAction:
    kind: str  # reproject | codex_recover | drop_dangling | skipped_no_live_project
    trace_id: str
    project_slug: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackfillPlan:
    bucket_root: Path
    audit: AuditReport
    reproject: list[BackfillAction]
    codex_recover: list[BackfillAction]
    drop_dangling: list[BackfillAction]
    skipped_no_live_project: list[BackfillAction]

    def counts(self) -> dict[str, int]:
        return {
            "reproject": len(self.reproject),
            "codex_recover": len(self.codex_recover),
            "drop_dangling": len(self.drop_dangling),
            "skipped_no_live_project": len(self.skipped_no_live_project),
        }

    def all_actions(self) -> list[BackfillAction]:
        return [
            *self.reproject,
            *self.codex_recover,
            *self.drop_dangling,
            *self.skipped_no_live_project,
        ]


def _codex_recoverable_drafts(trace_json_path: Path) -> tuple[TraceRecord | None, int]:
    """Dry-run probe: would a codex record-derived rebuild produce anything?

    Read-only — never touches the event log. Returns ``(record, node_count)``;
    ``node_count == 0`` means genuinely nothing to recover (no fabrication).
    """

    import json

    try:
        raw = json.loads(trace_json_path.read_text(encoding="utf-8"))
        record = TraceRecord.model_validate(raw)
    except Exception:
        return None, 0

    from ..capture.codex_cli.context_tree_capture import (
        build_context_tree_projection_from_record,
    )

    try:
        projection = build_context_tree_projection_from_record(record)
    except Exception:
        return record, 0
    return record, len(projection.nodes)


def plan_backfill(bucket_root: Path | None = None) -> BackfillPlan:
    """Read-only: classify every defective trace into an action bucket.

    Never writes. Safe to call against the real bucket at any time (this is
    what ``--dry-run`` reports).
    """

    from .paths import bucket_dir

    root = Path(bucket_root) if bucket_root is not None else bucket_dir()
    report = audit_bucket(root)

    reproject: list[BackfillAction] = []
    codex_recover: list[BackfillAction] = []
    drop_dangling: list[BackfillAction] = []

    for t in report.inconsistent_traces():
        missing_from_companion = t.mirror_node_ids - t.companion_node_ids
        extra_in_companion = t.companion_node_ids - t.mirror_node_ids
        unrecoverable = [nid for nid in t.dangling_ids if nid not in t.mirror_node_ids]

        # Codex external review (PR #217, issue #210) critical #3 — a trace
        # can carry BOTH defects at once (a stale/extra companion AND an
        # unrecoverable dangling id). The previous if/elif planned at most
        # one action per trace, so one of the two defects survived a full
        # apply pass and the "second run is a byte-identical no-op"
        # idempotency contract broke (a second dry-run still reported
        # outstanding work). Every branch below is independent so a trace
        # can land in both ``reproject`` and ``drop_dangling`` in the SAME
        # plan, healed in ONE ``apply_backfill`` call.
        actioned = False

        # A node-id SET mismatch in EITHER direction is a stale-projection
        # defect reproject fixes: missing_from_companion is the companion
        # lagging the mirror, extra_in_companion is the companion carrying
        # nodes the mirror doesn't (a previously silently-unplanned case —
        # reprojecting from the authoritative mirror purges the phantom
        # entries instead of leaving them planned as "no action").
        if missing_from_companion or extra_in_companion:
            reproject.append(
                BackfillAction(
                    kind="reproject",
                    trace_id=t.trace_id,
                    project_slug=t.project_slug,
                    detail={
                        "missing_node_count": len(missing_from_companion),
                        "extra_node_count": len(extra_in_companion),
                        "mirror_node_count": len(t.mirror_node_ids),
                    },
                )
            )
            actioned = True

        if unrecoverable:
            drop_dangling.append(
                BackfillAction(
                    kind="drop_dangling",
                    trace_id=t.trace_id,
                    project_slug=t.project_slug,
                    detail={"dangling_ids": unrecoverable},
                )
            )
            actioned = True

        if not actioned:
            # Should be unreachable given audit_trace's classification
            # logic (every "inconsistent" trace has a dangling id and/or a
            # node-set mismatch) — but never silently drop a flagged
            # defect into "no action" if that invariant ever breaks.
            raise RuntimeError(
                f"inconsistent trace {t.trace_id!r} ({t.project_slug}) has no "
                f"actionable defect classification — reasons={t.reasons!r}; "
                "refusing to silently plan no action for a flagged defect"
            )

    for t in report.legitimately_empty_traces():
        if t.agent_name != "codex-cli":
            continue
        if t.trace_json_path is None:
            continue
        _record, node_count = _codex_recoverable_drafts(t.trace_json_path)
        if node_count > 0:
            codex_recover.append(
                BackfillAction(
                    kind="codex_recover",
                    trace_id=t.trace_id,
                    project_slug=t.project_slug,
                    detail={"recoverable_node_count": node_count},
                )
            )

    return BackfillPlan(
        bucket_root=root,
        audit=report,
        reproject=reproject,
        codex_recover=codex_recover,
        drop_dangling=drop_dangling,
        skipped_no_live_project=[],
    )


def _apply_reproject(plan: BackfillPlan) -> list[dict[str, Any]]:
    from .bucket_events import read_events_mirror_batches
    from .bucket_envelope import project_per_trace_exports

    results = []
    if not plan.reproject:
        return results
    shared_events = list(read_events_mirror_batches())
    for action in plan.reproject:
        project_per_trace_exports(
            None,
            project_slug=action.project_slug,
            trace_id=action.trace_id,
            events=shared_events,
            events_authoritative=True,
        )
        results.append({"trace_id": action.trace_id, "applied": True})
    return results


def _apply_codex_recover(
    plan: BackfillPlan, *, project_paths: dict[str, Path]
) -> tuple[list[dict[str, Any]], list[BackfillAction], list[dict[str, Any]]]:
    from .bucket_trace_records import (
        read_trace_record_object,
        trace_record_path,
        write_trace_record,
    )
    from .bucket_envelope import project_per_trace_exports
    from .bucket_events import sync_events_mirror
    from .trails.event_log import append_event_batch
    from ..capture.codex_cli.context_tree_capture import (
        build_context_tree_projection_from_record,
    )

    results = []
    skipped: list[BackfillAction] = []
    mirror_sync_failures: list[dict[str, Any]] = []
    for action in plan.codex_recover:
        project_dir = project_paths.get(action.project_slug)
        if project_dir is None or not Path(project_dir).is_dir():
            skipped.append(
                BackfillAction(
                    kind="skipped_no_live_project",
                    trace_id=action.trace_id,
                    project_slug=action.project_slug,
                    detail={"reason": "no live project checkout on this machine"},
                )
            )
            continue

        obj = read_trace_record_object(
            trace_record_path(action.project_slug, action.trace_id)
        )
        if obj is None:
            skipped.append(
                BackfillAction(
                    kind="skipped_no_live_project",
                    trace_id=action.trace_id,
                    project_slug=action.project_slug,
                    detail={"reason": "no trace-record object on file"},
                )
            )
            continue

        record = obj.record
        projection = build_context_tree_projection_from_record(record)
        if not projection.drafts:
            # Never fabricate: nothing survives re-derivation, leave empty.
            continue

        append_event_batch(
            Path(project_dir),
            projection.drafts,
            writer="w2-backfill-codex-recover",
        )
        try:
            sync_events_mirror(Path(project_dir), repo_id=action.project_slug)
        except Exception as exc:
            # Codex external review (PR #217, issue #210) critical #3 — this
            # used to be a bare ``except Exception: pass``. The postcondition
            # of this action (companion consistent with the shared bucket
            # mirror) DEPENDS on the mirror sync succeeding: writing the
            # per-trace companion/trace.json here regardless would make this
            # trace's own export look correct locally (it is built straight
            # off the live repo, not the mirror) while the bucket-wide
            # events/v1 mirror the AUDIT and ``reproject`` trust stays
            # unaware of the newly appended nodes — a future ``reproject``
            # pass, trusting the (stale) mirror as authoritative, would then
            # regress this very fix. So: never swallow the failure, and
            # never half-apply. The freshly appended events already landed
            # on the canonical append-only Git log (no data loss); we simply
            # do NOT stamp trace.json / write the companion for this trace
            # yet, leaving it exactly as it was (``legitimately_empty``) so
            # the next backfill pass retries it whole once the mirror sync
            # succeeds.
            mirror_sync_failures.append({
                "trace_id": action.trace_id,
                "project_slug": action.project_slug,
                "error": str(exc),
            })
            continue

        step_map = projection.summary.get("step_node_id_map", {})
        for step in record.steps:
            nid = step_map.get(step.step_index)
            if nid:
                step.context_node_id = nid

        write_trace_record(
            record,
            project_slug=action.project_slug,
            source_layer=obj.source_layer,
            legacy_mirror=bool(obj.envelope.get("legacy_mirror", True)),
        )
        project_per_trace_exports(
            Path(project_dir),
            project_slug=action.project_slug,
            trace_id=action.trace_id,
            record=record,
        )
        results.append(
            {"trace_id": action.trace_id, "applied": True, "node_count": len(projection.nodes)}
        )
    return results, skipped, mirror_sync_failures


def _apply_drop_dangling(plan: BackfillPlan) -> list[dict[str, Any]]:
    from .bucket_trace_records import (
        read_trace_record_object,
        trace_record_path,
        write_trace_record,
    )
    from .bucket_envelope import project_per_trace_exports

    results = []
    for action in plan.drop_dangling:
        obj = read_trace_record_object(
            trace_record_path(action.project_slug, action.trace_id)
        )
        if obj is None:
            continue
        record = obj.record
        dangling = set(action.detail.get("dangling_ids") or [])
        changed = False
        for step in record.steps:
            if step.context_node_id in dangling:
                step.context_node_id = None
                changed = True
        if not changed:
            continue
        write_trace_record(
            record,
            project_slug=action.project_slug,
            source_layer=obj.source_layer,
            legacy_mirror=bool(obj.envelope.get("legacy_mirror", True)),
        )
        project_per_trace_exports(
            None,
            project_slug=action.project_slug,
            trace_id=action.trace_id,
            record=record,
        )
        results.append(
            {"trace_id": action.trace_id, "dropped": sorted(dangling)}
        )
    return results


def apply_backfill(
    plan: BackfillPlan, *, project_paths: dict[str, Path] | None = None
) -> dict[str, Any]:
    """Execute a previously computed :class:`BackfillPlan`.

    ``project_paths`` maps ``project_slug -> live project directory`` for the
    ``codex_recover`` action (it needs a live Git repo to append to the
    canonical event log). Traces whose project is not resolvable are reported
    under ``skipped_no_live_project`` rather than silently attempted.
    """

    reproject_results = _apply_reproject(plan)
    codex_results, codex_skipped, mirror_sync_failures = _apply_codex_recover(
        plan, project_paths=project_paths or {}
    )
    drop_results = _apply_drop_dangling(plan)

    return {
        "reproject": reproject_results,
        "codex_recover": codex_results,
        "drop_dangling": drop_results,
        "skipped_no_live_project": [
            {"trace_id": a.trace_id, "project_slug": a.project_slug, "detail": a.detail}
            for a in codex_skipped
        ],
        # Codex external review (PR #217, issue #210) critical #3 — surfaced,
        # never swallowed: a codex-recover whose sync_events_mirror() call
        # failed is reported here instead of being silently absorbed. The
        # trace is left untouched (still legitimately_empty) so the next
        # backfill pass retries it whole.
        "mirror_sync_failures": mirror_sync_failures,
    }
