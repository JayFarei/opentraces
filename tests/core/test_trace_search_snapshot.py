from __future__ import annotations

import json
import sqlite3 as _sqlite3
from pathlib import Path

from opentraces.core import paths
from opentraces.core.trace_search_snapshot import (
    SearchFilters,
    SearchSnapshotNeedsRebuild,
    build_trace_search_snapshot,
    default_snapshot_path,
    list_skill_invocation_units,
    list_skill_usage,
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


def test_skill_usage_lists_invocations_from_snapshot() -> None:
    review_a = _trace("trace-review-a", description="Review the search path")
    review_a.steps[1].tool_calls.insert(
        1,
        ToolCall(
            tool_call_id="trace-review-a-skill-second",
            tool_name="Skill",
            input={"name": "review"},
        ),
    )
    _write_trace("demo-project", review_a)
    _write_trace(
        "demo-project",
        _trace("trace-review-b", description="Review the API path", skill="review"),
    )
    _write_trace(
        "demo-project",
        _trace("trace-opentraces", description="Use the opentraces skill", skill="opentraces"),
    )

    build_trace_search_snapshot()
    page = list_skill_usage(SearchFilters(project="demo-project"), limit=10)

    assert page.total_skills == 2
    assert page.total_invocations == 4
    assert [(skill.skill_name, skill.invocation_count, skill.trace_count) for skill in page.skills] == [
        ("review", 3, 2),
        ("opentraces", 1, 1),
    ]
    assert page.skills[0].agents == {"pi": 3}
    assert page.skills[0].sources == {"tool_call": 3}
    assert page.skills[0].projects == {"demo-project": 3}
    assert page.diagnostics.raw_trace_scan is False
    assert page.diagnostics.wrote_to_index is False

    units = list_skill_invocation_units(skill="review", project="demo-project")
    assert len(units) == 3
    assert {unit.unit_type for unit in units} == {"skill_invocation"}
    assert {unit.metadata["snapshot_source"] for unit in units} == {
        "trace_search_snapshot.skill_invocations"
    }
    assert {unit.metadata["source"] for unit in units} == {"tool_call"}
    assert {unit.facets[0].name for unit in units} == {"agent.name"}


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
    # Branched for issue #91: the dirty-marker-not-cleared contract is
    # preserved, but the default read now SERVES the last-known-good snapshot
    # instead of dead-ending. STRICT callers (strict_freshness=True or
    # auto_rebuild=False) keep the original raise-on-stale contract.
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

    # STRICT-CONTRACT (preserved): auto_rebuild=False raises on the persistent
    # dirty marker — the snapshot is never silently mutated.
    try:
        search_traces(
            "marketing", SearchFilters(project="demo-project"), auto_rebuild=False
        )
    except SearchSnapshotNeedsRebuild as exc:
        assert exc.reason == "stale"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("auto_rebuild=False should raise on persistent dirty marker")

    # STRICT-CONTRACT (preserved): --fresh (strict_freshness=True) rebuilds once
    # to try for fresh, but a marker that survives the rebuild still raises.
    try:
        search_traces(
            "marketing",
            SearchFilters(project="demo-project"),
            strict_freshness=True,
        )
    except SearchSnapshotNeedsRebuild as exc:
        assert exc.reason == "stale"
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("strict_freshness=True should raise when freshness unprovable")

    # NEW (issue #91): the DEFAULT read serves last-known-good results with a
    # freshness warning instead of maintenance_needed.
    page = search_traces("marketing", SearchFilters(project="demo-project"))
    assert [hit.trace_id for hit in page.hits] == ["trace-site"]
    assert page.freshness is not None
    assert page.freshness["stale"] is True
    assert page.freshness["rebuild_recommended"] is True


def test_default_serves_last_known_good_on_persistent_dirty(monkeypatch) -> None:
    # Issue #91: under active capture the dirty marker is re-set during the
    # one self-heal rebuild, so the retry stays stale. The default read must
    # serve the existing valid snapshot (last-known-good) + a freshness object,
    # never maintenance_needed, and never touch the legacy index.db.
    from opentraces.core import trace_search_snapshot as snapshot
    from opentraces.core.trace_index import default_index_path

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

    page = search_traces("marketing", SearchFilters(project="demo-project"))

    assert [hit.trace_id for hit in page.hits] == ["trace-site"]
    assert page.total == 1
    assert page.freshness is not None
    assert page.freshness["stale"] is True
    assert page.freshness["stale_reason"] == "stale"
    assert page.freshness["rebuild_recommended"] is True
    assert page.freshness["dirty_token"] is not None
    assert "built_at" in page.freshness
    assert "source_hash" in page.freshness
    # Serve-stale must NOT reintroduce the #22 legacy index trap.
    assert not default_index_path().exists()


def test_freshness_object_when_fresh() -> None:
    # Issue #91: a clean (non-stale) snapshot reports freshness.stale=False
    # with built_at / source_hash provenance.
    _write_trace(
        "demo-project",
        _trace("trace-site", description="Fix the marketing site search hang"),
    )
    build_trace_search_snapshot()
    assert current_dirty_token() is None

    page = search_traces("marketing", SearchFilters(project="demo-project"))

    assert [hit.trace_id for hit in page.hits] == ["trace-site"]
    assert page.freshness is not None
    assert page.freshness["stale"] is False
    assert page.freshness.get("built_at")
    assert page.freshness.get("source_hash")


def test_allow_stale_still_raises_unservable(tmp_path) -> None:
    # Issue #91 (codex finding #4): allow_stale must bypass ONLY the dirty
    # marker check — a corrupt/missing/wrong-schema snapshot still raises.
    from opentraces.core import trace_search_snapshot as snapshot

    def _ro(db_path):
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row  # production uses _connect_readonly (Row)
        return conn

    # unreadable: a DB with no snapshot_meta table at all.
    unreadable = tmp_path / "unreadable.db"
    with _sqlite3.connect(unreadable) as conn:
        conn.execute("create table other(x)")
    with _ro(unreadable) as conn:
        try:
            snapshot._verify_snapshot(conn, unreadable, allow_stale=True)
        except SearchSnapshotNeedsRebuild as exc:
            assert exc.reason == "unreadable"
        else:  # pragma: no cover
            raise AssertionError("allow_stale must still raise for unreadable")

    # missing_schema: snapshot_meta exists but has no schema_version row.
    missing = tmp_path / "missing_schema.db"
    with _sqlite3.connect(missing) as conn:
        conn.execute("create table snapshot_meta(key text primary key, value text)")
    with _ro(missing) as conn:
        try:
            snapshot._verify_snapshot(conn, missing, allow_stale=True)
        except SearchSnapshotNeedsRebuild as exc:
            assert exc.reason == "missing_schema"
        else:  # pragma: no cover
            raise AssertionError("allow_stale must still raise for missing_schema")

    # wrong_schema: a schema_version that is not the current one.
    wrong = tmp_path / "wrong_schema.db"
    with _sqlite3.connect(wrong) as conn:
        conn.execute("create table snapshot_meta(key text primary key, value text)")
        conn.execute(
            "insert into snapshot_meta(key, value) values ('schema_version', '0.0.0-not-real')"
        )
    with _ro(wrong) as conn:
        try:
            snapshot._verify_snapshot(conn, wrong, allow_stale=True)
        except SearchSnapshotNeedsRebuild as exc:
            assert exc.reason == "wrong_schema"
        else:  # pragma: no cover
            raise AssertionError("allow_stale must still raise for wrong_schema")


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
def test_schema_version_is_v5_for_skill_invocation_rows() -> None:
    from opentraces.core.trace_search_snapshot import SNAPSHOT_SCHEMA_VERSION

    _write_trace("demo-project", _trace("trace-v", description="Schema bump check"))
    summary = build_trace_search_snapshot()
    assert summary.schema_version == SNAPSHOT_SCHEMA_VERSION == "opentraces.trace_search_snapshot.v5"


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


# --------------------------------------------------------------------------- #
# issue #41: external-content FTS5 delete bookkeeping in the per-row refresh
#
# The keep-warm refresh deletes content rows but the FTS5 index is
# external-content (``content='traces'``), so it is NOT auto-maintained on
# delete. Without an explicit FTS ``'delete'`` carrying the OLD column values,
# the index accumulates orphaned postings and silently desyncs / corrupts over
# repeated refreshes. These tests prove the bookkeeping keeps the index in
# lockstep with the content table across a long interleaved refresh sequence,
# matches a from-scratch rebuild exactly, and that skipping the bookkeeping is
# detectable by the FTS5 ``'integrity-check'`` command (the real guard).
def _fts_integrity_ok(snapshot_path: Path) -> bool:
    """Run the FTS5 ``integrity-check`` against a snapshot DB.

    ``integrity-check`` (SQLite >= 3.42) re-derives the index from the content
    table and raises ``SQLITE_CORRUPT`` if the stored FTS index disagrees — the
    canonical detector for external-content desync. Returns ``True`` when the
    index is consistent, ``False`` when the check raises a corruption error.
    """

    with _sqlite3.connect(snapshot_path) as conn:
        try:
            conn.execute("insert into trace_fts(trace_fts, rank) values('integrity-check', 1)")
        except _sqlite3.DatabaseError:
            return False
    return True


def _probe_rows(snapshot_path: Path, term: str) -> list[tuple[str, float]]:
    """Deterministic (trace_id, bm25) probe straight against trace_fts.

    Bypasses the dedup / partition layer so the result is a pure read of the
    FTS index contents — exactly what desync would corrupt. Ordered by
    (bm25, trace_id) so the comparison is stable.
    """

    with _sqlite3.connect(snapshot_path) as conn:
        rows = conn.execute(
            "select traces.trace_id, bm25(trace_fts) "
            "from trace_fts join traces on traces.rowid = trace_fts.rowid "
            "where trace_fts match ? "
            "order by bm25(trace_fts), traces.trace_id",
            (term,),
        ).fetchall()
    return [(str(tid), round(float(score), 6)) for tid, score in rows]


def test_refresh_fts_bookkeeping_keeps_index_consistent_under_interleaving(tmp_path) -> None:
    """N~80 corpus, ~60 interleaved per-row refreshes; FTS stays consistent.

    Mixes text-changing updates, deletes, and re-adds (3 updates + 1 delete per
    refresh) and then asserts: (a) the FTS ``integrity-check`` passes, (b) the
    raw FTS probe rows + bm25 scores are IDENTICAL to a from-scratch
    ``build_trace_search_snapshot`` of the same alive set, and (c) the snapshot
    source_hash converges with the full rebuild.
    """
    from opentraces.core.trace_search_snapshot import (
        default_snapshot_path,
        refresh_trace_search_snapshot,
    )

    # Distinctive shared term so the probe always has matchable content.
    corpus = {f"trace-{idx:03d}": f"importer pass {idx}" for idx in range(80)}
    for tid, desc in corpus.items():
        _write_trace("demo-project", _trace(tid, description=desc))

    build_trace_search_snapshot()
    snapshot_path = default_snapshot_path()
    assert _fts_integrity_ok(snapshot_path)

    alive = dict(corpus)
    # 15 rounds × (3 text-updates + 1 delete) + occasional re-add = ~60 refreshes.
    for round_idx in range(15):
        ids = sorted(alive)
        # 3 text-changing updates (re-rendered with new description text).
        changed: list[str] = []
        for offset in range(3):
            tid = ids[(round_idx * 7 + offset) % len(ids)]
            new_desc = f"importer pass {tid} rev {round_idx} {'fix' if offset else 'review'}"
            alive[tid] = new_desc
            _write_trace("demo-project", _trace(tid, description=new_desc))
            refresh_trace_search_snapshot([tid], [])
            changed.append(tid)
        # 1 delete (remove from disk + drop from the snapshot).
        del_id = ids[(round_idx * 11) % len(ids)]
        if del_id in alive and del_id not in changed:
            (paths.PROJECTS_DIR / "demo-project" / "traces" / f"{del_id}.jsonl").unlink()
            del alive[del_id]
            refresh_trace_search_snapshot([], [del_id])
        # Every 4th round, re-add a previously deleted id (re-add path).
        if round_idx % 4 == 3:
            re_id = f"trace-9{round_idx:02d}"
            re_desc = f"importer pass {re_id} re-added"
            alive[re_id] = re_desc
            _write_trace("demo-project", _trace(re_id, description=re_desc))
            refresh_trace_search_snapshot([re_id], [])

    # (a) FTS index is internally consistent with the content table.
    assert _fts_integrity_ok(snapshot_path)

    # (b) Raw FTS probe rows + bm25 scores match a from-scratch build of the
    # SAME alive set exactly. Build the reference snapshot at a separate path so
    # the live serving snapshot is untouched by the comparison build.
    reference_path = tmp_path / "reference.sqlite"
    build_trace_search_snapshot(path=reference_path)
    for term in ("importer", "fix", "review", "added"):
        assert _probe_rows(snapshot_path, term) == _probe_rows(reference_path, term), (
            f"FTS desync on term {term!r}: refreshed index disagrees with rebuild"
        )

    # (c) Order-independent corpus hash converges between refresh + rebuild.
    rebuilt = build_trace_search_snapshot()
    refreshed_status = snapshot_status()
    assert refreshed_status["source_hash"] == rebuilt.source_hash


def test_refresh_without_fts_bookkeeping_corrupts_index_negative_control(monkeypatch) -> None:
    """Negative control: skipping the FTS ``'delete'`` makes integrity-check fail.

    Patches ``_fts_delete_doc`` to a no-op (and removes the whole-table rebuild,
    which is already gone) so a text-changing refresh deletes+reinserts the
    content row but leaves the OLD FTS postings orphaned. ``integrity-check``
    must then raise — proving the bookkeeping is the load-bearing fix and the
    test genuinely fails without it.
    """
    from opentraces.core import trace_search_snapshot as tss
    from opentraces.core.trace_search_snapshot import (
        default_snapshot_path,
        refresh_trace_search_snapshot,
    )

    for idx in range(10):
        _write_trace(
            "demo-project",
            _trace(f"trace-neg-{idx:02d}", description=f"importer pass {idx}"),
        )
    build_trace_search_snapshot()
    snapshot_path = default_snapshot_path()
    assert _fts_integrity_ok(snapshot_path)

    # Disable the bookkeeping: a text change now orphans the OLD postings.
    monkeypatch.setattr(tss, "_fts_delete_doc", lambda conn, trace_id: None)

    for idx in range(5):
        tid = f"trace-neg-{idx:02d}"
        new_desc = f"importer pass {idx} mutated body {idx}"
        _write_trace("demo-project", _trace(tid, description=new_desc))
        refresh_trace_search_snapshot([tid], [])

    # The external-content FTS index is now out of sync with the content table.
    assert not _fts_integrity_ok(snapshot_path), (
        "negative control failed: skipping FTS bookkeeping should corrupt the index"
    )


def test_refresh_of_one_issues_no_whole_table_rebuild() -> None:
    """Perf guard: a refresh-of-1 must NOT issue ``values('rebuild')`` (issue #41).

    Counts the FTS ``'rebuild'`` command via a SQL trace hook (not wall-clock):
    the old per-refresh whole-table rebuild was O(corpus); the per-row delete
    bookkeeping replaces it, so refreshing one changed trace must touch only the
    changed rows. Also asserts the corresponding per-row ``'delete'`` IS issued.
    """
    from opentraces.core.trace_search_snapshot import (
        _refresh_snapshot_locked,
        default_snapshot_path,
    )

    for idx in range(20):
        _write_trace(
            "demo-project",
            _trace(f"trace-perf-{idx:02d}", description=f"importer pass {idx}"),
        )
    build_trace_search_snapshot()
    snapshot_path = default_snapshot_path()

    target = "trace-perf-05"
    _write_trace("demo-project", _trace(target, description="importer pass 5 mutated"))

    statements: list[str] = []

    def _trace_hook(statement: str) -> None:
        statements.append(statement)

    # Patch sqlite3.connect inside the module to install a trace hook on the
    # refresh connection only.
    import sqlite3 as _sqlite_mod
    from opentraces.core import trace_search_snapshot as tss

    real_connect = _sqlite_mod.connect

    def _connect_with_trace(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(_trace_hook)
        return conn

    orig = tss.sqlite3.connect
    tss.sqlite3.connect = _connect_with_trace  # type: ignore[assignment]
    try:
        _refresh_snapshot_locked(snapshot_path, [target], [target])
    finally:
        tss.sqlite3.connect = orig  # type: ignore[assignment]

    joined = " ".join(statements).lower()
    assert "values('rebuild')" not in joined.replace(" ", ""), (
        "refresh-of-1 must not issue a whole-table FTS rebuild"
    )
    # The paired per-row FTS delete IS present (delete+reinsert for the change).
    assert "'delete'" in joined, "refresh must issue the per-row FTS delete bookkeeping"


def test_refresh_clears_unchanged_dirty_marker_after_bookkeeping_edit() -> None:
    """PR #38 ``clear_dirty_marker_if_unchanged`` survives the issue #41 edit.

    A refresh whose dirty token is unchanged across the commit-then-replace must
    still clear the marker (the keep-warm convergence contract). This guards the
    token capture/clear ordering after the per-row bookkeeping was inserted.
    """
    from opentraces.core.trace_search_snapshot import (
        refresh_trace_search_snapshot,
    )
    from opentraces.core.trace_search_state import (
        current_dirty_token,
        mark_search_snapshot_dirty,
    )

    _write_trace("demo-project", _trace("trace-warm", description="initial importer pass"))
    build_trace_search_snapshot()
    assert current_dirty_token() is None

    # A new capture marks the snapshot dirty (the keep-warm precondition).
    _write_trace("demo-project", _trace("trace-warm-2", description="freshly captured pass"))
    mark_search_snapshot_dirty("capture", trace_id="trace-warm-2")
    assert current_dirty_token() is not None

    refreshed = refresh_trace_search_snapshot(["trace-warm-2"], [])
    assert refreshed is not None
    # The unchanged token was cleared after the atomic replace (PR #38 behavior).
    assert current_dirty_token() is None

    page = search_traces("freshly captured", SearchFilters(), limit=5)
    assert [hit.trace_id for hit in page.hits] == ["trace-warm-2"]
