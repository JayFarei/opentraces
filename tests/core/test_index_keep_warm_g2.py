"""Plan 087 G2 — projection-source cheap-signal seeding + capture keep-warm.

G1 landed the in-place WAL refresh. G2 closes the remaining hot-path blockers:

* SEMANTIC PATH: ``trace query --semantic`` drives
  ``cheap_sync_query_state(query_source="projection")``. Before G2 the
  per-source ``synced_cheap_signal:projection`` marker was NEVER seeded unless a
  prior query had already paid the full ~46s whole-corpus
  ``_current_bucket_trace_digests`` scan, so a steady-state ``--semantic`` query
  re-paid that heavy scan on every call. G2 bootstraps the projection markers
  from the serving build's stamped ``built_from_cheap_signal`` so a settled
  projection short-circuits O(1) — zero heavy scans — exactly like the index
  source does.

* CAPTURE KEEP-WARM: an ingest/watcher keep-warm for the projection source uses
  G1's cheap in-place delta refresh for the single just-written trace_id (no new
  build dir, ~0 disk), and the trace is queryable WITHOUT a manual refresh.

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


def _proj_markers(index_path: Path) -> dict[str, str | None]:
    import sqlite3 as _sqlite3

    out: dict[str, str | None] = {}
    with _sqlite3.connect(str(index_path)) as conn:
        for key in (
            ti._synced_cheap_signal_key("projection"),
            ti._synced_digest_key("projection"),
            ti._synced_trace_digests_key("projection"),
        ):
            row = conn.execute(
                "select value from meta where key = ?", (key,)
            ).fetchone()
            out[key] = row[0] if row else None
    return out


# --------------------------------------------------------------------------- #
# G2.1 — steady-state projection cheap_sync does ZERO heavy scans.
#
# The blocker: on a settled bucket, the very first
# cheap_sync_query_state(query_source="projection") after a build (i.e. the
# projection markers are not yet seeded) must NOT pay the whole-corpus
# _current_bucket_trace_digests scan. It bootstraps from the serving build's
# stamped built_from_cheap_signal and short-circuits O(1).
# --------------------------------------------------------------------------- #

def test_projection_cheap_sync_steady_state_zero_heavy_scan(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))

    # Warm the INDEX source only (mirrors the capture/watcher default), then
    # build the projection. The projection markers are intentionally NOT seeded
    # by any projection-source cheap_sync here — this is the cold-projection-
    # markers state that a fresh build + first --semantic query hits in prod.
    ti.rebuild_index()
    ti.cheap_sync_query_state(query_source="index")
    sp.build_search_projection()

    markers = _proj_markers(ti.default_index_path())
    assert markers[ti._synced_cheap_signal_key("projection")] is None, (
        "precondition: projection cheap-signal marker must start unseeded"
    )

    # Now spy on the heavy whole-corpus scan paths. A steady-state projection
    # cheap_sync MUST NOT touch any of them.
    counters = {"digests": 0, "sync": 0, "iter": 0, "manifest": 0}

    def spy_digests(*a, **k):
        counters["digests"] += 1
        raise AssertionError(
            "steady-state projection cheap_sync must not run the whole-corpus "
            "_current_bucket_trace_digests scan"
        )

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

    monkeypatch.setattr(ti, "_current_bucket_trace_digests", spy_digests)
    monkeypatch.setattr(ti, "sync_trace_records_from_local_stores", spy_sync)
    monkeypatch.setattr(ti, "iter_trace_record_objects", spy_iter)
    monkeypatch.setattr(ti, "bucket_manifest", spy_manifest)

    # First projection cheap_sync after a build: bootstraps markers, no heavy work.
    res1 = ti.cheap_sync_query_state(query_source="projection")
    assert res1.synced is False, "settled projection must be a no-op sync"
    assert counters == {"digests": 0, "sync": 0, "iter": 0, "manifest": 0}, (
        f"first steady-state projection cheap_sync did heavy work: {counters}"
    )

    # Markers are now seeded so the NEXT call also short-circuits O(1).
    markers2 = _proj_markers(ti.default_index_path())
    assert markers2[ti._synced_cheap_signal_key("projection")] is not None

    res2 = ti.cheap_sync_query_state(query_source="projection")
    assert res2.synced is False
    assert counters == {"digests": 0, "sync": 0, "iter": 0, "manifest": 0}, (
        f"second steady-state projection cheap_sync did heavy work: {counters}"
    )


# --------------------------------------------------------------------------- #
# G2.2 — capture keep-warm for the projection uses an in-place delta (no new
# build dir, ~0 disk) and the trace is queryable without a manual refresh.
# --------------------------------------------------------------------------- #

def test_capture_keep_warm_projection_in_place_delta_no_new_build(tmp_path):
    from opentraces.core import paths

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))

    ti.rebuild_index()
    sp.build_search_projection()
    # Seed both markers like a warm system.
    ti.cheap_sync_query_state(query_source="index")
    ti.cheap_sync_query_state(query_source="projection")

    root = paths.search_projection_root(sp.SEARCH_PROJECTION_VERSION)
    builds_dir = root / "builds"
    before_build_dirs = {p.name for p in builds_dir.iterdir() if p.is_dir()}
    serving_build_id = sp.search_projection_status()["build_id"]

    # Simulate a capture: a new trace lands in the bucket, then the post-INGEST
    # keep-warm runs for that exact trace_id over BOTH sources.
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))
    result = ti.keep_index_warm(
        trace_id="trace-gamma", query_sources=("index", "projection")
    )
    assert result.ok is True

    # No NEW build dir: the in-place delta mutated the serving sqlite.
    after_build_dirs = {p.name for p in builds_dir.iterdir() if p.is_dir()}
    assert after_build_dirs == before_build_dirs, (
        f"capture keep-warm created a new build dir (must be in-place): "
        f"{after_build_dirs - before_build_dirs}"
    )
    assert sp.search_projection_status()["build_id"] == serving_build_id

    # The freshly captured trace is queryable in the PROJECTION without a manual
    # refresh — the Phase-1 stale window is closed for --semantic too.
    page = sp.query_search_projection_page(lex="gamma", build_if_missing=False)
    assert any(c.trace_id == "trace-gamma" for c in page.candidates), (
        "captured trace must be queryable in the projection without a manual refresh"
    )


# --------------------------------------------------------------------------- #
# G2.3 — the watcher sweep keep-warm also warms the projection source.
# --------------------------------------------------------------------------- #

def test_scan_project_keep_warm_includes_projection_source(tmp_path, monkeypatch):
    """The watcher sweep's final keep_index_warm must warm the projection source
    too (not index-only), so a sweep that refreshed sessions leaves --semantic
    fresh."""
    from opentraces.core import ingest as ingest_mod

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    ti.rebuild_index()
    sp.build_search_projection()

    captured: list[dict] = []
    real = ingest_mod.keep_index_warm

    def spy(*args, **kwargs):
        captured.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(ingest_mod, "keep_index_warm", spy, raising=False)

    ingest_mod.scan_project(project)

    # At least one sweep-level keep_index_warm call must include the projection
    # source.
    assert any(
        "projection" in (call.get("query_sources") or ())
        for call in captured
    ), f"watcher sweep keep-warm never warmed the projection source: {captured}"


# --------------------------------------------------------------------------- #
# G2.4 — doctor freshness reports built_from lag cheaply (no bucket recompute).
# (G1 wired the cheap probe; G2 asserts the lag field is present + cheap.)
# --------------------------------------------------------------------------- #

def test_doctor_freshness_reports_built_from_lag_cheaply(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    ti.rebuild_index()
    sp.build_search_projection()

    from opentraces.core import bucket_store as bs

    manifest_calls = {"n": 0}
    real_manifest = bs.bucket_manifest

    def spy_manifest(*a, **k):
        manifest_calls["n"] += 1
        return real_manifest(*a, **k)

    monkeypatch.setattr(bs, "bucket_manifest", spy_manifest)

    fresh = sp.search_projection_freshness()
    assert fresh["state"] == "ok"
    # Reads projection_meta / persisted digest cheaply — zero heavy recompute.
    assert manifest_calls["n"] == 0
    # Reports built_from lag against the persisted on-disk digest.
    assert "built_from_digest_lag" in fresh
    assert "persisted_bucket_digest" in fresh
    assert "built_from_digest" in fresh
