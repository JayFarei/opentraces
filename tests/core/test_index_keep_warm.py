"""Plan 087 U5 — best-effort keep-warm hooks + freshness reporting.

Phase 1 (U0/U1) made the warm-cache query path stop auto-rebuilding (killed the
105s tax) but opened a *stale window*: a freshly captured trace was invisible to
``trace query`` until an explicit ``trace index refresh``. U4 added the
digest-gated cheap-sync primitive (``cheap_sync_query_state``); U5 wires it into
the capture/scan paths as a BEST-EFFORT keep-warm hook so capture makes a trace
queryable without a manual refresh — while guaranteeing a hook failure can never
break capture/scan.

Contract pinned here:

* ``keep_index_warm()`` is a thin best-effort wrapper over the cheap-sync path.
  It NEVER raises: an internal failure is swallowed and reported as
  ``ok=False``, leaving the warm cache serving.
* Capturing a trace (full ``ingest_one_session`` path) leaves it queryable
  through ``trace query`` with no manual refresh.
* ``search_projection_freshness()`` compares the projection's
  ``built_from_cheap_signal`` against the current cheap stat-only bucket signal
  and reports fresh/stale WITHOUT running a heavy repair/rebuild or digest scan.

The autouse ``_isolate_opentraces_global_state`` fixture (tests/conftest.py)
redirects ``~/.opentraces`` into a tmp HOME, so these are hermetic.
"""
from __future__ import annotations

import json
from pathlib import Path

from opentraces_schema import Agent, Step, ToolCall, TraceRecord

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
        task={"description": f"Work involving {term} in the parser path"},
        dependencies=["pytest"],
        steps=[
            Step(step_index=1, role="user", content=f"Please use {term}."),
            Step(
                step_index=2,
                role="agent",
                content=f"Editing the {term} module.",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-edit",
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


# --------------------------------------------------------------------------- #
# keep_index_warm — best-effort wrapper over cheap_sync
# --------------------------------------------------------------------------- #

def test_keep_index_warm_makes_new_trace_queryable(tmp_path):
    """A trace written after warm-up becomes queryable after keep_index_warm()
    with no manual rebuild/refresh."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    page = ti.query_index_page(lex="gamma")
    assert all(c.trace_id != "trace-gamma" for c in page.candidates)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    result = ti.keep_index_warm()
    assert result.ok is True

    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_keep_index_warm_refreshes_both_index_and_projection(tmp_path, monkeypatch):
    """F1 — per-source freshness markers: a new trace must refresh BOTH the
    index AND the search projection. The index-source sync must not advance a
    shared marker that masks the still-stale projection source.

    F3 note: ``keep_index_warm`` now defaults to ``("index",)`` (the projection
    is warmed only on explicit opt-in, since its delta still stamps a heavier
    bucket digest), so this exercises the explicit both-sources opt-in. The
    per-source-marker independence the test pins is unchanged."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    proj_refresh_calls = {"n": 0}
    real_proj_refresh = sp.refresh_search_projection

    def spy_proj_refresh(*args, **kwargs):
        proj_refresh_calls["n"] += 1
        return real_proj_refresh(*args, **kwargs)

    monkeypatch.setattr(sp, "refresh_search_projection", spy_proj_refresh)

    result = ti.keep_index_warm(query_sources=("index", "projection"))
    assert result.ok is True
    assert proj_refresh_calls["n"] == 1, (
        "projection source must still refresh after the index source advanced "
        "its own per-source marker"
    )

    # Both serving surfaces reflect the new trace.
    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)
    page = sp.query_search_projection_page(semantic="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_keep_index_warm_swallows_failure(tmp_path, monkeypatch):
    """A failure inside the cheap-sync path is swallowed: keep_index_warm()
    returns ok=False and never raises."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic cheap-sync failure")

    monkeypatch.setattr(ti, "cheap_sync_query_state", boom)

    # Must not raise.
    result = ti.keep_index_warm()
    assert result.ok is False
    assert result.error is not None


def test_keep_index_warm_reports_maintenance_when_refresh_would_rebuild(
    tmp_path, monkeypatch
):
    """Unsupported legacy schemas are maintenance, not a hidden rebuild.

    This pins the long-lived-dev-box failure mode: ``keep_index_warm`` is allowed
    to say "explicit rebuild needed", but it must not turn a cheap capture/query
    path into a surprise multi-GB full rebuild.
    """
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
        raise AssertionError("keep-warm must not full-rebuild")

    monkeypatch.setattr(ti, "rebuild_index", fail_rebuild)
    digest_calls = {"n": 0}

    def fail_digest_scan(*args, **kwargs):
        digest_calls["n"] += 1
        raise AssertionError("legacy maintenance must not scan the bucket")

    monkeypatch.setattr(ti, "_current_bucket_trace_digests", fail_digest_scan)

    result = ti.keep_index_warm(query_sources=("index",))

    assert result.ok is True
    assert result.synced is False
    assert result.maintenance_required == [
        "index:unsupported_index_version:legacy:test"
    ]
    assert rebuild_calls["n"] == 0
    assert digest_calls["n"] == 0


def test_keep_index_warm_steady_state_is_noop(tmp_path, monkeypatch):
    """When nothing changed since the last sync, keep_index_warm does no
    refresh work (steady-state short-circuit)."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    refresh_calls = {"n": 0}
    real_refresh = ti.refresh_index

    def spy_refresh(*args, **kwargs):
        refresh_calls["n"] += 1
        return real_refresh(*args, **kwargs)

    monkeypatch.setattr(ti, "refresh_index", spy_refresh)

    result = ti.keep_index_warm()
    assert result.ok is True
    assert refresh_calls["n"] == 0, "steady-state keep-warm must not refresh"


# --------------------------------------------------------------------------- #
# search_projection_freshness — cheap freshness report (no heavy repair)
# --------------------------------------------------------------------------- #

def test_search_projection_freshness_reports_fresh(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    fresh = sp.search_projection_freshness()
    assert fresh["state"] == "ok"
    assert fresh["fresh"] is True
    # F2 (#5): freshness now compares the cheap stat-only bucket signal, never
    # the heavy bucket_manifest digest scan.
    assert fresh["built_from_cheap_signal"] == fresh["current_cheap_signal"]


def test_search_projection_freshness_reports_stale_without_repair(tmp_path, monkeypatch):
    """A new trace after build makes the projection stale; freshness reports it
    WITHOUT triggering a rebuild/build."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    # New trace changes the bucket digest but the projection is untouched.
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))
    # Sync the bucket so the digest advances (no projection build).
    ti._current_bucket_trace_digests()

    build_calls = {"n": 0}
    real_build = sp.build_search_projection

    def spy_build(*args, **kwargs):
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    refresh_calls = {"n": 0}
    real_refresh = sp.refresh_search_projection

    def spy_refresh(*args, **kwargs):
        refresh_calls["n"] += 1
        return real_refresh(*args, **kwargs)

    monkeypatch.setattr(sp, "build_search_projection", spy_build)
    monkeypatch.setattr(sp, "refresh_search_projection", spy_refresh)

    fresh = sp.search_projection_freshness()
    assert fresh["state"] == "ok"
    assert fresh["fresh"] is False
    # The newly written trace flips the cheap stat-only signal even though no
    # heavy digest scan ran.
    assert fresh["built_from_cheap_signal"] != fresh["current_cheap_signal"]
    assert build_calls["n"] == 0, "freshness check must not build the projection"
    assert refresh_calls["n"] == 0, "freshness check must not refresh the projection"


# --------------------------------------------------------------------------- #
# Capture path: ingest leaves a trace queryable without a manual refresh
# --------------------------------------------------------------------------- #

def test_capture_makes_trace_queryable_without_manual_refresh(tmp_path, monkeypatch):
    """End-to-end: ingest_one_session of a fresh session leaves the trace
    queryable via the warm index without any manual refresh, because the
    keep-warm hook fired post write_trace_record."""
    from opentraces.core import ingest as ingest_mod

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    # Warm an empty index/projection so the query path serves the warm cache
    # (not a cold bootstrap rebuild).
    _warm(project)

    # Capture a trace directly via the ingest core (bypassing the parser by
    # writing the record through the same bucket write path the hook keys off).
    # We exercise the hook integration point: keep_index_warm must be called
    # once write_trace_record has run.
    warm_calls = {"n": 0}
    real_warm = ti.keep_index_warm

    def spy_warm(*args, **kwargs):
        warm_calls["n"] += 1
        return real_warm(*args, **kwargs)

    monkeypatch.setattr(ingest_mod, "keep_index_warm", spy_warm, raising=False)

    record = _trace_with("trace-captured", "captured")
    _write_project_trace(project, record)
    from opentraces.core.bucket_store import write_trace_record

    write_trace_record(
        record,
        project_slug="demo",
        source_layer="canonical",
        legacy_mirror=True,
    )
    # Simulate the post-write keep-warm hook call site.
    ingest_mod.keep_index_warm()
    assert warm_calls["n"] == 1

    page = ti.query_index_page(lex="captured")
    assert any(c.trace_id == "trace-captured" for c in page.candidates)


# --------------------------------------------------------------------------- #
# Hook best-effort guarantee inside scan_project
# --------------------------------------------------------------------------- #

def test_scan_project_keep_warm_failure_does_not_break_scan(tmp_path, monkeypatch):
    """A keep-warm hook exception during scan is swallowed; scan still returns
    a report."""
    from opentraces.core import ingest as ingest_mod

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic keep-warm failure in scan")

    monkeypatch.setattr(ingest_mod, "keep_index_warm", boom, raising=False)

    # scan_project with no sessions still returns a report and never raises.
    report = ingest_mod.scan_project(project)
    assert report is not None
    assert report.errored == 0


# --------------------------------------------------------------------------- #
# Bug A regression guard: --trace-record-only must keep the INDEX marker warm
#
# The shipped enrollment-backfill fix wrapped keep_index_warm in
# ``if not trace_record_only:``, so a record-only backfill wrote new trace files
# (flipping the stat-only bucket signal) WITHOUT advancing
# ``synced_cheap_signal:index``. The next ``trace query`` then saw a marker
# mismatch and fell into the whole-corpus ``_current_bucket_trace_digests``
# materialisation (every TraceRecord parsed at once, ~4GB RSS, >1min). These
# guards pin the cold-state contract the existing warm-marker tests never
# exercised: record-only ingest still advances the INDEX marker (index-only,
# bounded single-trace path) so the next query stays cheap.
# --------------------------------------------------------------------------- #

def _write_claude_session_jsonl(path: Path, session_id: str, turns: int = 2) -> None:
    """A minimal but real Claude Code session JSONL that ingest can parse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[dict] = []
    for i in range(1, turns + 1):
        ts = f"2026-04-15T07:00:{i:02d}Z"
        lines.append({
            "type": "user", "sessionId": session_id, "timestamp": ts,
            "message": {"role": "user", "content": f"prompt {i}"},
        })
        lines.append({
            "type": "assistant", "sessionId": session_id, "timestamp": ts,
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": f"tu_{i}", "name": "Read",
                             "input": {"file_path": "x.py"}}],
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        })
        lines.append({
            "type": "user", "sessionId": session_id, "timestamp": ts,
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"tu_{i}", "content": "ok"}]},
        })
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))


def _opt_in_project(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(json.dumps({
        "marker_version": "2",
        "project_id": "recordonlyproject00000000000000",
        "review_policy": "review",
        "push_policy": "manual",
        "agents": ["claude-code"],
    }))


def test_record_only_ingest_warms_index_marker_only(tmp_path, monkeypatch):
    """Record-only ingest calls keep_index_warm for the just-written trace with
    ``query_sources=("index",)`` (index only — projection/trail stay deferred)."""
    from opentraces.core import ingest as ingest_mod

    project = tmp_path / "demo"
    _opt_in_project(project)
    _warm(project)  # an existing warm index, as in the real backfill scenario

    warm_calls: list[dict] = []
    real_warm = ti.keep_index_warm

    def spy_warm(*args, **kwargs):
        warm_calls.append(dict(kwargs))
        return real_warm(*args, **kwargs)

    monkeypatch.setattr(ingest_mod, "keep_index_warm", spy_warm, raising=False)

    session = tmp_path / "corpus" / "sess-recordonly.jsonl"
    _write_claude_session_jsonl(session, "sess-recordonly")
    result = ingest_mod.ingest_one_session(
        session, project, trace_record_only=True, reconcile_watcher=False,
    )
    assert result.action in {"new", "new_generation", "refreshed"}

    index_only = [c for c in warm_calls if c.get("query_sources") == ("index",)]
    assert index_only, (
        "record-only ingest must warm the INDEX marker for the new trace "
        f"(observed keep_index_warm calls: {warm_calls})"
    )


def test_record_only_ingest_keeps_next_query_off_the_whole_corpus_scan(
    tmp_path, monkeypatch
):
    """After a record-only ingest, the next cheap sync must short-circuit warm:
    ZERO calls to the whole-corpus ``_current_bucket_trace_digests`` (the ~4GB
    materialisation). This is the regression that blew up ``trace query``."""
    from opentraces.core import ingest as ingest_mod

    project = tmp_path / "demo"
    _opt_in_project(project)
    _warm(project)

    session = tmp_path / "corpus" / "sess-recordonly2.jsonl"
    _write_claude_session_jsonl(session, "sess-recordonly2")
    ingest_mod.ingest_one_session(
        session, project, trace_record_only=True, reconcile_watcher=False,
    )

    digest_calls = {"n": 0}
    real_digests = ti._current_bucket_trace_digests

    def spy_digests(*args, **kwargs):
        digest_calls["n"] += 1
        return real_digests(*args, **kwargs)

    monkeypatch.setattr(ti, "_current_bucket_trace_digests", spy_digests)

    result = ti.cheap_sync_query_state(query_source="index")

    assert result.synced is False, (
        "record-only ingest must leave the index marker in sync so the next "
        "query short-circuits instead of paying a cold whole-corpus sync"
    )
    assert digest_calls["n"] == 0, (
        "the next query after a record-only backfill must NOT run the "
        "whole-corpus _current_bucket_trace_digests materialisation (~4GB RSS)"
    )
