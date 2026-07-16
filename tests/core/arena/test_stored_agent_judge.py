from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path

import pytest

from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import RunStore
from opentraces.core.arena.stored_judge import StoredJudgeError, judge_agent_commit_run


TRACE_ID = "trace-scenario-4"
COMMIT = "a" * 40
CLAIM = "The agent makes a small change and commits it."


def _finalized_run(tmp_path: Path, *, trail_evidence: bool) -> Path:
    store = RunStore(tmp_path / "runs" / "v1")
    draft = store.begin()

    def action(ordinal: int, argv: list[str], stdout: str) -> str:
        root = Path("actions") / f"{ordinal:04d}"
        draft.write_json(root / "invocation.json", {"ordinal": ordinal, "argv": argv})
        draft.write_text(root / "stdout", stdout)
        draft.write_text(root / "stderr", "")
        draft.write_json(root / "timing.json", {})
        result_ref = (root / "result.json").as_posix()
        draft.write_json(
            result_ref,
            {
                "execution_status": "complete",
                "returncode": 0,
                "duration_ms": 1,
                "stdout_ref": (root / "stdout").as_posix(),
                "stderr_ref": (root / "stderr").as_posix(),
                "timing_ref": (root / "timing.json").as_posix(),
                "reason": None,
            },
        )
        return result_ref

    refs = [
        action(1, ["git", "rev-parse", "HEAD"], f"{COMMIT}\n"),
        action(
            2,
            ["git", "show", f"{COMMIT}:.arena/scenario-4.txt"],
            "scenario-4-agent-change\n",
        ),
        action(
            3,
            ["opentraces", "trail", "blame", "commit", COMMIT, "--json"],
            json.dumps(
                {
                    "target": COMMIT,
                    "trailEvidence": (
                        [{"trace_id": TRACE_ID, "step_index": 1}]
                        if trail_evidence
                        else []
                    ),
                }
            ),
        ),
        action(
            4,
            ["opentraces", "trace", "get", TRACE_ID, "--json"],
            json.dumps({"trace": {"trace_id": TRACE_ID}}),
        ),
        action(
            5,
            ["opentraces", "ctx", TRACE_ID, "--json"],
            json.dumps({"trace_id": TRACE_ID, "nodes": [{"id": "node-1"}]}),
        ),
        action(
            6,
            ["opentraces", "bucket", "verify", "--json"],
            json.dumps({"status": "ok", "ok": True}),
        ),
    ]
    custody_ref = "artifacts/live-key-absence.json"
    draft.write_json(
        custody_ref,
        {
            "schema_version": "opentraces.bench.secret-absence.v0",
            "secret_names": ["ANTHROPIC_API_KEY"],
            "files_checked": 20,
            "bytes_checked": 1000,
            "capture_files_checked": 5,
            "candidate_result_checked": True,
            "matches": [],
            "absent": True,
        },
    )
    capture = {
        "schema_version": "opentraces.capture.result.v1",
        "completeness": "complete",
        "trace_refs": [TRACE_ID],
        "sources": [
            {
                "name": name,
                "status": "finalized",
                "completeness": "full",
                "evidence_refs": [f"finalizers/{name}.report.json"],
            }
            for name in ("session_jsonl", "telemetry", "git", "bucket")
        ],
    }
    requirements = [
        {"name": "scenario_4_world_state", "complete": True, "evidence_refs": refs},
        *[
            {
                "name": f"capture.{name}",
                "complete": True,
                "evidence_refs": [],
            }
            for name in ("trace", "context", "trail", "storage", "lifecycle")
        ],
        {
            "name": "agent.live_key_absence",
            "complete": True,
            "evidence_refs": [custody_ref],
        },
    ]
    return draft.finalize(
        build_result(
            run_id=draft.run_id,
            claim=CLAIM,
            nodeid="tests/arena/scenarios/test_agent_commits_change.py::test_agent_commits_change",
            source_ref="source/scenario.py",
            execution_mode="agent_live",
            started_at="2026-07-16T06:00:00Z",
            duration_ms=1,
            execution_status="complete",
            verdict="pass",
            reason=None,
            verifiers=[
                {
                    "name": "scenario_4_world_state",
                    "status": "pass",
                    "reason": None,
                    "evidence_refs": refs,
                    "source_ref": {"path": "source/scenario.py", "digest": "sha256:test"},
                }
            ],
            evidence={"complete": True, "requirements": requirements},
            recordings={"rewatchable": True, "channels": []},
            artifacts=[
                {"path": custody_ref, "media_type": "application/json", "kind": "secret_absence"}
            ],
            capture=capture,
            pins={
                "product": {"commit": COMMIT, "worktree": "clean"},
                "harness": {"name": "claude", "version": "2.1.210"},
                "model_wire": {"mode": "live"},
            },
        )
    )


def test_stored_judge_reobserves_scenario_4_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_path = _finalized_run(tmp_path, trail_evidence=True)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("stored-only judge attempted a live seam")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(os, "system", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)

    verdict = judge_agent_commit_run(run_path)

    assert verdict["verdict"] == "works"
    assert verdict["commit"] == COMMIT
    assert verdict["trace_id"] == TRACE_ID
    assert verdict["evidence_refs"]
    for reference in verdict["evidence_refs"]:
        stored_ref = Path(reference)
        assert not stored_ref.is_absolute()
        assert "runs/v1" not in stored_ref.as_posix()
        assert str(run_path) not in reference
        resolved = (run_path / stored_ref).resolve()
        assert resolved.is_relative_to(run_path)
        assert resolved.is_file()


def test_stored_judge_rejects_green_result_without_public_trail_evidence(
    tmp_path: Path,
) -> None:
    run_path = _finalized_run(tmp_path, trail_evidence=False)

    with pytest.raises(StoredJudgeError, match="public blame has no Trail evidence"):
        judge_agent_commit_run(run_path)
