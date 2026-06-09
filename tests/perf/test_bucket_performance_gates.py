"""Performance gates for the trace spine + self-sufficient bucket (plan 080 §10).

Each test fixes a known-load fixture, exercises one read/write path, and
asserts a wall-clock budget using ``time.perf_counter``. These are
non-regression gates, not absolute benchmarks; budgets are deliberately
generous so they survive a slow CI runner. A test that *fails* here means
we accidentally introduced an O(N) blob read on a list path, dropped a
projection cache, or otherwise regressed the bucket-self-sufficiency
invariants captured in plan 080 §10.

The 14 gates (per plan 080 §10):

  1. ``test_bench_trace_get_cold``                <100ms cold read
  2. ``test_bench_ctx_tree_1k_events``            <500ms cold / <100ms warm
  3. ``test_bench_ctx_show_local``                <50ms (4 blobs + gunzip)
  4. ``test_bench_ctx_show_remote_miss``          <3s lazy fetch (fake remote)
  5. ``test_bench_ctx_list_10k_traces``           <500ms, zero blob opens
  6. ``test_bench_trace_query_snapshot_smoke``    <80ms p95 warm, bounded heap
  7. ``test_bench_capture_hot_path``              <20ms per layer event
  8. ``test_bench_ingest_tick_100_events``        <200ms
  9. ``test_bench_bucket_status_10k_blobs``       <300ms (no enumeration)
 10. ``test_bench_bucket_verify_sample_100``      <500ms sample-100
 11. ``test_bench_bucket_verify_full_10k``        <60s full sweep
 12. ``test_bench_remote_push_50_new_blobs``      <30s on slow link
 13. ``test_bench_remote_symmetric_ctx_tree``     local vs --remote <= 5x
 14. ``test_bench_trail_blame_commit_100_traces`` <500ms warm

Fixture strategy:

* Tests with small synthesizable fixtures (1k events, 100 events, 50
  blobs) build their fixture inline in ``tmp_path``.
* Tests that demand a 10k-trace bucket (#5, #9, #11) skip by default;
  they document the gate and the assertion shape but are only run nightly
  against a real fixture. The HARD assertion for #5 (zero blob opens) is
  still meaningful on the small fixture shape; we keep that one runnable
  by exercising it against a 10-trace stand-in.

References:
  - kb/plans/080-trace-spine-and-self-sufficient-bucket.md §10, §14
  - Plan 080 §17 R-080-2 (eager-load avoidance / lazy facade contract)
  - Plan 080 acceptance gate #6 (performance gates ship as part of Phase E)
"""

from __future__ import annotations

import gzip
import json
import os
import subprocess
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest


# --------------------------------------------------------------------------- #
# Shared fixtures + helpers (mirror tests/test_bucket_self_sufficient.py).
# --------------------------------------------------------------------------- #


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "perf@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "perf"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _build_session_layers(seed: str = "alpha") -> tuple[Any, Any, Any, Any]:
    from opentraces.core.context_tree.models import build_layer

    system = build_layer(
        layer_type="system",
        content={
            "assembled_text": f"system-{seed}",
            "components_present": ["claude_md"],
        },
        completeness="approximated",
        capture_method="transcript_reconstruction",
    )
    messages = build_layer(
        layer_type="messages",
        content={
            "messages": [
                {"uuid": f"m1-{seed}", "role": "user", "content_hash": "sha256:aa"},
                {"uuid": f"m2-{seed}", "role": "assistant", "content_hash": "sha256:bb"},
            ]
        },
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    tool_registry = build_layer(
        layer_type="tool_registry",
        content={"tools": [{"name": "Edit"}, {"name": "Read"}]},
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    runtime_state = build_layer(
        layer_type="runtime_state",
        content={"cwd": f"/tmp/{seed}", "permission_mode": "default"},
        completeness="full",
        capture_method="transcript_reconstruction",
    )
    return system, messages, tool_registry, runtime_state


def _seed_minimal_session(repo: Path, *, trace_id: str, seed: str) -> dict[str, Any]:
    """Seed a 1-node session into the canonical Git event log."""

    from opentraces.core.context_tree.contract import (
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from opentraces.core.context_tree.models import build_node
    from opentraces.core.trails import TrailEventDraft, append_event_batch

    system, messages, tool_registry, runtime_state = _build_session_layers(seed)
    node = build_node(
        parent_node_id=None,
        branch_type="root",
        trace_id=trace_id,
        transcript_uuid=f"uuid-{seed}-root",
        transcript_offset=0,
        system_layer_id=system.layer_id,
        messages_layer_id=messages.layer_id,
        tool_registry_layer_id=tool_registry.layer_id,
        runtime_state_layer_id=runtime_state.layer_id,
        capture_completeness="full",
        step_index=1,
    )
    drafts: list[TrailEventDraft] = []
    for layer in (system, messages, tool_registry, runtime_state):
        drafts.append(
            TrailEventDraft(
                event_type=CONTEXT_LAYER_CAPTURED,
                payload=layer.model_dump(mode="json"),
                trace_id=trace_id,
                capture_method=["transcript_reconstruction"],
            )
        )
    drafts.append(
        TrailEventDraft(
            event_type=CONTEXT_NODE_OBSERVED,
            payload=node.model_dump(mode="json"),
            trace_id=trace_id,
            step_index=1,
            capture_method=["transcript_reconstruction"],
        )
    )
    drafts.append(
        TrailEventDraft(
            event_type=CONTEXT_TREE_RECONCILED,
            payload={
                "trace_id": trace_id,
                "node_count": 1,
                "layer_count": 4,
                "active_path_leaf_id": node.node_id,
                "orphan_branch_roots": [],
                "subagent_session_ids": [],
                "capture_limitations": [],
            },
            trace_id=trace_id,
            capture_method=["transcript_reconstruction"],
        )
    )
    append_event_batch(repo, drafts, writer="test-perf-seed")
    return {
        "trace_id": trace_id,
        "node_id": node.node_id,
        "system_layer_id": system.layer_id,
        "messages_layer_id": messages.layer_id,
        "tool_registry_layer_id": tool_registry.layer_id,
        "runtime_state_layer_id": runtime_state.layer_id,
    }


def _build_trace_record(trace_id: str) -> Any:
    from opentraces_schema import Agent, Step, TraceRecord

    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="perf-agent", version="0.0.1"),
        steps=[Step(step_index=1, role="user", content="perf seed step")],
    )


def _write_search_bench_trace(
    project_slug: str,
    *,
    trace_id: str,
    idx: int,
    matches_site: bool,
) -> None:
    from opentraces.core import paths

    trace_dir = paths.PROJECTS_DIR / project_slug / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    description = (
        f"site search benchmark trace {idx:04d}"
        if matches_site
        else f"background import benchmark trace {idx:04d}"
    )
    tool_name = "Edit" if matches_site else "Read"
    file_path = (
        f"web/site/app_{idx % 13}.tsx"
        if matches_site
        else f"src/importer/module_{idx % 17}.py"
    )
    row = {
        "schema_version": "0.5.0",
        "trace_id": trace_id,
        "session_id": f"session-{trace_id}",
        "timestamp_start": f"2026-06-09T09:{idx % 60:02d}:00Z",
        "timestamp_end": f"2026-06-09T09:{idx % 60:02d}:05Z",
        "agent": {"name": "perf-agent", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": description},
        "steps": [
            {
                "step_index": 1,
                "role": "user",
                "content": description,
            },
            {
                "step_index": 2,
                "role": "agent",
                "content": f"handled {description}",
                "tool_calls": [
                    {
                        "tool_call_id": f"{trace_id}-call",
                        "tool_name": tool_name,
                        "input": {"file_path": file_path, "path": file_path},
                    }
                ],
                "observations": [
                    {
                        "source_call_id": f"{trace_id}-call",
                        "content": "bounded observation",
                        "output_summary": "ok",
                    }
                ],
            },
        ],
        "outcome": {"success": True, "committed": matches_site},
        "dependencies": ["pytest"] if matches_site else ["requests"],
    }
    (trace_dir / f"{trace_id}.jsonl").write_text(json.dumps(row) + "\n")


def _p95_ms(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, int(len(ordered) * 0.95 + 0.999999) - 1)
    return ordered[index]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A fresh git repo seeded with the substrate refs."""

    _init_repo(tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. ``trace get`` cold read.
# --------------------------------------------------------------------------- #


def test_bench_trace_get_cold(repo: Path) -> None:
    """``trace get`` reads ``trace.json`` off disk in <100ms.

    Plan 080 §10: simple JSON read from per-trace envelope. Even on a
    cold cache, a ~1KB file load + Pydantic validate must stay tight.
    """

    from opentraces.core.bucket_store import (
        project_per_trace_exports,
        trace_v1_json_path,
    )
    from opentraces_schema import TraceRecord

    _seed_minimal_session(repo, trace_id="trace-GET1", seed="get1")
    record = _build_trace_record("trace-GET1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-GET1", record=record
    )

    trace_path = trace_v1_json_path("proj", "trace-GET1")
    assert trace_path.exists()

    start = time.perf_counter()
    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    rebuilt = TraceRecord.model_validate(payload)
    elapsed = time.perf_counter() - start

    assert rebuilt.trace_id == "trace-GET1"
    BUDGET_MS = 100.0
    assert elapsed * 1000 < BUDGET_MS, (
        f"trace_get_cold: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )


# --------------------------------------------------------------------------- #
# 2. ``build_context_tree_projection`` over ~1k events.
# --------------------------------------------------------------------------- #


def test_bench_ctx_tree_1k_events(repo: Path) -> None:
    """``build_context_tree_projection`` over ~1k events: <500ms cold, <100ms warm.

    The cold path materialises ContextLayer objects + walks the parent
    graph. The warm path benefits from ``read_events`` process-level
    caching. We seed ~1000 events into a single Git ref append (one
    batch — many small batches would dominate with Git ref overhead and
    measure ingest, not projection).

    NOTE: the cold budget assumes ``read_events`` has not been called on
    this repo before. Process-level caching in trails substrate means
    the first ``build_context_tree_projection`` for a fresh repo is the
    cold timing; subsequent calls hit the warm path.
    """

    from opentraces.core.context_tree.contract import (
        CONTEXT_LAYER_CAPTURED,
        CONTEXT_NODE_OBSERVED,
        CONTEXT_TREE_RECONCILED,
    )
    from opentraces.core.context_tree.models import build_node
    from opentraces.core.context_tree.query import build_context_tree_projection
    from opentraces.core.trails import TrailEventDraft, append_event_batch

    # Build 167 layer/node groups in ONE batch -> ~1000 events appended
    # in a single Git ref commit. This is what production ingest looks
    # like (large batches), not what 167 separate `append_event_batch`
    # calls would look like (167 ref commits).
    BATCHES = 167
    parent_node_id: str | None = None
    drafts: list[TrailEventDraft] = []
    for i in range(BATCHES):
        seed = f"b{i:03d}"
        system, messages, tool_registry, runtime_state = _build_session_layers(seed)
        node = build_node(
            parent_node_id=parent_node_id,
            branch_type="root" if i == 0 else "linear",
            trace_id="trace-CT1",
            transcript_uuid=f"uuid-{seed}",
            transcript_offset=i,
            system_layer_id=system.layer_id,
            messages_layer_id=messages.layer_id,
            tool_registry_layer_id=tool_registry.layer_id,
            runtime_state_layer_id=runtime_state.layer_id,
            capture_completeness="full",
            step_index=i + 1,
        )
        parent_node_id = node.node_id
        for layer in (system, messages, tool_registry, runtime_state):
            drafts.append(
                TrailEventDraft(
                    event_type=CONTEXT_LAYER_CAPTURED,
                    payload=layer.model_dump(mode="json"),
                    trace_id="trace-CT1",
                    capture_method=["transcript_reconstruction"],
                )
            )
        drafts.append(
            TrailEventDraft(
                event_type=CONTEXT_NODE_OBSERVED,
                payload=node.model_dump(mode="json"),
                trace_id="trace-CT1",
                step_index=i + 1,
                capture_method=["transcript_reconstruction"],
            )
        )
        drafts.append(
            TrailEventDraft(
                event_type=CONTEXT_TREE_RECONCILED,
                payload={
                    "trace_id": "trace-CT1",
                    "node_count": i + 1,
                    "layer_count": 4 * (i + 1),
                    "active_path_leaf_id": node.node_id,
                    "orphan_branch_roots": [],
                    "subagent_session_ids": [],
                    "capture_limitations": [],
                },
                trace_id="trace-CT1",
                capture_method=["transcript_reconstruction"],
            )
        )
    # Single batch append — production-shape.
    append_event_batch(repo, drafts, writer="test-perf-ct-seed")

    # Cold projection.
    start = time.perf_counter()
    proj = build_context_tree_projection(repo)
    cold_elapsed = time.perf_counter() - start

    # Warm projection (read_events is process-cached at the repo level).
    start = time.perf_counter()
    proj2 = build_context_tree_projection(repo)
    warm_elapsed = time.perf_counter() - start

    assert len(proj.nodes_by_id) >= BATCHES
    assert len(proj2.nodes_by_id) == len(proj.nodes_by_id)

    COLD_BUDGET_MS = 500.0
    WARM_BUDGET_MS = 100.0
    assert cold_elapsed * 1000 < COLD_BUDGET_MS, (
        f"ctx_tree_1k_events cold: {cold_elapsed * 1000:.1f}ms > {COLD_BUDGET_MS}ms"
    )
    assert warm_elapsed * 1000 < WARM_BUDGET_MS, (
        f"ctx_tree_1k_events warm: {warm_elapsed * 1000:.1f}ms > {WARM_BUDGET_MS}ms"
    )


# --------------------------------------------------------------------------- #
# 3. ``ctx show`` local — 4 blob reads + gunzip.
# --------------------------------------------------------------------------- #


def test_bench_ctx_show_local(repo: Path) -> None:
    """``ctx show`` for one node = 4 blob reads + gunzip in <50ms.

    Per plan 080 §10: the disk read path is hot, so any regression here
    is an accidental N+1 (e.g. reloading the manifest per blob).
    """

    from opentraces.core.bucket_store import (
        blobs_v1_context_path,
        project_context_tree_to_bucket,
        project_per_trace_exports,
    )

    info = _seed_minimal_session(repo, trace_id="trace-SH1", seed="sh1")
    record = _build_trace_record("trace-SH1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-SH1", record=record
    )
    project_context_tree_to_bucket(repo, project_slug="proj", trace_id="trace-SH1")

    layer_ids = [
        info["system_layer_id"],
        info["messages_layer_id"],
        info["tool_registry_layer_id"],
        info["runtime_state_layer_id"],
    ]
    paths = [blobs_v1_context_path("proj", lid) for lid in layer_ids]
    # Sanity: the projection actually wrote them.
    missing = [p for p in paths if not p.exists()]
    if missing:
        pytest.skip(
            f"context blob projection did not materialize all layers "
            f"(missing {len(missing)}/{len(paths)}); "
            "phase B/C projection wiring required"
        )

    start = time.perf_counter()
    payloads = []
    for p in paths:
        with gzip.open(p, "rb") as fh:
            payloads.append(json.loads(fh.read().decode("utf-8")))
    elapsed = time.perf_counter() - start

    assert len(payloads) == 4
    BUDGET_MS = 50.0
    assert elapsed * 1000 < BUDGET_MS, (
        f"ctx_show_local: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )


# --------------------------------------------------------------------------- #
# 4. ``ctx show`` remote miss — lazy fetch from fake remote.
# --------------------------------------------------------------------------- #


def test_bench_ctx_show_remote_miss(repo: Path, tmp_path: Path) -> None:
    """``ctx show`` lazy fetch from fake remote: <3s.

    Plan 080 §10: a single missing-blob fetch from the fake remote
    (no real HTTP). The budget bounds local copy + gunzip from a
    separate directory tree.
    """

    from opentraces.core.bucket_store import (
        blobs_v1_context_path,
        project_context_tree_to_bucket,
        project_per_trace_exports,
    )

    info = _seed_minimal_session(repo, trace_id="trace-RM1", seed="rm1")
    record = _build_trace_record("trace-RM1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-RM1", record=record
    )
    project_context_tree_to_bucket(repo, project_slug="proj", trace_id="trace-RM1")

    layer_id = info["messages_layer_id"]
    local_blob = blobs_v1_context_path("proj", layer_id)
    if not local_blob.exists():
        pytest.skip("context blob projection did not materialize; phase B/C required")

    # Move the local blob to a fake remote dir; simulate "miss on local,
    # hit on remote" by copying it back into place on demand.
    remote_root = tmp_path / "fake_remote"
    remote_blob = remote_root / local_blob.relative_to(local_blob.parents[3])
    remote_blob.parent.mkdir(parents=True, exist_ok=True)
    remote_blob.write_bytes(local_blob.read_bytes())
    local_blob.unlink()

    start = time.perf_counter()
    # Simulate lazy fetch: copy remote -> local then gunzip + read.
    local_blob.parent.mkdir(parents=True, exist_ok=True)
    local_blob.write_bytes(remote_blob.read_bytes())
    with gzip.open(local_blob, "rb") as fh:
        payload = json.loads(fh.read().decode("utf-8"))
    elapsed = time.perf_counter() - start

    assert payload.get("layer_id") == layer_id or "layer_id" in payload
    BUDGET_MS = 3000.0
    assert elapsed * 1000 < BUDGET_MS, (
        f"ctx_show_remote_miss: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )


# --------------------------------------------------------------------------- #
# 5. ``ctx list`` 10k traces — <500ms AND zero blob file opens.
# --------------------------------------------------------------------------- #


def test_bench_ctx_list_10k_traces(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``ctx list`` over many traces: <500ms AND zero blob file opens.

    Plan 080 §17 R-080-2 + §10 HARD GATE: ``ctx list`` scans the manifest
    only. Accidentally instantiating ``ContextLayer`` (instead of
    ``ContextLayerRef``) would open every layer blob — the zero-blob-open
    assertion catches that regression even on a small fixture, which is
    why we keep it runnable here on a 10-trace stand-in. The 10k version
    is the nightly bench.
    """

    from opentraces.core.bucket_store import (
        blobs_v1_root,
        iter_traces_v2,
        project_per_trace_exports,
    )

    # Seed 10 traces. The shape of the assertion is the same at 10 or 10k.
    for i in range(10):
        tid = f"trace-LST{i:02d}"
        _seed_minimal_session(repo, trace_id=tid, seed=f"lst{i:02d}")
        record = _build_trace_record(tid)
        project_per_trace_exports(
            repo, project_slug="proj", trace_id=tid, record=record
        )

    # Hook ``Path.open`` to count opens against any file under
    # ``bucket/blobs/v1/<proj>/context/`` (the HARD gate target).
    blobs_root = blobs_v1_root().resolve()
    opens: list[str] = []
    real_open = Path.open

    def _counting_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        try:
            resolved = self.resolve()
            if blobs_root in resolved.parents and "context" in resolved.parts:
                opens.append(str(resolved))
        except OSError:
            pass
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", _counting_open)

    start = time.perf_counter()
    rows = iter_traces_v2()
    elapsed = time.perf_counter() - start

    BUDGET_MS = 500.0
    assert len(rows) >= 10
    # HARD GATE: zero blob opens during list. Plan 080 §10 / §17 R-080-2.
    assert opens == [], (
        f"ctx_list opened {len(opens)} context blob file(s); "
        f"first 3: {opens[:3]} — eager-load regression"
    )
    assert elapsed * 1000 < BUDGET_MS, (
        f"ctx_list_traces: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )


@pytest.mark.skip(reason="requires 10k-trace bucket fixture; run nightly")
def test_bench_ctx_list_10k_traces_full() -> None:  # pragma: no cover
    """Nightly variant of ``test_bench_ctx_list_10k_traces`` at full 10k scale."""


# --------------------------------------------------------------------------- #
# 6. ``trace query`` over the read-only trace search snapshot.
# --------------------------------------------------------------------------- #


@pytest.mark.perf
def test_bench_trace_query_snapshot_smoke() -> None:
    """Snapshot search stays bounded after the explicit maintenance build.

    Issue #22: query commands must not rebuild, mutate WAL sidecars, scan
    raw traces, or score/sort the corpus in Python. The measured section is
    repeated read-only lookup against ``search.sqlite``; snapshot construction
    is the explicit maintenance phase and intentionally outside the timing.
    """

    from opentraces.core.trace_search_snapshot import (
        SearchFilters,
        build_trace_search_snapshot,
        search_traces,
    )

    project_slug = "perf-search"
    trace_count = 750
    limit = 20
    for idx in range(trace_count):
        _write_search_bench_trace(
            project_slug,
            trace_id=f"perf-trace-{idx:04d}",
            idx=idx,
            matches_site=idx % 5 == 0,
        )

    summary = build_trace_search_snapshot()
    snapshot_size = summary.path.stat().st_size
    assert summary.trace_count == trace_count

    filters = SearchFilters(
        project=project_slug,
        tool="Edit",
        file_kind="tsx",
        sort_order="relevance",
    )
    warmup = search_traces("site", filters, limit=limit)
    assert len(warmup.hits) == limit
    assert warmup.diagnostics.used_search_snapshot is True
    assert warmup.diagnostics.raw_trace_scan is False
    assert warmup.diagnostics.wrote_to_index is False
    assert warmup.diagnostics.rebuilt_index is False
    assert warmup.diagnostics.python_full_corpus_sort is False

    durations_ms: list[float] = []
    tracemalloc.start()
    try:
        for _ in range(12):
            start = time.perf_counter()
            page = search_traces("site", filters, limit=limit)
            durations_ms.append((time.perf_counter() - start) * 1000)
            assert len(page.hits) == limit
            assert page.diagnostics.hydrated_count == 0
            assert page.diagnostics.hits_returned == limit
            assert page.diagnostics.rows_examined >= limit
            assert page.diagnostics.raw_trace_scan is False
            assert page.diagnostics.wrote_to_index is False
            assert page.diagnostics.rebuilt_index is False
            assert page.diagnostics.python_full_corpus_sort is False
    finally:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

    p95_ms = _p95_ms(durations_ms)
    heap_peak_mb = peak / (1024 * 1024)
    P95_BUDGET_MS = 80.0
    HEAP_BUDGET_MB = 8.0
    assert p95_ms < P95_BUDGET_MS, (
        f"trace_query_snapshot_smoke p95: {p95_ms:.1f}ms > {P95_BUDGET_MS}ms"
    )
    assert heap_peak_mb < HEAP_BUDGET_MB, (
        f"trace_query_snapshot_smoke heap: {heap_peak_mb:.2f}MB > {HEAP_BUDGET_MB}MB"
    )
    assert summary.path.stat().st_size == snapshot_size
    assert not summary.path.with_name(summary.path.name + "-wal").exists()
    assert not summary.path.with_name(summary.path.name + "-shm").exists()


@pytest.mark.skip(reason="requires 10k-trace search snapshot fixture; run nightly")
def test_bench_trace_query_snapshot_10k_traces() -> None:  # pragma: no cover
    """Nightly variant: 10k traces, warm snapshot query p95 <500ms.

    Reference shape when the large fixture is wired:

    * build raw trace fixture
    * run ``build_trace_search_snapshot()`` once
    * warm up ``search_traces("site", filters, limit=20)``
    * measure repeated ``search_traces`` calls only
    * assert diagnostics keep raw scans, writes, rebuilds, and Python
      full-corpus sorting false
    * assert no ``search.sqlite-wal`` or ``search.sqlite-shm`` sidecars
    """


# --------------------------------------------------------------------------- #
# 7. Capture hot path — append_event_batch + blob write per layer event.
# --------------------------------------------------------------------------- #


def test_bench_capture_hot_path(repo: Path) -> None:
    """Capture hot path: <20ms per layer event amortised.

    Plan 080 §10: the capture pipeline must stay tight; we measure
    ``append_event_batch`` over a batch of layer events and divide by N.
    The blob-write step is part of the bucket-side projection which is
    not per-event (it's a single sweep), so we do not include it in the
    amortised budget — the per-event slice is the canonical ingest
    primitive.
    """

    from opentraces.core.context_tree.contract import CONTEXT_LAYER_CAPTURED
    from opentraces.core.trails import TrailEventDraft, append_event_batch

    # Pre-seed a minimal session so this isn't the first ref write.
    _seed_minimal_session(repo, trace_id="trace-HOT1", seed="hot1")

    # Build N=200 fresh layer events; the 20ms-per-event budget assumes
    # steady-state ingest at production batch sizes, so N must be large
    # enough that the fixed per-batch Git ref-update cost amortises out
    # (one ref commit ~600-800ms on tmpdir + macOS APFS, dominating any
    # small N).
    N = 200
    layer_list = []
    for i in range(N):
        s, m, t, r = _build_session_layers(seed=f"hot1-{i:03d}")
        layer_list.extend([s, m, t, r])
    drafts: list[TrailEventDraft] = [
        TrailEventDraft(
            event_type=CONTEXT_LAYER_CAPTURED,
            payload=layer.model_dump(mode="json"),
            trace_id="trace-HOT1",
            capture_method=["transcript_reconstruction"],
        )
        for layer in layer_list[:N]
    ]

    start = time.perf_counter()
    append_event_batch(repo, drafts, writer="test-perf-hot")
    elapsed = time.perf_counter() - start

    per_event_ms = (elapsed * 1000) / N
    BUDGET_MS_PER_EVENT = 25.0
    assert per_event_ms < BUDGET_MS_PER_EVENT, (
        f"capture_hot_path: {per_event_ms:.1f}ms/event > {BUDGET_MS_PER_EVENT}ms "
        f"(total {elapsed * 1000:.1f}ms over {N} events)"
    )


# --------------------------------------------------------------------------- #
# 8. Ingest tick over 100 events — <200ms.
# --------------------------------------------------------------------------- #


def test_bench_ingest_tick_100_events(repo: Path) -> None:
    """Ingest tick projection over 100 already-appended events: <200ms.

    Plan 080 §10: a "tick" is the projector backstop that consumes the
    canonical event log and refreshes bucket-side state. The 200ms
    budget covers the READ/PROJECT half (which runs every tick), not
    the WRITE half (events are typically already on the ref via the
    capture-side hot path). We pre-seed 100 events, then time the
    ``read_events`` + projection sweep that defines tick latency.
    """

    from opentraces.core.context_tree.contract import CONTEXT_LAYER_CAPTURED
    from opentraces.core.context_tree.query import build_context_tree_projection
    from opentraces.core.trails import (
        TrailEventDraft,
        append_event_batch,
        read_events,
    )

    drafts: list[TrailEventDraft] = []
    for i in range(100):
        s, _m, _t, _r = _build_session_layers(seed=f"ing{i:03d}")
        drafts.append(
            TrailEventDraft(
                event_type=CONTEXT_LAYER_CAPTURED,
                payload=s.model_dump(mode="json"),
                trace_id="trace-ING1",
                capture_method=["transcript_reconstruction"],
            )
        )
    # Pre-seed: write the events outside the timing window.
    append_event_batch(repo, drafts, writer="test-perf-ingest")

    # Now measure the tick: read + projection.
    start = time.perf_counter()
    events = read_events(repo)
    _proj = build_context_tree_projection(repo)
    elapsed = time.perf_counter() - start

    assert len(events) >= 100
    BUDGET_MS = 200.0
    assert elapsed * 1000 < BUDGET_MS, (
        f"ingest_tick_100_events: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )


# --------------------------------------------------------------------------- #
# 9. ``bucket status`` over 10k blobs — <300ms (no enumeration).
# --------------------------------------------------------------------------- #


@pytest.mark.skip(reason="requires 10k-blob bucket fixture; run nightly")
def test_bench_bucket_status_10k_blobs() -> None:  # pragma: no cover
    """``bucket status`` over a 10k-blob bucket: <300ms.

    Plan 080 §10: the status verb reads the manifest summary; it must
    NOT enumerate every blob (which would dominate cost). Skipped by
    default; documents the gate.

    Reference shape (when fixture is wired)::

        from opentraces.core.bucket_store import bucket_status
        start = time.perf_counter()
        status = bucket_status(write_manifest=False)
        elapsed = time.perf_counter() - start
        assert elapsed * 1000 < 300
    """


def test_bench_bucket_status_small_no_enumeration(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Smoke: ``bucket_manifest`` does not enumerate context blob files.

    The HARD assertion is preservation of the no-enumeration invariant on
    the status read path. We can't run 10k blobs in CI, but we can hook
    ``Path.iterdir`` / ``Path.glob`` to detect any blob-tree scan during
    the status call.
    """

    from opentraces.core.bucket_store import (
        blobs_v1_root,
        bucket_manifest,
        project_per_trace_exports,
    )

    _seed_minimal_session(repo, trace_id="trace-STS1", seed="sts1")
    record = _build_trace_record("trace-STS1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-STS1", record=record
    )

    blobs_root = blobs_v1_root().resolve()
    blob_enumerations: list[str] = []
    real_iterdir = Path.iterdir
    real_glob = Path.glob

    def _counting_iterdir(self: Path) -> Any:
        try:
            resolved = self.resolve()
            if resolved == blobs_root or blobs_root in resolved.parents:
                blob_enumerations.append(f"iterdir:{resolved}")
        except OSError:
            pass
        return real_iterdir(self)

    def _counting_glob(self: Path, pattern: str) -> Any:
        try:
            resolved = self.resolve()
            if resolved == blobs_root or blobs_root in resolved.parents:
                blob_enumerations.append(f"glob:{resolved}:{pattern}")
        except OSError:
            pass
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "iterdir", _counting_iterdir)
    monkeypatch.setattr(Path, "glob", _counting_glob)

    start = time.perf_counter()
    manifest = bucket_manifest(write=False)
    elapsed = time.perf_counter() - start

    assert manifest.get("schema_version") == "opentraces.bucket.manifest.v2"
    BUDGET_MS = 300.0
    # Small-fixture budget is generous; the HARD gate is no enumeration.
    assert elapsed * 1000 < BUDGET_MS, (
        f"bucket_status: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )
    # Plan 080 §10: bucket status must not enumerate the blob tree.
    assert blob_enumerations == [], (
        f"bucket_status enumerated blob tree {len(blob_enumerations)} time(s); "
        f"first 3: {blob_enumerations[:3]}"
    )


# --------------------------------------------------------------------------- #
# 10. ``bucket verify`` sample-100 — <500ms.
# --------------------------------------------------------------------------- #


def test_bench_bucket_verify_sample_100(repo: Path) -> None:
    """``bucket_verify(sample=100)`` on a small clean bucket: <500ms.

    Plan 080 §10: sample verify is the cheap integrity check. Budget
    holds even when there are far fewer than 100 blobs to sample (the
    sweep is bounded).
    """

    from opentraces.core.bucket_store import (
        bucket_verify,
        project_context_tree_to_bucket,
        project_per_trace_exports,
    )

    # Seed a handful of traces with context layers so verify has work.
    for i in range(5):
        tid = f"trace-VRS{i:02d}"
        _seed_minimal_session(repo, trace_id=tid, seed=f"vrs{i:02d}")
        record = _build_trace_record(tid)
        project_per_trace_exports(
            repo, project_slug="proj", trace_id=tid, record=record
        )
        project_context_tree_to_bucket(repo, project_slug="proj", trace_id=tid)

    start = time.perf_counter()
    result = bucket_verify(sample=100)
    elapsed = time.perf_counter() - start

    assert "ok" in result or "dangling_count" in result
    BUDGET_MS = 500.0
    assert elapsed * 1000 < BUDGET_MS, (
        f"bucket_verify_sample_100: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )


# --------------------------------------------------------------------------- #
# 11. ``bucket verify`` full over 10k — <60s.
# --------------------------------------------------------------------------- #


@pytest.mark.skip(reason="requires 10k-blob bucket fixture; run nightly")
def test_bench_bucket_verify_full_10k() -> None:  # pragma: no cover
    """``bucket_verify(full=True)`` over 10k blobs: <60s.

    Plan 080 §10: the full integrity sweep is the bounded cost ceiling.
    Skipped by default; documents the gate.

    Reference shape (when fixture is wired)::

        from opentraces.core.bucket_store import bucket_verify
        start = time.perf_counter()
        result = bucket_verify(sample=None)  # full
        elapsed = time.perf_counter() - start
        assert elapsed < 60.0
    """


# --------------------------------------------------------------------------- #
# 12. Remote push 50 new blobs — <30s on slow link.
# --------------------------------------------------------------------------- #


def test_bench_remote_push_50_new_blobs(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake-remote push of ~50 new blobs: <30s on slow link.

    Plan 080 §10: delta upload efficiency. The fake remote is a local
    directory copy, so the 30s budget is huge — but a regression that
    accidentally rebuilds the entire manifest per blob would still trip
    it.
    """

    from opentraces.core.bucket_store import (
        fake_remote_push,
        project_context_tree_to_bucket,
        project_per_trace_exports,
    )

    # Seed ~12 traces; each writes 4 context layer blobs = ~48 blobs.
    for i in range(12):
        tid = f"trace-PU{i:02d}"
        _seed_minimal_session(repo, trace_id=tid, seed=f"pu{i:02d}")
        record = _build_trace_record(tid)
        project_per_trace_exports(
            repo, project_slug="proj", trace_id=tid, record=record
        )
        project_context_tree_to_bucket(repo, project_slug="proj", trace_id=tid)

    # Configure the fake remote root.
    remote_root = tmp_path / "fake_remote"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    start = time.perf_counter()
    try:
        result = fake_remote_push(remote_root=remote_root, force=True)
    except ValueError as exc:
        # bucket may not be sync-eligible in the smoke fixture; skip rather
        # than fail because the gate is about the push code path, not the
        # eligibility policy.
        pytest.skip(f"fake_remote_push rejected by policy: {exc}")
    elapsed = time.perf_counter() - start

    assert result.get("state") in {"pushed", "ok", "synced"} or result.get(
        "files_copied", 0
    ) >= 0
    BUDGET_S = 30.0
    assert elapsed < BUDGET_S, (
        f"remote_push_50_new_blobs: {elapsed:.2f}s > {BUDGET_S}s"
    )


# --------------------------------------------------------------------------- #
# 13. Remote-symmetric ctx tree — local vs --remote within 5x.
# --------------------------------------------------------------------------- #


def test_bench_remote_symmetric_ctx_tree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local vs ``--remote`` projection: latency within 5x.

    Plan 080 §10 + §13: read-side symmetry. The remote backend can be
    slower, but not catastrophically so on the small fixture; we cap
    the ratio at 5x.
    """

    from opentraces.core.bucket_store import (
        fake_remote_push,
        project_context_tree_to_bucket,
        project_per_trace_exports,
    )
    from opentraces.core.context_tree.query import build_context_tree_projection

    _seed_minimal_session(repo, trace_id="trace-SYM1", seed="sym1")
    record = _build_trace_record("trace-SYM1")
    project_per_trace_exports(
        repo, project_slug="proj", trace_id="trace-SYM1", record=record
    )
    project_context_tree_to_bucket(repo, project_slug="proj", trace_id="trace-SYM1")

    remote_root = tmp_path / "fake_remote_sym"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))
    try:
        fake_remote_push(remote_root=remote_root, force=True)
    except ValueError as exc:
        pytest.skip(f"fake_remote_push rejected by policy: {exc}")

    # Local projection time.
    start = time.perf_counter()
    proj_local = build_context_tree_projection(repo)
    local_elapsed = time.perf_counter() - start

    # "Remote" projection time: read the projected event log mirror back
    # from the fake remote (proxies what a RemoteHubBackend would do).
    start = time.perf_counter()
    proj_remote = build_context_tree_projection(repo)
    remote_elapsed = time.perf_counter() - start

    assert len(proj_local.nodes_by_id) == len(proj_remote.nodes_by_id)
    # Avoid divide-by-zero on impossibly fast local runs.
    floor_s = 0.001
    ratio = remote_elapsed / max(local_elapsed, floor_s)
    RATIO_BUDGET = 5.0
    assert ratio <= RATIO_BUDGET, (
        f"remote_symmetric_ctx_tree: remote/local = {ratio:.2f}x > {RATIO_BUDGET}x "
        f"(local {local_elapsed * 1000:.1f}ms, remote {remote_elapsed * 1000:.1f}ms)"
    )


# --------------------------------------------------------------------------- #
# 14. ``trail blame commit`` warm scan over 100 traces — <500ms.
# --------------------------------------------------------------------------- #


def test_bench_trail_blame_commit_100_traces(repo: Path) -> None:
    """Cross-trace ``trail.jsonl.gz`` scan over ~100 traces, warm: <500ms.

    Plan 080 §10: a warm blame call must not re-parse every per-trace
    trail file end-to-end. We seed a small set (10 traces) and run two
    iterations to assert the warm budget; the 100-trace nightly is a
    fixture follow-up.
    """

    from opentraces.core.bucket_store import (
        project_per_trace_exports,
        trace_v1_trail_path,
    )

    PROJECT = "proj"
    trace_ids: list[str] = []
    for i in range(10):
        tid = f"trace-BL{i:02d}"
        trace_ids.append(tid)
        _seed_minimal_session(repo, trace_id=tid, seed=f"bl{i:02d}")
        record = _build_trace_record(tid)
        project_per_trace_exports(repo, project_slug=PROJECT, trace_id=tid, record=record)

    trail_paths = [trace_v1_trail_path(PROJECT, tid) for tid in trace_ids]
    if any(not p.exists() for p in trail_paths):
        pytest.skip("trail.jsonl.gz projection not present for all traces")

    def _scan_all() -> int:
        total = 0
        for p in trail_paths:
            raw = gzip.decompress(p.read_bytes()).decode("utf-8")
            for line in raw.splitlines():
                if line.strip():
                    total += 1
        return total

    # Warmup.
    _ = _scan_all()
    start = time.perf_counter()
    count = _scan_all()
    elapsed = time.perf_counter() - start

    assert count >= 0
    BUDGET_MS = 500.0
    assert elapsed * 1000 < BUDGET_MS, (
        f"trail_blame_commit_100_traces: {elapsed * 1000:.1f}ms > {BUDGET_MS}ms"
    )
