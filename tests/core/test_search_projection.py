"""Local bucket-shaped search projection behavior."""

from __future__ import annotations

import json
from pathlib import Path

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _trace() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-local-search-projection",
        session_id="session-local-search-projection",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Build a clack prompt and prove the parser path"},
        dependencies=["@clack/prompts", "pytest"],
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Use clack to add an interactive prompt, then run the parser test.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Plan:\n- inspect prompt code\n- edit the parser prompt\n- run pytest",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-read",
                        tool_name="Read",
                        input={"file_path": "src/prompt.ts"},
                    ),
                    ToolCall(
                        tool_call_id="tc-edit",
                        tool_name="Edit",
                        input={
                            "file_path": "src/prompt.ts",
                            "old_string": "text({ message: 'Name' })",
                            "new_string": "confirm({ message: 'Ship it?' })",
                        },
                    ),
                    ToolCall(
                        tool_call_id="tc-test",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc-test",
                        content="tests/test_parser.py . 1 passed",
                        output_summary="1 passed",
                    )
                ],
            ),
        ],
        outcome={"success": True, "committed": False},
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


def test_docs_default_to_local_source(tmp_path):
    """U3: a freshly built projection tags every doc with source='local'."""

    import sqlite3 as _sqlite3

    from opentraces.core.search_projection import (
        DEFAULT_SOURCE,
        build_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    summary = build_search_projection()
    assert summary.doc_count > 0

    assert DEFAULT_SOURCE == "local"
    status = search_projection_status()
    with _sqlite3.connect(status["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        sources = {row["source"] for row in conn.execute("select source from docs")}
        pt_sources = {
            row["source"] for row in conn.execute("select source from projection_traces")
        }
    assert sources == {"local"}
    assert pt_sources == {"local"}

    # The serialized docs.jsonl also carries source='local' on every row.
    docs = [json.loads(line) for line in summary.docs_path.read_text().splitlines() if line]
    assert all(doc.get("source") == "local" for doc in docs)


def test_remote_scoped_query_returns_only_that_source(tmp_path):
    """U3: a query scoped to a remote source returns only docs from that source
    and a default (local) query returns only local docs, never the remote ones."""

    from opentraces.core.search_projection import (
        build_search_projection,
        query_search_projection_page,
        refresh_search_projection,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _write_project_trace(project, _trace_with("trace-beta", "beta"))
    rebuild_index()
    build_search_projection()

    # Re-tag trace-beta's docs as coming from a remote bucket source.
    remote = "remote:acme/agent-traces"
    refresh_search_projection(changed_trace_ids=["trace-beta"], source=remote)

    # Default query (no source) excludes the remote docs entirely.
    local_page = query_search_projection_page(lex="parser", limit=20)
    local_traces = {c.trace_id for c in local_page.candidates}
    assert "trace-alpha" in local_traces
    assert "trace-beta" not in local_traces

    # A remote-scoped query returns only that source's docs.
    remote_page = query_search_projection_page(lex="parser", source=remote, limit=20)
    remote_traces = {c.trace_id for c in remote_page.candidates}
    assert remote_traces == {"trace-beta"}


def test_source_less_docs_treated_as_local(tmp_path):
    """U3 back-compat: a legacy build whose docs predate the source column is
    treated as local — a default query still returns them."""

    import sqlite3 as _sqlite3

    from opentraces.core.search_projection import (
        build_search_projection,
        query_search_projection_page,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    build_search_projection()

    # Simulate a legacy doc whose stored payload has no 'source' key and whose
    # docs-row source column is empty (pre-U3 shape).
    status = search_projection_status()
    sqlite_path = status["sqlite_path"]
    with _sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = _sqlite3.Row
        rows = conn.execute("select doc_id, payload_json from docs").fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            payload.pop("source", None)
            conn.execute(
                "update docs set source = '', payload_json = ? where doc_id = ?",
                (json.dumps(payload), row["doc_id"]),
            )
        conn.commit()

    # A default (local) query still surfaces the source-less docs.
    page = query_search_projection_page(lex="parser", limit=20)
    assert "trace-alpha" in {c.trace_id for c in page.candidates}


def test_refresh_search_projection_reindexes_only_changed_traces(tmp_path):
    """R2: an incremental refresh touching N trace_ids re-materializes only
    those N traces' docs, not the entire corpus."""

    from opentraces.core import search_projection as sp
    from opentraces.core.search_projection import (
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _write_project_trace(project, _trace_with("trace-beta", "beta"))

    rebuild_index()
    full = build_search_projection()
    assert full.trace_count == 2
    baseline_docs = full.doc_count

    # Add a third trace and refresh the index so its units exist.
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))
    refresh_index()

    calls: list[str] = []
    original = sp._doc_for_unit

    def _spy(unit, **kwargs):
        calls.append(unit.trace_id)
        return original(unit, **kwargs)

    monkeypatch_target = sp
    orig_fn = monkeypatch_target._doc_for_unit
    monkeypatch_target._doc_for_unit = _spy
    try:
        summary = refresh_search_projection(changed_trace_ids=["trace-gamma"])
    finally:
        monkeypatch_target._doc_for_unit = orig_fn

    # Only the changed trace's units were re-materialized.
    assert set(calls) == {"trace-gamma"}
    # In-place model: the refresh mutates the SAME serving build (no new build
    # dir, no pointer advance — COMMIT is the publish).
    assert summary.build_id == full.build_id
    # The corpus grew by exactly the new trace's docs; pre-existing docs survived.
    assert summary.trace_count == 3
    assert summary.doc_count > baseline_docs

    status = search_projection_status()
    assert status["state"] == "ok"
    assert status["build_id"] == summary.build_id

    # The new trace is now queryable in the projection sqlite.
    import json as _json
    import sqlite3 as _sqlite3

    with _sqlite3.connect(status["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        traces = {
            row["trace_id"]
            for row in conn.execute("select distinct trace_id from docs")
        }
        pt_rows = {
            row["trace_id"]: row["doc_count"]
            for row in conn.execute("select trace_id, doc_count from projection_traces")
        }
    assert {"trace-alpha", "trace-beta", "trace-gamma"} <= traces
    # projection_traces table mirrors per-trace doc counts.
    assert "trace-gamma" in pt_rows
    assert pt_rows["trace-gamma"] >= 1
    assert sum(pt_rows.values()) == summary.doc_count

    manifest = status["manifest"]
    assert "built_from_digest" in manifest


def test_refresh_search_projection_deletes_removed_traces(tmp_path):
    """A deleted trace_id removes its docs and projection_traces row."""

    from opentraces.core.search_projection import (
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _write_project_trace(project, _trace_with("trace-beta", "beta"))
    rebuild_index()
    build_search_projection()

    summary = refresh_search_projection(deleted_trace_ids=["trace-beta"])
    assert summary.trace_count == 1

    status = search_projection_status()
    import sqlite3 as _sqlite3

    with _sqlite3.connect(status["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        traces = {
            row["trace_id"] for row in conn.execute("select distinct trace_id from docs")
        }
        pt = {
            row["trace_id"] for row in conn.execute("select trace_id from projection_traces")
        }
    assert "trace-beta" not in traces
    assert "trace-beta" not in pt
    assert "trace-alpha" in traces


def test_refresh_search_projection_atomic_on_interruption(tmp_path):
    """R8 (in-place WAL): a crash/raise mid-transaction rolls back — the prior
    projection is left intact and queryable, with no half-written state. The
    serving build_id is unchanged (COMMIT is the publish; it never happened)."""

    from opentraces.core import search_projection as sp
    from opentraces.core.search_projection import (
        build_search_projection,
        query_search_projection_page,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    full = build_search_projection()
    prior_pointer = json.loads(full.current_path.read_text())

    _write_project_trace(project, _trace_with("trace-beta", "beta"))
    refresh_index()

    class _Boom(RuntimeError):
        pass

    # Raise mid-transaction: _insert_doc is called inside the BEGIN IMMEDIATE
    # block, AFTER deletes have run, so a naive non-transactional writer would
    # leave the DB half-mutated. The WAL rollback must restore the prior state.
    orig_insert = sp._insert_doc

    def _explode_insert(conn, doc):
        raise _Boom("crash mid-transaction")

    sp._insert_doc = _explode_insert
    try:
        try:
            refresh_search_projection(changed_trace_ids=["trace-beta"])
            raised = False
        except _Boom:
            raised = True
    finally:
        sp._insert_doc = orig_insert

    assert raised, "refresh should have been interrupted mid-transaction"

    # The pointer / serving build is unchanged (no publish happened).
    status = search_projection_status()
    assert status["state"] == "ok"
    assert status["build_id"] == full.build_id
    after_pointer = json.loads(full.current_path.read_text())
    assert after_pointer == prior_pointer

    # The serving sqlite is intact: trace-alpha survived, trace-beta never landed
    # (the mid-transaction deletes were rolled back too — no half state).
    import sqlite3 as _sqlite3

    with _sqlite3.connect(status["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        traces = {
            row["trace_id"] for row in conn.execute("select distinct trace_id from docs")
        }
    assert traces == {"trace-alpha"}

    # And it is still QUERYABLE — the prior projection serves correctly.
    page = query_search_projection_page(lex="alpha", limit=20)
    assert "trace-alpha" in {c.trace_id for c in page.candidates}


def test_refresh_concurrent_delta_loss_race(tmp_path):
    """R4/R8 ADVERSARIAL: two TRULY concurrent in-place refreshes against the
    same serving build must NOT drop either delta.

    Under the old copy+CAS model, two refreshes that both copied from B0 and both
    passed the CAS window clobbered each other (last-writer-wins) and one delta
    was lost. The in-place WAL model serializes every writer with an exclusive
    flock (+ BEGIN IMMEDIATE), so two threads that fire at the same instant are
    forced to commit one after the other against the same DB — both deltas land.

    We launch both refreshes from a barrier so they genuinely contend on the
    writer lock (not merely run sequentially).
    """

    import threading

    from opentraces.core.search_projection import (
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    build_search_projection()  # serving build B0

    # Two NEW traces land; thread A refreshes beta, thread B refreshes gamma.
    _write_project_trace(project, _trace_with("trace-beta", "beta"))
    _write_project_trace(project, _trace_with("trace-gamma", "gamma"))
    refresh_index()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def _refresh(tid: str) -> None:
        try:
            barrier.wait(timeout=10)
            refresh_search_projection(changed_trace_ids=[tid])
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            errors.append(exc)

    ta = threading.Thread(target=_refresh, args=("trace-beta",))
    tb = threading.Thread(target=_refresh, args=("trace-gamma",))
    ta.start()
    tb.start()
    ta.join(timeout=30)
    tb.join(timeout=30)

    assert not errors, f"concurrent refresh raised: {errors!r}"

    # Both deltas (beta AND gamma) must be present — the flock serialized the two
    # writers so neither was lost.
    import sqlite3 as _sqlite3

    status = search_projection_status()
    with _sqlite3.connect(status["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        traces = {
            row["trace_id"]
            for row in conn.execute("select distinct trace_id from docs")
        }
    assert "trace-beta" in traces, "thread A's delta was lost"
    assert "trace-gamma" in traces, "thread B's delta was lost (lock did not protect it)"
    assert "trace-alpha" in traces

    # projection_meta counts agree with the actual docs/traces (no double-count
    # or skew from the concurrent commits).
    assert status["trace_count"] == len(traces)


def test_search_projection_writes_immutable_local_bucket_build(tmp_path):
    from opentraces.core import paths
    from opentraces.core.search_projection import (
        SEARCH_DOC_SCHEMA_VERSION,
        build_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace())

    rebuild_index()
    summary = build_search_projection()

    assert summary.root_path == paths.OPENTRACES_DIR / "bucket" / "projections" / "search" / "v1"
    assert summary.doc_count == summary.unit_count
    assert summary.doc_count > 1
    assert summary.trace_count == 1
    assert summary.docs_path.exists()
    assert summary.sqlite_path.exists()
    assert summary.manifest_path.exists()
    assert summary.current_path.exists()

    docs = [json.loads(line) for line in summary.docs_path.read_text().splitlines()]
    trace_doc = next(doc for doc in docs if doc["doc_type"] == "trace")
    assert trace_doc["schema_version"] == SEARCH_DOC_SCHEMA_VERSION
    assert trace_doc["trace_id"] == "trace-local-search-projection"
    assert trace_doc["doc_id"] == "sd:tu:trace-local-search-projection:trace"
    assert "clack" in trace_doc["text"].lower()
    assert trace_doc["content_hash"]
    assert "ot://trace/trace-local-search-projection/map" in trace_doc["evidence_refs"]
    assert "library:clack" in trace_doc["semantic"]["concept_ids"]
    assert "interactive cli" in trace_doc["search_text"]

    pointer = json.loads(summary.current_path.read_text())
    assert pointer["build_id"] == summary.build_id
    assert pointer["manifest_path"] == f"builds/{summary.build_id}/manifest.json"

    status = search_projection_status()
    assert status["state"] == "ok"
    assert status["build_id"] == summary.build_id
    assert status["doc_count"] == summary.doc_count
    assert status["sqlite_path"] == str(summary.sqlite_path)
    assert status["embedding_ready"] is False


# --- F2: refresh must be O(delta), not O(corpus) --------------------------


def _build_corpus(project: Path, n: int) -> None:
    """Write ``n`` distinct traces into a project (no index build yet)."""

    for i in range(n):
        _write_project_trace(project, _trace_with(f"trace-bulk-{i:05d}", f"term{i}"))


def test_refresh_does_not_rewrite_full_docs_jsonl(tmp_path):
    """(b) In-place delta refresh: NO new build dir, docs.jsonl is NOT rewritten
    (mtime unchanged), docs_jsonl_stale flips true, and the whole-corpus read-back
    (``_all_docs``) never happens. Mutation is in the serving sqlite only."""

    from opentraces.core import paths, search_projection as sp
    from opentraces.core.search_projection import (
        SEARCH_PROJECTION_VERSION,
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _build_corpus(project, 30)
    rebuild_index()
    full = build_search_projection()

    builds_dir = paths.search_projection_root(SEARCH_PROJECTION_VERSION) / "builds"
    build_dirs_before = {p.name for p in builds_dir.iterdir() if p.is_dir()}
    docs_jsonl = full.docs_path
    assert docs_jsonl.exists()
    mtime_before = docs_jsonl.stat().st_mtime_ns
    bytes_before = docs_jsonl.read_bytes()

    _write_project_trace(project, _trace_with("trace-new-one", "brandnew"))
    refresh_index()

    calls = {"all_docs": 0}
    orig_all = sp._all_docs

    def _spy_all(path):
        calls["all_docs"] += 1
        return orig_all(path)

    sp._all_docs = _spy_all
    try:
        summary = refresh_search_projection(changed_trace_ids=["trace-new-one"])
    finally:
        sp._all_docs = orig_all

    # No whole-corpus read-back.
    assert calls["all_docs"] == 0, "refresh must not re-read the full corpus"
    # No new build dir — the serving build is mutated in place.
    build_dirs_after = {p.name for p in builds_dir.iterdir() if p.is_dir()}
    assert build_dirs_after == build_dirs_before, "refresh must not create a build dir"
    assert summary.build_id == full.build_id
    assert summary.docs_path == docs_jsonl

    # docs.jsonl is byte-identical and its mtime is untouched (lazy export).
    assert docs_jsonl.stat().st_mtime_ns == mtime_before, "docs.jsonl was rewritten"
    assert docs_jsonl.read_bytes() == bytes_before

    # projection_meta records the lazy export as stale.
    status = search_projection_status()
    assert status["docs_jsonl_stale"] is True
    # The new trace is present.
    assert summary.trace_count == 31


def test_refresh_wall_clock_independent_of_corpus_size(tmp_path):
    """F2: a single-trace delta refresh on a 10x larger corpus must NOT take
    ~10x longer. With an O(corpus) copy/rewrite the ratio tracks corpus size;
    with an O(delta) refresh it stays bounded."""

    import time

    from opentraces.core.search_projection import (
        build_search_projection,
        refresh_search_projection,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    def _measure(n_corpus: int, root_name: str) -> float:
        # Each measurement gets its OWN index db + projection root so the two
        # corpora never share the global cache (isolated, repeatable timing).
        index_path = tmp_path / f"{root_name}-index.db"
        proj_root = tmp_path / f"{root_name}-projroot"
        project = tmp_path / root_name
        _enroll_project(project, "1234567890abcdef1234567890abcde0")
        _build_corpus(project, n_corpus)
        rebuild_index(index_path)
        build_search_projection(index_path=index_path, root_path=proj_root)
        _write_project_trace(project, _trace_with(f"{root_name}-delta", "deltaterm"))
        refresh_index(index_path)
        start = time.perf_counter()
        refresh_search_projection(
            changed_trace_ids=[f"{root_name}-delta"],
            index_path=index_path,
            root_path=proj_root,
        )
        return time.perf_counter() - start

    small = _measure(40, "small")
    big = _measure(400, "big")

    # An O(corpus) refresh would scale ~10x with corpus size. An O(delta)
    # refresh should stay well under that. Allow generous headroom for fixed
    # per-call overhead and CI jitter, but reject linear-in-corpus behavior.
    assert big <= small * 4 + 0.5, (
        f"delta refresh scaled with corpus size: small={small:.4f}s big={big:.4f}s"
    )


def test_refresh_gc_keeps_build_dirs_bounded(tmp_path):
    """F2: after repeated refreshes the builds/ directory must not grow without
    bound — GC prunes non-current builds (keep current + at most one prior)."""

    from opentraces.core import paths
    from opentraces.core.search_projection import (
        SEARCH_PROJECTION_VERSION,
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-seed", "seed"))
    rebuild_index()
    build_search_projection()

    for i in range(6):
        _write_project_trace(project, _trace_with(f"trace-iter-{i}", f"iter{i}"))
        refresh_index()
        refresh_search_projection(changed_trace_ids=[f"trace-iter-{i}"])

    builds_dir = paths.search_projection_root(SEARCH_PROJECTION_VERSION) / "builds"
    remaining = [p for p in builds_dir.iterdir() if p.is_dir()]
    assert len(remaining) <= 2, f"builds/ grew unbounded: {[p.name for p in remaining]}"

    # The current build must still be present and serving.
    status = search_projection_status()
    assert status["state"] == "ok"
    current_build = builds_dir / status["build_id"]
    assert current_build.exists()
    assert current_build in remaining


def test_corpus_hash_is_build_path_independent(tmp_path):
    """F2 (#6): two projections built from the same corpus via different paths
    (full build vs build-then-delta-refresh) yield the same ``corpus_hash``."""

    from opentraces.core.search_projection import (
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    import shutil

    from opentraces.core import paths
    from opentraces.core.config import get_project_traces_dir

    project = tmp_path / "demo"
    _enroll_project(project, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    _write_project_trace(project, _trace_with("trace-beta", "beta"))

    # Path A: full build over the whole {alpha, beta} corpus.
    rebuild_index()
    build_search_projection()
    hash_full = search_projection_status()["manifest"]["corpus_hash"]

    # Reset every projection/index artifact so Path B starts from scratch.
    shutil.rmtree(paths.bucket_dir(), ignore_errors=True)

    # Path B: full build over {alpha} only, then delta-refresh in beta.
    traces_dir = get_project_traces_dir(project)
    beta_file = traces_dir / "trace-beta.jsonl"
    beta_text = beta_file.read_text()
    beta_file.unlink()
    rebuild_index()
    build_search_projection()
    beta_file.write_text(beta_text)
    refresh_index()
    refresh_search_projection(changed_trace_ids=["trace-beta"])
    hash_delta = search_projection_status()["manifest"]["corpus_hash"]

    assert hash_full == hash_delta, (
        f"corpus_hash is build-path-dependent: full={hash_full} delta={hash_delta}"
    )


def test_doctor_freshness_is_cheap_no_heavy_bucket_scan(tmp_path):
    """F2 (#5): search_projection_freshness must use the cheap stat-only signal,
    never the heavy bucket_manifest full scan, so ``opentraces doctor`` stays
    cheap (R5)."""

    from opentraces.core import search_projection as sp
    from opentraces.core.search_projection import (
        build_search_projection,
        search_projection_freshness,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    build_search_projection()

    import opentraces.core.bucket_store as bucket_store

    heavy = {"manifest": 0}
    orig_manifest = bucket_store.bucket_manifest

    def _spy_manifest(*a, **k):
        heavy["manifest"] += 1
        return orig_manifest(*a, **k)

    bucket_store.bucket_manifest = _spy_manifest
    if hasattr(sp, "bucket_manifest"):
        sp_orig = sp.bucket_manifest
        sp.bucket_manifest = _spy_manifest
    else:
        sp_orig = None
    try:
        fresh = search_projection_freshness()
    finally:
        bucket_store.bucket_manifest = orig_manifest
        if sp_orig is not None:
            sp.bucket_manifest = sp_orig

    assert fresh["state"] == "ok"
    assert heavy["manifest"] == 0, "freshness probe must not run the heavy bucket_manifest scan"


# --- G1: in-place WAL model -----------------------------------------------


def test_corpus_hash_equals_from_scratch_xor_and_is_versioned(tmp_path):
    """(c): after an in-place delta refresh, projection_meta's corpus_hash equals
    a from-scratch XOR aggregate computed over the same corpus, and it is the
    versioned (CORPUS_HASH_ALGO) derivation — never path-dependent."""

    import sqlite3 as _sqlite3

    from opentraces.core import search_projection as sp
    from opentraces.core.search_projection import (
        CORPUS_HASH_ALGO,
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    build_search_projection()

    _write_project_trace(project, _trace_with("trace-beta", "beta"))
    refresh_index()
    refresh_search_projection(changed_trace_ids=["trace-beta"])

    status = search_projection_status()
    assert status["corpus_hash_algo"] == CORPUS_HASH_ALGO

    # Recompute the XOR aggregate + counts straight from projection_traces and
    # the docs table, then derive the versioned hash — must match meta exactly.
    with _sqlite3.connect(status["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        rows = conn.execute(
            "select trace_id, doc_count, trace_hash, trace_contrib from projection_traces"
        ).fetchall()
        live_docs = conn.execute("select count(*) from docs").fetchone()[0]
        meta_xor = conn.execute(
            "select value from projection_meta where key = 'corpus_xor'"
        ).fetchone()[0]
    running = "0" * 64
    doc_count = 0
    for row in rows:
        contrib = row["trace_contrib"] or sp._trace_contrib(
            row["trace_id"], row["trace_hash"]
        )
        running = sp._xor_hex(running, contrib)
        doc_count += int(row["doc_count"])
    trace_count = len(rows)
    assert running == meta_xor, "stored XOR aggregate drifted from a from-scratch XOR"
    assert doc_count == live_docs == status["doc_count"]
    assert trace_count == status["trace_count"]
    expected = sp._corpus_hash_from_aggregate(running, doc_count, trace_count)
    assert status["corpus_hash"] == expected


def test_query_sees_inplace_refresh_committed_wal_data(tmp_path):
    """(d): a query after an in-place refresh sees the newly inserted docs (WAL
    committed data is visible to fresh read connections) and does NOT see docs
    for a trace deleted in the same/subsequent refresh."""

    from opentraces.core.search_projection import (
        build_search_projection,
        query_search_projection_page,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    build_search_projection()

    # The serving DB really is WAL.
    import sqlite3 as _sqlite3

    status = search_projection_status()
    with _sqlite3.connect(status["sqlite_path"]) as conn:
        mode = conn.execute("pragma journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"

    # Insert a brand-new trace via in-place refresh; a fresh query sees it.
    _write_project_trace(project, _trace_with("trace-zeta", "zetaterm"))
    refresh_index()
    refresh_search_projection(changed_trace_ids=["trace-zeta"])
    page = query_search_projection_page(lex="zetaterm", limit=20)
    assert "trace-zeta" in {c.trace_id for c in page.candidates}

    # Now delete trace-zeta in place; a fresh query no longer returns it.
    refresh_search_projection(deleted_trace_ids=["trace-zeta"])
    page_after = query_search_projection_page(lex="zetaterm", limit=20)
    assert "trace-zeta" not in {c.trace_id for c in page_after.candidates}
    # alpha is still queryable throughout.
    assert "trace-alpha" in {
        c.trace_id for c in query_search_projection_page(lex="alpha", limit=20).candidates
    }


def test_export_docs_jsonl_regenerates_deterministically(tmp_path):
    """(6): docs.jsonl is lazy after a refresh; export_docs_jsonl regenerates it
    deterministically from the serving sqlite and clears the stale flag."""

    from opentraces.core.search_projection import (
        build_search_projection,
        export_docs_jsonl,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alpha"))
    rebuild_index()
    build_search_projection()

    _write_project_trace(project, _trace_with("trace-beta", "beta"))
    refresh_index()
    refresh_search_projection(changed_trace_ids=["trace-beta"])
    assert search_projection_status()["docs_jsonl_stale"] is True

    out = export_docs_jsonl()
    assert out is not None and out.exists()
    first = out.read_bytes()
    # Stale flag cleared; the export now reflects both traces.
    assert search_projection_status()["docs_jsonl_stale"] is False
    docs = [json.loads(line) for line in first.decode().splitlines() if line]
    trace_ids = {doc["trace_id"] for doc in docs}
    assert {"trace-alpha", "trace-beta"} <= trace_ids

    # Deterministic: a forced re-export is byte-identical.
    second = export_docs_jsonl(force=True).read_bytes()
    assert first == second


# --- FTS5 delete-by-rowid (O(1) per-doc FTS delete) ------------------------


def test_fts_delete_is_rowid_lookup_not_full_scan(tmp_path):
    """The per-doc FTS delete must be an O(1) rowid lookup, NOT a full FTS
    scan. FTS5 cannot index an `unindexed` column, so `delete from docs_fts
    where doc_id = ?` forces a SCAN over the whole FTS. After the fix the
    delete is keyed by the docs_fts rowid, so EXPLAIN QUERY PLAN must show a
    rowid lookup and never a SCAN of docs_fts."""

    import sqlite3 as _sqlite3

    from opentraces.core.search_projection import (
        build_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _build_corpus(project, 30)
    rebuild_index()
    build_search_projection()

    status = search_projection_status()
    with _sqlite3.connect(status["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        # The fix stores the docs_fts rowid in docs.fts_rowid at insert time.
        cols = {row[1] for row in conn.execute("pragma table_info(docs)").fetchall()}
        assert "fts_rowid" in cols, "docs must carry the docs_fts rowid mapping"
        # Every doc row must have a populated rowid mapping.
        missing = conn.execute(
            "select count(*) from docs where fts_rowid is null"
        ).fetchone()[0]
        assert missing == 0, f"{missing} docs lack an fts_rowid mapping"

        # EXPLAIN the two candidate deletes. FTS5 always renders a virtual-table
        # access as "SCAN <tbl> VIRTUAL TABLE INDEX 0:<constraint>"; the
        # load-bearing distinction is the constraint suffix:
        #   * rowid equality -> "INDEX 0:=" (an O(1) point lookup)
        #   * unindexed doc_id -> "INDEX 0:"  (no constraint -> full FTS scan)
        rowid_plan = " | ".join(
            str(tuple(r))
            for r in conn.execute(
                "explain query plan delete from docs_fts where rowid = ?", (1,)
            ).fetchall()
        ).upper()
        docid_plan = " | ".join(
            str(tuple(r))
            for r in conn.execute(
                "explain query plan delete from docs_fts where doc_id = ?", ("x",)
            ).fetchall()
        ).upper()
        # The fix's delete (by rowid) is constrained; the old delete (by the
        # unindexed doc_id) is not.
        assert "INDEX 0:=" in rowid_plan, (
            f"rowid delete is not a constrained lookup: {rowid_plan}"
        )
        assert "INDEX 0:=" not in docid_plan, (
            f"doc_id delete unexpectedly constrained: {docid_plan}"
        )


def test_fts_delete_time_independent_of_corpus_size(tmp_path):
    """A single-trace in-place delta refresh's wall-clock must be independent of
    corpus size once the FTS delete is O(1). With the old `where doc_id = ?`
    FULL-FTS-SCAN delete the time scales with corpus size; with delete-by-rowid
    it stays bounded."""

    import time

    from opentraces.core.search_projection import (
        build_search_projection,
        refresh_search_projection,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    def _measure(n_corpus: int, root_name: str) -> float:
        index_path = tmp_path / f"{root_name}-index.db"
        proj_root = tmp_path / f"{root_name}-projroot"
        project = tmp_path / root_name
        _enroll_project(project, "1234567890abcdef1234567890abcde1")
        _build_corpus(project, n_corpus)
        rebuild_index(index_path)
        build_search_projection(index_path=index_path, root_path=proj_root)
        # Re-edit an EXISTING trace so the refresh runs deletes (purge + reinsert).
        _write_project_trace(
            project, _trace_with("trace-bulk-00000", "deltaupdated")
        )
        refresh_index(index_path)
        start = time.perf_counter()
        refresh_search_projection(
            changed_trace_ids=["trace-bulk-00000"],
            index_path=index_path,
            root_path=proj_root,
        )
        return time.perf_counter() - start

    small = _measure(50, "small")
    big = _measure(500, "big")

    assert big <= small * 4 + 0.5, (
        f"FTS delete scaled with corpus size: small={small:.4f}s big={big:.4f}s"
    )


def test_query_correct_after_delete_by_rowid(tmp_path):
    """(b) Query correctness after delete-by-rowid: deleted docs are gone from FTS
    results, surviving docs intact, and re-inserted docs are found again."""

    from opentraces.core.search_projection import (
        build_search_projection,
        query_search_projection_page,
        refresh_search_projection,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alphaterm"))
    _write_project_trace(project, _trace_with("trace-beta", "betaterm"))
    rebuild_index()
    build_search_projection()

    # Both findable up front.
    assert "trace-alpha" in {
        c.trace_id for c in query_search_projection_page(lex="alphaterm", limit=20).candidates
    }
    assert "trace-beta" in {
        c.trace_id for c in query_search_projection_page(lex="betaterm", limit=20).candidates
    }

    # Delete beta (delete-by-rowid path).
    refresh_search_projection(deleted_trace_ids=["trace-beta"])
    assert "trace-beta" not in {
        c.trace_id for c in query_search_projection_page(lex="betaterm", limit=20).candidates
    }
    # Surviving doc intact.
    assert "trace-alpha" in {
        c.trace_id for c in query_search_projection_page(lex="alphaterm", limit=20).candidates
    }

    # Re-insert beta under a new term (changed-trace path re-keys via delete+insert).
    _write_project_trace(project, _trace_with("trace-beta", "betareborn"))
    refresh_index()
    refresh_search_projection(changed_trace_ids=["trace-beta"])
    assert "trace-beta" in {
        c.trace_id for c in query_search_projection_page(lex="betareborn", limit=20).candidates
    }
    # No stale FTS row for the old term.
    assert "trace-beta" not in {
        c.trace_id for c in query_search_projection_page(lex="betaterm", limit=20).candidates
    }


def test_old_schema_build_forces_full_rebuild(tmp_path):
    """An old-schema serving build (docs_fts unindexed, no fts_rowid mapping, and
    possibly corrupt projection_meta counts) must be migrated by a one-time full
    rebuild on the next refresh — never mutated in place. After the refresh the
    build carries the new schema (fts_rowid populated) and CORRECT counts."""

    import sqlite3 as _sqlite3

    from opentraces.core import paths
    from opentraces.core.search_projection import (
        SEARCH_PROJECTION_VERSION,
        build_search_projection,
        refresh_search_projection,
        search_projection_status,
    )
    from opentraces.core.trace_index import rebuild_index, refresh_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _trace_with("trace-alpha", "alphaterm"))
    _write_project_trace(project, _trace_with("trace-beta", "betaterm"))
    rebuild_index()
    build_search_projection()

    status = search_projection_status()
    old_sqlite = status["sqlite_path"]

    # Simulate an OLD-schema serving build: drop the fts_rowid column mapping AND
    # corrupt the projection_meta counts (as the real serving build had).
    with _sqlite3.connect(old_sqlite) as conn:
        # Remove the rowid mapping to mimic a build that predates the fix.
        cols = {row[1] for row in conn.execute("pragma table_info(docs)").fetchall()}
        if "fts_rowid" in cols:
            # SQLite can't drop a column on old versions; null it out to mimic
            # "no usable mapping" so the migration gate must trigger.
            conn.execute("update docs set fts_rowid = null")
        conn.execute(
            "update projection_meta set value = '547' where key = 'doc_count'"
        )
        conn.execute(
            "update projection_meta set value = '1' where key = 'trace_count'"
        )
        conn.commit()

    # Add a new trace and refresh — the old-schema gate must force a full rebuild.
    _write_project_trace(project, _trace_with("trace-gamma", "gammaterm"))
    refresh_index()
    summary = refresh_search_projection(changed_trace_ids=["trace-gamma"])

    # Counts are now CORRECT (3 traces, real doc_count), not the corrupt 1/547.
    assert summary.trace_count == 3, "full rebuild must repair corrupt trace_count"

    status2 = search_projection_status()
    with _sqlite3.connect(status2["sqlite_path"]) as conn:
        conn.row_factory = _sqlite3.Row
        cols = {row[1] for row in conn.execute("pragma table_info(docs)").fetchall()}
        assert "fts_rowid" in cols
        missing = conn.execute(
            "select count(*) from docs where fts_rowid is null"
        ).fetchone()[0]
        assert missing == 0, "rebuilt schema must populate fts_rowid for every doc"
        meta_traces = int(
            conn.execute(
                "select value from projection_meta where key = 'trace_count'"
            ).fetchone()[0]
        )
        assert meta_traces == 3
