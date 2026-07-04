"""``import_capsule`` — write a resolved capsule into the local bucket.

The explicit opt-in WRITE side of the capsule consume ladder (issue #196). Where
``capsule get`` resolves+prints and creates zero state, ``capsule import``
materializes the carried projection into the bucket as a FIRST-CLASS trace:

* ``source.trace_id`` is REUSED as the bucket primary key (never minted), so
  ``map`` / ``slice`` / ``trace get`` / ``run-intel`` project the imported unit
  natively — a capsule becomes indistinguishable from a local trace.
* the carried spine (``slice.steps`` + intent + environment) is materialized into
  a **schema-valid** ``TraceRecord`` — ``TraceRecord.model_validate`` runs BEFORE
  any file is written, so an unparseable projection is an import ERROR, never a
  partial write.
* the carried ``trail_anchors`` (recorded anchor rows) are written into the
  per-trace Trail companion; on empty anchors the companion is written empty and
  ``trail_anchors_unavailable`` is surfaced.

Collision rules preserve "capture once, project many":

* re-importing the SAME ``capsule_id`` is an idempotent no-op (dedup key);
* a DIFFERENT ``capsule_id`` over the same ``trace_id`` SCOPE-MERGES — union
  disjoint steps/anchors, last-import-wins on an overlapping leaf (recorded as a
  conflict), provenance appended, never an overwrite-or-delete.
"""

from __future__ import annotations

import gzip
from typing import Any

from opentraces_schema import Step, TraceRecord
from pydantic import ValidationError

from .._bucket_io import _atomic_write_gzip, _atomic_write_json, _canonical_json
from ..bucket_layout import (
    trace_v1_context_path,
    trace_v1_json_path,
    trace_v1_sources_path,
    trace_v1_trail_path,
)
from ..bucket_trace_records import (
    read_trace_record_object,
    trace_record_path,
    write_trace_record,
)
from .contract import validate_capsule

CAPSULE_IMPORT_REPORT_SCHEMA = "opentraces.capsule.import.v1"
CAPSULE_IMPORT_PROVENANCE_SCHEMA = "opentraces.capsule.import_provenance.v1"


class CapsuleImportError(RuntimeError):
    """Import refused: no trace id, or an unparseable (non-schema-valid) projection.

    Raised BEFORE any bucket file is written, so a bad projection never leaves a
    partial write behind.
    """


def _ctx_summary(capsule: dict[str, Any]) -> dict[str, Any]:
    """Minimal Context Tree summary for fidelity detection (``capture_methods``)."""

    cm = (capsule.get("source") or {}).get("capture_method")
    return {"capture_methods": [cm]} if cm else {}


def materialize_trace_record(
    capsule: dict[str, Any],
    *,
    capsule_ids: list[str] | None = None,
    conflicts: list[dict[str, Any]] | None = None,
) -> TraceRecord:
    """Build a schema-valid ``TraceRecord`` from the carried capsule spine.

    Validates via ``TraceRecord.model_validate`` and raises
    :class:`CapsuleImportError` on any shape problem — the caller runs this
    BEFORE writing, so an unparseable projection is a clean error.
    """

    source = capsule.get("source") or {}
    trace_id = str(source.get("trace_id") or "").strip()
    if not trace_id:
        raise CapsuleImportError(
            "capsule.source.trace_id is required; cannot import a capsule with no trace id."
        )
    slice_payload = capsule.get("slice") or {}
    raw_steps = list(slice_payload.get("steps") or [])
    intent = capsule.get("intent") or {}
    env = capsule.get("environment") or {}
    eco = env.get("language_ecosystem")
    if isinstance(eco, str) and eco:
        eco_list = [eco]
    elif isinstance(eco, list):
        eco_list = [str(v) for v in eco if v]
    else:
        eco_list = []

    payload = {
        "trace_id": trace_id,
        # The capsule carries no session_id; reuse the trace_id so the
        # materialized record is self-consistent and deterministic across machines.
        "session_id": trace_id,
        "agent": {
            "name": source.get("agent") or "unknown",
            "version": source.get("agent_version"),
            "model": source.get("model"),
        },
        "task": {"description": intent.get("headline")},
        "environment": {"language_ecosystem": eco_list},
        "steps": raw_steps,
        "dependencies": list(env.get("dependencies") or [])[:200],
        "context_tree_summary": _ctx_summary(capsule),
        "metadata": {
            "capsule_import": {
                "schema_version": CAPSULE_IMPORT_PROVENANCE_SCHEMA,
                "capsule_ids": (
                    list(capsule_ids)
                    if capsule_ids is not None
                    else [str(capsule.get("capsule_id") or "")]
                ),
                "conflicts": list(conflicts or []),
            }
        },
    }
    try:
        return TraceRecord.model_validate(payload)
    except ValidationError as exc:
        raise CapsuleImportError(
            f"capsule projection is not a schema-valid TraceRecord: {exc}"
        ) from exc


def _merge_steps(
    prior_steps: list[Step], incoming_steps: list[Step]
) -> tuple[list[Step], list[dict[str, Any]]]:
    """Union two step lists by ``step_index``; last-import-wins on an overlap.

    Disjoint indices union; an overlapping index whose content DIFFERS is a
    recorded conflict resolved last-import-wins (never a silent discard).
    """

    by_index: dict[int, Step] = {}
    for s in prior_steps:
        by_index[s.step_index] = s
    conflicts: list[dict[str, Any]] = []
    for s in incoming_steps:
        idx = s.step_index
        prior = by_index.get(idx)
        if prior is not None and prior.model_dump(mode="json") != s.model_dump(mode="json"):
            conflicts.append(
                {"section": "steps", "step_index": idx, "resolution": "last_import_wins"}
            )
        by_index[idx] = s
    merged = [by_index[i] for i in sorted(by_index)]
    return merged, conflicts


def _anchor_key(row: dict[str, Any]) -> str:
    return (
        str(row.get("git_anchor_id") or "")
        or str(row.get("trace_patch_id") or "")
        or _canonical_json(row)
    )


def _merge_anchor_rows(
    prior_rows: list[dict[str, Any]], incoming_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union anchor rows by stable key; incoming (last import) wins on overlap."""

    out: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in list(prior_rows) + list(incoming_rows):
        key = _anchor_key(row)
        if key not in out:
            order.append(key)
        out[key] = row
    return [out[key] for key in order]


def _read_existing_anchor_rows(slug: str, trace_id: str) -> list[dict[str, Any]]:
    """Read anchor rows previously written by import into the trail companion.

    Import writes the carried anchor ROWS (not TrailEvents) as gzip JSONL, so a
    scope-merge reads them back with a plain gzip+json parse. A present companion
    that is NOT row-shaped (e.g. a real TrailEvent companion) yields ``[]``. This
    read alone does NOT protect a native companion — a scope-merge onto a native
    trace is refused up-front by ``import_capsule`` (#208 data-loss guard); this
    helper only ever runs against a prior CAPSULE import's row-shaped companion.
    """

    path = trace_v1_trail_path(slug, trace_id)
    if not path.exists():
        return []
    try:
        body = gzip.decompress(path.read_bytes()).decode("utf-8")
    except (OSError, ValueError):
        return []
    rows: list[dict[str, Any]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            import json

            obj = json.loads(line)
        except ValueError:
            return []
        if not isinstance(obj, dict) or "event_type" in obj:
            # Not an import-written anchor row (a TrailEvent has event_type).
            return []
        rows.append(obj)
    return rows


def _write_imported(
    record: TraceRecord,
    *,
    slug: str,
    trace_id: str,
    anchors: list[dict[str, Any]],
    source_layer: str,
) -> None:
    """Write the object record + per-trace envelope so the trace projects natively."""

    # 1. Object store + current pointer — the source ``map`` / ``slice`` /
    #    ``run-intel`` read via ``read_trace_record_object``.
    write_trace_record(record, project_slug=slug, source_layer=source_layer)

    # 2. Per-trace envelope: trail companion (carried anchor rows), empty context /
    #    sources placeholders, then trace.json. Gzip is mtime=0 + canonical JSON so
    #    two imports of the same capsule are byte-identical across machines.
    trail_lines = [_canonical_json(row) for row in anchors]
    trail_body = ("\n".join(trail_lines) + "\n").encode("utf-8") if trail_lines else b""
    _atomic_write_gzip(trace_v1_trail_path(slug, trace_id), trail_body)
    _atomic_write_gzip(trace_v1_context_path(slug, trace_id), b"")
    _atomic_write_gzip(trace_v1_sources_path(slug, trace_id), b"")
    _atomic_write_json(trace_v1_json_path(slug, trace_id), record.model_dump(mode="json"))

    # 3. Manifest row so the imported trace is discoverable by the manifest-only
    #    readers (bucket list / trace resolution). Best-effort: never fatal.
    try:
        from ..bucket_store import upsert_manifest_trace_row

        upsert_manifest_trace_row(None, project_slug=slug, trace_id=trace_id, record=record)
    except Exception:  # pragma: no cover - manifest heal is non-fatal
        pass


def _report(
    *,
    status: str,
    trace_id: str,
    capsule_id: str,
    slug: str,
    anchors: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    limitations: list[str],
    written: bool,
) -> dict[str, Any]:
    return {
        "schema_version": CAPSULE_IMPORT_REPORT_SCHEMA,
        "status": status,
        "trace_id": trace_id,
        "capsule_id": capsule_id,
        "project_slug": slug,
        "written": written,
        "trail_anchor_count": len(anchors),
        "conflicts": conflicts,
        "limitations": sorted(set(limitations)),
    }


def import_capsule(
    capsule: dict[str, Any], *, source_layer: str = "capsule_import"
) -> dict[str, Any]:
    """Import ``capsule`` into the local bucket; return the frozen import report.

    Report is the frozen ``opentraces.capsule.import.v1`` envelope; ``status`` is
    ``created`` / ``noop`` / ``scope_merged``. Raises :class:`CapsuleImportError`
    (before any write) on an unparseable projection.
    """

    validate_capsule(capsule)  # frozen contract + reject-newer
    source = capsule.get("source") or {}
    trace_id = str(source.get("trace_id") or "").strip()
    if not trace_id:
        raise CapsuleImportError("capsule.source.trace_id is required to import.")
    slug = str(source.get("project_slug") or "").strip() or "imported"
    incoming_cid = str(capsule.get("capsule_id") or "")
    anchors = [dict(a) for a in (capsule.get("trail_anchors") or []) if isinstance(a, dict)]

    limitations: list[str] = []
    if not anchors:
        limitations.append("trail_anchors_unavailable")

    existing = read_trace_record_object(trace_record_path(slug, trace_id))

    if existing is None:
        # CREATE — validate (raises before write) then materialize.
        record = materialize_trace_record(capsule)
        _write_imported(
            record, slug=slug, trace_id=trace_id, anchors=anchors, source_layer=source_layer
        )
        return _report(
            status="created", trace_id=trace_id, capsule_id=incoming_cid, slug=slug,
            anchors=anchors, conflicts=[], limitations=limitations, written=True,
        )

    prior = existing.record
    prior_is_import_created = "capsule_import" in (prior.metadata or {})
    prior_meta = (prior.metadata or {}).get("capsule_import") or {}
    prior_cids = [str(c) for c in (prior_meta.get("capsule_ids") or [])]

    if incoming_cid and incoming_cid in prior_cids:
        # Idempotent no-op: this exact capsule is already imported.
        return _report(
            status="noop", trace_id=trace_id, capsule_id=incoming_cid, slug=slug,
            anchors=anchors, conflicts=list(prior_meta.get("conflicts") or []),
            limitations=limitations, written=False,
        )

    if not prior_is_import_created:
        # DATA-LOSS GUARD (#208): the trace at this id was captured NATIVELY (it
        # carries no ``capsule_import`` provenance). The scope-merge below would
        # overwrite its Trail companion with only the capsule's thin anchor rows
        # and rewrite its context/sources companions to ``b""`` — destroying real
        # TrailEvents, ContextTree events, and source blobs. REFUSE before any
        # write; a capsule may only scope-merge onto a prior CAPSULE import.
        raise CapsuleImportError(
            f"refusing to import capsule {incoming_cid!r}: trace {trace_id!r} already "
            f"exists as a NATIVE (non-imported) trace in project {slug!r}. A scope-merge "
            "would destroy its captured trail/context/sources companions. Remove the "
            "native trace first (`opentraces trace remove`) if you intend to replace it."
        )

    # SCOPE-MERGE — validate the incoming projection (raises before write).
    incoming_record = materialize_trace_record(capsule)
    merged_steps, step_conflicts = _merge_steps(list(prior.steps), list(incoming_record.steps))
    merged_cids = prior_cids + ([incoming_cid] if incoming_cid else [])
    all_conflicts = list(prior_meta.get("conflicts") or []) + step_conflicts

    merged = prior.model_copy(deep=True)
    merged.steps = merged_steps
    merged.metadata = {
        **(prior.metadata or {}),
        "capsule_import": {
            "schema_version": CAPSULE_IMPORT_PROVENANCE_SCHEMA,
            "capsule_ids": merged_cids,
            "conflicts": all_conflicts,
        },
    }
    merged_anchors = _merge_anchor_rows(_read_existing_anchor_rows(slug, trace_id), anchors)
    _write_imported(
        merged, slug=slug, trace_id=trace_id, anchors=merged_anchors, source_layer=source_layer
    )
    return _report(
        status="scope_merged", trace_id=trace_id, capsule_id=incoming_cid, slug=slug,
        anchors=merged_anchors, conflicts=all_conflicts, limitations=limitations, written=True,
    )


__all__ = [
    "CAPSULE_IMPORT_REPORT_SCHEMA",
    "CapsuleImportError",
    "import_capsule",
    "materialize_trace_record",
]
