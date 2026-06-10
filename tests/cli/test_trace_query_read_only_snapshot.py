from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core import paths
from opentraces.core.trace_search_snapshot import build_trace_search_snapshot, default_snapshot_path
from opentraces.core.trace_search_state import mark_search_snapshot_dirty
from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


def _write_trace(slug: str, record: TraceRecord) -> None:
    trace_dir = paths.PROJECTS_DIR / slug / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _trace(trace_id: str, text: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="pi", model="anthropic/claude-opus-4-6"),
        task={"description": text},
        timestamp_start="2026-06-09T09:00:00Z",
        timestamp_end="2026-06-09T09:00:00Z",
        steps=[
            Step(step_index=1, role="user", content=text),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id=f"{trace_id}-tool",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_site.py"},
                    )
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
    )


def test_trace_query_uses_snapshot_without_forbidden_maintenance_or_raw_scan(monkeypatch) -> None:
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()

    from opentraces.core import search_projection as sp
    from opentraces.core import trace_index as ti

    def forbidden(*_args, **_kwargs):
        raise AssertionError("query hot path called forbidden maintenance/raw scan")

    monkeypatch.setattr(ti, "cheap_sync_query_state", forbidden)
    monkeypatch.setattr(ti, "refresh_index", forbidden)
    monkeypatch.setattr(ti, "rebuild_index", forbidden)
    monkeypatch.setattr(ti, "_iter_trace_file_records", forbidden)
    monkeypatch.setattr(sp, "build_search_projection", forbidden)
    monkeypatch.setattr(sp, "refresh_search_projection", forbidden)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [item["trace_id"] for item in payload["candidates"]] == ["trace-site"]
    assert payload["search_diagnostics"]["used_search_snapshot"] is True
    assert payload["search_diagnostics"]["raw_trace_scan"] is False
    assert payload["search_diagnostics"]["wrote_to_index"] is False
    assert payload["search_diagnostics"]["rebuilt_index"] is False
    assert payload["search_diagnostics"]["python_full_corpus_sort"] is False
    assert payload["search_diagnostics"]["hydrated_count"] == 0


def test_repeated_trace_query_does_not_mutate_snapshot_or_create_wal() -> None:
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()
    snapshot = default_snapshot_path()
    before_size = snapshot.stat().st_size
    runner = CliRunner()

    for _ in range(5):
        result = runner.invoke(
            main,
            ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
        )
        assert result.exit_code == 0, result.output

    assert snapshot.stat().st_size == before_size
    assert not snapshot.with_name(snapshot.name + "-wal").exists()
    assert not snapshot.with_name(snapshot.name + "-shm").exists()


def test_missing_snapshot_returns_maintenance_needed_json() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["status"] == "maintenance_needed"
    assert payload["advice"] == "opentraces trace index"


def test_stale_snapshot_returns_maintenance_needed_json() -> None:
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()
    mark_search_snapshot_dirty("test", trace_id="trace-site")
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["status"] == "maintenance_needed"
    assert payload["reason"] == "stale"
    assert payload["search_diagnostics"]["raw_trace_scan"] is False
    assert payload["search_diagnostics"]["wrote_to_index"] is False
    assert payload["search_diagnostics"]["rebuilt_index"] is False


def test_trace_index_command_rebuilds_search_snapshot() -> None:
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    runner = CliRunner()

    result = runner.invoke(main, ["trace", "index", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["search_snapshot"]["trace_count"] == 1
    assert Path(payload["search_snapshot"]["path"]).exists()


def test_trace_index_command_does_not_rebuild_existing_legacy_trace_index(monkeypatch) -> None:
    """With a legacy index present, ``trace index`` must never full-rebuild it.

    The one exception is bootstrap: when the legacy DB is missing entirely
    (the issue-#22 operator recovery deletes it), the verb heals it once so
    ``trace map/get/slice`` keep working — hence the explicit seed below
    before full rebuilds are forbidden.
    """
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    from opentraces.core import trace_index as ti

    ti.refresh_index()  # seed the legacy index (missing-db bootstrap path)

    def forbidden(*_args, **_kwargs):
        raise AssertionError(
            "trace index must not full-rebuild an existing legacy Trace Index"
        )

    monkeypatch.setattr(ti, "rebuild_index", forbidden)
    runner = CliRunner()

    result = runner.invoke(main, ["trace", "index", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["search_snapshot"]["trace_count"] == 1
    assert "index" not in payload
    assert payload["legacy_index"]["healed"] is False


def test_trace_index_status_is_snapshot_first_without_legacy_inspection(monkeypatch) -> None:
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()
    from opentraces.core import search_projection as sp
    from opentraces.core import trace_index as ti

    def forbidden(*_args, **_kwargs):
        raise AssertionError("default trace index status must not inspect legacy state")

    monkeypatch.setattr(ti, "list_units", forbidden)
    monkeypatch.setattr(ti, "trail_freshness_warnings", forbidden)
    monkeypatch.setattr(sp, "search_projection_status", forbidden)
    snapshot = default_snapshot_path()
    before = (snapshot.stat().st_size, snapshot.stat().st_mtime_ns)
    runner = CliRunner()

    result = runner.invoke(main, ["trace", "index", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["search_snapshot"]["state"] == "ok"
    assert payload["search_snapshot"]["trace_count"] == 1
    assert payload["search_snapshot"]["dirty"] is False
    assert payload["search_snapshot"]["wal_exists"] is False
    assert payload["search_snapshot"]["shm_exists"] is False
    assert "index" not in payload
    assert "search_projection" not in payload
    assert "trail_freshness" not in payload
    assert (snapshot.stat().st_size, snapshot.stat().st_mtime_ns) == before


def test_trace_discover_missing_snapshot_returns_maintenance_needed_json() -> None:
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "discover", "site", "--json"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["status"] == "maintenance_needed"
    assert payload["advice"] == "opentraces trace index"


def test_trace_query_rejects_unit_level_candidate_kind() -> None:
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "query", "--candidate-kind", "patch", "--json"],
    )

    assert result.exit_code == 2
    assert "trace-level" in result.output
