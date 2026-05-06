"""Local bucket-shaped search projection over Trace Units.

This is intentionally object-store friendly: each rebuild writes an immutable
``builds/<build-id>/`` directory and then advances a tiny ``current.json``
pointer. Today it lives only under ``~/.opentraces/bucket``; a later remote
sync can mirror the same shape without changing query or dataset workflows.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opentraces_schema import TraceFacet, TraceSignal, TraceUnit

from . import paths
from .trace_index import default_index_path, list_units, rebuild_index


SEARCH_PROJECTION_VERSION = "v1"
SEARCH_DOC_SCHEMA_VERSION = "opentraces.search_doc.v1"
SEARCH_MANIFEST_SCHEMA_VERSION = "opentraces.search_projection.v1"


@dataclass(frozen=True)
class SearchProjectionSummary:
    root_path: Path
    build_path: Path
    current_path: Path
    manifest_path: Path
    docs_path: Path
    index_path: Path
    build_id: str
    doc_count: int
    unit_count: int
    trace_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "projection": "search",
            "version": SEARCH_PROJECTION_VERSION,
            "root_path": str(self.root_path),
            "build_path": str(self.build_path),
            "current_path": str(self.current_path),
            "manifest_path": str(self.manifest_path),
            "docs_path": str(self.docs_path),
            "index_path": str(self.index_path),
            "build_id": self.build_id,
            "doc_count": self.doc_count,
            "unit_count": self.unit_count,
            "trace_count": self.trace_count,
        }


def build_search_projection(
    *,
    index_path: Path | None = None,
    root_path: Path | None = None,
    rebuild_index_first: bool = False,
) -> SearchProjectionSummary:
    """Materialize the local search-document projection from Trace Units."""

    db_path = index_path or default_index_path()
    if rebuild_index_first or not db_path.exists():
        rebuild_index(db_path)

    root = root_path or paths.search_projection_root(SEARCH_PROJECTION_VERSION)
    units = list_units(index_path=db_path)
    docs = [_doc_for_unit(unit) for unit in units]
    doc_lines = [_canonical_json(doc) for doc in docs]
    corpus_hash = _sha256_text("\n".join(doc_lines))
    built_at = _utc_now()
    build_id = f"{built_at.strftime('%Y%m%dT%H%M%S%fZ')}-{corpus_hash[:12]}"

    build_path = root / "builds" / build_id
    build_path.mkdir(parents=True, exist_ok=False)
    docs_path = build_path / "docs.jsonl"
    docs_path.write_text(("\n".join(doc_lines) + "\n") if doc_lines else "")

    manifest = {
        "schema_version": SEARCH_MANIFEST_SCHEMA_VERSION,
        "projection": "search",
        "version": SEARCH_PROJECTION_VERSION,
        "build_id": build_id,
        "built_at": built_at.isoformat().replace("+00:00", "Z"),
        "storage": {
            "mode": "local",
            "root": str(paths.bucket_dir()),
        },
        "input": {
            "index_path": str(db_path),
            "trace_unit_count": len(units),
        },
        "corpus_hash": corpus_hash,
        "doc_count": len(docs),
        "unit_count": len(units),
        "trace_count": len({unit.trace_id for unit in units}),
        "files": {
            "docs_jsonl": "docs.jsonl",
        },
        "capabilities": {
            "search_docs": True,
            "lexical_ready": True,
            "embedding_ready": False,
            "evidence_refs": True,
        },
    }
    manifest_path = build_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    pointer = {
        "projection": "search",
        "version": SEARCH_PROJECTION_VERSION,
        "build_id": build_id,
        "manifest_path": f"builds/{build_id}/manifest.json",
        "docs_path": f"builds/{build_id}/docs.jsonl",
        "updated_at": manifest["built_at"],
    }
    current_path = root / "current.json"
    current_path.write_text(json.dumps(pointer, indent=2, sort_keys=True) + "\n")

    return SearchProjectionSummary(
        root_path=root,
        build_path=build_path,
        current_path=current_path,
        manifest_path=manifest_path,
        docs_path=docs_path,
        index_path=db_path,
        build_id=build_id,
        doc_count=len(docs),
        unit_count=len(units),
        trace_count=len({unit.trace_id for unit in units}),
    )


def search_projection_status(root_path: Path | None = None) -> dict[str, Any]:
    """Return the current local search projection pointer and manifest."""

    root = root_path or paths.search_projection_root(SEARCH_PROJECTION_VERSION)
    current_path = root / "current.json"
    base: dict[str, Any] = {
        "projection": "search",
        "version": SEARCH_PROJECTION_VERSION,
        "root_path": str(root),
        "current_path": str(current_path),
    }
    if not current_path.exists():
        return {**base, "state": "missing"}
    try:
        pointer = json.loads(current_path.read_text())
    except Exception as exc:
        return {**base, "state": "error", "error": str(exc)}

    manifest_rel = pointer.get("manifest_path")
    manifest_path = root / manifest_rel if isinstance(manifest_rel, str) else None
    if manifest_path is None or not manifest_path.exists():
        return {
            **base,
            "state": "dangling",
            "current": pointer,
            "manifest_path": str(manifest_path) if manifest_path else None,
        }

    try:
        manifest = json.loads(manifest_path.read_text())
    except Exception as exc:
        return {
            **base,
            "state": "error",
            "current": pointer,
            "manifest_path": str(manifest_path),
            "error": str(exc),
        }

    return {
        **base,
        "state": "ok",
        "current": pointer,
        "manifest_path": str(manifest_path),
        "build_id": manifest.get("build_id"),
        "doc_count": manifest.get("doc_count", 0),
        "unit_count": manifest.get("unit_count", 0),
        "trace_count": manifest.get("trace_count", 0),
        "embedding_ready": bool(
            (manifest.get("capabilities") or {}).get("embedding_ready")
        ),
        "manifest": manifest,
    }


def _doc_for_unit(unit: TraceUnit) -> dict[str, Any]:
    fields = {
        "title": unit.title_text,
        "intent": unit.intent_text,
        "action": unit.action_text,
        "evidence": unit.evidence_text,
        "artifact": unit.artifact_text,
    }
    material = {
        "schema_version": SEARCH_DOC_SCHEMA_VERSION,
        "doc_id": f"sd:{unit.unit_id}",
        "doc_type": unit.unit_type,
        "unit_id": unit.unit_id,
        "trace_id": unit.trace_id,
        "project_slug": unit.project_slug,
        "title": unit.title_text,
        "text": "\n\n".join(value for value in fields.values() if value),
        "fields": fields,
        "files": sorted(set(unit.files)),
        "skills": sorted(set(unit.skills)),
        "facets": [_facet_payload(facet) for facet in unit.facets],
        "signals": [_signal_payload(signal) for signal in unit.signals],
        "metadata": unit.metadata,
        "trail_refs": unit.trail_refs,
        "evidence_refs": _evidence_refs(unit),
    }
    return {**material, "content_hash": _sha256_json(material)}


def _facet_payload(facet: TraceFacet) -> dict[str, Any]:
    return facet.model_dump(mode="json")


def _signal_payload(signal: TraceSignal) -> dict[str, Any]:
    return signal.model_dump(mode="json")


def _evidence_refs(unit: TraceUnit) -> list[str]:
    refs: list[str] = [unit.unit_id, f"ot://trace/{unit.trace_id}/map"]
    refs.extend(unit.trail_refs)
    for facet in unit.facets:
        if facet.evidence_ref:
            refs.append(facet.evidence_ref)
    for signal in unit.signals:
        refs.extend(signal.evidence_refs)
    refs.extend(_metadata_refs(unit.metadata))
    return sorted({ref for ref in refs if isinstance(ref, str) and ref})


def _metadata_refs(metadata: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in (
        "map_ref",
        "map_node_ref",
        "map_node_refs",
        "patch_ref",
        "patch_refs",
        "trace_patch_ref",
        "trace_patch_refs",
        "resource_ref",
        "resource_refs",
        "anchor_ref",
        "anchor_refs",
        "git_anchor_ref",
        "git_anchor_refs",
        "containing_segment_id",
        "slice_id",
    ):
        value = metadata.get(key)
        if isinstance(value, str):
            refs.append(value)
        elif isinstance(value, list):
            refs.extend(item for item in value if isinstance(item, str))
    return refs


def _sha256_json(data: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(data))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
