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


def test_missing_snapshot_bootstraps_and_serves() -> None:
    # Issue #30: a world with traces but no search snapshot must NOT dead-end at
    # maintenance_needed/exit 3. query self-heals once (auto-rebuild of the
    # compact snapshot) and serves rc=0 results, reporting rebuilt_index=True
    # on the first call and False on the next (steady state).
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    assert not default_snapshot_path().exists()
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
    assert payload["search_diagnostics"]["rebuilt_index"] is True
    assert default_snapshot_path().exists()

    # Second invocation finds a servable snapshot: read-only steady state.
    result2 = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )
    assert result2.exit_code == 0, result2.output
    payload2 = json.loads(result2.output)
    assert [item["trace_id"] for item in payload2["candidates"]] == ["trace-site"]
    assert payload2["search_diagnostics"]["rebuilt_index"] is False


def test_stale_snapshot_self_heals() -> None:
    # Issue #30: a stale dirty marker self-heals with one build + retry and
    # serves rc=0 results; the dirty marker is cleared by the rebuild.
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()
    mark_search_snapshot_dirty("test", trace_id="trace-site")
    from opentraces.core.trace_search_state import current_dirty_token

    assert current_dirty_token() is not None
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [item["trace_id"] for item in payload["candidates"]] == ["trace-site"]
    assert payload["search_diagnostics"]["rebuilt_index"] is True
    assert payload["search_diagnostics"]["raw_trace_scan"] is False
    # Rebuild cleared the dirty marker.
    assert current_dirty_token() is None


def test_query_exit_3_when_rebuild_fails(monkeypatch) -> None:
    # Issue #30 contract: maintenance_needed/exit 3 remains ONLY when the
    # self-heal build itself fails. Force the auto-rebuild to raise and assert
    # the payload shape and exit code are preserved.
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    assert not default_snapshot_path().exists()

    from opentraces.core import trace_search_snapshot as tss

    def boom(*_args, **_kwargs):
        raise tss.SearchSnapshotNeedsRebuild("missing", path=default_snapshot_path())

    monkeypatch.setattr(tss, "build_trace_search_snapshot", boom)
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["status"] == "maintenance_needed"
    assert payload["advice"] == "opentraces trace index"
    assert payload["search_diagnostics"]["rebuilt_index"] is False
    assert payload["search_diagnostics"]["raw_trace_scan"] is False


def test_query_after_trace_index_rebuild_is_read_only_steady_state() -> None:
    # PR #24 honesty contract: the documented maintenance verb (`trace
    # index`) produces a servable snapshot, and the NEXT query serves from it
    # read-only (rebuilt_index=False) — no hidden second rebuild. This is the
    # explicit "rebuild then query succeeds" end-to-end pair; the self-heal
    # tests above cover the no-rebuild-first ordering.
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    assert not default_snapshot_path().exists()
    runner = CliRunner()

    rebuild = runner.invoke(main, ["trace", "index", "--json"])
    assert rebuild.exit_code == 0, rebuild.output
    assert default_snapshot_path().exists()

    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [item["trace_id"] for item in payload["candidates"]] == ["trace-site"]
    assert payload["search_diagnostics"]["used_search_snapshot"] is True
    assert payload["search_diagnostics"]["rebuilt_index"] is False
    assert payload["search_diagnostics"]["raw_trace_scan"] is False


def test_query_self_heal_notice_is_stderr_only_and_tty_gated(monkeypatch) -> None:
    # PR #38 item M residual gap: the one-time self-heal notice
    # (_notify_rebuilding) is TTY-gated by design, and CliRunner's captured
    # stderr is not a TTY — so the notice was invisible to every test above
    # and only covered indirectly via rebuilt_index diagnostics. Simulate an
    # interactive stderr by patching the runner's stream wrapper and assert
    # BOTH halves of the honesty contract: the staleness signpost lands on
    # stderr (never stdout), and the --json stdout document stays pure
    # parseable JSON.
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    assert not default_snapshot_path().exists()
    runner = CliRunner()

    # Non-TTY consumers (pipes, scripts, CI) never see the notice, even when
    # the self-heal actually runs.
    quiet = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )
    assert quiet.exit_code == 0, quiet.output
    assert json.loads(quiet.stdout)["search_diagnostics"]["rebuilt_index"] is True
    assert "rebuilding search snapshot" not in quiet.stderr

    # Re-trigger the self-heal (stale dirty marker) with an interactive
    # stderr: the operator-facing signpost must appear on stderr only.
    mark_search_snapshot_dirty("test", trace_id="trace-site")
    from click import testing as click_testing

    original_isatty = click_testing._NamedTextIOWrapper.isatty

    def stderr_is_a_tty(self):
        if getattr(self, "name", "") == "<stderr>":
            return True
        return original_isatty(self)

    monkeypatch.setattr(click_testing._NamedTextIOWrapper, "isatty", stderr_is_a_tty)
    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 0, result.output
    # stdout stays a single parseable JSON document even with the notice on.
    payload = json.loads(result.stdout)
    assert payload["search_diagnostics"]["rebuilt_index"] is True
    assert "opentraces: rebuilding search snapshot (one-time)..." in result.stderr
    assert "rebuilding search snapshot" not in result.stdout


def test_trace_index_command_rebuilds_search_snapshot() -> None:
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    runner = CliRunner()

    result = runner.invoke(main, ["trace", "index", "--json"])

    assert result.exit_code == 0, result.output
    # Parse result.stdout (the stream the --json contract governs), not
    # result.output: under Click 8.2+ result.output is the user-terminal view
    # that mixes the stderr bootstrap signpost in. stdout stays pure JSON.
    payload = json.loads(result.stdout)
    assert payload["search_snapshot"]["trace_count"] == 1
    assert Path(payload["search_snapshot"]["path"]).exists()
    assert payload["telemetry"]["duration_ms"] >= 0
    assert payload["telemetry"]["snapshot_build_duration_ms"] >= 0


def test_trace_index_command_does_not_rebuild_existing_legacy_trace_index(monkeypatch) -> None:
    """The default ``trace index`` path is snapshot-only."""
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    from opentraces.core import trace_index as ti

    ti.refresh_index()  # seed the legacy index (missing-db bootstrap path)

    def forbidden_rebuild(*_args, **_kwargs):
        raise AssertionError(
            "trace index must not full-rebuild an existing legacy Trace Index"
        )

    def forbidden_keep_warm(*_args, **_kwargs):
        raise AssertionError("trace index must not run legacy keep-warm by default")

    monkeypatch.setattr(ti, "rebuild_index", forbidden_rebuild)
    monkeypatch.setattr(ti, "keep_index_warm", forbidden_keep_warm)
    runner = CliRunner()

    result = runner.invoke(main, ["trace", "index", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["search_snapshot"]["trace_count"] == 1
    assert "index" not in payload
    assert payload["legacy_index"]["healed"] is False
    assert payload["keep_warm"]["skipped"] is True


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


def test_trace_discover_missing_snapshot_bootstraps_and_serves() -> None:
    # Issue #30: discover routes through search_traces, so it inherits the
    # same self-heal — a missing snapshot now serves rc=0 discovery output.
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    assert not default_snapshot_path().exists()
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "discover", "site", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert "discovery" in payload
    assert default_snapshot_path().exists()


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


def test_trace_query_envelope_never_emits_dead_trail_freshness() -> None:
    # Issue #27 item I: SearchPage.warnings has zero writers in the read-only
    # snapshot kernel, so the query envelope must never carry the dead
    # ``trail_freshness`` / ``warnings`` keys (PR #34 moved freshness onto
    # SearchDiagnostics.rebuilt_index). Guards against the dead emission
    # silently coming back.
    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()
    runner = CliRunner()

    result = runner.invoke(
        main,
        ["trace", "query", "--lex", "site", "--limit", "3", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "trail_freshness" not in payload
    assert "warnings" not in payload


def test_trace_index_status_reports_per_table_db_sizes() -> None:
    # Issue #27 item A: status --json must expose a byte breakdown for both
    # on-disk SQLite DBs so the multi-GB bloat (issue #22) is measurable.
    # Envelope-budget contract: the DEFAULT --json render carries aggregate
    # scalars only (the 20+ FTS shadow tables blew the agent-facing token
    # budget); the full per-table map moves behind --verbose.
    from opentraces.core.trace_index import default_index_path, refresh_index
    from opentraces.core.trace_search_snapshot import default_snapshot_path

    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()
    refresh_index()  # materialize the legacy index.db

    snapshot_before = (
        default_snapshot_path().stat().st_size,
        default_snapshot_path().stat().st_mtime_ns,
    )
    index_before = (
        default_index_path().stat().st_size,
        default_index_path().stat().st_mtime_ns,
    )

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    db_sizes = payload["db_sizes"]

    for label in ("legacy_index", "search_snapshot"):
        entry = db_sizes[label]
        assert entry["exists"] is True
        assert entry["size_bytes"] > 0
        # dbstat is compiled into CPython's bundled sqlite3; the aggregate
        # scalars must resolve from a non-empty per-table scan.
        assert entry["table_count"] > 0
        assert entry["tables_bytes_total"] > 0
        assert entry["largest_table"]["bytes"] > 0
        # The bounded default never inlines the per-table map.
        assert "tables" not in entry

    verbose = runner.invoke(main, ["trace", "index", "status", "--json", "--verbose"])

    assert verbose.exit_code == 0, verbose.output
    verbose_payload = json.loads(verbose.output)
    for label in ("legacy_index", "search_snapshot"):
        entry = verbose_payload["db_sizes"][label]
        assert entry["tables"] is not None, entry.get("tables_error")
        assert sum(stat["bytes"] for stat in entry["tables"].values()) > 0

    # Read-only contract: neither DB was mutated by reporting sizes.
    assert (
        default_snapshot_path().stat().st_size,
        default_snapshot_path().stat().st_mtime_ns,
    ) == snapshot_before
    assert (
        default_index_path().stat().st_size,
        default_index_path().stat().st_mtime_ns,
    ) == index_before


def test_trace_index_status_db_sizes_degrade_when_dbstat_unavailable(monkeypatch) -> None:
    # Issue #27 item A: when dbstat is missing (no SQLITE_ENABLE_DBSTAT_VTAB) or
    # the DB is unreadable, the block degrades to tables=None + tables_error
    # rather than failing the command.
    import sqlite3 as _sqlite3

    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    build_trace_search_snapshot()

    real_connect = _sqlite3.connect

    class _NoDbstatConn:
        def __init__(self, inner):
            object.__setattr__(self, "_inner", inner)

        def execute(self, sql, *a, **k):
            if "dbstat" in sql:
                raise _sqlite3.OperationalError("no such table: dbstat")
            return self._inner.execute(sql, *a, **k)

        def __enter__(self):
            self._inner.__enter__()
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __setattr__(self, name, value):
            # Forward attribute sets (e.g. row_factory) to the real connection
            # so callers that expect sqlite3.Row rows keep working.
            setattr(self._inner, name, value)

    def _no_dbstat(*args, **kwargs):
        return _NoDbstatConn(real_connect(*args, **kwargs))

    # The helper does ``import sqlite3`` locally, so it resolves to the same
    # module object — patching the module's ``connect`` covers it.
    monkeypatch.setattr(_sqlite3, "connect", _no_dbstat)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    entry = payload["db_sizes"]["search_snapshot"]
    assert entry["exists"] is True
    assert entry["table_count"] is None
    assert "dbstat" in entry["tables_error"]

    verbose = runner.invoke(main, ["trace", "index", "status", "--json", "--verbose"])

    assert verbose.exit_code == 0, verbose.output
    verbose_entry = json.loads(verbose.output)["db_sizes"]["search_snapshot"]
    assert verbose_entry["tables"] is None
    assert "dbstat" in verbose_entry["tables_error"]


def test_trace_index_missing_legacy_human_path_reports_explicit_repair() -> None:
    # The human path is snapshot-only too: missing legacy map/get/slice cache is
    # advice, not an implicit long-running bootstrap.
    from opentraces.core.trace_index import default_index_path

    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    assert not default_index_path().exists()
    runner = CliRunner()

    result = runner.invoke(main, ["trace", "index"])

    assert result.exit_code == 0, result.output
    assert "Search snapshot rebuilt" in result.output
    assert "Legacy Trace Index is missing" in result.stderr
    assert "rebuild --legacy" in result.stderr
    assert not default_index_path().exists()


def test_trace_index_missing_legacy_reports_explicit_repair_without_bootstrap() -> None:
    # Default trace index rebuild is snapshot-only. Missing legacy map/get/slice
    # cache is reported with explicit repair advice, not bootstrapped
    # implicitly.
    from opentraces.core.trace_index import default_index_path

    _write_trace("demo-project", _trace("trace-site", "Fix site search"))
    assert not default_index_path().exists()
    runner = CliRunner()

    result = runner.invoke(main, ["trace", "index", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)  # parseable: no stderr leak
    assert payload["status"] == "ok"
    assert payload["legacy_index"]["healed"] is False
    assert payload["legacy_index"]["missing"] is True
    assert payload["legacy_index"]["advice"] == "opentraces trace index rebuild --legacy"
    assert not default_index_path().exists()
    assert "Bootstrapping" not in result.stdout
    assert "Bootstrapping" not in result.stderr
