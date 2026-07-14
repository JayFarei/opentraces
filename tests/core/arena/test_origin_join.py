from __future__ import annotations

import json
from pathlib import Path

import pytest
import opentraces.core.arena.run_store as run_store_module
import opentraces.core.ingest as ingest_module
from opentraces.core import paths
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.labels import read_labels
from opentraces.core.arena.origin import (
    OriginJoinError,
    attach_captured_bench_labels,
    attach_explicit_bench_labels,
    detect_bench_invocations,
)
from opentraces.core.arena.run_store import RunStore
from opentraces.core.bucket_trace_records import (
    read_bucket_record_for_trace,
    trace_record_path,
)
from opentraces.core.bucket_layout import trace_v1_json_path, trace_v1_labels_path
from opentraces_schema import Agent, Observation, Outcome, Step, TraceRecord


RUN_ID = "run_20260714T190000000000Z_abcdef123456"
CLAIM = "Publishing reaches the configured remote."
PROJECT_SLUG = "project-origin"


def _record(*contents: str, step_content: str | None = None) -> TraceRecord:
    return TraceRecord(
        trace_id="trace-origin-123",
        session_id="session-origin-123",
        agent=Agent(name="test-agent"),
        task={"description": "Run a bench scenario."},
        steps=[
            Step(
                step_index=7,
                role="agent",
                content=step_content,
                observations=[
                    Observation(source_call_id=f"call-{index}", content=content)
                    for index, content in enumerate(contents, start=1)
                ],
            )
        ],
        outcome=Outcome(success=None),
    )


def _finalized_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    claim: str = CLAIM,
    verdict: str = "pass",
    product_pin: dict | None = None,
) -> RunStore:
    monkeypatch.setattr(run_store_module, "_new_run_id", lambda: RUN_ID)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    draft.write_text("source/scenario.py", "def test_publish(): pass\n")
    draft.write_json("actions/0001/result.json", {"returncode": 0})
    draft.finalize(
        build_result(
            run_id=draft.run_id,
            claim=claim,
            nodeid="bench/test_publish.py::test_publish",
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at="2026-07-14T19:00:00Z",
            duration_ms=1,
            execution_status="complete",
            verdict=verdict,
            reason=(
                {"code": "assertion_failed", "message": "product red"}
                if verdict == "fail"
                else None
            ),
            verifiers=[
                {
                    "name": "scenarios.publish.remote_commit_exists",
                    "source_ref": {
                        "path": "bench/test_publish.py",
                        "digest": "sha256:" + "a" * 64,
                    },
                    "status": verdict,
                    "duration_ms": 1,
                    "evidence_refs": ["actions/0001/result.json"],
                    "reason": None,
                }
            ],
            evidence={"complete": True, "requirements": []},
            recordings={"rewatchable": False, "channels": []},
            artifacts=[],
            capture=None,
            pins={
                "product": product_pin
                or {
                    "commit": "2ab03ac637e",
                    "worktree": "clean",
                    "dirty_diff_digest": None,
                }
            },
        )
    )
    return store


def _write_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, record: TraceRecord) -> None:
    monkeypatch.setattr(paths, "bucket_dir", lambda: tmp_path / "bucket")
    path = trace_v1_json_path(PROJECT_SLUG, record.trace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json() + "\n", encoding="utf-8")


def _origin_record() -> TraceRecord:
    record = _record("ordinary tool output")
    record.steps = [
        Step(step_index=index, role="agent", content=f"step {index}")
        for index in (1, 2, 3)
    ]
    return record


def test_detector_accepts_only_the_two_frozen_captured_output_forms() -> None:
    structured = json.dumps(
        {
            "status": "ok",
            "run_id": RUN_ID,
            "verdict": "pass",
            "claim": CLAIM,
            "result_ref": "/private/run/result.json",
        }
    )
    human = (
        f"bench_run_{RUN_ID} pass {CLAIM}\n"
        f"claim: {CLAIM}\n"
        "verdict: pass\n"
    )

    invocations = detect_bench_invocations(_record(human, structured))

    assert [item.output_format for item in invocations] == ["human", "json"]
    assert [item.run_id for item in invocations] == [RUN_ID, RUN_ID]
    assert [item.verdict for item in invocations] == ["pass", "pass"]
    assert [item.claim for item in invocations] == [CLAIM, CLAIM]
    assert [item.step_index for item in invocations] == [7, 7]
    assert [item.source_call_id for item in invocations] == ["call-1", "call-2"]


@pytest.mark.parametrize(
    "content",
    [
        f"prefix bench_run_{RUN_ID} pass {CLAIM}",
        f"bench_run_run_not-an-id pass {CLAIM}",
        f"bench_run_{RUN_ID} PASS {CLAIM}",
        json.dumps({"run_id": RUN_ID, "verdict": "pass"}),
        json.dumps({"run_id": RUN_ID, "verdict": "pass", "claim": ""}),
        json.dumps([{"run_id": RUN_ID, "verdict": "pass", "claim": CLAIM}]),
        json.dumps({"run_id": RUN_ID, "verdict": "pass", "claim": CLAIM}) + "\nextra",
    ],
)
def test_detector_rejects_near_matches_and_uncaptured_narration(content: str) -> None:
    record = _record(content, step_content=f"bench_run_{RUN_ID} pass {CLAIM}")

    assert detect_bench_invocations(record) == []


def test_captured_projector_verifies_and_attaches_once_after_trace_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _finalized_run(tmp_path, monkeypatch)
    record = _record(f"bench_run_{RUN_ID} pass {CLAIM}\nverdict: pass\n")
    _write_trace(tmp_path, monkeypatch, record)

    first = attach_captured_bench_labels(record, project_slug=PROJECT_SLUG, store=store)
    companion = trace_v1_labels_path(PROJECT_SLUG, record.trace_id)
    first_bytes = companion.read_bytes()
    second = attach_captured_bench_labels(record, project_slug=PROJECT_SLUG, store=store)

    assert first == second
    assert [(item.run_id, item.address, item.resolution) for item in first] == [
        (RUN_ID, record.trace_id, "captured")
    ]
    assert companion.read_bytes() == first_bytes
    rows = read_labels(PROJECT_SLUG, record.trace_id)
    assert len(rows) == 1
    assert rows[0]["run"]["id"] == RUN_ID
    assert rows[0]["subject"] == {"kind": "trace", "address": record.trace_id}


def test_captured_projector_refuses_dangling_or_mismatched_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = _record(f"bench_run_{RUN_ID} pass {CLAIM}")
    _write_trace(tmp_path, monkeypatch, record)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")

    with pytest.raises(OriginJoinError, match="finalized run"):
        attach_captured_bench_labels(record, project_slug=PROJECT_SLUG, store=store)

    store = _finalized_run(tmp_path, monkeypatch, claim="A different stored claim.")
    with pytest.raises(OriginJoinError, match="claim or verdict"):
        attach_captured_bench_labels(record, project_slug=PROJECT_SLUG, store=store)

    assert not trace_v1_labels_path(PROJECT_SLUG, record.trace_id).exists()


def test_captured_projector_refuses_inconsistent_product_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _finalized_run(
        tmp_path,
        monkeypatch,
        product_pin={
            "commit": "2ab03ac637e",
            "worktree": "clean",
            "dirty_diff_digest": "sha256:" + "b" * 64,
        },
    )
    record = _record(f"bench_run_{RUN_ID} pass {CLAIM}")
    _write_trace(tmp_path, monkeypatch, record)

    with pytest.raises(OriginJoinError, match="product pin"):
        attach_captured_bench_labels(record, project_slug=PROJECT_SLUG, store=store)

    assert not trace_v1_labels_path(PROJECT_SLUG, record.trace_id).exists()


def test_trace_without_captured_invocation_gets_no_origin_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _finalized_run(tmp_path, monkeypatch)
    record = _record("ordinary tool output")
    _write_trace(tmp_path, monkeypatch, record)

    assert attach_captured_bench_labels(record, project_slug=PROJECT_SLUG, store=store) == []
    assert not trace_v1_labels_path(PROJECT_SLUG, record.trace_id).exists()


def test_trace_bucket_ingestion_projects_captured_origin_idempotently(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _finalized_run(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "bucket_dir", lambda: tmp_path / "bucket")
    project_state = tmp_path / "projects" / PROJECT_SLUG
    project_state.mkdir(parents=True)
    monkeypatch.setattr(ingest_module, "get_project_dir", lambda _project: project_state)
    source = tmp_path / "session.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    record = _record(f"bench_run_{RUN_ID} pass {CLAIM}")

    first = ingest_module.write_trace_to_bucket(
        record,
        tmp_path,
        parser_name="claude-code",
        source_jsonl=source,
        trace_record_only=True,
    )
    companion = trace_v1_labels_path(PROJECT_SLUG, record.trace_id)
    first_bytes = companion.read_bytes()
    second = ingest_module.write_trace_to_bucket(
        record,
        tmp_path,
        parser_name="claude-code",
        source_jsonl=source,
        trace_record_only=True,
    )

    assert first.writes["arena_origin_labels"] is None
    assert second.writes["arena_origin_labels"] is None
    assert companion.read_bytes() == first_bytes
    assert len(read_labels(PROJECT_SLUG, record.trace_id)) == 1
    assert store.verify(store.root / RUN_ID)


@pytest.mark.parametrize(
    ("address_suffix", "expected_subject"),
    [
        ("", {"kind": "trace", "address": "trace-origin-123"}),
        (":2", {"kind": "slice", "address": "trace-origin-123:2-2"}),
        (":1-3", {"kind": "slice", "address": "trace-origin-123:1-3"}),
    ],
)
def test_explicit_origin_resolves_shared_trace_addresses_and_mints_same_label_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    address_suffix: str,
    expected_subject: dict[str, str],
) -> None:
    store = _finalized_run(tmp_path, monkeypatch)
    record = _origin_record()
    _write_trace(tmp_path, monkeypatch, record)

    attachment = attach_explicit_bench_labels(
        store.root / RUN_ID,
        address=f"{record.trace_id}{address_suffix}",
        store=store,
    )

    assert (attachment.run_id, attachment.address, attachment.resolution) == (
        RUN_ID,
        expected_subject["address"],
        "explicit",
    )
    rows = read_labels(PROJECT_SLUG, record.trace_id)
    assert len(rows) == 1
    assert rows[0]["subject"] == expected_subject


@pytest.mark.parametrize(
    ("address_suffix", "expected_subject"),
    [
        ("", {"kind": "trace", "address": "trace-origin-123"}),
        (":2", {"kind": "slice", "address": "trace-origin-123:2-2"}),
        (":1-3", {"kind": "slice", "address": "trace-origin-123:1-3"}),
    ],
)
def test_explicit_origin_resolves_canonical_record_without_additive_trace_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    address_suffix: str,
    expected_subject: dict[str, str],
) -> None:
    store = _finalized_run(tmp_path, monkeypatch)
    monkeypatch.setattr(paths, "bucket_dir", lambda: tmp_path / "bucket")
    project_state = tmp_path / "projects" / PROJECT_SLUG
    project_state.mkdir(parents=True)
    monkeypatch.setattr(ingest_module, "get_project_dir", lambda _project: project_state)
    source = tmp_path / "session.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    record = _origin_record()

    ingest_module.write_trace_to_bucket(
        record,
        project_state,
        parser_name="claude-code",
        source_jsonl=source,
        trace_record_only=True,
    )

    assert trace_record_path(PROJECT_SLUG, record.trace_id).is_file()
    stored = read_bucket_record_for_trace(record.trace_id)
    assert stored is not None
    assert stored.record.trace_id == record.trace_id
    assert [step.step_index for step in stored.record.steps] == [1, 2, 3]
    assert not trace_v1_json_path(PROJECT_SLUG, record.trace_id).exists()

    attachment = attach_explicit_bench_labels(
        store.root / RUN_ID,
        address=f"{record.trace_id}{address_suffix}",
        store=store,
    )

    assert (attachment.run_id, attachment.address, attachment.resolution) == (
        RUN_ID,
        expected_subject["address"],
        "explicit",
    )
    rows = read_labels(PROJECT_SLUG, record.trace_id)
    assert len(rows) == 1
    assert rows[0]["subject"] == expected_subject


@pytest.mark.parametrize("address", ["trace-origin-123:4", "trace-origin-123:3-1"])
def test_explicit_origin_refuses_dangling_or_invalid_points_and_spans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    address: str,
) -> None:
    store = _finalized_run(tmp_path, monkeypatch)
    record = _origin_record()
    _write_trace(tmp_path, monkeypatch, record)

    with pytest.raises(OriginJoinError, match="origin address"):
        attach_explicit_bench_labels(store.root / RUN_ID, address=address, store=store)

    assert not trace_v1_labels_path(PROJECT_SLUG, record.trace_id).exists()
