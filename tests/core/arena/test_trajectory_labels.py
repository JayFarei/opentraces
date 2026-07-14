from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from opentraces_schema import TraceRecord

import opentraces.core.arena.run_store as run_store_module
from opentraces.capture.claude_code.parse import ClaudeCodeParser
from opentraces.core import paths
from opentraces.core._bucket_io import _canonical_json
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.labels import (
    LabelIntegrityError,
    attach_labels,
    mint_labels_for_run,
    read_labels,
    stage_slice_artifact,
    verify_label,
)
from opentraces.core.arena.run_store import RunStore
from opentraces.core.bucket_layout import trace_v1_json_path, trace_v1_labels_path
from opentraces.core.slicing.models import Trajectory
from opentraces.core.trace_slices import (
    TraceMaterializationRef,
    slice_by_steps,
)


RUN_ID = "run_20260714T230000000000Z_291291291291"
PROJECT_SLUG = "project-a8-trajectory-labels"
SHA_A = "sha256:" + "a" * 64
_REAL_CAPTURE_FIXTURE = (
    Path(__file__).parents[2]
    / "fixtures"
    / "claude"
    / "claude-linear-edit-real-session.jsonl"
)


def _real_capture_record() -> TraceRecord:
    record = ClaudeCodeParser().parse_session(_REAL_CAPTURE_FIXTURE)
    assert record is not None
    assert [step.step_index for step in record.steps] == [1, 2, 3, 4, 5, 6]
    return record


def _set_bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bucket = tmp_path / "bucket"
    monkeypatch.setattr(paths, "bucket_dir", lambda: bucket)
    return bucket


def _persist_subject(record: TraceRecord) -> Path:
    path = trace_v1_json_path(PROJECT_SLUG, record.trace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    return path


def _result(*, run_id: str, evidence_ref: str) -> dict:
    return build_result(
        run_id=run_id,
        claim="The selected captured trajectory satisfies the verifier.",
        nodeid="bench/test_trajectory.py::test_selected_trajectory",
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at="2026-07-14T23:00:00Z",
        duration_ms=100,
        execution_status="complete",
        verdict="pass",
        reason=None,
        verifiers=[
            {
                "name": "scenarios.trajectory.selected_steps_match",
                "source_ref": {
                    "path": "bench/test_trajectory.py",
                    "digest": SHA_A,
                },
                "status": "pass",
                "duration_ms": 8,
                "evidence_refs": [evidence_ref],
                "reason": None,
            }
        ],
        evidence={
            "complete": True,
            "requirements": [
                {
                    "name": "materialized trajectory",
                    "complete": True,
                    "evidence_refs": [evidence_ref],
                }
            ],
        },
        recordings={"rewatchable": False, "channels": []},
        artifacts=[{"path": evidence_ref, "media_type": "application/json"}],
        capture=None,
        pins={
            "product": {
                "commit": "df617243001",
                "worktree": "clean",
                "dirty_diff_digest": None,
            }
        },
    )


def _finalize_slice_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    trace_ref: TraceMaterializationRef,
    trajectory: Trajectory | None = None,
    subject: dict[str, str] | None = None,
    verifier_evidence_ref: str | None = None,
) -> tuple[RunStore, Path, dict]:
    monkeypatch.setattr(run_store_module, "_new_run_id", lambda: RUN_ID)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    draft.write_text("source/scenario.py", "def test_selected_trajectory(): pass\n")
    staged = stage_slice_artifact(
        draft,
        trace_ref,
        trajectory=trajectory,
        subject=subject,
    )
    run_path = draft.finalize(
        _result(
            run_id=draft.run_id,
            evidence_ref=verifier_evidence_ref or staged["artifact_ref"],
        )
    )
    return store, run_path, staged


def test_authentic_trajectory_pin_round_trips_positions_to_canonical_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    record = _real_capture_record()
    _persist_subject(record)
    trace_ref = TraceMaterializationRef.from_record(record)
    trajectory = Trajectory(start=0, end=2, kind="user_turn", label="captured run")
    store, run_path, staged = _finalize_slice_run(
        tmp_path,
        monkeypatch,
        trace_ref=trace_ref,
        trajectory=trajectory,
    )

    rows = mint_labels_for_run(
        run_path,
        subject=staged["subject"],
        store=store,
        trace_ref=trace_ref,
        trajectory=trajectory,
    )
    label = rows[0]
    artifact_path = run_path / staged["artifact_ref"]
    artifact_bytes = artifact_path.read_bytes()
    materialized = json.loads(artifact_bytes)
    addressed = slice_by_steps(
        trace_ref.trace_map,
        record,
        start_step_index=1,
        end_step_index=3,
    )

    assert label["schema_version"] == "opentraces.arena.label.v0"
    assert label["subject"] == {
        "kind": "slice",
        "address": f"{record.trace_id}:1-3",
    }
    assert [step["step_index"] for step in materialized["steps"]] == [1, 2, 3]
    assert _canonical_json(materialized["steps"]) == _canonical_json(addressed["steps"])
    assert materialized["map_node_refs"] == addressed["map_node_refs"]
    assert label["slice_pin"]["materialized_ref"] == staged["artifact_ref"]
    assert label["slice_pin"]["artifact_digest"] == (
        "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
    )
    assert label["slice_pin"]["slice_id"] == materialized["slice_id"]
    assert label["slice_pin"]["provenance_kind"] == "trajectory"
    assert label["slice_pin"]["trajectory"] == trajectory.to_dict()
    assert label["slice_pin"]["slicing_schema_version"] == "opentraces.slicing.v1"
    assert label["slice_pin"]["trajectory_position_range"] == {"start": 0, "end": 2}
    assert label["slice_pin"]["coordinate_translation"] == (
        "array_position_to_step_index"
    )
    assert verify_label(label, store=store)

    first_path = attach_labels(
        project_slug=PROJECT_SLUG,
        trace_id=record.trace_id,
        labels=rows,
        store=store,
    )
    first_bytes = first_path.read_bytes()
    second_rows = mint_labels_for_run(
        run_path,
        subject=staged["subject"],
        store=store,
        trace_ref=trace_ref,
        trajectory=trajectory,
    )
    second_path = attach_labels(
        project_slug=PROJECT_SLUG,
        trace_id=record.trace_id,
        labels=second_rows,
        store=store,
    )
    assert second_rows == rows
    assert second_path.read_bytes() == first_bytes
    assert len(read_labels(PROJECT_SLUG, record.trace_id)) == 1


@pytest.mark.parametrize(("start", "end"), [(2, 2), (2, 4)])
def test_explicit_point_and_span_use_canonical_step_slicing_without_positions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    start: int,
    end: int,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    record = _real_capture_record()
    _persist_subject(record)
    trace_ref = TraceMaterializationRef.from_record(record)
    subject = {"kind": "slice", "address": f"{record.trace_id}:{start}-{end}"}
    store, run_path, staged = _finalize_slice_run(
        tmp_path,
        monkeypatch,
        trace_ref=trace_ref,
        subject=subject,
    )

    label = mint_labels_for_run(
        run_path,
        subject=subject,
        store=store,
        trace_ref=trace_ref,
    )[0]

    assert staged["subject"] == subject
    assert label["slice_pin"]["provenance_kind"] == "explicit"
    assert "trajectory_position_range" not in label["slice_pin"]
    assert "coordinate_translation" not in label["slice_pin"]
    assert "trajectory" not in label["slice_pin"]
    assert verify_label(label, store=store)


def test_slice_mint_refuses_when_grading_verifier_did_not_name_the_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    record = _real_capture_record()
    _persist_subject(record)
    trace_ref = TraceMaterializationRef.from_record(record)
    trajectory = Trajectory(start=0, end=2, kind="user_turn", label="captured run")
    store, run_path, staged = _finalize_slice_run(
        tmp_path,
        monkeypatch,
        trace_ref=trace_ref,
        trajectory=trajectory,
        verifier_evidence_ref="source/scenario.py",
    )

    with pytest.raises(LabelIntegrityError, match="verifier evidence_refs"):
        mint_labels_for_run(
            run_path,
            subject=staged["subject"],
            store=store,
            trace_ref=trace_ref,
            trajectory=trajectory,
        )


def test_bare_slice_subject_without_materialization_ref_is_never_mintable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    record = _real_capture_record()
    _persist_subject(record)
    trace_ref = TraceMaterializationRef.from_record(record)
    subject = {"kind": "slice", "address": f"{record.trace_id}:1-3"}
    store, run_path, _staged = _finalize_slice_run(
        tmp_path,
        monkeypatch,
        trace_ref=trace_ref,
        subject=subject,
    )

    with pytest.raises(LabelIntegrityError, match="materialization reference"):
        mint_labels_for_run(run_path, subject=subject, store=store)


def test_slice_artifact_is_inside_run_integrity_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_bucket(tmp_path, monkeypatch)
    record = _real_capture_record()
    trace_ref = TraceMaterializationRef.from_record(record)
    store, run_path, staged = _finalize_slice_run(
        tmp_path,
        monkeypatch,
        trace_ref=trace_ref,
        trajectory=Trajectory(start=0, end=2, kind="user_turn", label="captured run"),
    )

    integrity = json.loads((run_path / ".integrity.json").read_text(encoding="utf-8"))
    assert staged["artifact_ref"].startswith("artifacts/slices/")
    assert staged["artifact_ref"] in integrity["files"]
    assert store.verify(run_path)
