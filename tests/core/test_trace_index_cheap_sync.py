"""Plan 087 U4 — cheap-sync-then-serve for the ``trace query`` hot path.

Phase 1 (U0/U1) made the warm-cache query path stop auto-rebuilding, which
removed the 105s tax but opened a *stale window*: a freshly captured/altered
trace was invisible to ``trace query`` until an explicit ``trace index
refresh``. U4 closes that window WITHOUT reintroducing the full-rebuild cost.

The contract pinned here:

* ``cheap_sync_query_state`` is a digest-gated incremental sync. After a trace
  is written or altered, calling it advances the index (and, for the
  projection source, the search projection) so the next query reflects the
  change — via ``refresh_index`` / ``refresh_search_projection`` (bounded
  delta), never ``rebuild_index`` / ``build_search_projection`` (full corpus).
* Steady state (bucket digest unchanged since the last sync) short-circuits:
  zero ``refresh_index`` calls, zero ``refresh_search_projection`` calls, and
  the wall-clock stays well under the 2s budget.

NOTE (read-only snapshot architecture): ``trace query`` no longer routes
through cheap sync at all — queries serve an immutable SQLite FTS snapshot
and never build/sync/mutate. ``cheap_sync_query_state`` remains the bounded
incremental maintenance primitive for the capture-time keep-warm path
(``keep_index_warm`` / ``trace index refresh``), which is what these core
contracts pin. The CLI-facing test at the bottom pins the new read-only
query contract.

The autouse ``_isolate_opentraces_global_state`` fixture (tests/conftest.py)
redirects ``~/.opentraces`` into a tmp HOME, so these are hermetic.
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
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
            Step(
                step_index=1,
                role="user",
                content=f"Please use {term} to fix the parser.",
            ),
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
    """Build a warm index + projection from the current traces on disk."""
    ti.rebuild_index()
    sp.build_search_projection()
    # Record the synced digest so steady-state probes short-circuit.
    ti.cheap_sync_query_state(query_source="index")
    ti.cheap_sync_query_state(query_source="projection")


def test_cheap_sync_reflects_new_trace_without_full_rebuild(tmp_path, monkeypatch):
    """R2 — a trace written after warm-up becomes queryable via a bounded
    incremental sync, never a full rebuild."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    # The new trace is not yet in the warm index.
    page = ti.query_index_page(lex="gamma")
    assert all(c.trace_id != "trace-gamma" for c in page.candidates)

    # Write a brand-new trace.
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    rebuild_calls = {"n": 0}
    refresh_calls = {"n": 0}
    refresh_kwargs: list[dict] = []
    real_rebuild = ti.rebuild_index
    real_refresh = ti.refresh_index

    def spy_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        return real_rebuild(*args, **kwargs)

    def spy_refresh(*args, **kwargs):
        refresh_calls["n"] += 1
        refresh_kwargs.append(dict(kwargs))
        return real_refresh(*args, **kwargs)

    monkeypatch.setattr(ti, "rebuild_index", spy_rebuild)
    monkeypatch.setattr(ti, "refresh_index", spy_refresh)

    result = ti.cheap_sync_query_state(query_source="index")

    assert result.synced is True
    assert "trace-gamma" in set(result.changed_trace_ids)
    assert rebuild_calls["n"] == 0, "cheap sync must not full-rebuild the index"
    assert refresh_calls["n"] == 1, "cheap sync should incrementally refresh once"
    assert refresh_kwargs[0].get("allow_rebuild") is False
    assert refresh_kwargs[0].get("refresh_trails") is False

    # The query now reflects the new trace.
    page = ti.query_index_page(lex="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_cheap_sync_steady_state_does_zero_refresh_work(tmp_path, monkeypatch):
    """Steady state: no bucket change since last sync -> zero refresh calls."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    rebuild_calls = {"n": 0}
    refresh_calls = {"n": 0}
    real_rebuild = ti.rebuild_index
    real_refresh = ti.refresh_index

    def spy_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        return real_rebuild(*args, **kwargs)

    def spy_refresh(*args, **kwargs):
        refresh_calls["n"] += 1
        return real_refresh(*args, **kwargs)

    monkeypatch.setattr(ti, "rebuild_index", spy_rebuild)
    monkeypatch.setattr(ti, "refresh_index", spy_refresh)

    start = time.perf_counter()
    result = ti.cheap_sync_query_state(query_source="index")
    elapsed = time.perf_counter() - start

    assert result.synced is False, "steady state must short-circuit"
    assert result.changed_trace_ids == []
    assert rebuild_calls["n"] == 0
    assert refresh_calls["n"] == 0, "steady-state probe must not refresh"
    assert elapsed < 2.0, f"steady-state probe took {elapsed:.2f}s (> 2s budget)"


def test_repeated_projection_cheap_sync_stays_warm(tmp_path, monkeypatch):
    """U1 — many sequential discovery-loop queries must not re-enter the
    corpus-digest delta path after the warm marker is set."""

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    digest_calls = {"n": 0}

    def spy_digests(*args, **kwargs):
        digest_calls["n"] += 1
        raise AssertionError("steady-state repeated queries must not scan the corpus")

    monkeypatch.setattr(ti, "_current_bucket_trace_digests", spy_digests)

    results = [ti.cheap_sync_query_state(query_source="projection") for _ in range(20)]

    assert all(result.synced is False for result in results)
    assert digest_calls["n"] == 0


def test_parallel_projection_cheap_sync_pays_delta_once(tmp_path, monkeypatch):
    """U1 — concurrent callers seeing the same stale bucket serialize behind
    one delta sync, then re-check the marker and serve warm."""

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    digest_calls = {"n": 0}
    refresh_index_calls = {"n": 0}
    refresh_projection_calls = {"n": 0}
    real_digests = ti._current_bucket_trace_digests
    real_refresh_index = ti.refresh_index
    real_refresh_projection = sp.refresh_search_projection

    def spy_digests(*args, **kwargs):
        digest_calls["n"] += 1
        return real_digests(*args, **kwargs)

    def spy_refresh_index(*args, **kwargs):
        refresh_index_calls["n"] += 1
        return real_refresh_index(*args, **kwargs)

    def spy_refresh_projection(*args, **kwargs):
        refresh_projection_calls["n"] += 1
        return real_refresh_projection(*args, **kwargs)

    monkeypatch.setattr(ti, "_current_bucket_trace_digests", spy_digests)
    monkeypatch.setattr(ti, "refresh_index", spy_refresh_index)
    monkeypatch.setattr(sp, "refresh_search_projection", spy_refresh_projection)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _i: ti.cheap_sync_query_state(query_source="projection"),
                range(8),
            )
        )

    assert sum(1 for result in results if result.synced) == 1
    assert digest_calls["n"] == 1
    assert refresh_index_calls["n"] == 1
    assert refresh_projection_calls["n"] == 1


def test_cheap_sync_steady_state_does_zero_heavy_corpus_scan(tmp_path, monkeypatch):
    """F1 — the steady-state freshness probe is O(1): zero corpus scans.

    The plan-087 regression was that ``cheap_sync_query_state`` unconditionally
    ran ``sync_trace_records_from_local_stores`` + ``iter_trace_record_objects``
    + ``bucket_manifest`` to *produce* the digest BEFORE the short-circuit, so a
    true steady-state query paid ~46s on the live bucket. This pins the fix:
    when the cheap filesystem signal is unchanged, the probe makes ZERO calls to
    any of those heavy functions.
    """
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    sync_calls = {"n": 0}
    iter_calls = {"n": 0}
    manifest_calls = {"n": 0}
    real_sync = ti.sync_trace_records_from_local_stores
    real_iter = ti.iter_trace_record_objects
    real_manifest = ti.bucket_manifest

    def spy_sync(*args, **kwargs):
        sync_calls["n"] += 1
        return real_sync(*args, **kwargs)

    def spy_iter(*args, **kwargs):
        iter_calls["n"] += 1
        return real_iter(*args, **kwargs)

    def spy_manifest(*args, **kwargs):
        manifest_calls["n"] += 1
        return real_manifest(*args, **kwargs)

    monkeypatch.setattr(ti, "sync_trace_records_from_local_stores", spy_sync)
    monkeypatch.setattr(ti, "iter_trace_record_objects", spy_iter)
    monkeypatch.setattr(ti, "bucket_manifest", spy_manifest)

    result = ti.cheap_sync_query_state(query_source="index")

    assert result.synced is False, "steady state must short-circuit"
    assert sync_calls["n"] == 0, "steady-state probe must not sync local stores"
    assert iter_calls["n"] == 0, "steady-state probe must not iterate trace objects"
    assert manifest_calls["n"] == 0, "steady-state probe must not recompute the manifest"


def test_cheap_sync_touch_flips_cheap_signal_into_delta_path(tmp_path, monkeypatch):
    """F1 — bumping a trace file's mtime flips the cheap signal so the delta
    path engages without running the legacy bucket mirror."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    from opentraces.core.config import get_project_traces_dir

    # Steady state first: cheap signal matches -> no heavy work.
    sync_calls = {"n": 0}
    refresh_calls = {"n": 0}
    refresh_kwargs: list[dict] = []
    real_sync = ti.sync_trace_records_from_local_stores
    real_refresh = ti.refresh_index

    def spy_sync(*args, **kwargs):
        sync_calls["n"] += 1
        return real_sync(*args, **kwargs)

    def spy_refresh(*args, **kwargs):
        refresh_calls["n"] += 1
        refresh_kwargs.append(dict(kwargs))
        return real_refresh(*args, **kwargs)

    monkeypatch.setattr(ti, "sync_trace_records_from_local_stores", spy_sync)
    monkeypatch.setattr(ti, "refresh_index", spy_refresh)

    ti.cheap_sync_query_state(query_source="index")
    assert sync_calls["n"] == 0, "steady-state must not sync"
    assert refresh_calls["n"] == 0, "steady-state must not refresh"

    # Touch the trace file (bump mtime + grow it) -> cheap signal changes.
    trace_file = get_project_traces_dir(project) / "trace-alpha.jsonl"
    bumped = time.time() + 10
    os.utime(trace_file, (bumped, bumped))

    result = ti.cheap_sync_query_state(query_source="index")
    assert result.synced is True
    assert sync_calls["n"] == 0, "delta path must not run the legacy bucket mirror"
    assert refresh_calls["n"] == 1, "changed cheap signal must refresh the index"
    assert refresh_kwargs[0].get("allow_rebuild") is False
    assert refresh_kwargs[0].get("refresh_trails") is False


def test_cheap_sync_large_delta_refuses_before_refresh(tmp_path, monkeypatch):
    """A large legacy delta is explicit maintenance, not a surprise long repair."""
    from opentraces.core import trace_index_sync as tis

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    refresh_calls = {"n": 0}

    def fail_refresh(*args, **kwargs):
        refresh_calls["n"] += 1
        raise AssertionError("large deltas must not enter refresh_index")

    monkeypatch.setattr(tis, "MAX_INCREMENTAL_TRACE_DELTA", 0)
    monkeypatch.setattr(ti, "refresh_index", fail_refresh)

    result = ti.cheap_sync_query_state(query_source="index")

    assert result.synced is False
    assert result.maintenance_required == "index:large_delta:1:max:0"
    assert refresh_calls["n"] == 0


def test_cheap_sync_unchanged_digest_records_post_sync_signal(tmp_path, monkeypatch):
    """When the heavy digest proves no logical data changed, record the cheap
    signal *after* local-store mirroring.

    The live real-bucket failure was: cheap signal changed, the digest path ran,
    ``sync_trace_records_from_local_stores`` touched mirror files but the digest
    stayed equal, and we stored the pre-sync signal. The next query immediately
    saw a different post-sync signal and paid the heavy path again.
    """

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    db_path = ti.default_index_path()
    with ti._connect(db_path) as conn:
        prev_digest = ti._meta_get(conn, ti._synced_digest_key("index"))
        ti._meta_set(conn, ti._synced_cheap_signal_key("index"), "old-signal")
        conn.commit()
    assert prev_digest is not None

    digest_calls = {"n": 0}

    def same_digest_after_sync(*args, **kwargs):
        digest_calls["n"] += 1
        return prev_digest, {}

    monkeypatch.setattr(ti, "_current_bucket_trace_digests", same_digest_after_sync)
    monkeypatch.setattr(ti, "_cheap_bucket_signal", lambda: "post-sync-signal")

    result = ti.cheap_sync_query_state(query_source="index", cheap_signal="pre-sync-signal")

    assert result.synced is False
    assert digest_calls["n"] == 1
    with ti._connect(db_path) as conn:
        assert (
            ti._meta_get(conn, ti._synced_cheap_signal_key("index"))
            == "post-sync-signal"
        )


def test_cheap_sync_projection_source_uses_bounded_delta(tmp_path, monkeypatch):
    """For --semantic (projection source) a new trace triggers an incremental
    refresh_search_projection, not a full build."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)

    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    build_calls = {"n": 0}
    refresh_proj_calls: list[dict] = []
    doc_for_unit_calls: list[str] = []
    real_build = sp.build_search_projection
    real_refresh_proj = sp.refresh_search_projection
    real_doc = sp._doc_for_unit

    def spy_build(*args, **kwargs):
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    def spy_refresh_proj(*args, **kwargs):
        refresh_proj_calls.append(dict(kwargs))
        return real_refresh_proj(*args, **kwargs)

    def spy_doc(unit, **kwargs):
        doc_for_unit_calls.append(unit.trace_id)
        return real_doc(unit, **kwargs)

    monkeypatch.setattr(sp, "build_search_projection", spy_build)
    monkeypatch.setattr(sp, "refresh_search_projection", spy_refresh_proj)
    monkeypatch.setattr(sp, "_doc_for_unit", spy_doc)

    result = ti.cheap_sync_query_state(query_source="projection")

    assert result.synced is True
    assert "trace-gamma" in set(result.changed_trace_ids)
    assert build_calls["n"] == 0, "projection sync must not full-build"
    assert len(refresh_proj_calls) == 1, "exactly one incremental projection refresh"
    # Only the changed trace's docs were re-materialized (R2).
    assert set(doc_for_unit_calls) == {"trace-gamma"}

    page = sp.query_search_projection_page(semantic="gamma")
    assert any(c.trace_id == "trace-gamma" for c in page.candidates)


def test_cheap_sync_detects_deleted_trace(tmp_path):
    """A removed trace is reported as deleted and pruned from the projection."""
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _write_project_trace(project, _trace_with("trace-beta", "beta"))
    _warm(project)

    # Remove trace-beta from the project store.
    from opentraces.core.config import get_project_traces_dir

    (get_project_traces_dir(project) / "trace-beta.jsonl").unlink()

    result = ti.cheap_sync_query_state(query_source="projection")
    assert result.synced is True
    assert "trace-beta" in set(result.deleted_trace_ids)

    page = sp.query_search_projection_page(semantic="beta")
    assert all(c.trace_id != "trace-beta" for c in page.candidates)


def test_trace_query_is_read_only_and_reflects_new_trace_after_explicit_rebuild(
    tmp_path, monkeypatch
):
    """End-to-end: ``trace query`` never syncs/rebuilds at query time anymore.

    The plan-087 cheap-sync-then-serve hot path was replaced by the explicit
    read-only search snapshot: a trace written after the snapshot build (a
    direct file write, which does NOT mark the snapshot dirty) is served STALE
    (absent, status ok) without triggering any index rebuild. Only an explicit
    ``opentraces trace index`` makes it queryable.
    """
    from click.testing import CliRunner

    from opentraces.cli import main
    from opentraces.core.trace_search_snapshot import build_trace_search_snapshot

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _warm(project)
    build_trace_search_snapshot()

    # Brand-new trace after the snapshot was built.
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))

    rebuild_calls = {"n": 0}
    real_rebuild = ti.rebuild_index

    def spy_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        return real_rebuild(*args, **kwargs)

    monkeypatch.setattr(ti, "rebuild_index", spy_rebuild)

    runner = CliRunner()
    stale = runner.invoke(main, ["trace", "query", "--lex", "gamma", "--json"])
    assert stale.exit_code == 0, stale.output
    stale_payload = json.loads(stale.output)
    assert all(c["trace_id"] != "trace-gamma" for c in stale_payload["candidates"]), (
        "query must serve the immutable snapshot, not sync in the new trace"
    )
    assert stale_payload["search_diagnostics"]["wrote_to_index"] is False
    assert stale_payload["search_diagnostics"]["rebuilt_index"] is False
    assert rebuild_calls["n"] == 0, "CLI query must never rebuild the index"

    # Maintenance path (capture-time keep-warm in real flows): the bounded
    # cheap sync mirrors the new trace into the bucket store — still no full
    # rebuild — and the explicit snapshot rebuild then picks it up.
    sync = ti.cheap_sync_query_state(query_source="index")
    assert sync.synced is True
    assert "trace-gamma" in set(sync.changed_trace_ids)

    rebuilt = runner.invoke(main, ["trace", "index", "--json"])
    assert rebuilt.exit_code == 0, rebuilt.output

    result = runner.invoke(main, ["trace", "query", "--lex", "gamma", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    trace_ids = {c["trace_id"] for c in payload["candidates"]}
    assert "trace-gamma" in trace_ids
    assert rebuild_calls["n"] == 0, (
        "the snapshot rebuild must not full-rebuild the legacy Trace Index"
    )
