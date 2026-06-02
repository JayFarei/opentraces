"""Plan 087 F3 — keep-warm + doctor freshness efficiency fixes.

F2's adversarial review (with a REAL-bucket measurement) found that the Phase-2
keep-warm path regressed the hot path: ``keep_index_warm`` ran the ~46s full
digest probe after every ingest and every watcher scan, twice (once per query
source). F3 fixes that in place:

* ``keep_index_warm`` computes the cheap stat-only bucket signal ONCE and
  reuses it across query sources (no double scan).
* Steady state is genuinely O(1): zero ``sync_trace_records_from_local_stores``
  / ``iter_trace_record_objects`` / ``bucket_manifest`` calls, zero refreshes.
* The post-INGEST hook refreshes ONLY the single ``trace_id`` just written (the
  delta ingest already knows it) — it does NOT re-derive a whole-corpus delta
  via the ~46s ``_current_bucket_trace_digests`` scan.
* The default keep-warm query sources for capture/watcher is ``("index",)``;
  the projection is warmed only via F2's now-cheap delta refresh, opt-in.
* A hook exception is swallowed: capture/scan still succeeds.
* ``doctor.search_projection_freshness`` does ZERO heavy ``bucket_manifest``
  recompute on a settled bucket.

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
# F3.1 — steady-state keep_index_warm is O(1): zero heavy bucket work.
# --------------------------------------------------------------------------- #

def test_keep_index_warm_steady_state_does_zero_heavy_work(tmp_path, monkeypatch):
    """On a settled bucket, keep_index_warm() runs NO heavy bucket work:
    zero sync_trace_records_from_local_stores, zero iter_trace_record_objects,
    zero bucket_manifest, and zero refresh_index calls. The only cost is the
    cheap stat-only signal scan."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    counters = {"sync": 0, "iter": 0, "manifest": 0, "refresh_index": 0}

    real_sync = ti.sync_trace_records_from_local_stores

    def spy_sync(*a, **k):
        counters["sync"] += 1
        return real_sync(*a, **k)

    real_iter = ti.iter_trace_record_objects

    def spy_iter(*a, **k):
        counters["iter"] += 1
        return real_iter(*a, **k)

    real_manifest = ti.bucket_manifest

    def spy_manifest(*a, **k):
        counters["manifest"] += 1
        return real_manifest(*a, **k)

    real_refresh = ti.refresh_index

    def spy_refresh(*a, **k):
        counters["refresh_index"] += 1
        return real_refresh(*a, **k)

    monkeypatch.setattr(ti, "sync_trace_records_from_local_stores", spy_sync)
    monkeypatch.setattr(ti, "iter_trace_record_objects", spy_iter)
    monkeypatch.setattr(ti, "bucket_manifest", spy_manifest)
    monkeypatch.setattr(ti, "refresh_index", spy_refresh)

    result = ti.keep_index_warm()
    assert result.ok is True
    assert counters == {
        "sync": 0,
        "iter": 0,
        "manifest": 0,
        "refresh_index": 0,
    }, f"steady-state keep-warm must be O(1), got {counters}"


# --------------------------------------------------------------------------- #
# F3.2 — keep_index_warm computes the cheap signal ONCE across query sources.
# --------------------------------------------------------------------------- #

def test_keep_index_warm_computes_cheap_signal_once(tmp_path, monkeypatch):
    """When keep-warm runs over multiple query sources, the cheap stat-only
    bucket signal is computed ONCE and reused — not once per source (which
    would double the only steady-state cost)."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    signal_calls = {"n": 0}
    real_signal = ti._cheap_bucket_signal

    def spy_signal(*a, **k):
        signal_calls["n"] += 1
        return real_signal(*a, **k)

    monkeypatch.setattr(ti, "_cheap_bucket_signal", spy_signal)

    result = ti.keep_index_warm(query_sources=("index", "projection"))
    assert result.ok is True
    assert signal_calls["n"] == 1, (
        "keep-warm must compute the cheap bucket signal once and reuse it "
        f"across query sources, got {signal_calls['n']}"
    )


# --------------------------------------------------------------------------- #
# F3.3 — post-ingest hook refreshes exactly the one written trace_id.
# --------------------------------------------------------------------------- #

def test_keep_index_warm_with_trace_id_refreshes_only_that_trace(tmp_path, monkeypatch):
    """Passing trace_id (the post-ingest fast path) refreshes ONLY that trace:
    it does NOT re-derive a whole-corpus delta via the ~46s
    _current_bucket_trace_digests scan, and the projection delta carries exactly
    the one written trace_id."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    # Write a NEW trace; the ingest path knows its trace_id.
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    digest_calls = {"n": 0}

    def spy_digests(*a, **k):
        digest_calls["n"] += 1
        raise AssertionError("whole-corpus digest scan must NOT run on the "
                             "trace_id fast path")

    monkeypatch.setattr(ti, "_current_bucket_trace_digests", spy_digests)

    proj_deltas: list[list[str]] = []
    real_proj = sp.refresh_search_projection

    def spy_proj(*args, **kwargs):
        proj_deltas.append(list(kwargs.get("changed_trace_ids") or []))
        return real_proj(*args, **kwargs)

    monkeypatch.setattr(sp, "refresh_search_projection", spy_proj)

    result = ti.keep_index_warm(
        trace_id="trace-gamma", query_sources=("index", "projection")
    )
    assert result.ok is True
    assert digest_calls["n"] == 0
    # The projection refresh carried exactly the one written trace_id.
    assert proj_deltas == [["trace-gamma"]], (
        f"projection delta must be exactly the one written trace, got {proj_deltas}"
    )
    # And the new trace is queryable.
    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


# --------------------------------------------------------------------------- #
# F3.4 — default capture/watcher source is index-only.
# --------------------------------------------------------------------------- #

def test_keep_index_warm_default_source_is_index_only():
    """The default query_sources for keep-warm is ('index',) — the projection
    is warmed only on explicit opt-in (F2's now-cheap delta refresh), keeping
    the per-capture path off the projection's heavier stamp."""
    import inspect

    sig = inspect.signature(ti.keep_index_warm)
    default = sig.parameters["query_sources"].default
    assert default == ("index",), (
        f"default keep-warm source must be index-only, got {default!r}"
    )


# --------------------------------------------------------------------------- #
# F3.5 — hook exception is swallowed; capture/scan still succeeds.
# --------------------------------------------------------------------------- #

def test_keep_index_warm_swallows_failure_capture_survives(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic keep-warm failure")

    monkeypatch.setattr(ti, "cheap_sync_query_state", boom)
    monkeypatch.setattr(ti, "refresh_index", boom)

    result = ti.keep_index_warm(trace_id="trace-gamma")
    assert result.ok is False
    assert result.error is not None


def test_scan_project_keep_warm_failure_does_not_break_scan(tmp_path, monkeypatch):
    from opentraces.core import ingest as ingest_mod

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")

    def boom(*args, **kwargs):
        raise RuntimeError("synthetic keep-warm failure in scan")

    monkeypatch.setattr(ingest_mod, "keep_index_warm", boom, raising=False)

    report = ingest_mod.scan_project(project)
    assert report is not None
    assert report.errored == 0


# --------------------------------------------------------------------------- #
# F3.6 — doctor freshness does zero bucket_manifest recompute on a settled bucket.
# --------------------------------------------------------------------------- #

def test_doctor_freshness_does_zero_manifest_recompute(tmp_path, monkeypatch):
    """search_projection_freshness on a settled bucket runs ZERO heavy
    bucket_manifest recomputes (the ~46s full scan). It compares the cheap
    stat-only signal and reads the persisted on-disk digest only."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    manifest_calls = {"n": 0}
    from opentraces.core import bucket_store as bs

    real_manifest = bs.bucket_manifest

    def spy_manifest(*a, **k):
        manifest_calls["n"] += 1
        return real_manifest(*a, **k)

    monkeypatch.setattr(bs, "bucket_manifest", spy_manifest)

    fresh = sp.search_projection_freshness()
    assert fresh["state"] == "ok"
    assert manifest_calls["n"] == 0, (
        f"doctor freshness must do zero bucket_manifest recompute, got "
        f"{manifest_calls['n']}"
    )
    # It still reports the persisted on-disk bucket digest cheaply (F1).
    assert "persisted_bucket_digest" in fresh
