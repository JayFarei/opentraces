from __future__ import annotations

import hashlib
import socket
import subprocess
from pathlib import Path

import pytest

from opentraces.core.arena.contract import build_result
from opentraces.core.arena.retrieval import (
    StoredVerifierMismatch,
    list_stored_runs,
    rerender_stored_run,
    reverify_stored_run,
)
from opentraces.core.arena.run_store import RunIntegrityError, RunStore


def _source_digest() -> str:
    return f"sha256:{hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}"


def stored_artifact_verifier(evidence) -> dict[str, list[str]]:
    observation = evidence.read_json("artifacts/observation.json")
    assert observation["healthy"] is True
    return {"evidence_refs": ["artifacts/observation.json"]}


def _result(run_id: str) -> dict:
    return build_result(
        run_id=run_id,
        claim="A finalized run can be retrieved and reverifed without a box.",
        nodeid="tests/core/arena/test_retrieval.py::test_reverify_uses_stored_evidence_only",
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at="2026-07-15T12:00:00Z",
        duration_ms=1,
        execution_status="complete",
        verdict="pass",
        reason=None,
        verifiers=[
            {
                "name": f"{stored_artifact_verifier.__module__}.{stored_artifact_verifier.__qualname__}",
                "source_ref": {
                    "path": "tests/core/arena/test_retrieval.py",
                    "digest": _source_digest(),
                },
                "status": "pass",
                "duration_ms": 0,
                "evidence_refs": ["artifacts/observation.json"],
                "reason": None,
            }
        ],
        evidence={"complete": True, "requirements": []},
        recordings={"rewatchable": False, "channels": []},
        artifacts=["artifacts/observation.json"],
        capture=None,
        pins={},
    )


def _finalized(store: RunStore, *, healthy: bool = True) -> Path:
    draft = store.begin()
    draft.write_text("source/scenario.py", "def scenario(): pass\n")
    draft.write_json("source/source.json", {"nodeid": "scenario::stored"})
    draft.write_json(
        "source/verifiers.json",
        {
            "sources": [
                {
                    "path": "tests/core/arena/test_retrieval.py",
                    "digest": _source_digest(),
                }
            ]
        },
    )
    draft.write_json("artifacts/observation.json", {"healthy": healthy})
    return draft.finalize(_result(draft.run_id))


def _snapshot(path: Path) -> dict[str, bytes]:
    return {
        child.relative_to(path).as_posix(): child.read_bytes()
        for child in sorted(path.rglob("*"))
        if child.is_file()
    }


def test_list_returns_only_verified_finalized_runs(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)
    pending = store.begin()
    pending.write_text("artifacts/pending.txt", "not finalized")
    recovery = store.recovery_root / "run_recovery"
    recovery.mkdir(parents=True)
    (recovery / "provisional_result.json").write_text("{}\n", encoding="utf-8")

    records = list_stored_runs(store)

    assert [record.run_id for record in records] == [finalized.name]
    assert records[0].verdict == "pass"
    assert records[0].claim == "A finalized run can be retrieved and reverifed without a box."
    assert pending.run_id not in {record.run_id for record in records}
    assert "run_recovery" not in {record.run_id for record in records}


def test_list_fails_closed_when_an_indexed_run_byte_changes(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)
    artifact = finalized / "artifacts" / "observation.json"
    artifact.chmod(0o600)
    artifact.write_text('{"healthy":false}\n', encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="artifacts/observation.json"):
        list_stored_runs(store)


def test_rerender_verifies_first_and_is_byte_stable(tmp_path: Path, monkeypatch) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)
    output = tmp_path / "views" / "run.html"
    calls: list[Path] = []
    original_verify = RunStore.verify

    def observed_verify(self: RunStore, run_path: Path | str) -> bool:
        calls.append(Path(run_path))
        return original_verify(self, run_path)

    monkeypatch.setattr(RunStore, "verify", observed_verify)

    first = rerender_stored_run(store, finalized.name, output_path=output).read_bytes()
    second = rerender_stored_run(store, finalized.name, output_path=output).read_bytes()

    assert first == second
    assert calls[0] == finalized
    assert len(calls) >= 2


def test_rerender_uses_no_process_network_or_new_run(tmp_path: Path, monkeypatch) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)

    def forbidden(*args, **kwargs):
        raise AssertionError("rerender attempted a side effect")

    monkeypatch.setattr(RunStore, "begin", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    rendered = rerender_stored_run(store, finalized.name)

    assert rendered.is_file()
    assert rendered.name == "index.html"


@pytest.mark.parametrize("operation", ["rerender", "reverify"])
def test_read_projections_fail_closed_when_an_indexed_byte_changes(
    tmp_path: Path, operation: str
) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)
    artifact = finalized / "artifacts" / "observation.json"
    artifact.chmod(0o600)
    artifact.write_text('{"healthy":false}\n', encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="artifacts/observation.json"):
        if operation == "rerender":
            rerender_stored_run(store, finalized.name)
        else:
            reverify_stored_run(
                store,
                finalized.name,
                verifier_name=(
                    f"{stored_artifact_verifier.__module__}.{stored_artifact_verifier.__qualname__}"
                ),
                verifier_digest=_source_digest(),
                verifier=stored_artifact_verifier,
            )


def test_reverify_uses_stored_evidence_only(tmp_path: Path, monkeypatch) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)
    before = _snapshot(store.root.parent)

    def forbidden(*args, **kwargs):
        raise AssertionError("reverify attempted a side effect")

    monkeypatch.setattr(RunStore, "begin", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    result = reverify_stored_run(
        store,
        finalized.name,
        verifier_name=(
            f"{stored_artifact_verifier.__module__}.{stored_artifact_verifier.__qualname__}"
        ),
        verifier_digest=_source_digest(),
        verifier=stored_artifact_verifier,
    )

    assert result == {
        "schema_version": "opentraces.bench.reverification.v0",
        "run_id": finalized.name,
        "verifier": {
            "name": f"{stored_artifact_verifier.__module__}.{stored_artifact_verifier.__qualname__}",
            "digest": _source_digest(),
        },
        "status": "pass",
        "evidence_refs": ["artifacts/observation.json"],
        "reason": None,
    }
    assert _snapshot(store.root.parent) == before


def test_reverify_rejects_a_name_or_digest_not_bound_to_the_run(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)

    with pytest.raises(StoredVerifierMismatch, match="not bound"):
        reverify_stored_run(
            store,
            finalized.name,
            verifier_name="other.verifier",
            verifier_digest=_source_digest(),
            verifier=stored_artifact_verifier,
        )

    with pytest.raises(StoredVerifierMismatch, match="source digest"):
        reverify_stored_run(
            store,
            finalized.name,
            verifier_name=(
                f"{stored_artifact_verifier.__module__}.{stored_artifact_verifier.__qualname__}"
            ),
            verifier_digest="sha256:" + "0" * 64,
            verifier=stored_artifact_verifier,
        )


def test_reverify_rejects_a_forged_wrapper_before_invocation(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs" / "v1")
    finalized = _finalized(store)
    invoked = False

    def forged_wrapper(evidence):
        nonlocal invoked
        invoked = True
        return {"evidence_refs": []}

    forged_wrapper.__module__ = stored_artifact_verifier.__module__
    forged_wrapper.__qualname__ = stored_artifact_verifier.__qualname__
    forged_wrapper.__wrapped__ = stored_artifact_verifier

    with pytest.raises(StoredVerifierMismatch, match="wrapped callable"):
        reverify_stored_run(
            store,
            finalized.name,
            verifier_name=(
                f"{stored_artifact_verifier.__module__}.{stored_artifact_verifier.__qualname__}"
            ),
            verifier_digest=_source_digest(),
            verifier=forged_wrapper,
        )

    assert invoked is False
