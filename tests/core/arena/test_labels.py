from __future__ import annotations

import json
from pathlib import Path

import opentraces.core.arena.run_store as run_store_module
import pytest
from opentraces.cli.trace import _trace_overview
from opentraces.core import paths
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.labels import (
    LabelContractError,
    LabelIntegrityError,
    attach_labels,
    complete_run_digest,
    label_summary_for_trace,
    mint_labels_for_run,
    read_labels,
    verify_label,
)
from opentraces.core.arena.run_store import RunIntegrityError, RunStore
from opentraces.core.bucket_layout import trace_v1_json_path, trace_v1_labels_path
from opentraces_schema import Agent, Outcome, TraceRecord


RUN_ID = "run_20260714T190000000000Z_abcdef123456"
TRACE_ID = "trace-origin-123"
PROJECT_SLUG = "project-labels"
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64


def _result(
    *,
    run_id: str,
    machinery_error: bool = False,
    missing_evidence: bool = False,
    duplicate_verifiers: bool = False,
) -> dict:
    verifiers = (
        []
        if machinery_error
        else [
            {
                "name": "scenarios.publish.remote_commit_exists",
                "source_ref": {"path": "bench/test_publish.py", "digest": SHA_A},
                "status": "pass",
                "duration_ms": 8,
                "evidence_refs": ["ledgers/huggingface.jsonl"],
                "reason": None,
            },
            {
                "name": "scenarios.publish.local_state_matches",
                "source_ref": {"path": "bench/test_publish.py", "digest": SHA_B},
                "status": "pass",
                "duration_ms": 3,
                "evidence_refs": [
                    "actions/9999/missing.json" if missing_evidence else "actions/0001/result.json"
                ],
                "reason": None,
            },
        ]
    )
    if duplicate_verifiers:
        verifiers[1] = json.loads(json.dumps(verifiers[0]))
    return build_result(
        run_id=run_id,
        claim="Publishing reaches the configured remote.",
        nodeid="bench/test_publish.py::test_publish",
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at="2026-07-14T19:00:00Z",
        duration_ms=100,
        execution_status="error" if machinery_error else "complete",
        verdict=None if machinery_error else "pass",
        reason=({"code": "runner_failed", "message": "runner failed"} if machinery_error else None),
        verifiers=verifiers,
        evidence=(
            {
                "complete": False,
                "requirements": [
                    {
                        "name": "bench.adjudication",
                        "complete": False,
                        "evidence_refs": [],
                    }
                ],
            }
            if machinery_error
            else {
                "complete": True,
                "requirements": [
                    {
                        "name": "remote commit",
                        "complete": True,
                        "evidence_refs": ["ledgers/huggingface.jsonl"],
                    }
                ],
            }
        ),
        recordings={"rewatchable": False, "channels": []},
        artifacts=[],
        capture=None,
        pins={
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "dirty",
                "dirty_diff_digest": "sha256:" + "c" * 64,
            }
        },
    )


def _finalized_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    machinery_error: bool = False,
    missing_evidence: bool = False,
    duplicate_verifiers: bool = False,
) -> tuple[RunStore, Path]:
    monkeypatch.setattr(run_store_module, "_new_run_id", lambda: RUN_ID)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    draft.write_text("source/scenario.py", "def test_publish(): pass\n")
    draft.write_text("ledgers/huggingface.jsonl", '{"operation":"commit"}\n')
    draft.write_json("actions/0001/result.json", {"returncode": 0})
    return store, draft.finalize(
        _result(
            run_id=draft.run_id,
            machinery_error=machinery_error,
            missing_evidence=missing_evidence,
            duplicate_verifiers=duplicate_verifiers,
        )
    )


def _set_bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bucket = tmp_path / "bucket"
    monkeypatch.setattr(paths, "bucket_dir", lambda: bucket)
    return bucket


def _write_subject_trace(*, metadata: dict[str, object] | None = None) -> Path:
    path = trace_v1_json_path(PROJECT_SLUG, TRACE_ID)
    path.parent.mkdir(parents=True)
    record = TraceRecord(
        trace_id=TRACE_ID,
        session_id="session-label-subject",
        agent=Agent(name="test-agent"),
        task={"description": "Publishing reaches the configured remote."},
        steps=[],
        outcome=Outcome(success=None),
        metadata=metadata or {},
    )
    path.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    return path


def test_mint_freezes_one_deterministic_row_per_verifier_with_complete_run_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch)

    first = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )
    second = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )

    assert second == first
    assert len(first) == 2
    assert all(verify_label(row, store=store) for row in first)
    assert set(first[0]) == {
        "schema_version",
        "label_id",
        "kind",
        "subject",
        "claim",
        "verdict",
        "verifier",
        "run",
        "product_pin",
        "run_facts",
    }
    assert len({row["label_id"] for row in first}) == 2
    assert [row["label_id"] for row in first] == [
        "lbl_19db0aa70f3b77d3827602806c04a1da0fa520f3f499420293ffaa953ac2e04c",
        "lbl_dd673a1f49c93a9ea1ba681b9c8d30295fdf24fe60c68da845322bd9fe109498",
    ]
    assert [row["verifier"]["ordinal"] for row in first] == [1, 2]
    assert {row["schema_version"] for row in first} == {"opentraces.arena.label.v0"}
    assert {row["kind"] for row in first} == {"bench"}
    assert {row["subject"]["kind"] for row in first} == {"trace"}
    assert {row["subject"]["address"] for row in first} == {TRACE_ID}
    assert {row["claim"] for row in first} == {"Publishing reaches the configured remote."}
    assert {row["verdict"] for row in first} == {"pass"}
    assert first[0]["verifier"] == {
        "ordinal": 1,
        "name": "scenarios.publish.remote_commit_exists",
        "source_ref": {"path": "bench/test_publish.py", "digest": SHA_A},
        "status": "pass",
        "evidence_refs": ["ledgers/huggingface.jsonl"],
    }
    assert {row["run"]["id"] for row in first} == {RUN_ID}
    assert {row["run"]["ref"] for row in first} == {f"runs/v1/{RUN_ID}"}
    assert {row["run"]["complete_digest"] for row in first} == {
        complete_run_digest(run_path, store=store)
    }
    assert complete_run_digest(run_path, store=store) == (
        "sha256:7d43965f4d1f287f29428af1b8fa992846254dbde0e91166b184a2d98a0f6fb2"
    )
    assert first[0]["product_pin"] == {
        "commit": "2ab03ac637e",
        "worktree": "dirty",
        "dirty_diff_digest": "sha256:" + "c" * 64,
    }
    # Recording and required-evidence completeness remain independent of grade.
    assert first[0]["run_facts"] == {
        "evidence_complete": True,
        "rewatchable": False,
    }


def test_attach_is_idempotent_byte_identical_canonical_and_spine_preserving(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    trace_path = _write_subject_trace(metadata={"untouched": True})
    trace_before = trace_path.read_bytes()
    rows = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )

    first_path = attach_labels(
        project_slug=PROJECT_SLUG,
        trace_id=TRACE_ID,
        labels=[rows[0]],
        store=store,
    )
    first_bytes = first_path.read_bytes()
    second_path = attach_labels(
        project_slug=PROJECT_SLUG,
        trace_id=TRACE_ID,
        labels=[rows[0]],
        store=store,
    )

    assert second_path == trace_v1_labels_path(PROJECT_SLUG, TRACE_ID)
    assert second_path.read_bytes() == first_bytes
    assert first_bytes[4:8] == b"\x00\x00\x00\x00"
    assert trace_path.read_bytes() == trace_before
    assert read_labels(PROJECT_SLUG, TRACE_ID) == [rows[0]]

    attach_labels(
        project_slug=PROJECT_SLUG,
        trace_id=TRACE_ID,
        labels=reversed(rows),
        store=store,
    )
    assert [row["label_id"] for row in read_labels(PROJECT_SLUG, TRACE_ID)] == sorted(
        row["label_id"] for row in rows
    )
    assert trace_path.read_bytes() == trace_before


def test_identical_verifier_calls_remain_two_rows_by_ordinal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch, duplicate_verifiers=True)

    rows = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )

    assert len(rows) == 2
    assert [row["verifier"]["ordinal"] for row in rows] == [1, 2]
    assert rows[0]["verifier"]["name"] == rows[1]["verifier"]["name"]
    assert rows[0]["label_id"] != rows[1]["label_id"]


def test_tampering_any_run_byte_invalidates_an_existing_label_and_new_mint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    label = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )[0]
    ledger = run_path / "ledgers" / "huggingface.jsonl"
    ledger.chmod(0o600)
    ledger.write_text('{"operation":"tampered"}\n', encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="ledgers/huggingface.jsonl"):
        verify_label(label, store=store)
    with pytest.raises(RunIntegrityError, match="ledgers/huggingface.jsonl"):
        mint_labels_for_run(
            run_path,
            subject={"kind": "trace", "address": TRACE_ID},
            store=store,
        )


def test_tampering_result_json_invalidates_the_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    label = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )[0]
    result_path = run_path / "result.json"
    result_path.chmod(0o600)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["scenario"]["claim"] = "forged"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="result.json"):
        verify_label(label, store=store)


def test_missing_run_and_mismatched_digest_refuse_mint_or_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    rows = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )

    with pytest.raises(RunIntegrityError, match="missing result"):
        mint_labels_for_run(
            store.root / "run_missing",
            subject={"kind": "trace", "address": TRACE_ID},
            store=store,
        )
    with pytest.raises(LabelIntegrityError, match="complete run digest"):
        mint_labels_for_run(
            run_path,
            subject={"kind": "trace", "address": TRACE_ID},
            store=store,
            expected_complete_run_digest="sha256:" + "0" * 64,
        )
    forged = json.loads(json.dumps(rows[0]))
    forged["run"]["complete_digest"] = "sha256:" + "0" * 64
    with pytest.raises(LabelIntegrityError, match="label_id"):
        verify_label(forged, store=store)


@pytest.mark.parametrize(
    "subject",
    [
        {"kind": "trace", "address": "trace:1-2"},
        {"kind": "slice", "address": "trace"},
        {"kind": "slice", "address": "trace:3-2"},
        {"kind": "run", "address": TRACE_ID},
        {"kind": "trace", "address": ""},
        {"kind": "trace", "address": "."},
        {"kind": "trace", "address": ".."},
    ],
)
def test_mint_refuses_noncanonical_trace_or_slice_subjects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    subject: dict[str, str],
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch)

    with pytest.raises(LabelContractError, match="subject"):
        mint_labels_for_run(run_path, subject=subject, store=store)


def test_machinery_error_with_null_verdict_cannot_mint_a_grade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch, machinery_error=True)

    with pytest.raises(LabelContractError, match="adjudicated verdict"):
        mint_labels_for_run(
            run_path,
            subject={"kind": "trace", "address": TRACE_ID},
            store=store,
        )


def test_mint_refuses_a_verifier_evidence_ref_not_persisted_in_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch, missing_evidence=True)

    with pytest.raises(LabelIntegrityError, match="not persisted"):
        mint_labels_for_run(
            run_path,
            subject={"kind": "trace", "address": TRACE_ID},
            store=store,
        )


def test_normal_trace_summary_is_bounded_and_reads_the_companion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    _write_subject_trace()
    rows = mint_labels_for_run(
        run_path,
        subject={"kind": "trace", "address": TRACE_ID},
        store=store,
    )
    attach_labels(
        project_slug=PROJECT_SLUG,
        trace_id=TRACE_ID,
        labels=rows,
        store=store,
    )

    summary = label_summary_for_trace(TRACE_ID, limit=1)

    assert summary["count"] == 2
    assert summary["truncated"] is True
    assert len(summary["items"]) == 1
    assert set(summary["items"][0]) == {
        "label_id",
        "verdict",
        "verifier",
        "subject",
        "run_ref",
    }
    record = TraceRecord(
        trace_id=TRACE_ID,
        session_id="session-label-summary",
        agent=Agent(name="test-agent"),
        task={"description": "Publishing reaches the configured remote."},
        steps=[],
        outcome=Outcome(success=None),
    )
    assert _trace_overview(record, include_labels=True)["labels"] == (
        label_summary_for_trace(TRACE_ID)
    )
