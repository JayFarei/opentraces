"""Plan 087 U5 — ``trace index refresh`` CLI command + doctor freshness.

``trace index refresh`` is the explicit warm-keeping verb (the manual companion
to the best-effort hooks): it runs the digest-gated cheap sync and reports what
changed. ``opentraces doctor`` gains a search-projection freshness section that
reports fresh/stale WITHOUT a heavy repair.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from click.testing import CliRunner
from opentraces_schema import Agent, Step, ToolCall, TraceRecord

from opentraces.cli import main
from opentraces.core import search_projection as sp
from opentraces.core import trace_index as ti


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(
        record.model_dump_json() + "\n"
    )


def _trace_with(trace_id: str, term: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": f"Work involving {term}"},
        dependencies=["pytest"],
        steps=[
            Step(step_index=1, role="user", content=f"Use {term}."),
            Step(
                step_index=2,
                role="agent",
                content=f"Editing {term}.",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc",
                        tool_name="Edit",
                        input={
                            "file_path": f"src/{term}.ts",
                            "old_string": "a",
                            "new_string": "b",
                        },
                    ),
                ],
            ),
        ],
        outcome={"success": True, "committed": False},
    )


def _warm(project: Path) -> None:
    ti.rebuild_index()
    sp.build_search_projection()
    ti.cheap_sync_query_state(query_source="index")
    ti.cheap_sync_query_state(query_source="projection")


def test_trace_index_refresh_syncs_new_trace(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    rebuild_calls = {"n": 0}
    real_rebuild = ti.rebuild_index

    def spy_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        return real_rebuild(*args, **kwargs)

    monkeypatch.setattr(ti, "rebuild_index", spy_rebuild)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "refresh", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["synced"] is True
    assert "trace-gamma" in set(payload["changed_trace_ids"])
    assert rebuild_calls["n"] == 0, "refresh must not full-rebuild"

    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_trace_index_refresh_reports_maintenance_when_refresh_would_rebuild(
    tmp_path, monkeypatch
):
    """The explicit cheap refresh command must not hide a full legacy rebuild."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    monkeypatch.setattr(
        ti,
        "_refresh_rebuild_reason",
        lambda conn: "unsupported_index_version:legacy:test",
    )
    rebuild_calls = {"n": 0}

    def fail_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        raise AssertionError("refresh must not full-rebuild")

    monkeypatch.setattr(ti, "rebuild_index", fail_rebuild)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "refresh", "--json"])
    assert result.exit_code == 2, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "maintenance_required"
    assert payload["synced"] is False
    assert payload["maintenance_required"] == [
        "index:unsupported_index_version:legacy:test",
        "projection:unsupported_index_version:legacy:test",
    ]
    assert payload["refused_full_rebuild"] is True
    assert payload["advice"] == "opentraces trace index rebuild --legacy"
    assert rebuild_calls["n"] == 0


def test_trace_index_refresh_migrates_v8_meta_without_full_rebuild(
    tmp_path, monkeypatch
):
    """A v8 index that is structurally v9-compatible should be migrated, not rebuilt."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    with ti._connect(ti.default_index_path()) as conn:
        ti._meta_set(conn, "index_version", "plan056-m1-v8")
        conn.commit()

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    rebuild_calls = {"n": 0}

    def fail_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        raise AssertionError("v8->v9 schema migration must not full-rebuild")

    monkeypatch.setattr(ti, "rebuild_index", fail_rebuild)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "refresh", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["synced"] is True
    assert "trace-gamma" in set(payload["changed_trace_ids"])
    assert rebuild_calls["n"] == 0

    with ti._connect(ti.default_index_path()) as conn:
        version = ti._meta_get(conn, "index_version")
    assert version == ti.INDEX_VERSION

    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_refresh_without_trails_skips_changed_trace_trail_projection(
    tmp_path, monkeypatch
):
    """Cheap trace refresh must not read the Trail event log for changed traces."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    def fail_trail_projection(*args, **kwargs):
        raise AssertionError(
            "refresh_index(refresh_trails=False) must not build Trail projection"
        )

    monkeypatch.setattr(ti, "_trail_projection_for_project", fail_trail_projection)

    summary = ti.refresh_index(refresh_trails=False)

    assert summary.trace_count >= 2
    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_refresh_preflight_migrates_v8_trail_sources_column(tmp_path):
    """The v8->v9 migration adds the missing additive column in place."""
    db = tmp_path / "index.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as conn:
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
            create table trail_sources (
                project_slug text primary key,
                repo_path text not null,
                ref_sha text not null,
                indexed_at text not null
            );
            insert into meta(key, value)
            values ('index_version', 'plan056-m1-v8');
            insert into trail_sources(project_slug, repo_path, ref_sha, indexed_at)
            values ('demo', '/tmp/demo', 'abc123', '2026-06-16T00:00:00Z');
            """
        )

    assert ti.refresh_index_rebuild_reason(db) is None

    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("pragma table_info(trail_sources)")}
        version = conn.execute(
            "select value from meta where key='index_version'"
        ).fetchone()[0]
        limitations = conn.execute(
            "select limitations_json from trail_sources where project_slug='demo'"
        ).fetchone()[0]
    assert "limitations_json" in columns
    assert limitations == "[]"
    assert version == ti.INDEX_VERSION


def test_trace_index_refresh_steady_state(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    runner = CliRunner()
    result = runner.invoke(main, ["trace", "index", "refresh", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["synced"] is False


def test_doctor_reports_search_projection_freshness(tmp_path, monkeypatch):
    """doctor's trace_index section reports search-projection freshness without
    a heavy repair."""
    from opentraces.core import doctor as doctor_mod

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    build_calls = {"n": 0}
    real_build = sp.build_search_projection

    def spy_build(*args, **kwargs):
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(sp, "build_search_projection", spy_build)

    section = doctor_mod._trace_index_status()
    assert "search_projection_freshness" in section
    fr = section["search_projection_freshness"]
    assert fr["state"] == "ok"
    assert fr["fresh"] is True
    assert build_calls["n"] == 0, "doctor freshness must not build the projection"
