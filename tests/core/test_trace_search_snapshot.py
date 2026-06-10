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
    # With auto_rebuild disabled, a stale snapshot still raises (issue #30
    # leaves the strict raise-on-needs-rebuild path intact behind the flag).
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    build_trace_search_snapshot()

    mark_search_snapshot_dirty("test", trace_id="trace-site")

    try:
        search_traces(
            "marketing", SearchFilters(project="demo-project"), auto_rebuild=False
        )
    except SearchSnapshotNeedsRebuild as exc:
        assert exc.reason == "stale"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("stale snapshot should require rebuild")
    assert snapshot_status()["state"] == "stale"

    build_trace_search_snapshot()
    assert current_dirty_token() is None
    page = search_traces(
        "marketing", SearchFilters(project="demo-project"), auto_rebuild=False
    )
    assert [hit.trace_id for hit in page.hits] == ["trace-site"]


def test_search_bootstraps_snapshot_when_missing() -> None:
    # Issue #30: search_traces with no snapshot self-heals once (compact build)
    # and serves the hit, reporting rebuilt_index=True.
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    assert not default_snapshot_path().exists()

    page = search_traces("marketing", SearchFilters(project="demo-project"), limit=3)

    assert [hit.trace_id for hit in page.hits] == ["trace-site"]
    assert page.diagnostics.rebuilt_index is True
    assert page.diagnostics.wrote_to_index is True
    assert page.diagnostics.raw_trace_scan is False
    assert default_snapshot_path().exists()

    # Steady state: a second query finds the snapshot and does not rebuild.
    page2 = search_traces("marketing", SearchFilters(project="demo-project"), limit=3)
    assert [hit.trace_id for hit in page2.hits] == ["trace-site"]
    assert page2.diagnostics.rebuilt_index is False


def test_search_missing_snapshot_raises_when_auto_rebuild_disabled() -> None:
    # Issue #30 contract: auto_rebuild=False keeps the strict raise on a
    # missing snapshot path.
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    assert not default_snapshot_path().exists()

    try:
        search_traces(
            "marketing", SearchFilters(project="demo-project"), auto_rebuild=False
        )
    except SearchSnapshotNeedsRebuild as exc:
        assert exc.reason == "missing"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("missing snapshot should raise with auto_rebuild=False")


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


def test_semantic_query_without_lexicon_concept_returns_empty_not_maintenance() -> None:
    """An unmatched semantic term is an empty result, never a rebuild loop.

    The concept lexicon is deterministic and in-code: no rebuild can make an
    unknown term match, so advising ``trace index`` would be an unresolvable
    maintenance_needed dead-end.
    """
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    build_trace_search_snapshot()

    page = search_traces(
        "unindexed idea", SearchFilters(project="demo-project"), semantic="unindexed idea"
    )
    assert page.hits == []
    assert page.total == 0
    assert page.next_page_token is None


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


# --------------------------------------------------------------------------- #
# review fixes: facet binding, layer union, incremental keep-warm refresh
# --------------------------------------------------------------------------- #
def test_facet_filter_name_binds_as_parameter_not_identifier() -> None:
    """A facet whose name collides with a column (``project_slug``) must match.

    Interpolating the name with double quotes made SQLite resolve it as the
    ``traces.project_slug`` COLUMN, silently returning zero rows.
    """
    _write_trace("demo-project", _trace("trace-facet", description="Facet binding probe"))
    build_trace_search_snapshot()

    page = search_traces(
        None,
        SearchFilters(facet_filters=("project_slug=demo-project",)),
        limit=5,
    )
    assert [hit.trace_id for hit in page.hits] == ["trace-facet"]

    quoted = search_traces(
        None,
        SearchFilters(facet_filters=('we"ird=value',)),
        limit=5,
    )
    assert quoted.hits == []


def test_unknown_success_filter_binds_without_placeholder_drift() -> None:
    """``--unknown-success`` previously emitted a ``?`` with no bound value."""
    record = _trace("trace-unknown-outcome", description="Outcome unknown probe")
    record.outcome.success = None
    _write_trace("demo-project", record)
    _write_trace("demo-project", _trace("trace-known", description="Outcome known probe"))
    build_trace_search_snapshot()

    page = search_traces(None, SearchFilters(success_unknown=True), limit=5)
    assert [hit.trace_id for hit in page.hits] == ["trace-unknown-outcome"]

    # Outcome.committed is non-nullable in the schema, so unknown-committed
    # matches nothing — base parity (the projection matcher behaved the same).
    committed_page = search_traces(None, SearchFilters(committed_unknown=True), limit=5)
    assert committed_page.hits == []


def test_rebuild_unions_bucket_and_legacy_layers() -> None:
    """A legacy-store trace stays searchable even when bucket records exist."""
    from opentraces.core.bucket_store import write_trace_record

    write_trace_record(
        _trace("trace-bucket", description="Bucket layer probe"),
        project_slug="demo-project",
        source_layer="canonical",
        legacy_mirror=False,
    )
    _write_trace("demo-project", _trace("trace-legacy-only", description="Legacy layer probe"))
    build_trace_search_snapshot()

    page = search_traces(None, SearchFilters(), limit=10)
    assert {hit.trace_id for hit in page.hits} == {"trace-bucket", "trace-legacy-only"}


def test_keep_warm_refreshes_snapshot_incrementally() -> None:
    """A captured trace converges the snapshot via keep-warm — no explicit rebuild."""
    from opentraces.core.trace_index import keep_index_warm, refresh_index

    _write_trace("demo-project", _trace("trace-first", description="Initial corpus entry"))
    # A capturing machine has a live legacy index + sync markers; cheap-sync
    # deliberately no-ops when the legacy DB is missing.
    refresh_index()
    keep_index_warm(query_sources=("index",))
    build_trace_search_snapshot()
    assert current_dirty_token() is None

    # Capture shape: the record lands in the canonical project store; the
    # post-ingest hook then passes the single trace it just wrote (plan 087
    # F3) and the sync mirrors it into the bucket (marking the snapshot
    # dirty) before the snapshot refresh converges it back to clean.
    _write_trace("demo-project", _trace("trace-fresh", description="Freshly captured bucket trace"))

    result = keep_index_warm(trace_id="trace-fresh", query_sources=("index",))
    assert result.ok
    assert result.snapshot_refreshed is True
    assert current_dirty_token() is None

    page = search_traces("freshly captured", SearchFilters(), limit=5)
    assert [hit.trace_id for hit in page.hits] == ["trace-fresh"]

    snapshot_path = default_snapshot_path()
    assert not snapshot_path.with_name(f"{snapshot_path.name}-wal").exists()
    assert not snapshot_path.with_name(f"{snapshot_path.name}-shm").exists()


def test_incremental_refresh_removes_deleted_traces_and_converges_hash() -> None:
    """Refresh drops removed traces; hash matches a from-scratch rebuild."""
    from opentraces.core.trace_search_snapshot import refresh_trace_search_snapshot

    keep = _write_trace("demo-project", _trace("trace-keep", description="Keeper entry"))
    gone_path = _write_trace("demo-project", _trace("trace-gone", description="Removed entry"))
    del keep
    summary = build_trace_search_snapshot()
    assert summary.trace_count == 2

    gone_path.unlink()
    refreshed = refresh_trace_search_snapshot([], ["trace-gone"])
    assert refreshed is not None
    assert refreshed.trace_count == 1

    page = search_traces(None, SearchFilters(), limit=5)
    assert [hit.trace_id for hit in page.hits] == ["trace-keep"]

    rebuilt = build_trace_search_snapshot()
    assert rebuilt.source_hash == refreshed.source_hash


def test_refresh_returns_none_without_snapshot() -> None:
    from opentraces.core.trace_search_snapshot import refresh_trace_search_snapshot

    assert refresh_trace_search_snapshot(["trace-x"], []) is None


# --------------------------------------------------------------------------- #
# issue #27 B: non-ASCII titles must not collapse into one dedup group
# --------------------------------------------------------------------------- #
def test_distinct_non_ascii_titles_both_surface() -> None:
    # Two traces with DISTINCT CJK / Cyrillic titles must both appear: before
    # the fix, the ASCII-stripped group key was "__empty__" for both, so the
    # SQL row_number() partition returned only one of them per query.
    _write_trace(
        "demo-project",
        _trace("trace-cjk", description="検索のバグを修正する"),
    )
    _write_trace(
        "demo-project",
        _trace("trace-cyr", description="Исправить поиск"),
    )
    build_trace_search_snapshot()

    page = search_traces(None, SearchFilters(project="demo-project"), limit=10)

    assert {hit.trace_id for hit in page.hits} == {"trace-cjk", "trace-cyr"}
    assert page.total == 2


def test_identical_non_ascii_titles_still_dedup() -> None:
    # True duplicates (same normalized non-ASCII title) must still collapse to
    # one surviving generation, keeping the dedup contract intact.
    _write_trace(
        "demo-project",
        _trace(
            "trace-dup-old",
            description="検索のバグを修正する",
            timestamp="2026-06-08T09:00:00Z",
        ),
    )
    _write_trace(
        "demo-project",
        _trace(
            "trace-dup-new",
            description="検索のバグを修正する",
            timestamp="2026-06-09T09:00:00Z",
        ),
    )
    build_trace_search_snapshot()

    page = search_traces(None, SearchFilters(project="demo-project"), limit=10)

    assert [hit.trace_id for hit in page.hits] == ["trace-dup-new"]
    assert page.total == 1


# --------------------------------------------------------------------------- #
# issue #27 C: --since must compare on a UTC-normalized timestamp
# --------------------------------------------------------------------------- #
def test_since_normalizes_offset_timestamps_to_utc() -> None:
    # 10:00:00+02:00 == 08:00:00Z. A raw-string compare against a Z-normalized
    # bound puts it on the wrong side of the boundary; build-time UTC
    # normalization fixes that.
    _write_trace(
        "demo-project",
        _trace(
            "trace-offset",
            description="Fix the offset boundary case",
            timestamp="2026-06-09T10:00:00+02:00",
        ),
    )
    build_trace_search_snapshot()

    excluded = search_traces(
        None,
        SearchFilters(project="demo-project", since="2026-06-09T09:00:00Z"),
        limit=10,
    )
    assert [hit.trace_id for hit in excluded.hits] == []

    included = search_traces(
        None,
        SearchFilters(project="demo-project", since="2026-06-09T07:00:00Z"),
        limit=10,
    )
    assert [hit.trace_id for hit in included.hits] == ["trace-offset"]


# --------------------------------------------------------------------------- #
# issue #27 D: inverse superseded pointer + surfacing older generations
# --------------------------------------------------------------------------- #
def _trace_with_metadata(trace_id: str, *, description: str, metadata: dict, timestamp: str):
    record = _trace(trace_id, description=description, timestamp=timestamp)
    record.metadata.update(metadata)
    return record


def test_inverse_superseded_pointer_demotes_older_generation() -> None:
    # The newer trace declares it replaces the older via the inverse
    # ``superseded_trace_ids`` pointer (the forward ``superseded_by`` marker is
    # absent on the older one). Default latest-only search must drop the older.
    _write_trace(
        "demo-project",
        _trace(
            "trace-gen-old",
            description="Auth flow rewrite first pass",
            timestamp="2026-06-08T09:00:00Z",
        ),
    )
    _write_trace(
        "demo-project",
        _trace_with_metadata(
            "trace-gen-new",
            description="Auth flow rewrite second pass",
            metadata={"superseded_trace_ids": ["trace-gen-old"]},
            timestamp="2026-06-09T09:00:00Z",
        ),
    )
    build_trace_search_snapshot()

    latest = search_traces(None, SearchFilters(project="demo-project"), limit=10)
    assert {hit.trace_id for hit in latest.hits} == {"trace-gen-new"}


def test_include_superseded_surfaces_equal_title_older_generation() -> None:
    # Older + newer generation share a title. With --include-superseded
    # (latest_generation=False) BOTH must surface; before the fix the title
    # dedup partition collapsed them before generation could distinguish them.
    _write_trace(
        "demo-project",
        _trace(
            "trace-eq-old",
            description="Stabilize the importer",
            timestamp="2026-06-08T09:00:00Z",
        ),
    )
    _write_trace(
        "demo-project",
        _trace_with_metadata(
            "trace-eq-new",
            description="Stabilize the importer",
            metadata={"superseded_trace_ids": ["trace-eq-old"]},
            timestamp="2026-06-09T09:00:00Z",
        ),
    )
    build_trace_search_snapshot()

    latest = search_traces(None, SearchFilters(project="demo-project"), limit=10)
    assert {hit.trace_id for hit in latest.hits} == {"trace-eq-new"}

    both = search_traces(
        None,
        SearchFilters(project="demo-project", latest_generation=False),
        limit=10,
    )
    assert {hit.trace_id for hit in both.hits} == {"trace-eq-old", "trace-eq-new"}
    assert both.total == 2


# --------------------------------------------------------------------------- #
# issue #27: a schema version bump auto-rebuilds existing snapshots once
# --------------------------------------------------------------------------- #
def test_schema_version_is_v4_for_corrected_documents() -> None:
    from opentraces.core.trace_search_snapshot import SNAPSHOT_SCHEMA_VERSION

    _write_trace("demo-project", _trace("trace-v", description="Schema bump check"))
    summary = build_trace_search_snapshot()
    assert summary.schema_version == SNAPSHOT_SCHEMA_VERSION == "opentraces.trace_search_snapshot.v4"


def test_old_schema_snapshot_auto_rebuilds_exactly_once() -> None:
    # An existing snapshot stamped with a prior schema version is treated as
    # needs-rebuild; auto_rebuild self-heals once and serves rc=0 results.
    import sqlite3

    _write_trace("demo-project", _trace("trace-old-schema", description="needs rebuild"))
    build_trace_search_snapshot()
    snap = default_snapshot_path()
    with sqlite3.connect(snap) as conn:
        conn.execute(
            "update snapshot_meta set value = ? where key = 'schema_version'",
            ("opentraces.trace_search_snapshot.v3",),
        )
        conn.commit()
    # A prior schema version is a needs-rebuild condition (issue #30 self-heal).
    assert snapshot_status()["state"] == "wrong_schema"

    page = search_traces(None, SearchFilters(project="demo-project"), limit=10)
    assert [hit.trace_id for hit in page.hits] == ["trace-old-schema"]
    assert page.diagnostics.rebuilt_index is True

    page2 = search_traces(None, SearchFilters(project="demo-project"), limit=10)
    assert page2.diagnostics.rebuilt_index is False


def test_notify_rebuilding_writes_to_stderr_not_stdout(capsys, monkeypatch) -> None:
    # M-half: the one-time rebuild notice must never touch stdout (JSON
    # contract) and only emits when stderr is interactive.
    import sys

    from opentraces.core import trace_search_snapshot as tss

    monkeypatch.setattr(sys.stderr, "isatty", lambda: True, raising=False)
    tss._notify_rebuilding()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "rebuilding search snapshot" in captured.err

    # Non-interactive stderr stays silent so piped / CliRunner JSON is clean.
    monkeypatch.setattr(sys.stderr, "isatty", lambda: False, raising=False)
    tss._notify_rebuilding()
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
