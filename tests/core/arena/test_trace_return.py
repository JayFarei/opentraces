from __future__ import annotations

import json
from pathlib import Path

import opentraces.core.arena.run_store as run_store_module
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import RunStore
from opentraces.core.arena.trace_return import return_run_as_trace
from opentraces.core.bucket_trace_records import read_bucket_record_for_trace
from opentraces.core.config import Config


RUN_ID = "run_20260714T120000000000Z_abcdef123456"
TRACE_ID = "d70e8530-430d-5260-9b59-3f61f57a2a13"


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "product"
    project.mkdir()
    (project / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": "1234567890abcdef1234567890abcdef",
            }
        ),
        encoding="utf-8",
    )
    return project


def _finalized_run(
    tmp_path: Path,
    monkeypatch,
) -> tuple[RunStore, Path]:
    monkeypatch.setattr(run_store_module, "_new_run_id", lambda: RUN_ID)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    draft.write_text("source/scenario.py", "def test_publish(): pass\n")
    draft.write_json(
        "source/source.json",
        {
            "nodeid": "tests/scenarios/test_publish.py::test_publish",
            "claim": "Publishing reaches the configured remote.",
            "scenario_path": "tests/scenarios/test_publish.py",
            "repository": "JayFarei/opentraces",
            "commit": "2ab03ac637e",
            "dirty_diff_digest": None,
            "copied_source_path": "source/scenario.py",
        },
    )
    actions = [
        (
            ["opentraces", "dataset", "publish", "demo"],
            ".",
            "published demo\n",
            "",
            0,
            17,
            "2026-07-14T12:00:01Z",
        ),
        (
            ["opentraces", "dataset", "status", "demo", "--json"],
            "/workspace",
            '{"published":true}\n',
            "warning retained\n",
            0,
            9,
            "2026-07-14T12:00:02Z",
        ),
    ]
    for ordinal, (argv, cwd, stdout, stderr, returncode, duration_ms, started_at) in enumerate(
        actions, start=1
    ):
        action = f"actions/{ordinal:04d}"
        draft.write_json(
            f"{action}/invocation.json",
            {
                "ordinal": ordinal,
                "argv": argv,
                "env_pins": {"HF_TOKEN": "sha256:token-pin"},
                "cwd": cwd,
                "started_at": started_at,
            },
        )
        draft.write_text(f"{action}/stdout", stdout)
        draft.write_text(f"{action}/stderr", stderr)
        draft.write_json(f"{action}/timing.json", {"schemaVersion": 1})
        draft.write_json(
            f"{action}/result.json",
            {
                "returncode": returncode,
                "duration_ms": duration_ms,
                "stdout_ref": f"{action}/stdout",
                "stderr_ref": f"{action}/stderr",
                "timing_ref": f"{action}/timing.json",
            },
        )

    result = build_result(
        run_id=draft.run_id,
        claim="Publishing reaches the configured remote.",
        nodeid="tests/scenarios/test_publish.py::test_publish",
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at="2026-07-14T12:00:00Z",
        duration_ms=3000,
        execution_status="complete",
        verdict="pass",
        reason=None,
        verifiers=[],
        evidence={"complete": True, "requirements": []},
        recordings={"rewatchable": False, "channels": []},
        artifacts=[],
        capture=None,
        pins={
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "clean",
                "dirty_diff_digest": None,
            }
        },
    )
    return store, draft.finalize(result)


def test_verified_run_returns_as_one_deterministic_manufactured_trace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    project = _project(tmp_path)

    first = return_run_as_trace(
        run_path,
        project_dir=project,
        store=store,
        cfg=Config(),
    )
    first_bytes = first.model_dump_json()
    second = return_run_as_trace(
        run_path,
        project_dir=project,
        store=store,
        cfg=Config(),
    )

    assert first.trace_id == TRACE_ID
    assert second.trace_id == TRACE_ID
    assert second.model_dump_json() == first_bytes
    assert first.session_id == RUN_ID
    assert first.execution_context == "runtime"
    assert first.task.description == "Publishing reaches the configured remote."
    assert first.outcome.success is None
    assert first.context_tree_summary == {}
    assert first.patches == []
    assert [step.step_index for step in first.steps] == [1, 2, 3, 4]
    assert [step.role for step in first.steps] == ["user", "agent", "agent", "agent"]
    assert first.steps[0].content == "Publishing reaches the configured remote."
    assert [step.tool_calls[0].input["argv"] for step in first.steps[1:3]] == [
        ["opentraces", "dataset", "publish", "demo"],
        ["opentraces", "dataset", "status", "demo", "--json"],
    ]
    assert first.steps[1].tool_calls[0].duration_ms == 17
    assert first.steps[1].observations[0].content == "published demo\n"
    assert first.steps[2].observations[0].content == (
        '{"published":true}\n\n[stderr]\nwarning retained\n'
    )
    assert first.steps[3].content == (
        f"Bench run {RUN_ID} completed; its verdict remains in the stored run."
    )

    stored = read_bucket_record_for_trace(TRACE_ID)
    assert stored is not None
    assert stored.source_layer == "manufactured"
    assert stored.record.model_dump_json() == first_bytes

