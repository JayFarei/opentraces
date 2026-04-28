"""Local Trace Index cache for Plan 56 query workflows."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from opentraces_schema import (
    CandidatePacket,
    TraceFacet,
    TraceMap,
    TraceMapEdge,
    TraceMapNode,
    TraceRecord,
    TraceSignal,
    TraceUnit,
)

from . import paths
from .trace_map import build_trace_map
from .trace_map import slice_trace_map_for_candidate


INDEX_VERSION = "plan056-m1-v1"
_M1_UNIT_TYPES = {
    "trace",
    "trace_map_node",
    "trace_slice",
    "trace_intent_candidate",
    "patch",
    "skill_invocation",
    "tool_sequence",
    "test_or_error_signal",
    "git_anchor",
}
_FIELD_WEIGHTS = {
    "title_text": 5.0,
    "intent_text": 4.0,
    "action_text": 3.0,
    "evidence_text": 2.0,
    "artifact_text": 1.0,
}
_TEST_COMMAND_RE = re.compile(
    r"\b(pytest|npm\s+test|pnpm\s+test|yarn\s+test|cargo\s+test|go\s+test|"
    r"vitest|jest|rspec|mvn\s+test|gradle\s+test)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")


@dataclass(frozen=True)
class RebuildSummary:
    index_path: Path
    trace_count: int
    unit_count: int
    map_node_count: int


@dataclass(frozen=True)
class QueryPage:
    candidates: list[CandidatePacket]
    next_page_token: str | None
    total: int


def default_index_path() -> Path:
    return paths.OPENTRACES_DIR / "index" / "index.db"


def rebuild_index(index_path: Path | None = None) -> RebuildSummary:
    """Rebuild the local cache from retained project trace stores."""

    db_path = index_path or default_index_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    with sqlite3.connect(db_path) as conn:
        _create_schema(conn)
        project_sources = _project_sources_by_slug()
        for project_home in _iter_project_homes():
            project_slug = project_home.name
            for trace_path in _iter_trace_paths(project_home):
                records = _iter_trace_file_records(trace_path)
                for record in records:
                    _delete_trace_ids(conn, [record.trace_id])
                    trace_map = build_trace_map(record)
                    units = _build_units(record, trace_map, project_slug)
                    _insert_trace(conn, record, project_slug, trace_path, trace_map)
                    for unit in units:
                        _insert_unit(conn, unit)
                    for node in trace_map.nodes:
                        _insert_map_node(conn, node)
                    for edge in trace_map.edges:
                        _insert_map_edge(conn, edge)
                _record_source(conn, trace_path, len(records))
            source_repo = project_sources.get(project_slug)
            if source_repo and source_repo.exists():
                trail_units = _build_trail_units(source_repo, project_slug)
                for trail_map in _trail_maps_from_units(trail_units):
                    _insert_trail_map(conn, trail_map)
                for unit in trail_units:
                    _insert_unit(conn, unit)
        conn.execute(
            "insert into meta(key, value) values (?, ?)",
            ("index_version", INDEX_VERSION),
        )
        summary = _index_totals(conn, db_path)
        conn.commit()
    return summary


def refresh_index(index_path: Path | None = None) -> RebuildSummary:
    """Refresh changed trace-store sources without replacing the cache file."""

    db_path = index_path or default_index_path()
    if not db_path.exists():
        return rebuild_index(db_path)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not _schema_supports_refresh(conn):
            return rebuild_index(db_path)

        project_sources = _project_sources_by_slug()
        seen_sources: set[str] = set()
        for project_home in _iter_project_homes():
            project_slug = project_home.name
            for trace_path in _iter_trace_paths(project_home):
                source_key = str(trace_path)
                seen_sources.add(source_key)
                stat = trace_path.stat()
                existing = conn.execute(
                    "select mtime_ns, size from sources where path = ?",
                    (source_key,),
                ).fetchone()
                if (
                    existing
                    and int(existing["mtime_ns"]) == stat.st_mtime_ns
                    and int(existing["size"]) == stat.st_size
                ):
                    continue
                old_trace_ids = [
                    str(row["trace_id"])
                    for row in conn.execute(
                        "select trace_id from traces where trace_path = ?",
                        (source_key,),
                    )
                ]
                records = _iter_trace_file_records(trace_path)
                _delete_trace_ids(
                    conn,
                    [*old_trace_ids, *[record.trace_id for record in records]],
                )
                for record in records:
                    trace_map = build_trace_map(record)
                    _insert_trace(conn, record, project_slug, trace_path, trace_map)
                    for unit in _build_units(record, trace_map, project_slug):
                        _insert_unit(conn, unit)
                _record_source(conn, trace_path, len(records))

        stale_sources = [
            str(row["path"])
            for row in conn.execute("select path from sources")
            if str(row["path"]) not in seen_sources
        ]
        for source_key in stale_sources:
            old_trace_ids = [
                str(row["trace_id"])
                for row in conn.execute(
                    "select trace_id from traces where trace_path = ?",
                    (source_key,),
                )
            ]
            _delete_trace_ids(conn, old_trace_ids)
            conn.execute("delete from sources where path = ?", (source_key,))

        _delete_units_by_types(conn, {"patch", "git_anchor"})
        _delete_trail_maps(conn)
        for project_home in _iter_project_homes():
            project_slug = project_home.name
            source_repo = project_sources.get(project_slug)
            if source_repo and source_repo.exists():
                trail_units = _build_trail_units(source_repo, project_slug)
                for trail_map in _trail_maps_from_units(trail_units):
                    _insert_trail_map(conn, trail_map)
                for unit in trail_units:
                    _insert_unit(conn, unit)

        conn.execute(
            "insert or replace into meta(key, value) values (?, ?)",
            ("index_version", INDEX_VERSION),
        )
        conn.commit()
        return _index_totals(conn, db_path)


def query_index(
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
    committed: bool | None = None,
    candidate_kind: str | None = None,
    latest_generation: bool = True,
    project: str | None = None,
    limit: int = 20,
    page_token: str | None = None,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
) -> list[CandidatePacket]:
    """Return bounded candidate packets from the local index."""
    return query_index_page(
        lex=lex,
        skill=skill,
        tool=tool,
        files=files,
        file_kind=file_kind,
        file_op=file_op,
        signal=signal,
        facet_filters=facet_filters,
        metadata_filters=metadata_filters,
        provider=provider,
        cmd_family=cmd_family,
        bash_action=bash_action,
        test_framework=test_framework,
        service=service,
        service_channel=service_channel,
        dependency=dependency,
        git_tier=git_tier,
        survival=survival,
        since=since,
        success=success,
        committed=committed,
        candidate_kind=candidate_kind,
        latest_generation=latest_generation,
        project=project,
        limit=limit,
        page_token=page_token,
        include_slice=include_slice,
        max_slice_nodes=max_slice_nodes,
        index_path=index_path,
    ).candidates


def query_index_page(
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
    committed: bool | None = None,
    candidate_kind: str | None = None,
    latest_generation: bool = True,
    project: str | None = None,
    limit: int = 20,
    page_token: str | None = None,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
) -> QueryPage:
    """Return one stable page of bounded candidate packets."""

    db_path = index_path or default_index_path()
    if not db_path.exists():
        rebuild_index(db_path)
    else:
        refresh_index(db_path)
    parsed_facets = _parse_key_value_filters(facet_filters)
    parsed_metadata = _parse_key_value_filters(metadata_filters)
    since_dt = _parse_since(since) if since else None
    terms = _terms(lex or "")

    unit_type_filter = _requested_unit_type(parsed_facets, candidate_kind)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = _select_unit_rows(conn, terms, unit_type_filter)

    candidates = [_unit_from_row(row) for row in rows]
    if latest_generation:
        candidates = _latest_units(candidates)
    if project:
        candidates = [unit for unit in candidates if unit.project_slug == project]
    if skill:
        candidates = [unit for unit in candidates if skill in unit.skills]
    if tool:
        candidates = [unit for unit in candidates if tool in _facet_values(unit, "tool.name")]
    if files:
        candidates = [unit for unit in candidates if any(fnmatch(path, files) for path in unit.files)]
    if file_kind:
        expected_kind = file_kind.lstrip(".")
        candidates = [unit for unit in candidates if expected_kind in _facet_values(unit, "file.kind")]
    if file_op:
        candidates = [unit for unit in candidates if file_op in _facet_values(unit, "file.operation")]
    if signal:
        candidates = [
            unit
            for unit in candidates
            if any(sig.name == signal and bool(sig.value) for sig in unit.signals)
        ]
    if parsed_facets:
        candidates = [unit for unit in candidates if _matches_facet_filters(unit, parsed_facets)]
    if parsed_metadata:
        candidates = [
            unit for unit in candidates if _matches_metadata_filters(unit, parsed_metadata)
        ]
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
    if named_filters:
        candidates = [unit for unit in candidates if _matches_facet_filters(unit, named_filters)]
    if since_dt:
        candidates = [unit for unit in candidates if _unit_timestamp(unit) >= since_dt]
    if success is not None:
        candidates = [
            unit
            for unit in candidates
            if _bool_facet(unit, "outcome.success") is success
        ]
    if committed is not None:
        candidates = [
            unit
            for unit in candidates
            if _bool_facet(unit, "outcome.committed") is committed
        ]
    if candidate_kind:
        candidates = [
            unit
            for unit in candidates
            if _candidate_kind(unit) == candidate_kind or unit.unit_type == candidate_kind
        ]

    scored: list[tuple[float, dict[str, float], dict[str, list[str]], TraceUnit]] = []
    has_filter = (
        any(
            value is not None
            for value in (
                lex,
                skill,
                tool,
                files,
                file_kind,
                file_op,
                signal,
                success,
                committed,
                candidate_kind,
                project,
                provider,
                cmd_family,
                bash_action,
                test_framework,
                service,
                service_channel,
                dependency,
                git_tier,
                survival,
                since,
            )
        )
        or bool(parsed_facets)
        or bool(parsed_metadata)
    )
    for unit in candidates:
        lexical_score, matched_fields = _lexical_score(unit, terms)
        if terms and lexical_score <= 0:
            continue
        signal_score = 5.0 if signal else 0.0
        skill_score = 3.0 if skill else 0.0
        metadata_score = _metadata_score(
            unit,
            tool=tool,
            files=files,
            file_kind=file_kind,
            file_op=file_op,
            success=success,
            committed=committed,
            candidate_kind=candidate_kind,
            project=project,
            facet_filters=parsed_facets,
            metadata_filters=parsed_metadata,
            named_filters=named_filters,
            since=since,
        )
        total = lexical_score + signal_score + skill_score + metadata_score
        if total <= 0 and has_filter:
            total = 1.0
        if not has_filter:
            continue
        scored.append(
            (
                total,
                {
                    "lexical": lexical_score,
                    "signal": signal_score,
                    "facet": skill_score,
                    "metadata": metadata_score,
                },
                matched_fields,
                unit,
            )
        )

    scored.sort(key=lambda item: (-item[0], item[3].trace_id))
    offset = _page_offset(page_token)
    page_size = max(1, limit)
    selected = scored[offset : offset + page_size]
    next_offset = offset + page_size
    next_page_token = f"offset:{next_offset}" if next_offset < len(scored) else None
    candidates_page = [
        _candidate_packet(
            unit,
            score,
            score_parts,
            matched_fields,
            include_slice=include_slice,
            max_slice_nodes=max_slice_nodes,
            index_path=db_path,
        )
        for score, score_parts, matched_fields, unit in selected
    ]
    return QueryPage(
        candidates=candidates_page,
        next_page_token=next_page_token,
        total=len(scored),
    )


def get_trace_map(trace_id: str, *, index_path: Path | None = None) -> TraceMap | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        rebuild_index(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        nodes = [
            TraceMapNode.model_validate_json(row["payload"])
            for row in conn.execute(
                "select payload from trace_map_nodes where trace_id = ? order by ordinal",
                (trace_id,),
            )
        ]
        edges = [
            row["payload"]
            for row in conn.execute(
                "select payload from trace_map_edges where trace_id = ? order by ordinal",
                (trace_id,),
            )
        ]
    if not nodes:
        return None
    from opentraces_schema import TraceMapEdge

    return TraceMap(
        trace_id=trace_id,
        root_node_ids=[nodes[0].node_id],
        nodes=nodes,
        edges=[TraceMapEdge.model_validate_json(payload) for payload in edges],
    )


def get_unit(unit_id: str, *, index_path: Path | None = None) -> TraceUnit | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        rebuild_index(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "select * from units where unit_id = ?",
            (unit_id,),
        ).fetchone()
    return _unit_from_row(row) if row else None


def get_map_node(node_id: str, *, index_path: Path | None = None) -> TraceMapNode | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        rebuild_index(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select payload from trace_map_nodes where node_id = ?",
            (node_id,),
        ).fetchone()
    return TraceMapNode.model_validate_json(row[0]) if row else None


def get_trace_path(trace_id: str, *, index_path: Path | None = None) -> Path | None:
    db_path = index_path or default_index_path()
    if not db_path.exists():
        rebuild_index(db_path)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select trace_path from traces where trace_id = ?",
            (trace_id,),
        ).fetchone()
    return Path(row[0]) if row else None


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table meta (
            key text primary key,
            value text not null
        );
        create table sources (
            path text primary key,
            mtime_ns integer not null,
            size integer not null,
            record_count integer not null
        );
        create table traces (
            trace_id text primary key,
            project_slug text not null,
            session_id text not null,
            generation_index integer not null,
            trace_path text not null,
            title text not null
        );
        create table units (
            unit_id text primary key,
            unit_type text not null,
            trace_id text not null,
            project_slug text not null,
            title_text text not null,
            intent_text text not null,
            action_text text not null,
            evidence_text text not null,
            artifact_text text not null,
            files_json text not null,
            skills_json text not null,
            facets_json text not null,
            signals_json text not null,
            metadata_json text not null,
            trail_refs_json text not null
        );
        create virtual table units_fts using fts5(
            title_text,
            intent_text,
            action_text,
            evidence_text,
            artifact_text,
            content='units',
            content_rowid='rowid'
        );
        create table facets (
            unit_id text not null,
            name text not null,
            value text not null
        );
        create table signals (
            unit_id text not null,
            name text not null,
            value text not null
        );
        create table trace_map_nodes (
            node_id text primary key,
            trace_id text not null,
            ordinal integer not null,
            payload text not null
        );
        create table trace_map_edges (
            edge_id text primary key,
            trace_id text not null,
            ordinal integer not null,
            payload text not null
        );
        """
    )


def _iter_project_homes() -> list[Path]:
    if not paths.PROJECTS_DIR.exists():
        return []
    return sorted(path for path in paths.PROJECTS_DIR.iterdir() if path.is_dir())


def _schema_supports_refresh(conn: sqlite3.Connection) -> bool:
    try:
        version = conn.execute(
            "select value from meta where key = 'index_version'"
        ).fetchone()
        sources = conn.execute(
            "select name from sqlite_master where type = 'table' and name = 'sources'"
        ).fetchone()
    except sqlite3.Error:
        return False
    return bool(version and version[0] == INDEX_VERSION and sources)


def _index_totals(conn: sqlite3.Connection, db_path: Path) -> RebuildSummary:
    trace_count = int(conn.execute("select count(*) from traces").fetchone()[0])
    unit_count = int(conn.execute("select count(*) from units").fetchone()[0])
    map_node_count = int(conn.execute("select count(*) from trace_map_nodes").fetchone()[0])
    return RebuildSummary(
        index_path=db_path,
        trace_count=trace_count,
        unit_count=unit_count,
        map_node_count=map_node_count,
    )


def _record_source(conn: sqlite3.Connection, trace_path: Path, record_count: int) -> None:
    stat = trace_path.stat()
    conn.execute(
        """
        insert or replace into sources(path, mtime_ns, size, record_count)
        values (?, ?, ?, ?)
        """,
        (str(trace_path), stat.st_mtime_ns, stat.st_size, record_count),
    )


def _delete_trace_ids(conn: sqlite3.Connection, trace_ids: list[str]) -> None:
    ids = sorted({trace_id for trace_id in trace_ids if trace_id})
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    _delete_units_where(conn, f"trace_id in ({placeholders})", ids)
    conn.execute(f"delete from traces where trace_id in ({placeholders})", ids)
    conn.execute(f"delete from trace_map_nodes where trace_id in ({placeholders})", ids)
    conn.execute(f"delete from trace_map_edges where trace_id in ({placeholders})", ids)


def _delete_units_by_types(conn: sqlite3.Connection, unit_types: set[str]) -> None:
    if not unit_types:
        return
    values = sorted(unit_types)
    placeholders = ",".join("?" for _ in values)
    _delete_units_where(conn, f"unit_type in ({placeholders})", values)


def _delete_units_where(conn: sqlite3.Connection, clause: str, params: list[str]) -> None:
    rows = list(conn.execute(f"select rowid, unit_id from units where {clause}", params))
    for rowid, unit_id in rows:
        conn.execute("delete from units_fts where rowid = ?", (rowid,))
        conn.execute("delete from facets where unit_id = ?", (unit_id,))
        conn.execute("delete from signals where unit_id = ?", (unit_id,))
        conn.execute("delete from units where unit_id = ?", (unit_id,))


def _delete_trail_maps(conn: sqlite3.Connection) -> None:
    conn.execute("delete from trace_map_edges where edge_id like 'tme:%:trail:%'")
    conn.execute("delete from trace_map_nodes where node_id like 'tmn:%:trail:%'")


def _project_sources_by_slug() -> dict[str, Path]:
    try:
        from .config import load_config

        cfg = load_config()
    except Exception:
        return {}
    out: dict[str, Path] = {}
    for project_path, registration in getattr(cfg, "projects", {}).items():
        slug = getattr(registration, "slug", None)
        if slug:
            out[str(slug)] = Path(project_path)
    return out


def _iter_trace_records(project_home: Path) -> list[tuple[TraceRecord, Path]]:
    records: list[tuple[TraceRecord, Path]] = []
    for trace_path in _iter_trace_paths(project_home):
        records.extend((record, trace_path) for record in _iter_trace_file_records(trace_path))
    return records


def _iter_trace_paths(project_home: Path) -> list[Path]:
    traces_dir = project_home / "traces"
    if not traces_dir.exists():
        return []
    return sorted(traces_dir.glob("*.jsonl"))


def _iter_trace_file_records(trace_path: Path) -> list[TraceRecord]:
    records: list[TraceRecord] = []
    for line in trace_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            records.append(TraceRecord.model_validate_json(line))
        except Exception:
            continue
    return records


def _insert_trace(
    conn: sqlite3.Connection,
    record: TraceRecord,
    project_slug: str,
    trace_path: Path,
    trace_map: TraceMap,
) -> None:
    title = record.task.description or _first_user_text(record) or record.trace_id
    conn.execute(
        """
        insert into traces(trace_id, project_slug, session_id, generation_index, trace_path, title)
        values (?, ?, ?, ?, ?, ?)
        """,
        (
            record.trace_id,
            project_slug,
            record.session_id,
            record.generation_index,
            str(trace_path),
            title,
        ),
    )
    for ordinal, node in enumerate(trace_map.nodes, 1):
        conn.execute(
            """
            insert into trace_map_nodes(node_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (node.node_id, node.trace_id, ordinal, node.model_dump_json()),
        )
    for ordinal, edge in enumerate(trace_map.edges, 1):
        conn.execute(
            """
            insert into trace_map_edges(edge_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (edge.edge_id, edge.trace_id, ordinal, edge.model_dump_json()),
        )


def _insert_trail_map(conn: sqlite3.Connection, trace_map: TraceMap) -> None:
    node_offset = int(
        conn.execute(
            "select coalesce(max(ordinal), 0) from trace_map_nodes where trace_id = ?",
            (trace_map.trace_id,),
        ).fetchone()[0]
    )
    edge_offset = int(
        conn.execute(
            "select coalesce(max(ordinal), 0) from trace_map_edges where trace_id = ?",
            (trace_map.trace_id,),
        ).fetchone()[0]
    )
    for ordinal, node in enumerate(trace_map.nodes, node_offset + 1):
        conn.execute(
            """
            insert or replace into trace_map_nodes(node_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (node.node_id, node.trace_id, ordinal, node.model_dump_json()),
        )
    for ordinal, edge in enumerate(trace_map.edges, edge_offset + 1):
        conn.execute(
            """
            insert or replace into trace_map_edges(edge_id, trace_id, ordinal, payload)
            values (?, ?, ?, ?)
            """,
            (edge.edge_id, edge.trace_id, ordinal, edge.model_dump_json()),
        )


def _insert_unit(conn: sqlite3.Connection, unit: TraceUnit) -> None:
    facets = _unit_facets(unit)
    conn.execute(
        """
        insert into units(
            unit_id, unit_type, trace_id, project_slug,
            title_text, intent_text, action_text, evidence_text, artifact_text,
            files_json, skills_json, facets_json, signals_json, metadata_json, trail_refs_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            unit.unit_id,
            unit.unit_type,
            unit.trace_id,
            unit.project_slug,
            unit.title_text,
            unit.intent_text,
            unit.action_text,
            unit.evidence_text,
            unit.artifact_text,
            json.dumps(unit.files),
            json.dumps(unit.skills),
            json.dumps([facet.model_dump(mode="json") for facet in facets]),
            json.dumps([signal.model_dump(mode="json") for signal in unit.signals]),
            json.dumps(unit.metadata),
            json.dumps(unit.trail_refs),
        ),
    )
    rowid = conn.execute("select last_insert_rowid()").fetchone()[0]
    conn.execute(
        """
        insert into units_fts(
            rowid, title_text, intent_text, action_text, evidence_text, artifact_text
        ) values (?, ?, ?, ?, ?, ?)
        """,
        (
            rowid,
            unit.title_text,
            unit.intent_text,
            unit.action_text,
            unit.evidence_text,
            unit.artifact_text,
        ),
    )
    for facet in facets:
        conn.execute(
            "insert into facets(unit_id, name, value) values (?, ?, ?)",
            (unit.unit_id, facet.name, str(facet.value)),
        )
    for signal in unit.signals:
        conn.execute(
            "insert into signals(unit_id, name, value) values (?, ?, ?)",
            (unit.unit_id, signal.name, json.dumps(signal.value)),
        )


def _insert_map_node(conn: sqlite3.Connection, node: TraceMapNode) -> None:
    # Nodes are inserted by _insert_trace so map rebuild and trace insert stay atomic.
    return None


def _insert_map_edge(conn: sqlite3.Connection, edge: Any) -> None:
    # Edges are inserted by _insert_trace so map rebuild and trace insert stay atomic.
    return None


def _unit_facets(unit: TraceUnit) -> list[TraceFacet]:
    facets = list(unit.facets)
    if not any(facet.name == "unit.type" for facet in facets):
        facets.append(TraceFacet(name="unit.type", value=unit.unit_type, source="exact_schema"))
    return facets


def _build_units(record: TraceRecord, trace_map: TraceMap, project_slug: str) -> list[TraceUnit]:
    files = _trace_files(trace_map)
    skills = _trace_skills(record)
    tools = _trace_tools(record)
    test_commands = _test_commands(record)
    signals = _trace_signals(record, trace_map)
    facets = _trace_facets(record, project_slug, skills, tools, files)
    title = record.task.description or _first_user_text(record) or record.trace_id
    unit = TraceUnit(
        unit_id=f"tu:{record.trace_id}:trace",
        unit_type="trace",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=files,
        skills=skills,
        title_text=title,
        intent_text=" ".join(filter(None, [record.task.description, _first_user_text(record)])),
        action_text=" ".join(_tool_texts(record)),
        evidence_text=" ".join(_observation_summaries(record)),
        artifact_text=" ".join([*files, *record.dependencies]),
        facets=facets,
        signals=signals,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
            "test_commands": test_commands,
            "map_node_refs": [
                node.node_id
                for node in trace_map.nodes
                if node.action_type
                in {"user_instruction", "agent_plan", "file_edit", "test_run", "final_response"}
            ],
        },
    )
    units = [
        unit,
        _trace_intent_unit(record, project_slug, title, facets, signals),
        _trace_slice_unit(record, trace_map, project_slug, title, facets, signals),
        _tool_sequence_unit(record, project_slug, title, facets),
    ]
    units.extend(_skill_invocation_units(record, project_slug, title, facets))
    for node in trace_map.nodes:
        units.append(
            TraceUnit(
                unit_id=node.unit_id,
                unit_type="trace_map_node",
                trace_id=record.trace_id,
                project_slug=project_slug,
                files=[*node.files_read, *node.files_modified],
                skills=skills if node.action_type == "skill_invocation" else [],
                title_text=title,
                action_text=node.text_preview or "",
                evidence_text=node.text_preview or "",
                artifact_text=" ".join([*node.files_read, *node.files_modified]),
                facets=facets if node.action_type == "skill_invocation" else [],
                signals=[],
                metadata={
                    "node_id": node.node_id,
                    "session_id": record.session_id,
                    "generation_index": record.generation_index,
                    "timestamp_start": record.timestamp_start,
                    "timestamp_end": record.timestamp_end,
                },
            )
        )
    if any(signal.name == "tested_successful_fix_candidate" for signal in signals):
        units.append(
            TraceUnit(
                unit_id=f"tu:{record.trace_id}:signal:tested_successful_fix_candidate",
                unit_type="test_or_error_signal",
                trace_id=record.trace_id,
                project_slug=project_slug,
                files=files,
                skills=skills,
                title_text=title,
                intent_text=_first_user_text(record) or "",
                action_text=" ".join(test_commands),
                evidence_text="test failed then passed after edit",
                artifact_text=" ".join(files),
                facets=facets,
                signals=signals,
                metadata={
                    "session_id": record.session_id,
                    "generation_index": record.generation_index,
                    "timestamp_start": record.timestamp_start,
                    "timestamp_end": record.timestamp_end,
                    "test_commands": test_commands,
                    "map_node_refs": unit.metadata["map_node_refs"],
                },
            )
        )
    return units


def _trace_intent_unit(
    record: TraceRecord,
    project_slug: str,
    title: str,
    facets: list[TraceFacet],
    signals: list[TraceSignal],
) -> TraceUnit:
    return TraceUnit(
        unit_id=f"tu:{record.trace_id}:intent",
        unit_type="trace_intent_candidate",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=[],
        skills=_trace_skills(record),
        title_text=title,
        intent_text=" ".join(filter(None, [record.task.description, _first_user_text(record)])),
        action_text="",
        evidence_text="intent candidate derived from task and first user instruction",
        artifact_text="",
        facets=facets,
        signals=signals,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
        },
    )


def _trace_slice_unit(
    record: TraceRecord,
    trace_map: TraceMap,
    project_slug: str,
    title: str,
    facets: list[TraceFacet],
    signals: list[TraceSignal],
) -> TraceUnit:
    selected_nodes = [
        node
        for node in trace_map.nodes
        if node.action_type
        in {"user_instruction", "agent_plan", "file_edit", "test_run", "final_response"}
    ][:12]
    files = sorted(
        dict.fromkeys(
            path
            for node in selected_nodes
            for path in [*node.files_read, *node.files_modified]
        )
    )
    return TraceUnit(
        unit_id=f"tu:{record.trace_id}:slice:intent",
        unit_type="trace_slice",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=files,
        skills=_trace_skills(record),
        title_text=title,
        intent_text=_first_user_text(record) or record.task.description or "",
        action_text=" ".join(node.action_type for node in selected_nodes),
        evidence_text="bounded intent-to-evidence Trace Map slice",
        artifact_text=" ".join(files),
        facets=facets,
        signals=signals,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
            "slice_kind": "intent",
            "map_node_refs": [node.node_id for node in selected_nodes],
        },
    )


def _skill_invocation_units(
    record: TraceRecord,
    project_slug: str,
    title: str,
    trace_facets: list[TraceFacet],
) -> list[TraceUnit]:
    units: list[TraceUnit] = []
    for step in record.steps:
        for call in step.tool_calls:
            if "skill" not in call.tool_name.lower():
                continue
            skill = call.input.get("name") or call.input.get("skill")
            if not skill:
                continue
            skill_name = str(skill)
            facets = [
                facet
                for facet in trace_facets
                if facet.name in {"project_slug", "agent.name", "model", "provider.kind"}
            ]
            facets.append(TraceFacet(name="skill.name", value=skill_name, source="exact_schema"))
            units.append(
                TraceUnit(
                    unit_id=f"tu:{record.trace_id}:skill:{call.tool_call_id}",
                    unit_type="skill_invocation",
                    trace_id=record.trace_id,
                    project_slug=project_slug,
                    skills=[skill_name],
                    title_text=f"Skill invocation {skill_name}",
                    intent_text=record.task.description or _first_user_text(record) or "",
                    action_text=f"{call.tool_name} {skill_name}",
                    evidence_text=f"skill.name={skill_name}",
                    facets=facets,
                    signals=[
                        TraceSignal(
                            name="skill_invoked",
                            value=[skill_name],
                            confidence="high",
                            evidence_refs=[f"tu:{record.trace_id}:tool:{call.tool_call_id}"],
                        )
                    ],
                    metadata={
                        "session_id": record.session_id,
                        "generation_index": record.generation_index,
                        "timestamp_start": record.timestamp_start,
                        "timestamp_end": record.timestamp_end,
                        "step_index": step.step_index,
                        "tool_call_id": call.tool_call_id,
                    },
                )
            )
    return units


def _tool_sequence_unit(
    record: TraceRecord,
    project_slug: str,
    title: str,
    facets: list[TraceFacet],
) -> TraceUnit:
    tools = [call.tool_name for step in record.steps for call in step.tool_calls]
    return TraceUnit(
        unit_id=f"tu:{record.trace_id}:tool-sequence",
        unit_type="tool_sequence",
        trace_id=record.trace_id,
        project_slug=project_slug,
        files=[],
        skills=_trace_skills(record),
        title_text=f"Tool sequence for {title}",
        intent_text=record.task.description or _first_user_text(record) or "",
        action_text=" ".join(tools),
        evidence_text=f"{len(tools)} tool calls",
        artifact_text="",
        facets=facets,
        metadata={
            "session_id": record.session_id,
            "generation_index": record.generation_index,
            "timestamp_start": record.timestamp_start,
            "timestamp_end": record.timestamp_end,
            "tools": tools,
        },
    )


def _build_trail_units(repo: Path, project_slug: str) -> list[TraceUnit]:
    try:
        from .trails import build_trail_query_projection

        projection = build_trail_query_projection(repo)
    except Exception:
        return []

    units: list[TraceUnit] = []
    for patch in projection.patches_by_id.values():
        trace_id = patch.get("trace_id")
        patch_id = patch.get("trace_patch_id")
        if not trace_id or not patch_id:
            continue
        file_path = patch.get("file_path") or patch.get("path")
        commit_sha = patch.get("commit_sha")
        facets = _trail_facets(patch)
        signals = [
            TraceSignal(
                name="trail_patch_created",
                value=True,
                confidence="high",
                evidence_refs=list(_trail_refs(patch)),
            )
        ]
        if "git_anchor_unknown" in (patch.get("limitations") or []):
            signals.append(
                TraceSignal(
                    name="trail_anchor_unknown",
                    value=True,
                    confidence="medium",
                    evidence_refs=list(_trail_refs(patch)),
                )
            )
        units.append(
            TraceUnit(
                unit_id=f"tu:{trace_id}:patch:{patch_id}",
                unit_type="patch",
                trace_id=trace_id,
                project_slug=project_slug,
                files=[file_path] if file_path else [],
                title_text=f"Trace patch {patch_id}",
                action_text=" ".join(
                    str(value)
                    for value in (
                        file_path,
                        patch.get("relation"),
                        patch.get("evidence_tier"),
                        patch.get("evidence_firmness"),
                    )
                    if value
                ),
                evidence_text=" ".join(
                    str(value)
                    for value in (
                        patch_id,
                        patch.get("relation"),
                        commit_sha,
                        " ".join(patch.get("limitations") or []),
                    )
                    if value
                ),
                artifact_text=" ".join(str(value) for value in (file_path, commit_sha) if value),
                facets=facets,
                signals=signals,
                metadata=_trail_metadata(patch),
                trail_refs=list(_trail_refs(patch)),
            )
        )

    for anchor in projection.anchors_by_id.values():
        trace_id = anchor.get("trace_id")
        anchor_id = anchor.get("git_anchor_id")
        patch_id = anchor.get("trace_patch_id")
        if not trace_id or not anchor_id:
            continue
        file_path = anchor.get("file_path") or anchor.get("path")
        commit_sha = anchor.get("commit_sha")
        units.append(
            TraceUnit(
                unit_id=f"tu:{trace_id}:git-anchor:{anchor_id}",
                unit_type="git_anchor",
                trace_id=trace_id,
                project_slug=project_slug,
                files=[file_path] if file_path else [],
                title_text=f"Git anchor {anchor_id}",
                evidence_text=" ".join(
                    str(value)
                    for value in (
                        anchor_id,
                        patch_id,
                        commit_sha,
                        anchor.get("evidence_tier"),
                        anchor.get("evidence_firmness"),
                    )
                    if value
                ),
                artifact_text=" ".join(str(value) for value in (file_path, commit_sha) if value),
                facets=_trail_facets(anchor),
                signals=[
                    TraceSignal(
                        name="trail_git_anchored",
                        value=True,
                        confidence="high",
                        evidence_refs=list(_trail_refs(anchor)),
                    )
                ],
                metadata=_trail_metadata(anchor),
                trail_refs=list(_trail_refs(anchor)),
            )
        )
    return units


def _trail_facets(row: dict[str, Any]) -> list[TraceFacet]:
    facets: list[TraceFacet] = []

    def add(name: str, value: Any) -> None:
        if value is not None:
            facets.append(TraceFacet(name=name, value=str(value), source="trail_projection"))

    add("file.path", row.get("file_path") or row.get("path"))
    add("trail.relation", row.get("relation"))
    add("trail.evidence_tier", row.get("evidence_tier"))
    add("trail.evidence_firmness", row.get("evidence_firmness"))
    add("trace_patch.id", row.get("trace_patch_id"))
    add("git_anchor.id", row.get("git_anchor_id"))
    add("git.commit_sha", row.get("commit_sha"))
    return facets


def _trail_refs(row: dict[str, Any]) -> tuple[str, ...]:
    refs = row.get("resource_refs") or {}
    values = [value for value in refs.values() if value]
    trace_slice = row.get("trace_slice") or {}
    if trace_slice.get("trace_patch_ref"):
        values.append(trace_slice["trace_patch_ref"])
    if trace_slice.get("git_anchor_ref"):
        values.append(trace_slice["git_anchor_ref"])
    return tuple(dict.fromkeys(str(value) for value in values))


def _trail_metadata(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "trace_patch_id",
        "step_id",
        "step_index",
        "generation_index",
        "relation",
        "patch_status",
        "affected_range",
        "line_count",
        "git_anchor_id",
        "commit_sha",
        "commit_id",
        "evidence_tier",
        "evidence_firmness",
        "limitations",
        "source_refs",
        "range",
        "line_origin",
    )
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _trail_maps_from_units(units: list[TraceUnit]) -> list[TraceMap]:
    by_trace: dict[str, list[TraceUnit]] = {}
    for unit in units:
        by_trace.setdefault(unit.trace_id, []).append(unit)

    maps: list[TraceMap] = []
    for trace_id, trace_units in sorted(by_trace.items()):
        ordered = sorted(
            trace_units,
            key=lambda unit: (
                int(unit.metadata.get("step_index") or 0),
                0 if unit.unit_type == "patch" else 1,
                unit.unit_id,
            ),
        )
        nodes: list[TraceMapNode] = []
        edges: list[TraceMapEdge] = []
        previous: TraceMapNode | None = None
        for ordinal, unit in enumerate(ordered, 1):
            action_type = "patch_created" if unit.unit_type == "patch" else "git_anchor"
            node = TraceMapNode(
                node_id=f"tmn:{trace_id}:trail:{ordinal}",
                trace_id=trace_id,
                unit_id=unit.unit_id,
                action_type=action_type,  # type: ignore[arg-type]
                step_index=unit.metadata.get("step_index"),
                start_step_index=unit.metadata.get("step_index"),
                end_step_index=unit.metadata.get("step_index"),
                previous_node_id=previous.node_id if previous else None,
                files_modified=unit.files if unit.unit_type == "patch" else [],
                anchor_refs=unit.trail_refs,
                text_preview=_preview(unit.title_text or unit.evidence_text),
                metadata={
                    key: unit.metadata[key]
                    for key in (
                        "trace_patch_id",
                        "git_anchor_id",
                        "commit_sha",
                        "relation",
                        "evidence_tier",
                        "evidence_firmness",
                    )
                    if key in unit.metadata
                },
            )
            unit.metadata["map_node_refs"] = [node.node_id]
            if previous:
                previous.next_node_id = node.node_id
                edges.append(
                    TraceMapEdge(
                        edge_id=f"tme:{trace_id}:trail:{len(edges) + 1}",
                        trace_id=trace_id,
                        source_node_id=previous.node_id,
                        target_node_id=node.node_id,
                        edge_type="previous_next",
                    )
                )
            nodes.append(node)
            previous = node
        if nodes:
            maps.append(
                TraceMap(
                    trace_id=trace_id,
                    root_node_ids=[nodes[0].node_id],
                    nodes=nodes,
                    edges=edges,
                    limitations=["trail_event_projection_only"],
                )
            )
    return maps


def _unit_from_row(row: sqlite3.Row) -> TraceUnit:
    return TraceUnit(
        unit_id=row["unit_id"],
        unit_type=row["unit_type"],
        trace_id=row["trace_id"],
        project_slug=row["project_slug"],
        files=json.loads(row["files_json"]),
        skills=json.loads(row["skills_json"]),
        title_text=row["title_text"],
        intent_text=row["intent_text"],
        action_text=row["action_text"],
        evidence_text=row["evidence_text"],
        artifact_text=row["artifact_text"],
        facets=[TraceFacet.model_validate(f) for f in json.loads(row["facets_json"])],
        signals=[TraceSignal.model_validate(s) for s in json.loads(row["signals_json"])],
        metadata=json.loads(row["metadata_json"]),
        trail_refs=json.loads(row["trail_refs_json"]),
    )


def _latest_units(units: list[TraceUnit]) -> list[TraceUnit]:
    latest_by_session: dict[str, int] = {}
    for unit in units:
        session_id = str(unit.metadata.get("session_id") or unit.trace_id)
        generation = int(unit.metadata.get("generation_index") or 0)
        latest_by_session[session_id] = max(latest_by_session.get(session_id, -1), generation)
    return [
        unit
        for unit in units
        if int(unit.metadata.get("generation_index") or 0)
        == latest_by_session.get(str(unit.metadata.get("session_id") or unit.trace_id), 0)
    ]


def _candidate_packet(
    unit: TraceUnit,
    score: float,
    score_parts: dict[str, float],
    matched_fields: dict[str, list[str]],
    *,
    include_slice: str | None = None,
    max_slice_nodes: int = 40,
    index_path: Path | None = None,
) -> CandidatePacket:
    title = unit.title_text or unit.trace_id
    map_node_refs = [str(ref) for ref in unit.metadata.get("map_node_refs", [])]
    slice_preview = _slice_preview_for_unit(unit, max_slice_nodes, index_path) if include_slice else None
    return CandidatePacket(
        unit_id=unit.unit_id,
        unit_type=unit.unit_type,
        trace_id=unit.trace_id,
        project_slug=unit.project_slug,
        title=title,
        intent_preview=_preview(unit.intent_text),
        candidate_kind=_candidate_kind(unit),
        match_explanation=_match_explanation(matched_fields, unit),
        score=round(score, 3),
        score_parts={key: round(value, 3) for key, value in score_parts.items() if value},
        matched_fields=matched_fields,
        facets=_packet_facets(unit.facets),
        signals=unit.signals,
        skills=unit.skills,
        files=unit.files,
        test_commands=[str(cmd) for cmd in unit.metadata.get("test_commands", [])],
        trail_refs=unit.trail_refs,
        map_ref=f"ot://trace/{unit.trace_id}/map",
        map_node_refs=map_node_refs,
        refs={
            "trace": unit.trace_id,
            "unit": unit.unit_id,
            "map": f"ot://trace/{unit.trace_id}/map",
        },
        slice_preview=slice_preview,
        limitations=["candidate_packet_intent_only"],
        metadata={
            "session_id": unit.metadata.get("session_id"),
            "generation_index": unit.metadata.get("generation_index"),
            "include_slice": include_slice,
        },
    )


def _lexical_score(unit: TraceUnit, terms: list[str]) -> tuple[float, dict[str, list[str]]]:
    if not terms:
        return 0.0, {}
    score = 0.0
    matched: dict[str, list[str]] = {}
    for field, weight in _FIELD_WEIGHTS.items():
        field_terms = set(_terms(str(getattr(unit, field, "")).lower()))
        hits = [term for term in terms if term in field_terms]
        if hits:
            matched[field] = hits
            score += weight * len(hits)
    return score, matched


def _metadata_score(
    unit: TraceUnit,
    *,
    tool: str | None,
    files: str | None,
    file_kind: str | None,
    file_op: str | None,
    success: bool | None,
    committed: bool | None,
    candidate_kind: str | None,
    project: str | None,
    facet_filters: tuple[tuple[str, str], ...] = (),
    metadata_filters: tuple[tuple[str, str], ...] = (),
    named_filters: tuple[tuple[str, str], ...] = (),
    since: str | None = None,
) -> float:
    score = 0.0
    if tool and tool in _facet_values(unit, "tool.name"):
        score += 2.0
    if files and any(fnmatch(path, files) for path in unit.files):
        score += 2.0
    if file_kind and file_kind.lstrip(".") in _facet_values(unit, "file.kind"):
        score += 1.0
    if file_op and file_op in _facet_values(unit, "file.operation"):
        score += 1.0
    if success is not None and _bool_facet(unit, "outcome.success") is success:
        score += 1.0
    if committed is not None and _bool_facet(unit, "outcome.committed") is committed:
        score += 1.0
    if candidate_kind and _candidate_kind(unit) == candidate_kind:
        score += 2.0
    if project and unit.project_slug == project:
        score += 1.0
    score += float(len(facet_filters) + len(metadata_filters) + len(named_filters))
    if since:
        score += 1.0
    return score


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


def _matches_facet_filters(unit: TraceUnit, filters: tuple[tuple[str, str], ...]) -> bool:
    return all(value in _facet_values(unit, name) for name, value in filters)


def _matches_metadata_filters(unit: TraceUnit, filters: tuple[tuple[str, str], ...]) -> bool:
    return all(_metadata_value_matches(unit.metadata.get(name), value) for name, value in filters)


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
    filters: list[tuple[str, str]] = []
    for name, value in (
        ("provider.kind", provider),
        ("bash.command_family", cmd_family),
        ("bash.action", bash_action),
        ("test.framework", test_framework),
        ("service.name", service),
        ("service.channel", service_channel),
        ("dependency.name", dependency),
        ("git_link_tier", git_tier),
        ("survival.state", survival),
    ):
        if value:
            filters.append((name, value))
    return tuple(filters)


def _requested_unit_type(
    facet_filters: tuple[tuple[str, str], ...],
    candidate_kind: str | None,
) -> str | None:
    for name, value in facet_filters:
        if name == "unit.type" and value in _M1_UNIT_TYPES:
            return value
    if candidate_kind in _M1_UNIT_TYPES:
        return candidate_kind
    return None


def _select_unit_rows(
    conn: sqlite3.Connection,
    terms: list[str],
    unit_type_filter: str | None,
) -> list[sqlite3.Row]:
    unit_type = unit_type_filter or "trace"
    if terms:
        return list(
            conn.execute(
                """
                select units.* from units
                join units_fts on units_fts.rowid = units.rowid
                where units_fts match ? and units.unit_type = ?
                order by units.trace_id, units.unit_id
                """,
                (_fts_query(terms), unit_type),
            )
        )
    return list(
        conn.execute(
            "select * from units where unit_type = ? order by trace_id, unit_id",
            (unit_type,),
        )
    )


def _fts_query(terms: list[str]) -> str:
    quoted: list[str] = []
    for term in terms:
        safe = term.replace('"', "")
        if safe:
            quoted.append(f'"{safe}"')
    return " ".join(quoted)


def _metadata_value_matches(actual: Any, expected: str) -> bool:
    if isinstance(actual, bool):
        return str(actual).lower() == expected.lower()
    if isinstance(actual, (str, int, float)):
        return str(actual) == expected
    if isinstance(actual, list):
        return any(_metadata_value_matches(item, expected) for item in actual)
    return False


def _facet_values(unit: TraceUnit, name: str) -> set[str]:
    return {str(facet.value) for facet in unit.facets if facet.name == name}


def _bool_facet(unit: TraceUnit, name: str) -> bool | None:
    for facet in unit.facets:
        if facet.name != name:
            continue
        if isinstance(facet.value, bool):
            return facet.value
        if isinstance(facet.value, str):
            return facet.value.lower() in {"1", "true", "yes"}
    return None


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
    try:
        parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--since must be an ISO date/time or duration like 7d") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _unit_timestamp(unit: TraceUnit) -> datetime:
    raw = unit.metadata.get("timestamp_end") or unit.metadata.get("timestamp_start")
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _terms(text: str) -> list[str]:
    return [term.lower() for term in re.findall(r"[a-zA-Z0-9_./-]+", text) if len(term) > 1]


def _trace_files(trace_map: TraceMap) -> list[str]:
    files: list[str] = []
    for node in trace_map.nodes:
        files.extend(node.files_read)
        files.extend(node.files_modified)
    return sorted(dict.fromkeys(files))


def _trace_skills(record: TraceRecord) -> list[str]:
    skills: list[str] = []
    for step in record.steps:
        for call in step.tool_calls:
            normalized = call.tool_name.lower()
            if "skill" in normalized:
                value = call.input.get("name") or call.input.get("skill")
                if value:
                    skills.append(str(value))
    return sorted(dict.fromkeys(skills))


def _trace_tools(record: TraceRecord) -> list[str]:
    tools: list[str] = []
    for step in record.steps:
        tools.extend(call.tool_name for call in step.tool_calls)
    return sorted(dict.fromkeys(tools))


def _test_commands(record: TraceRecord) -> list[str]:
    commands: list[str] = []
    for step in record.steps:
        for call in step.tool_calls:
            command = str(call.input.get("command") or "")
            if command and _TEST_COMMAND_RE.search(command):
                commands.append(command)
    return commands


def _trace_signals(record: TraceRecord, trace_map: TraceMap) -> list[TraceSignal]:
    observations = " ".join(_observation_summaries(record)).lower()
    failed = any(word in observations for word in ("failed", "failures", "error"))
    passed = "passed" in observations or "success" in observations
    edited = any(node.action_type == "file_edit" for node in trace_map.nodes)
    test_seen = any(node.action_type == "test_run" for node in trace_map.nodes)
    signals: list[TraceSignal] = []

    def add(name: str, value: bool, confidence: str = "high") -> None:
        if value:
            signals.append(
                TraceSignal(
                    name=name,
                    value=True,
                    confidence=confidence,  # type: ignore[arg-type]
                    evidence_refs=[
                        node.node_id
                        for node in trace_map.nodes
                        if node.action_type in {"test_run", "file_edit", "tool_result"}
                    ][:8],
                )
            )

    add("test_command_seen", test_seen)
    add("test_failed_seen", failed)
    add("test_passed_seen", passed)
    add("patch_or_edit_seen", edited)
    add("test_passed_after_edit_seen", test_seen and passed and edited)
    add("test_failed_then_passed_seen", failed and passed)
    add("outcome_success", record.outcome.success is True)
    add("outcome_committed", record.outcome.committed is True)
    add(
        "tested_successful_fix_candidate",
        test_seen and failed and passed and edited and record.outcome.success is True,
    )
    if _trace_skills(record):
        signals.append(
            TraceSignal(
                name="skill_invoked",
                value=_trace_skills(record),
                confidence="high",
                evidence_refs=[
                    node.node_id for node in trace_map.nodes if node.action_type == "skill_invocation"
                ],
            )
        )
    return signals


def _trace_facets(
    record: TraceRecord,
    project_slug: str,
    skills: list[str],
    tools: list[str],
    files: list[str],
) -> list[TraceFacet]:
    facets = [
        TraceFacet(name="project_slug", value=project_slug, source="exact_schema"),
        TraceFacet(name="agent.name", value=record.agent.name, source="exact_schema"),
    ]
    provider = _provider_kind(record)
    if provider:
        facets.append(TraceFacet(name="provider.kind", value=provider, source="exact_schema"))
    if record.agent.model:
        facets.append(TraceFacet(name="model", value=record.agent.model, source="exact_schema"))
    facets.append(
        TraceFacet(name="outcome.committed", value=record.outcome.committed, source="exact_schema")
    )
    if record.outcome.success is not None:
        facets.append(
            TraceFacet(name="outcome.success", value=record.outcome.success, source="exact_schema")
        )
    for skill in skills:
        facets.append(TraceFacet(name="skill.name", value=skill, source="exact_schema"))
    for tool in tools:
        facets.append(TraceFacet(name="tool.name", value=tool, source="exact_schema"))
    for link in record.git_links:
        facets.append(TraceFacet(name="git_link_tier", value=link.tier, source="exact_schema"))
    for dependency in record.dependencies:
        facets.append(TraceFacet(name="dependency.name", value=dependency, source="dependency"))
    for state in _survival_states(record):
        facets.append(TraceFacet(name="survival.state", value=state, source="exact_schema"))
    for file_path in files:
        facets.append(TraceFacet(name="file.path", value=file_path, source="file_path"))
        suffix = Path(file_path).suffix.lstrip(".")
        if suffix:
            facets.append(TraceFacet(name="file.kind", value=suffix, source="file_path"))
    facets.extend(_command_facets(record))
    facets.extend(_file_operation_facets(record))
    return facets


def _provider_kind(record: TraceRecord) -> str | None:
    model = record.agent.model or ""
    if "/" in model:
        provider = model.split("/", 1)[0].strip()
        return provider or None
    if record.agent.name:
        return record.agent.name.split("-", 1)[0]
    return None


def _survival_states(record: TraceRecord) -> list[str]:
    raw = record.metadata.get("survival_state") or record.metadata.get("survival_states")
    if raw is None:
        return []
    if isinstance(raw, list):
        return sorted({str(item) for item in raw if item})
    return [str(raw)]


def _command_facets(record: TraceRecord) -> list[TraceFacet]:
    facets: list[TraceFacet] = []
    for step in record.steps:
        for call in step.tool_calls:
            if call.tool_name.lower() not in {"bash", "shell"}:
                continue
            command = str(call.input.get("command") or "")
            if not command:
                continue
            family = _command_family(command)
            if family:
                facets.append(
                    TraceFacet(
                        name="bash.command_family",
                        value=family,
                        source="bash_command",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            action = _bash_action(command)
            if action:
                facets.append(
                    TraceFacet(
                        name="bash.action",
                        value=action,
                        source="bash_command",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            framework = _test_framework(command)
            if framework:
                facets.append(
                    TraceFacet(
                        name="test.framework",
                        value=framework,
                        source="bash_command",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            for name, channel in _service_mentions(command):
                facets.append(
                    TraceFacet(
                        name="service.name",
                        value=name,
                        source="web_url",
                        confidence="medium",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
                facets.append(
                    TraceFacet(
                        name="service.channel",
                        value=channel,
                        source="web_url",
                        confidence="medium",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
    return _dedupe_facets(facets)


def _file_operation_facets(record: TraceRecord) -> list[TraceFacet]:
    facets: list[TraceFacet] = []
    for step in record.steps:
        for call in step.tool_calls:
            normalized = call.tool_name.lower().replace("_", "").replace("-", "")
            if normalized in {"edit", "write", "multiedit"}:
                facets.append(
                    TraceFacet(
                        name="file.operation",
                        value="edit",
                        source="tool_input",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
            elif normalized in {"read", "view", "glob", "grep"}:
                facets.append(
                    TraceFacet(
                        name="file.operation",
                        value="read",
                        source="tool_input",
                        evidence_ref=f"tu:{record.trace_id}:tool:{call.tool_call_id}",
                    )
                )
    return _dedupe_facets(facets)


def _dedupe_facets(facets: list[TraceFacet]) -> list[TraceFacet]:
    seen: set[tuple[str, str]] = set()
    out: list[TraceFacet] = []
    for facet in facets:
        key = (facet.name, str(facet.value))
        if key in seen:
            continue
        seen.add(key)
        out.append(facet)
    return out


def _command_family(command: str) -> str | None:
    stripped = command.strip()
    if not stripped:
        return None
    parts = stripped.split()
    if len(parts) >= 2 and parts[0] in {"npm", "pnpm", "yarn", "go", "cargo", "mvn", "gradle"}:
        if parts[1] == "test":
            return f"{parts[0]} test"
    return parts[0]


def _bash_action(command: str) -> str | None:
    if _TEST_COMMAND_RE.search(command):
        return "test"
    family = _command_family(command)
    if family in {"curl", "wget"}:
        return "service_probe"
    return None


def _test_framework(command: str) -> str | None:
    lowered = command.lower()
    for framework in ("pytest", "vitest", "jest", "rspec"):
        if framework in lowered:
            return framework
    if "npm test" in lowered:
        return "npm test"
    if "pnpm test" in lowered:
        return "pnpm test"
    if "yarn test" in lowered:
        return "yarn test"
    if "cargo test" in lowered:
        return "cargo test"
    if "go test" in lowered:
        return "go test"
    return None


def _service_mentions(command: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for match in _URL_RE.findall(command):
        try:
            parsed = urlparse(match)
            host = parsed.hostname
        except ValueError:
            continue
        if host and parsed.scheme:
            mentions.append((host, parsed.scheme))
    return mentions


def _first_user_text(record: TraceRecord) -> str | None:
    for step in sorted(record.steps, key=lambda s: s.step_index):
        if step.role == "user" and step.content:
            return step.content
    return None


def _tool_texts(record: TraceRecord) -> list[str]:
    texts: list[str] = []
    for step in record.steps:
        for call in step.tool_calls:
            command = call.input.get("command")
            file_path = call.input.get("file_path") or call.input.get("file")
            if command:
                texts.append(str(command))
            elif file_path:
                texts.append(f"{call.tool_name} {file_path}")
            else:
                texts.append(call.tool_name)
    return texts


def _observation_summaries(record: TraceRecord) -> list[str]:
    summaries: list[str] = []
    for step in record.steps:
        for observation in step.observations:
            text = observation.output_summary or observation.content or observation.error
            if text:
                summaries.append(_preview(text, limit=240))
    return summaries


def _candidate_kind(unit: TraceUnit) -> str | None:
    signal_names = {signal.name for signal in unit.signals}
    if "tested_successful_fix_candidate" in signal_names:
        return "bug_fix"
    return None


def _match_explanation(matched_fields: dict[str, list[str]], unit: TraceUnit) -> str:
    parts = [f"{field} matched {', '.join(terms)}" for field, terms in matched_fields.items()]
    if unit.skills:
        parts.append(f"skill.name matched {', '.join(unit.skills)}")
    signal_names = [signal.name for signal in unit.signals if bool(signal.value)]
    if signal_names:
        parts.append(f"signals matched {', '.join(signal_names)}")
    return "; ".join(parts) or "metadata filter matched"


def _packet_facets(facets: list[TraceFacet]) -> list[TraceFacet]:
    priority = {"skill.name": 0, "outcome.success": 1, "outcome.committed": 2}
    return sorted(
        facets,
        key=lambda facet: (priority.get(facet.name, 10), facet.name, str(facet.value)),
    )


def _slice_preview_for_unit(
    unit: TraceUnit,
    max_slice_nodes: int,
    index_path: Path | None,
) -> TraceMap | None:
    trace_map = get_trace_map(unit.trace_id, index_path=index_path)
    if trace_map is None:
        return None
    candidate_node = _candidate_node_for_unit(trace_map, unit)
    if candidate_node is None:
        return None
    return slice_trace_map_for_candidate(trace_map, candidate_node.node_id, max_steps=max_slice_nodes)


def _candidate_node_for_unit(trace_map: TraceMap, unit: TraceUnit) -> TraceMapNode | None:
    for node in trace_map.nodes:
        if node.unit_id == unit.unit_id or node.node_id == unit.unit_id:
            return node
    preferred = ("file_edit", "test_run", "agent_plan", "user_instruction")
    if unit.unit_type in {"trace", "test_or_error_signal"}:
        for action_type in preferred:
            node = next((n for n in trace_map.nodes if n.action_type == action_type), None)
            if node:
                return node
    return trace_map.nodes[0] if trace_map.nodes else None


def _preview(text: str, *, limit: int = 240) -> str:
    compact = " ".join(str(text).split())
    return compact[: limit - 3] + "..." if len(compact) > limit else compact
