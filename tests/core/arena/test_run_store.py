from __future__ import annotations

import json
from pathlib import Path

import pytest

import opentraces.core.arena.run_store as run_store_module
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import (
    FinalizedRunError,
    RunIntegrityError,
    RunStore,
    StorageFinalizeError,
)


def _result(run_id: str, *, verdict: str = "pass") -> dict:
    reason = {
        "pass": None,
        "fail": {"code": "assertion_failed", "message": "red"},
        "skip": {"code": "absent_prerequisite", "message": "not available"},
    }[verdict]
    return build_result(
        run_id=run_id,
        claim="A stored run keeps its complete declared exhaust.",
        nodeid="tests/scenarios/test_store.py::test_store",
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at="2026-07-13T12:00:00Z",
        duration_ms=1,
        execution_status="complete",
        verdict=verdict,
        reason=reason,
        verifiers=[],
        evidence={"complete": True, "requirements": []},
        recordings={"rewatchable": False, "channels": []},
        artifacts=[],
        capture=None,
        pins={},
    )


def test_begin_creates_the_canonical_complete_exhaust_layout(tmp_path: Path) -> None:
    draft = RunStore(tmp_path / "runs" / "v1").begin()

    assert {p.name for p in draft.path.iterdir() if p.is_dir()} == {
        "source",
        "actions",
        "ledgers",
        "capture",
        "recordings",
        "artifacts",
    }
    assert not (draft.path / "result.json").exists()


def test_result_json_is_last_and_finalizes_a_write_once_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    draft.write_text("actions/0001/stdout", "healthy\n")

    finalized = draft.finalize(_result(draft.run_id))

    assert finalized == store.root / draft.run_id
    assert json.loads((finalized / "result.json").read_text())["verdict"] == "pass"
    assert store.verify(finalized) is True
    with pytest.raises(FinalizedRunError):
        draft.write_text("actions/0001/stderr", "late mutation")


def test_external_post_finalize_mutation_is_detected(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    draft.write_text("actions/0001/stdout", "original")
    finalized = draft.finalize(_result(draft.run_id))
    output = finalized / "actions" / "0001" / "stdout"
    output.chmod(0o600)
    output.write_text("tampered", encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="actions/0001/stdout"):
        store.verify(finalized)


def test_retry_always_mints_a_fresh_run_id(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs" / "v1")

    first = store.begin()
    second = store.begin()

    assert first.run_id != second.run_id


def test_storage_failure_retains_the_provisional_outcome_in_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    provisional = _result(draft.run_id, verdict="fail")

    def fail_result_write(path: Path, payload: dict) -> None:
        raise OSError("disk refused final result")

    monkeypatch.setattr(draft, "_write_result", fail_result_write)

    with pytest.raises(StorageFinalizeError) as caught:
        draft.finalize(provisional)

    recovery = caught.value.recovery_path
    recovered = json.loads((recovery / "provisional_result.json").read_text())
    assert recovered["verdict"] == "fail"
    assert recovered["execution_status"] == "complete"
    assert not (store.root / draft.run_id / "result.json").exists()


def test_manifest_write_failure_recovers_the_provisional_outcome_from_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    provisional = _result(draft.run_id, verdict="fail")
    original_write_json = draft.write_json

    def fail_manifest_write(relative: str | Path, payload: dict) -> Path:
        if str(relative) == ".integrity.json":
            raise OSError("disk refused integrity manifest")
        return original_write_json(relative, payload)

    monkeypatch.setattr(draft, "write_json", fail_manifest_write)

    with pytest.raises(StorageFinalizeError) as caught:
        draft.finalize(provisional)

    recovery = caught.value.recovery_path
    recovered = json.loads((recovery / "provisional_result.json").read_text())
    assert recovered["verdict"] == "fail"
    assert recovered["execution_status"] == "complete"
    assert not (store.root / draft.run_id).exists()
    assert not (store.index_root / f"{draft.run_id}.json").exists()


def test_staging_move_failure_recovers_the_provisional_outcome_from_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    provisional = _result(draft.run_id, verdict="skip")
    staging_path = draft.path
    original_replace = Path.replace
    failed_once = False

    def fail_first_staging_move(path: Path, target: Path) -> Path:
        nonlocal failed_once
        if path == staging_path and not failed_once:
            failed_once = True
            raise OSError("disk refused staging move")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_first_staging_move)

    with pytest.raises(StorageFinalizeError) as caught:
        draft.finalize(provisional)

    recovery = caught.value.recovery_path
    recovered = json.loads((recovery / "provisional_result.json").read_text())
    assert recovered["verdict"] == "skip"
    assert not (store.root / draft.run_id).exists()
    assert not (store.index_root / f"{draft.run_id}.json").exists()


def test_index_failure_occurs_before_result_becomes_the_finalization_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()
    provisional = _result(draft.run_id, verdict="pass")
    final_path = store.root / draft.run_id
    index_path = store.index_root / f"{draft.run_id}.json"
    observed_result_presence: list[bool] = []
    original_atomic_write_json = run_store_module._atomic_write_json

    def kill_during_index_write(path: Path, payload: dict) -> None:
        if path == index_path:
            observed_result_presence.append((final_path / "result.json").exists())
            raise OSError("killed before index commit")
        original_atomic_write_json(path, payload)

    monkeypatch.setattr(run_store_module, "_atomic_write_json", kill_during_index_write)

    with pytest.raises(StorageFinalizeError) as caught:
        draft.finalize(provisional)

    assert observed_result_presence == [False]
    recovery = caught.value.recovery_path
    assert not (recovery / "result.json").exists()
    recovered = json.loads((recovery / "provisional_result.json").read_text())
    assert recovered["verdict"] == "pass"
    assert not index_path.exists()


def test_source_record_copies_executed_source_and_identity(tmp_path: Path) -> None:
    scenario = tmp_path / "test_scenario.py"
    scenario.write_text('def test_claim(bench):\n    """A claim."""\n', encoding="utf-8")
    draft = RunStore(tmp_path / "runs" / "v1").begin()

    record = draft.record_source(
        source_path=scenario,
        nodeid="test_scenario.py::test_claim",
        claim="A claim.",
        scenario_path="tests/test_scenario.py",
        repository="JayFarei/opentraces",
        commit="abc123",
        dirty_diff_digest="sha256:deadbeef",
    )

    assert set(record) == {
        "nodeid",
        "claim",
        "scenario_path",
        "repository",
        "commit",
        "dirty_diff_digest",
        "copied_source_path",
    }
    assert record["copied_source_path"] == "source/scenario.py"
    assert (draft.path / "source" / "scenario.py").read_bytes() == scenario.read_bytes()
