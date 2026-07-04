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


def _read_mirror_events_scoped(
    trace_ids: set[str],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Bounded, corruption-honest read of the bucket events mirror.

    PR #217 real-bucket apply crash follow-up. The previous apply path did
    ``list(read_events_mirror_batches())`` — a full-mirror materialization
    with strict per-line ``TrailEvent.model_validate_json`` over EVERY event
    (~505K attribution/anchor events on the real bucket): the #87 O(corpus)
    anti-pattern, and fatally fragile — one truncated line anywhere in the
    mirror pydantic-crashed the whole apply before a single write.

    This reader mirrors ``read_events_scoped``'s prefilter-then-validate
    pattern: a cheap raw-substring prefilter (planned ``trace_ids``) decides
    which lines are worth the pydantic model cost; only those are
    materialized as ``TrailEvent``. The over-inclusive prefilter is
    harmless — ``_events_for_trace_from_iter`` post-filters exactly, and it
    also catches the plan-090 ``git_anchor_search_completed`` summary
    events (top-level trace_id=None) because their ``results[]`` entries
    carry the per-patch trace ids in the raw bytes.

    Corruption policy (the lane's "skip is a lie" rule, applied honestly in
    both directions):

      - a batch that cannot be opened at all → blocking ``ValueError``
        (consistent with the audit's batch-level rule);
      - a corrupt line that LOOKS like a context event (cheap string
        prefilter on the four context event-type markers) → blocking
        ``ValueError`` naming the batch file — it may be the very data the
        reproject NEEDS;
      - a corrupt NON-context line (e.g. the truncated attribution event
        observed on the real bucket) → skipped for reproject purposes but
        COUNTED and returned so the apply report surfaces it as
        ``mirror_corrupt_lines`` — never silent.

    Returns ``(events, corrupt_lines)``.
    """

    import gzip
    import json as _json

    from .bucket_layout import events_v1_batches_dir
    from .context_tree.contract import CONTEXT_EVENT_TYPES
    from .trails import TrailEvent

    events: list[Any] = []
    corrupt_lines: list[dict[str, Any]] = []

    batches_dir = events_v1_batches_dir()
    if not batches_dir.exists():
        raise FileNotFoundError(
            f"event mirror batches dir missing at {batches_dir} — cannot "
            "apply a backfill without the canonical mirror; run "
            "'opentraces setup watcher tick' or 'opentraces bucket repair'"
        )

    def _looks_context(raw: str) -> bool:
        return any(marker in raw for marker in CONTEXT_EVENT_TYPES)

    for batch_path in sorted(batches_dir.glob("*.jsonl.gz")):
        try:
            with gzip.open(batch_path, "rt", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, gzip.BadGzipFile) as exc:
            raise ValueError(
                f"unreadable events mirror batch {batch_path}: {exc} — a "
                "corrupt batch cannot be silently skipped during apply"
            ) from exc
        for line_no, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                _json.loads(line)
            except _json.JSONDecodeError as exc:
                if _looks_context(line):
                    raise ValueError(
                        f"corrupt context event line in events mirror batch "
                        f"{batch_path} (line {line_no}): {exc} — a context "
                        "event the backfill may NEED is unreadable; refusing "
                        "to apply (fail-closed, zero writes)"
                    ) from exc
                corrupt_lines.append({
                    "batch": batch_path.name,
                    "line": line_no,
                    "error": str(exc),
                })
                continue
            if not any(tid in line for tid in trace_ids):
                continue
            try:
                events.append(TrailEvent.model_validate_json(line))
            except Exception as exc:
                if _looks_context(line):
                    raise ValueError(
                        f"invalid context event line in events mirror batch "
                        f"{batch_path} (line {line_no}): {exc} — refusing to "
                        "apply (fail-closed, zero writes)"
                    ) from exc
                corrupt_lines.append({
                    "batch": batch_path.name,
                    "line": line_no,
                    "error": str(exc),
                })
    return events, corrupt_lines


def _apply_reproject(
    plan: BackfillPlan, *, shared_events: list[Any]
) -> list[dict[str, Any]]:
    from .bucket_envelope import project_per_trace_exports

    results = []
    for action in plan.reproject:
        project_per_trace_exports(
            None,
            project_slug=action.project_slug,
            trace_id=action.trace_id,
            events=shared_events,
            events_authoritative=True,
            # shared_events IS the (scoped, corruption-vetted) mirror read —
            # the interior issue-#28 fallback would be a redundant strict
            # full-mirror re-read that can crash on a corrupt non-context
            # line this apply already counted.
            mirror_fallback=False,
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


def _apply_drop_dangling(
    plan: BackfillPlan, *, shared_events: list[Any]
) -> list[dict[str, Any]]:
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
            # PR #217 apply-crash follow-up: previously this passed no
            # events, so the interior issue-#28 fallback did a FULL strict
            # mirror read PER dropped trace (O(N × corpus), and the same
            # corrupt-line fragility as the reproject phase). The shared
            # scoped read (whose trace_ids include the drop_dangling set)
            # supplies whatever mirror events these traces have (usually
            # none — that is what makes the ids dangling), and
            # mirror_fallback=False keeps the interior full read off.
            # events_authoritative=False preserves the prior anchor
            # behavior byte-identically: the fallback path never set
            # anchor_source_ok, so trace.json patches were (and stay)
            # written verbatim, never de-attributed by this phase.
            events=shared_events,
            events_authoritative=False,
            mirror_fallback=False,
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

    The mirror is read ONCE, scoped to the planned reproject + drop_dangling
    trace ids (:func:`_read_mirror_events_scoped`), BEFORE any phase writes —
    so a blocking mirror-corruption error is fail-closed (zero writes), and
    the O(corpus) strict full-mirror materialization that crashed the first
    real-bucket apply is gone. Corrupt non-context mirror lines are counted
    and surfaced under ``mirror_corrupt_lines``, never silently skipped.
    """

    scoped_ids = {a.trace_id for a in plan.reproject} | {
        a.trace_id for a in plan.drop_dangling
    }
    shared_events: list[Any] = []
    mirror_corrupt_lines: list[dict[str, Any]] = []
    if scoped_ids:
        shared_events, mirror_corrupt_lines = _read_mirror_events_scoped(scoped_ids)

    reproject_results = _apply_reproject(plan, shared_events=shared_events)
    codex_results, codex_skipped, mirror_sync_failures = _apply_codex_recover(
        plan, project_paths=project_paths or {}
    )
    drop_results = _apply_drop_dangling(plan, shared_events=shared_events)

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
        # PR #217 apply-crash follow-up: corrupt NON-context mirror lines the
        # scoped read skipped for reproject purposes — counted and surfaced,
        # never silent ("skip is a lie"). Corrupt CONTEXT lines never reach
        # here: they raise before any write.
        "mirror_corrupt_lines": mirror_corrupt_lines,
    }
