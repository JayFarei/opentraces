from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import opentraces.core.arena.run_store as run_store_module
import pytest
from click.testing import CliRunner
from opentraces.cli import main
from opentraces.core import bucket_store, ingest
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import RunIntegrityError, RunStore
from opentraces.core.arena.trace_return import TraceReturnError, return_run_as_trace
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
    *,
    claim: str = "Publishing reaches the configured remote.",
    commit_shaped_action: bool = False,
    first_stdout: str = "published demo\n",
    omit_first_stdout: bool = False,
    pins: dict[str, object] | None = None,
) -> tuple[RunStore, Path]:
    monkeypatch.setattr(run_store_module, "_new_run_id", lambda: RUN_ID)
    store = RunStore(tmp_path / "bucket" / "runs" / "v1")
    draft = store.begin()
    draft.write_text("source/scenario.py", "def test_publish(): pass\n")
    draft.write_json(
        "source/source.json",
        {
            "nodeid": "tests/scenarios/test_publish.py::test_publish",
            "claim": claim,
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
            first_stdout,
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
    if commit_shaped_action:
        actions[0] = (
            ["git", "commit", "-m", "done"],
            ".",
            "[main abc1234] done\n",
            "",
            0,
            17,
            "2026-07-14T12:00:01Z",
        )
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
        if not (omit_first_stdout and ordinal == 1):
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
        claim=claim,
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
        pins=(
            pins
            if pins is not None
            else {
                "product": {
                    "commit": "2ab03ac637e",
                    "worktree": "clean",
                    "dirty_diff_digest": None,
                }
            }
        ),
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


def test_commit_shaped_action_does_not_grade_the_manufactured_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(
        tmp_path,
        monkeypatch,
        commit_shaped_action=True,
    )

    returned = return_run_as_trace(
        run_path,
        project_dir=_project(tmp_path),
        store=store,
        cfg=Config(),
    )

    assert returned.outcome.success is None
    assert returned.outcome.committed is False
    assert returned.outcome.commit_sha is None


@pytest.mark.parametrize(
    "pins",
    [
        {},
        {"product": {"commit": "2ab03ac637e", "worktree": "clean"}},
        {
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "unknown",
                "dirty_diff_digest": None,
            }
        },
        {
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "dirty",
                "dirty_diff_digest": None,
            }
        },
        {
            "product": {
                "commit": "2ab03ac637e",
                "worktree": "clean",
                "dirty_diff_digest": "sha256:" + "a" * 64,
            }
        },
        {
            "product": {
                "commit": "",
                "worktree": "dirty",
                "dirty_diff_digest": "sha256:not-a-digest",
            }
        },
    ],
)
def test_missing_partial_or_malformed_product_pin_is_refused(
    tmp_path: Path,
    monkeypatch,
    pins: dict[str, object],
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch, pins=pins)

    with pytest.raises(TraceReturnError, match="product pin"):
        return_run_as_trace(
            run_path,
            project_dir=_project(tmp_path),
            store=store,
            cfg=Config(),
        )

    assert read_bucket_record_for_trace(TRACE_ID) is None


def test_security_pipeline_preserves_canonical_claim_but_sanitizes_other_content(
    tmp_path: Path,
    monkeypatch,
) -> None:
    claim_token = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
    output_token = "ghp_zyxwvutsrqponmlkjihgfedcba0987654321"
    claim = f"The scanner recognizes {claim_token}."
    store, run_path = _finalized_run(
        tmp_path,
        monkeypatch,
        claim=claim,
        first_stdout=f"observed {output_token}\n",
    )
    cfg = Config()
    cfg.security.regex.enabled = True

    returned = return_run_as_trace(
        run_path,
        project_dir=_project(tmp_path),
        store=store,
        cfg=cfg,
    )

    assert returned.task.description == claim
    assert returned.steps[0].content == claim
    assert output_token not in (returned.steps[1].observations[0].content or "")
    assert "[REDACTED]" in (returned.steps[1].observations[0].content or "")
    assert "regex" in returned.metadata["security"]["tools_applied"]


def test_tampered_run_is_refused_before_any_trace_is_written(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    stdout = run_path / "actions" / "0001" / "stdout"
    stdout.chmod(0o600)
    stdout.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(RunIntegrityError, match="actions/0001/stdout"):
        return_run_as_trace(
            run_path,
            project_dir=_project(tmp_path),
            store=store,
            cfg=Config(),
        )

    assert read_bucket_record_for_trace(TRACE_ID) is None


def test_missing_declared_action_output_is_refused_before_trace_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(
        tmp_path,
        monkeypatch,
        omit_first_stdout=True,
    )
    assert store.verify(run_path) is True

    with pytest.raises(TraceReturnError, match="missing action output.*stdout"):
        return_run_as_trace(
            run_path,
            project_dir=_project(tmp_path),
            store=store,
            cfg=Config(),
        )

    assert read_bucket_record_for_trace(TRACE_ID) is None


def test_returned_run_resolves_through_standard_trace_read_verbs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, run_path = _finalized_run(tmp_path, monkeypatch)
    return_run_as_trace(
        run_path,
        project_dir=_project(tmp_path),
        store=store,
        cfg=Config(),
    )

    runner = CliRunner()
    got = runner.invoke(main, ["trace", "get", TRACE_ID, "--json"])
    mapped = runner.invoke(main, ["trace", "map", TRACE_ID, "--json"])
    sliced = runner.invoke(
        main,
        [
            "trace",
            "slice",
            TRACE_ID,
            "--from-step",
            "1",
            "--to-step",
            "3",
            "--json",
        ],
    )
    queried = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--lex",
            "configured remote",
            "--unknown-success",
            "--json",
        ],
    )

    assert got.exit_code == 0, got.output
    assert mapped.exit_code == 0, mapped.output
    assert sliced.exit_code == 0, sliced.output
    assert queried.exit_code == 0, queried.output
    assert json.loads(got.output)["trace"]["trace_id"] == TRACE_ID
    mapped_payload = json.loads(mapped.output)
    assert mapped_payload["trace_id"] == TRACE_ID
    assert json.loads(sliced.output)["slices"][0]["trace_id"] == TRACE_ID
    assert TRACE_ID in {
        candidate["trace_id"] for candidate in json.loads(queried.output)["candidates"]
    }


def test_existing_bucket_writer_callers_keep_the_canonical_source_layer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        ingest,
        "get_project_dir",
        lambda _project: SimpleNamespace(name="existing-project"),
    )
    monkeypatch.setattr(
        bucket_store,
        "write_trace_record",
        lambda _record, **kwargs: seen.append(kwargs["source_layer"]),
    )
    monkeypatch.setattr(bucket_store, "write_raw_source_artifact", lambda *_a, **_k: None)

    ingest.write_trace_to_bucket(
        SimpleNamespace(trace_id="existing-canonical-trace"),
        tmp_path,
        parser_name="claude-code",
        source_jsonl=tmp_path / "session.jsonl",
        trace_record_only=True,
    )

    assert seen == ["canonical"]
