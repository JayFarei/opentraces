"""Issue #89 — the clean cut away from the legacy Trace Index (``index.db``).

Factual test set: seed credible bucket traces and prove the user-facing read
models (``trace map`` / ``get`` / ``slice`` / ``query`` / ``index rebuild``)
serve from the durable bucket + compact search snapshot with NO legacy
``index.db`` present, and that the bucket security remediation actually filters
records when configured.
"""

from __future__ import annotations

import json
import shutil

from click.testing import CliRunner
from opentraces_schema import Agent, Observation, Step, ToolCall, Patch, TraceRecord

from opentraces.cli import main
from opentraces.core import paths
from opentraces.core.bucket_store import write_trace_record
from opentraces.core.trace_index import (
    default_index_path,
    get_map_node,
    get_trace_map,
    get_trace_path,
    get_unit,
)
from opentraces.security import SECURITY_VERSION


def _credible_trace(trace_id: str, *, scanned: bool = True) -> TraceRecord:
    """A small but realistic edit session: ask -> tool call -> observation -> patch."""
    record = TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-sonnet-4-5"),
        task={"description": "Add a retry helper to the HTTP client"},
        steps=[
            Step(step_index=1, role="user", content="Add a retry helper to the client"),
            Step(
                step_index=2,
                role="agent",
                content="I'll add a retry helper.",
                tool_calls=[
                    ToolCall(
                        tool_call_id="toolu_edit_1",
                        tool_name="Edit",
                        input={"file_path": "client.py", "old_string": "x", "new_string": "y"},
                    )
                ],
                observations=[
                    Observation(source_call_id="toolu_edit_1", content="ok", output_summary="edited")
                ],
            ),
        ],
        patches=[
            Patch(
                patch_id="patch_1",
                file_path="client.py",
                step_index=2,
                tool_call_id="toolu_edit_1",
                capture_method=["edit_tool"],
            )
        ],
    )
    if scanned:
        record.security.scanned = True
        record.security.classifier_version = SECURITY_VERSION
    return record


def _seed(trace_id: str, **kw) -> TraceRecord:
    record = _credible_trace(trace_id, **kw)
    write_trace_record(
        record,
        project_slug="issue89-demo",
        source_layer="canonical",
        legacy_mirror=False,
    )
    return record


# -- WS1: map / get / slice hydrate from the bucket with NO legacy index.db ----


def test_map_get_slice_hydrate_from_bucket_without_legacy_index():
    _seed("11111111-1111-4111-8111-111111111111")
    # The whole point of the cut: no legacy DB on a fresh machine.
    assert not default_index_path().exists()

    trace_id = "11111111-1111-4111-8111-111111111111"
    tmap = get_trace_map(trace_id)
    assert tmap is not None, "trace map must derive from the bucket record"
    assert tmap.trace_id == trace_id
    assert len(tmap.nodes) >= 2

    # get_trace_path resolves the durable bucket object (consumed by trace get).
    path = get_trace_path(trace_id)
    assert path is not None and path.exists()
    assert "bucket" in str(path)

    # Sub-trace addressing (unit / node ids embed the trace id) resolves too.
    node = tmap.nodes[len(tmap.nodes) // 2]
    assert get_map_node(node.node_id) is not None
    if node.unit_id:
        unit = get_unit(node.unit_id)
        assert unit is not None and unit.unit_id == node.unit_id

    # Still no legacy DB was created as a side effect.
    assert not default_index_path().exists()


def test_unknown_trace_returns_none_not_legacy_error():
    assert get_trace_map("00000000-0000-4000-8000-000000000000") is None
    assert get_trace_path("00000000-0000-4000-8000-000000000000") is None
    assert get_unit("tu:00000000-0000-4000-8000-000000000000:step:1") is None


def test_cli_map_get_slice_serve_from_bucket():
    trace_id = "22222222-2222-4222-8222-222222222222"
    _seed(trace_id)
    runner = CliRunner()

    m = runner.invoke(main, ["trace", "map", trace_id, "--json"])
    assert m.exit_code == 0, m.output
    map_payload = json.loads(m.output)
    assert map_payload["trace_id"] == trace_id
    assert map_payload["map"]["trace_id"] == trace_id

    g = runner.invoke(main, ["trace", "get", trace_id, "--json"])
    assert g.exit_code == 0, g.output
    assert json.loads(g.output)["trace"]["trace_id"] == trace_id

    s = runner.invoke(main, ["trace", "slice", trace_id, "--template", "bursts", "--json"])
    assert s.exit_code == 0, s.output

    assert not default_index_path().exists()


# -- WS1: rebuild is snapshot-only by default; query never touches legacy ------


def test_index_rebuild_is_snapshot_only_by_default():
    _seed("33333333-3333-4333-8333-333333333333")
    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "rebuild", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["search_snapshot"]["trace_count"] >= 1
    # Default rebuild must NOT force or bootstrap the legacy index.
    assert payload["legacy_index"]["forced"] is False
    assert payload["keep_warm"]["skipped"] is True
    # Snapshot-only rebuild does not materialize a legacy DB.
    assert not default_index_path().exists()


def test_trace_query_uses_snapshot_not_legacy():
    _seed("44444444-4444-4444-8444-444444444444")
    runner = CliRunner()
    build = runner.invoke(main, ["trace", "index", "rebuild", "--json"])
    assert build.exit_code == 0, build.output

    result = runner.invoke(main, ["trace", "query", "--lex", "retry", "--json"])
    assert result.exit_code == 0, result.output
    diag = json.loads(result.output)["search_diagnostics"]
    assert diag["used_search_snapshot"] is True
    assert diag["rebuilt_index"] is False
    assert diag["wrote_to_index"] is False
    assert not default_index_path().exists()


def test_trace_query_remote_bucket_pulls_snapshot_without_legacy_index(monkeypatch, tmp_path):
    trace_id = "45454545-4545-4545-8545-454545454545"
    _seed(trace_id)
    remote_root = tmp_path / "remote-bucket"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))
    runner = CliRunner()

    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.output)["remote"]["state"] == "pushed"

    shutil.rmtree(paths.bucket_dir())
    shutil.rmtree(paths.OPENTRACES_DIR / "index", ignore_errors=True)
    assert not default_index_path().exists()

    result = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--lex",
            "retry",
            "--remote-bucket",
            "--force-remote-bucket",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["remote_bucket"]["remote"]["state"] == "pulled"
    assert payload["remote_bucket"]["search_snapshot"]["trace_count"] >= 1
    assert any(candidate["trace_id"] == trace_id for candidate in payload["candidates"])
    assert payload["search_diagnostics"]["used_search_snapshot"] is True
    assert (paths.OPENTRACES_DIR / "index" / "search.sqlite").exists()
    assert not default_index_path().exists()


# -- WS3c: bucket security run applies the configured filter -------------------


def test_bucket_security_run_not_configured_is_safe_noop():
    _seed("55555555-5555-4555-8555-555555555555", scanned=False)
    runner = CliRunner()
    result = runner.invoke(main, ["bucket", "security", "run", "--all", "--json"])
    assert result.exit_code == 2, result.output  # not actionable -> exit 2
    payload = json.loads(result.output)["security_run"]
    assert payload["status"] == "not_configured"
    assert payload["filtered"] == 0
    assert payload["remediation"]["state"] == "not_configured"


def test_bucket_security_run_filters_when_configured():
    _seed("66666666-6666-4666-8666-666666666666", scanned=False)
    runner = CliRunner()

    status_before = runner.invoke(main, ["bucket", "security", "status", "--json"])
    assert status_before.exit_code == 0, status_before.output
    before = json.loads(status_before.output)["security"]
    assert before["unfiltered"] >= 1
    assert before["filtering_configured"] is False

    # Configure the filter, then apply it.
    pol = runner.invoke(main, ["bucket", "security", "policy", "--policy", "basic", "--json"])
    assert pol.exit_code == 0, pol.output

    run = runner.invoke(main, ["bucket", "security", "run", "--all", "--json"])
    assert run.exit_code == 0, run.output
    summary = json.loads(run.output)["security_run"]
    assert summary["status"] == "ok"
    assert summary["filtered"] >= 1

    status_after = runner.invoke(main, ["bucket", "security", "status", "--json"])
    after = json.loads(status_after.output)["security"]
    assert after["filtered"] >= 1
    assert after["unfiltered"] < before["unfiltered"]


def test_bucket_security_run_single_trace_and_not_found():
    trace_id = "77777777-7777-4777-8777-777777777777"
    _seed(trace_id, scanned=False)
    runner = CliRunner()
    runner.invoke(main, ["bucket", "security", "policy", "--policy", "basic", "--json"])

    ok = runner.invoke(main, ["bucket", "security", "run", "--trace", trace_id, "--json"])
    assert ok.exit_code == 0, ok.output
    assert json.loads(ok.output)["security_run"]["total"] == 1

    missing = runner.invoke(
        main, ["bucket", "security", "run", "--trace", "does-not-exist", "--json"]
    )
    assert missing.exit_code == 2, missing.output
    assert json.loads(missing.output)["security_run"]["status"] == "not_found"


# -- WS3a: doctor renders skipped verification as skipped, not invalid ---------


def _render_trail(info: dict) -> str:
    from opentraces.cli.doctor_cli import _trail_event_log_section

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        _trail_event_log_section(info)
    return buf.getvalue()


def test_doctor_trail_skipped_large_log_is_not_invalid():
    # When the event log is too large, verification is skipped: booleans are
    # JSON null. This must render as skipped, never as invalid.
    out = _render_trail(
        {
            "ref": "refs/opentraces/local/events/v1",
            "state": "unverified_large",
            "head": "abc123abc123",
            "batch_count": 6074,
            "doctor_scan_skipped": True,
            "skip_reason": "event snapshot too large",
            "batch_parents_linear": None,
            "content_hashes_valid": None,
            "event_chain_valid": None,
        }
    )
    assert "skipped" in out
    assert "invalid" not in out


def test_doctor_trail_actual_false_renders_invalid():
    # A real integrity failure (boolean False) still renders invalid.
    out = _render_trail(
        {
            "ref": "refs/opentraces/local/events/v1",
            "state": "invalid",
            "head": "deadbeefdead",
            "batch_count": 3,
            "event_count": 10,
            "batch_parents_linear": True,
            "content_hashes_valid": False,
            "event_chain_valid": True,
        }
    )
    assert "invalid" in out  # the False boolean
    assert "valid" in out    # the True booleans


def test_doctor_trail_all_valid_renders_clean():
    out = _render_trail(
        {
            "ref": "refs/opentraces/local/events/v1",
            "state": "ok",
            "head": "feedface0000",
            "batch_count": 3,
            "event_count": 10,
            "batch_parents_linear": True,
            "content_hashes_valid": True,
            "event_chain_valid": True,
        }
    )
    assert "linear" in out
    assert "invalid" not in out
    assert "skipped" not in out
