from __future__ import annotations

import json
from pathlib import Path

from opentraces.core import paths
from opentraces.core.trace_search_snapshot import (
    SearchFilters,
    SearchSnapshotNeedsRebuild,
    build_trace_search_snapshot,
    default_snapshot_path,
    search_traces,
    snapshot_status,
)
from opentraces.core.trace_search_state import current_dirty_token, mark_search_snapshot_dirty
from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


def _write_trace(slug: str, record: TraceRecord) -> Path:
    trace_dir = paths.PROJECTS_DIR / slug / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / f"{record.trace_id}.jsonl"
    path.write_text(record.model_dump_json() + "\n")
    return path


def _trace(
    trace_id: str,
    *,
    description: str,
    file_path: str = "web/site/src/app.tsx",
    skill: str = "review",
    tool: str = "Edit",
    timestamp: str = "2026-06-09T09:00:00Z",
) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="pi", model="anthropic/claude-opus-4-6"),
        task={"description": description},
        timestamp_start=timestamp,
        timestamp_end=timestamp,
        steps=[
            Step(step_index=1, role="user", content=description),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id=f"{trace_id}-skill",
                        tool_name="Skill",
                        input={"name": skill},
                    ),
                    ToolCall(
                        tool_call_id=f"{trace_id}-tool",
                        tool_name=tool,
                        input={
                            "file_path": file_path,
                            "old_string": "old",
                            "new_string": "new",
                        },
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id=f"{trace_id}-tool",
                        content="site test passed",
                        output_summary="site test passed",
                    )
                ],
            ),
        ],
        outcome={"success": True, "committed": True},
        dependencies=["pytest"],
    )


def test_snapshot_builds_trace_level_docs_and_searches_read_only() -> None:
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    _write_trace(
        "demo-project",
        _trace("trace-api", description="Tune the API importer"),
    )

    summary = build_trace_search_snapshot()

    assert summary.path == default_snapshot_path()
    assert summary.trace_count == 2
    assert not summary.path.with_name(summary.path.name + "-wal").exists()
    assert snapshot_status()["state"] == "ok"

    page = search_traces(
        "marketing",
        SearchFilters(project="demo-project", skill="review", tool="Edit", file_kind="tsx"),
        limit=3,
    )

    assert [hit.trace_id for hit in page.hits] == ["trace-site"]
    assert page.diagnostics.as_dict() == {
        "used_search_snapshot": True,
        "used_fts": True,
        "rows_examined": 1,
        "hits_returned": 1,
        "hydrated_count": 0,
        "raw_trace_scan": False,
        "wrote_to_index": False,
        "rebuilt_index": False,
        "python_full_corpus_sort": False,
    }


def test_snapshot_db_does_not_store_full_trace_payload() -> None:
    record = _trace(
        "trace-bounded",
        description="Investigate site search",
    )
    record.steps[1].observations[0].content = "x" * 50_000
    _write_trace("demo-project", record)

    summary = build_trace_search_snapshot()

    raw = summary.path.read_bytes()
    assert b"x" * 5000 not in raw
    assert json.loads((paths.PROJECTS_DIR / "demo-project" / "traces" / "trace-bounded.jsonl").read_text())


def test_stale_snapshot_requires_explicit_rebuild() -> None:
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    build_trace_search_snapshot()

    mark_search_snapshot_dirty("test", trace_id="trace-site")

    try:
        search_traces("marketing", SearchFilters(project="demo-project"))
    except SearchSnapshotNeedsRebuild as exc:
        assert exc.reason == "stale"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("stale snapshot should require rebuild")
    assert snapshot_status()["state"] == "stale"

    build_trace_search_snapshot()
    assert current_dirty_token() is None
    page = search_traces("marketing", SearchFilters(project="demo-project"))
    assert [hit.trace_id for hit in page.hits] == ["trace-site"]


def test_rebuild_does_not_clear_dirty_marker_created_during_build(monkeypatch) -> None:
    from opentraces.core import trace_search_snapshot as snapshot

    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    real_iter_documents = snapshot._iter_documents

    def dirty_during_build():
        for doc in real_iter_documents():
            mark_search_snapshot_dirty("during-build", trace_id=doc.trace_id)
            yield doc

    monkeypatch.setattr(snapshot, "_iter_documents", dirty_during_build)

    build_trace_search_snapshot()

    assert current_dirty_token() is not None
    try:
        search_traces("marketing", SearchFilters(project="demo-project"))
    except SearchSnapshotNeedsRebuild as exc:
        assert exc.reason == "stale"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("concurrent dirty marker should keep snapshot stale")


def test_semantic_query_requires_indexed_concept() -> None:
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    build_trace_search_snapshot()

    try:
        search_traces("unindexed idea", SearchFilters(project="demo-project"), semantic="unindexed idea")
    except SearchSnapshotNeedsRebuild as exc:
        assert exc.reason == "semantic_index_missing"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("semantic search should require indexed concepts")


def test_semantic_query_uses_indexed_concept_table() -> None:
    mongo = _trace(
        "trace-mongo",
        description="Patch database client setup",
        file_path="src/db.py",
    )
    mongo.dependencies = ["pymongo"]
    _write_trace("demo-project", mongo)
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    build_trace_search_snapshot()

    page = search_traces(
        None,
        SearchFilters(project="demo-project"),
        semantic="mongodb",
        limit=3,
    )

    assert [hit.trace_id for hit in page.hits] == ["trace-mongo"]
    assert page.diagnostics.used_search_snapshot is True
    assert page.diagnostics.used_fts is False
    assert page.diagnostics.raw_trace_scan is False
    assert page.diagnostics.python_full_corpus_sort is False


def test_discover_hydrates_only_top_search_hits(monkeypatch) -> None:
    from opentraces.core import discovery

    _write_trace(
        "demo-project",
        _trace("trace-site-1", description="Fix the site search ranking"),
    )
    _write_trace(
        "demo-project",
        _trace("trace-site-2", description="Review the site search filters"),
    )
    _write_trace(
        "demo-project",
        _trace("trace-api", description="Tune the API importer"),
    )
    build_trace_search_snapshot()

    hydrated: list[str] = []
    real_candidate_card = discovery.candidate_card

    def counted_candidate_card(ref: str, **kwargs):
        hydrated.append(ref)
        return real_candidate_card(ref, **kwargs)

    monkeypatch.setattr(discovery, "candidate_card", counted_candidate_card)

    packet = discovery.discover("site", limit=1, per_group=1, project="demo-project")

    assert packet.total_candidates == 1
    assert packet.total_cards == 1
    assert packet.search_diagnostics["used_search_snapshot"] is True
    assert packet.search_diagnostics["raw_trace_scan"] is False
    assert packet.search_diagnostics["wrote_to_index"] is False
    assert packet.search_diagnostics["rebuilt_index"] is False
    assert packet.search_diagnostics["python_full_corpus_sort"] is False
    assert packet.search_diagnostics["hydrated_count"] == 1
    assert len(hydrated) == 1
    assert hydrated[0] in {"trace-site-1", "trace-site-2"}


def test_snapshot_orders_in_sql_for_time_and_recency() -> None:
    _write_trace(
        "demo-project",
        _trace(
            "trace-older",
            description="Fix the site search ranking",
            timestamp="2026-06-08T09:00:00Z",
        ),
    )
    _write_trace(
        "demo-project",
        _trace(
            "trace-newer",
            description="Fix the site search filters",
            timestamp="2026-06-09T09:00:00Z",
        ),
    )
    build_trace_search_snapshot()

    oldest_first = search_traces(
        "site",
        SearchFilters(project="demo-project", sort_order="time"),
        limit=2,
    )
    newest_first = search_traces(
        "site",
        SearchFilters(project="demo-project", sort_order="recency"),
        limit=2,
    )

    assert [hit.trace_id for hit in oldest_first.hits] == ["trace-older", "trace-newer"]
    assert [hit.trace_id for hit in newest_first.hits] == ["trace-newer", "trace-older"]


def test_query_packets_cap_visible_files_but_keep_file_filters() -> None:
    from opentraces.core.trace_search_snapshot import candidate_packet_for_hit

    file_paths = [f"src/module_{idx}.py" for idx in range(12)]
    record = _trace(
        "trace-many-files",
        description="Fix the trace capsule file payload",
        file_path=file_paths[0],
    )
    for idx, path in enumerate(file_paths[1:], start=3):
        record.steps.append(
            Step(
                step_index=idx,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id=f"trace-many-files-tool-{idx}",
                        tool_name="Edit",
                        input={
                            "file_path": path,
                            "old_string": "old",
                            "new_string": "new",
                        },
                    )
                ],
            )
        )
    _write_trace("demo-project", record)
    build_trace_search_snapshot()

    page = search_traces("capsule", SearchFilters(project="demo-project"), limit=1)
    packet, hydrated = candidate_packet_for_hit(page.hits[0])
    hidden_path = file_paths[-1]
    filtered = search_traces(
        "capsule",
        SearchFilters(project="demo-project", files=hidden_path),
        limit=1,
    )

    assert hydrated == 0
    assert len(packet.files) == 8
    assert hidden_path not in packet.files
    assert packet.metadata["file_count"] == len(file_paths)
    assert packet.metadata["files_truncated"] is True
    assert [hit.trace_id for hit in filtered.hits] == ["trace-many-files"]


def test_search_collapses_duplicate_titles_in_sql() -> None:
    _write_trace(
        "demo-project",
        _trace(
            "trace-dup-old",
            description="Trace capsule duplicate handoff",
            timestamp="2026-06-08T09:00:00Z",
        ),
    )
    _write_trace(
        "demo-project",
        _trace(
            "trace-dup-new",
            description="Trace capsule duplicate handoff",
            timestamp="2026-06-09T09:00:00Z",
        ),
    )
    _write_trace(
        "demo-project",
        _trace(
            "trace-distinct",
            description="Trace capsule distinct feature review",
            timestamp="2026-06-07T09:00:00Z",
        ),
    )
    build_trace_search_snapshot()

    page = search_traces("trace capsule", SearchFilters(project="demo-project"), limit=10)

    assert [hit.trace_id for hit in page.hits] == ["trace-dup-new", "trace-distinct"]
    assert page.total == 2
    assert page.diagnostics.python_full_corpus_sort is False
