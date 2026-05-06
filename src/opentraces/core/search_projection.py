"""Local bucket-shaped search projection over Trace Units.

This is intentionally object-store friendly: each rebuild writes an immutable
``builds/<build-id>/`` directory and then advances a tiny ``current.json``
pointer. Today it lives only under ``~/.opentraces/bucket``; a later remote
sync can mirror the same shape without changing query or dataset workflows.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from opentraces_schema import TraceFacet, TraceSignal, TraceUnit

from . import paths
from .trace_index import (
    QueryPage,
    candidate_packet_for_unit,
    default_index_path,
    get_unit,
    latest_units,
    list_units,
    rebuild_index,
)


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
    sqlite_path: Path
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
            "sqlite_path": str(self.sqlite_path),
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
    sqlite_path = build_path / "search.sqlite"
    _write_search_sqlite(sqlite_path, docs)

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
            "search_sqlite": "search.sqlite",
        },
        "capabilities": {
            "search_docs": True,
            "lexical_ready": True,
            "projection_sqlite_ready": True,
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
        sqlite_path=sqlite_path,
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
        "sqlite_path": str(manifest_path.parent / (manifest.get("files") or {}).get("search_sqlite", "search.sqlite")),
        "build_id": manifest.get("build_id"),
        "doc_count": manifest.get("doc_count", 0),
        "unit_count": manifest.get("unit_count", 0),
        "trace_count": manifest.get("trace_count", 0),
        "embedding_ready": bool(
            (manifest.get("capabilities") or {}).get("embedding_ready")
        ),
        "manifest": manifest,
    }


def query_search_projection_page(
    *,
    lex: str | None = None,
    skill: str | None = None,
    tool: str | None = None,
    files: str | None = None,
    file_kind: str | None = None,
    file_op: str | None = None,
    signal: str | None = None,
    facet_filters: tuple[str, ...] = (),
    metadata_filters: tuple[str, ...] = (),
    provider: str | None = None,
    cmd_family: str | None = None,
    bash_action: str | None = None,
    test_framework: str | None = None,
    service: str | None = None,
    service_channel: str | None = None,
    dependency: str | None = None,
    git_tier: str | None = None,
    survival: str | None = None,
    since: str | None = None,
    success: bool | None = None,
    success_unknown: bool = False,
    committed: bool | None = None,
    committed_unknown: bool = False,
    candidate_kind: str | None = None,
    latest_generation: bool = True,
    project: str | None = None,
    limit: int = 20,
    page_token: str | None = None,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
    root_path: Path | None = None,
    build_if_missing: bool = True,
) -> QueryPage:
    """Query the immutable local search projection and return CandidatePackets."""

    db_path = index_path or default_index_path()
    status = search_projection_status(root_path)
    if status.get("state") != "ok":
        if not build_if_missing:
            return QueryPage(candidates=[], next_page_token=None, total=0)
        build_search_projection(index_path=db_path, root_path=root_path, rebuild_index_first=True)
        status = search_projection_status(root_path)
    sqlite_path = Path(status["sqlite_path"])
    if not sqlite_path.exists():
        if not build_if_missing:
            return QueryPage(candidates=[], next_page_token=None, total=0)
        build_search_projection(index_path=db_path, root_path=root_path, rebuild_index_first=True)
        status = search_projection_status(root_path)
        sqlite_path = Path(status["sqlite_path"])

    parsed_facets = _parse_key_value_filters(facet_filters)
    parsed_metadata = _parse_key_value_filters(metadata_filters)
    named_filters = _named_facet_filters(
        provider=provider,
        cmd_family=cmd_family,
        bash_action=bash_action,
        test_framework=test_framework,
        service=service,
        service_channel=service_channel,
        dependency=dependency,
        git_tier=git_tier,
        survival=survival,
    )
    since_dt = _parse_since(since) if since else None
    terms = _terms(lex or "")
    docs = _select_docs(sqlite_path, terms)
    scored: list[tuple[float, dict[str, float], dict[str, list[str]], TraceUnit]] = []
    for doc in docs:
        if since_dt and _doc_timestamp(doc) < since_dt:
            continue
        if not _doc_matches(
            doc,
            skill=skill,
            tool=tool,
            files=files,
            file_kind=file_kind,
            file_op=file_op,
            signal=signal,
            facet_filters=parsed_facets,
            metadata_filters=parsed_metadata,
            named_filters=named_filters,
            project=project,
            success=success,
            success_unknown=success_unknown,
            committed=committed,
            committed_unknown=committed_unknown,
            candidate_kind=candidate_kind,
        ):
            continue
        lexical_score, matched_fields = _lexical_score_doc(doc, terms)
        metadata_score = _metadata_score_doc(
            doc,
            skill=skill,
            tool=tool,
            files=files,
            file_kind=file_kind,
            file_op=file_op,
            signal=signal,
            facet_filters=parsed_facets,
            metadata_filters=parsed_metadata,
            named_filters=named_filters,
            project=project,
            success=success,
            success_unknown=success_unknown,
            committed=committed,
            committed_unknown=committed_unknown,
            candidate_kind=candidate_kind,
            since=since,
        )
        if terms and lexical_score <= 0:
            continue
        if not terms and metadata_score <= 0:
            continue
        unit = get_unit(str(doc["unit_id"]), index_path=db_path)
        if unit is None:
            continue
        scored.append(
            (
                lexical_score + metadata_score,
                {
                    "projection_lexical": lexical_score,
                    "projection_metadata": metadata_score,
                },
                matched_fields,
                unit,
            )
        )

    if latest_generation:
        latest_ids = {unit.unit_id for unit in latest_units([item[3] for item in scored])}
        scored = [item for item in scored if item[3].unit_id in latest_ids]
    scored.sort(key=lambda item: (-item[0], item[3].trace_id, item[3].unit_id))
    offset = _page_offset(page_token)
    page_size = max(1, limit)
    selected = scored[offset : offset + page_size]
    next_offset = offset + page_size
    next_page_token = f"offset:{next_offset}" if next_offset < len(scored) else None
    return QueryPage(
        candidates=[
            candidate_packet_for_unit(
                unit,
                score,
                score_parts,
                matched_fields,
                include_slice=include_slice,
                max_slice_nodes=max_slice_nodes,
                index_path=db_path,
            )
            for score, score_parts, matched_fields, unit in selected
        ],
        next_page_token=next_page_token,
        total=len(scored),
    )


def _doc_for_unit(unit: TraceUnit) -> dict[str, Any]:
    fields = {
        "title": unit.title_text,
        "intent": unit.intent_text,
        "action": unit.action_text,
        "evidence": unit.evidence_text,
        "artifact": unit.artifact_text,
    }
    facets = [_facet_payload(facet) for facet in unit.facets]
    signals = [_signal_payload(signal) for signal in unit.signals]
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
        "facets": facets,
        "signals": signals,
        "metadata": unit.metadata,
        "trail_refs": unit.trail_refs,
        "evidence_refs": _evidence_refs(unit),
    }
    material["search_text"] = _projection_search_text(material)
    return {**material, "content_hash": _sha256_json(material)}


def _write_search_sqlite(sqlite_path: Path, docs: list[dict[str, Any]]) -> None:
    if sqlite_path.exists():
        sqlite_path.unlink()
    with sqlite3.connect(sqlite_path) as conn:
        conn.executescript(
            """
            create table docs (
                doc_id text primary key,
                unit_id text not null,
                trace_id text not null,
                project_slug text not null,
                doc_type text not null,
                title text not null,
                payload_json text not null
            );
            create virtual table docs_fts using fts5(
                doc_id unindexed,
                title,
                text,
                search_text
            );
            """
        )
        for doc in docs:
            payload = _canonical_json(doc)
            conn.execute(
                """
                insert into docs(
                    doc_id, unit_id, trace_id, project_slug, doc_type, title, payload_json
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc["doc_id"],
                    doc["unit_id"],
                    doc["trace_id"],
                    doc["project_slug"],
                    doc["doc_type"],
                    doc["title"],
                    payload,
                ),
            )
            conn.execute(
                """
                insert into docs_fts(doc_id, title, text, search_text)
                values (?, ?, ?, ?)
                """,
                (
                    doc["doc_id"],
                    doc["title"],
                    doc["text"],
                    doc["search_text"],
                ),
            )
        conn.commit()


def _select_docs(sqlite_path: Path, terms: list[str]) -> list[dict[str, Any]]:
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        if terms:
            rows = conn.execute(
                """
                select docs.payload_json from docs
                join docs_fts on docs_fts.doc_id = docs.doc_id
                where docs_fts match ?
                order by docs.trace_id, docs.unit_id
                """,
                (_fts_query(terms),),
            ).fetchall()
        else:
            rows = conn.execute(
                "select payload_json from docs order by trace_id, unit_id"
            ).fetchall()
    return [json.loads(row["payload_json"]) for row in rows]


def _doc_matches(
    doc: dict[str, Any],
    *,
    skill: str | None,
    tool: str | None,
    files: str | None,
    file_kind: str | None,
    file_op: str | None,
    signal: str | None,
    facet_filters: tuple[tuple[str, str], ...],
    metadata_filters: tuple[tuple[str, str], ...],
    named_filters: tuple[tuple[str, str], ...],
    project: str | None,
    success: bool | None,
    success_unknown: bool,
    committed: bool | None,
    committed_unknown: bool,
    candidate_kind: str | None,
) -> bool:
    if project and doc.get("project_slug") != project:
        return False
    if (
        candidate_kind
        and doc.get("doc_type") != candidate_kind
        and _doc_candidate_kind(doc) != candidate_kind
    ):
        return False
    if skill and skill not in doc.get("skills", []):
        return False
    if tool and tool not in _facet_values(doc, "tool.name"):
        return False
    if files and not any(fnmatch(path, files) for path in doc.get("files", [])):
        return False
    if file_kind and file_kind.lstrip(".") not in _facet_values(doc, "file.kind"):
        return False
    if file_op and file_op not in _facet_values(doc, "file.operation"):
        return False
    if signal and not _has_signal(doc, signal):
        return False
    if facet_filters and not _matches_facet_filters(doc, facet_filters):
        return False
    if metadata_filters and not _matches_metadata_filters(doc, metadata_filters):
        return False
    if named_filters and not _matches_facet_filters(doc, named_filters):
        return False
    if success_unknown and success is not None:
        raise ValueError("Use --success/--no-success or --unknown-success, not both.")
    if committed_unknown and committed is not None:
        raise ValueError("Use --committed/--uncommitted or --unknown-committed, not both.")
    success_value = _bool_facet(doc, "outcome.success")
    committed_value = _bool_facet(doc, "outcome.committed")
    if success is not None and success_value is not success:
        return False
    if success_unknown and success_value is not None:
        return False
    if committed is not None and committed_value is not committed:
        return False
    if committed_unknown and committed_value is not None:
        return False
    return True


def _lexical_score_doc(
    doc: dict[str, Any],
    terms: list[str],
) -> tuple[float, dict[str, list[str]]]:
    if not terms:
        return 0.0, {}
    weights = {
        "title": 5.0,
        "intent": 4.0,
        "action": 3.0,
        "evidence": 2.0,
        "artifact": 1.0,
        "search_text": 0.5,
    }
    score = 0.0
    matched: dict[str, list[str]] = {}
    fields = dict(doc.get("fields") or {})
    fields["search_text"] = doc.get("search_text", "")
    for field, weight in weights.items():
        field_terms = set(_terms(str(fields.get(field, "")).lower()))
        hits = [term for term in terms if term in field_terms]
        if hits:
            matched[f"{field}_text" if field != "search_text" else field] = hits
            score += weight * len(hits)
    return score, matched


def _metadata_score_doc(
    doc: dict[str, Any],
    *,
    skill: str | None,
    tool: str | None,
    files: str | None,
    file_kind: str | None,
    file_op: str | None,
    signal: str | None,
    facet_filters: tuple[tuple[str, str], ...],
    metadata_filters: tuple[tuple[str, str], ...],
    named_filters: tuple[tuple[str, str], ...],
    project: str | None,
    success: bool | None,
    success_unknown: bool,
    committed: bool | None,
    committed_unknown: bool,
    candidate_kind: str | None,
    since: str | None,
) -> float:
    score = 0.0
    if skill:
        score += 3.0
    if tool:
        score += 2.0
    if files:
        score += 2.0
    if file_kind or file_op:
        score += 1.0
    if signal:
        score += 5.0
    if project:
        score += 1.0
    if success is not None or success_unknown:
        score += 1.0
    if committed is not None or committed_unknown:
        score += 1.0
    if candidate_kind:
        score += 2.0
    if since:
        score += 1.0
    score += float(len(facet_filters) + len(metadata_filters) + len(named_filters))
    return score


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


def _projection_search_text(doc: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.extend(str(value) for value in (doc.get("fields") or {}).values() if value)
    parts.extend(str(value) for value in doc.get("files", []))
    parts.extend(str(value) for value in doc.get("skills", []))
    for facet in doc.get("facets", []):
        parts.append(str(facet.get("name", "")))
        parts.append(str(facet.get("value", "")))
    for signal in doc.get("signals", []):
        parts.append(str(signal.get("name", "")))
        parts.append(str(signal.get("value", "")))
    parts.extend(_simple_metadata_values(doc.get("metadata") or {}))
    parts.extend(str(value) for value in doc.get("trail_refs", []))
    return " ".join(part for part in parts if part)


def _simple_metadata_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, bool):
        return [str(value).lower()]
    if isinstance(value, (str, int, float)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_simple_metadata_values(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            out.append(str(key))
            out.extend(_simple_metadata_values(item))
        return out
    return []


def _parse_key_value_filters(filters: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    parsed: list[tuple[str, str]] = []
    for item in filters:
        if "=" not in item:
            raise ValueError("filters must use name=value syntax")
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            raise ValueError("filters must use name=value syntax")
        parsed.append((name, value))
    return tuple(parsed)


def _named_facet_filters(
    *,
    provider: str | None,
    cmd_family: str | None,
    bash_action: str | None,
    test_framework: str | None,
    service: str | None,
    service_channel: str | None,
    dependency: str | None,
    git_tier: str | None,
    survival: str | None,
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (name, value)
        for name, value in (
            ("provider.kind", provider),
            ("bash.command_family", cmd_family),
            ("bash.action", bash_action),
            ("test.framework", test_framework),
            ("service.name", service),
            ("service.channel", service_channel),
            ("dependency.name", dependency),
            ("git_link_tier", git_tier),
            ("trail.survival_state", survival),
        )
        if value is not None
    )


def _matches_facet_filters(
    doc: dict[str, Any],
    filters: tuple[tuple[str, str], ...],
) -> bool:
    for name, expected in filters:
        values = _facet_values(doc, name)
        if not any(str(value).lower() == expected.lower() for value in values):
            return False
    return True


def _matches_metadata_filters(
    doc: dict[str, Any],
    filters: tuple[tuple[str, str], ...],
) -> bool:
    metadata = doc.get("metadata") or {}
    for name, expected in filters:
        if not _metadata_value_matches(metadata.get(name), expected):
            return False
    return True


def _metadata_value_matches(actual: Any, expected: str) -> bool:
    if isinstance(actual, bool):
        return str(actual).lower() == expected.lower()
    if isinstance(actual, (str, int, float)):
        return str(actual) == expected
    if isinstance(actual, list):
        return any(_metadata_value_matches(item, expected) for item in actual)
    return False


def _facet_values(doc: dict[str, Any], name: str) -> set[str]:
    return {
        str(facet.get("value"))
        for facet in doc.get("facets", [])
        if facet.get("name") == name
    }


def _bool_facet(doc: dict[str, Any], name: str) -> bool | None:
    for facet in doc.get("facets", []):
        if facet.get("name") != name:
            continue
        value = facet.get("value")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() in {"1", "true", "yes"}
    return None


def _has_signal(doc: dict[str, Any], name: str) -> bool:
    for signal in doc.get("signals", []):
        if signal.get("name") == name and bool(signal.get("value")):
            return True
    return False


def _doc_candidate_kind(doc: dict[str, Any]) -> str:
    if _has_signal(doc, "tested_successful_fix_candidate"):
        return "bug_fix"
    return str(doc.get("doc_type") or "trace")


def _terms(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_@./-]+", text)]


def _fts_query(terms: list[str]) -> str:
    quoted: list[str] = []
    for term in terms:
        safe = term.replace('"', "")
        if safe:
            quoted.append(f'"{safe}"')
    return " ".join(quoted)


def _page_offset(page_token: str | None) -> int:
    if not page_token:
        return 0
    if not page_token.startswith("offset:"):
        raise ValueError("page token must have the form offset:<n>")
    try:
        return max(0, int(page_token.split(":", 1)[1]))
    except ValueError as exc:
        raise ValueError("page token must have the form offset:<n>") from exc


def _parse_since(value: str) -> datetime:
    stripped = value.strip()
    now = datetime.now(timezone.utc)
    duration = re.fullmatch(r"(\d+)([dhm])", stripped.lower())
    if duration:
        amount = int(duration.group(1))
        unit = duration.group(2)
        if unit == "d":
            return now - timedelta(days=amount)
        if unit == "h":
            return now - timedelta(hours=amount)
        return now - timedelta(minutes=amount)
    parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _doc_timestamp(doc: dict[str, Any]) -> datetime:
    metadata = doc.get("metadata") or {}
    raw = metadata.get("timestamp_end") or metadata.get("timestamp_start")
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def _sha256_json(data: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(data))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
