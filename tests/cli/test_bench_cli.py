from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import RunStore


def _scenario(tmp_path: Path) -> Path:
    path = tmp_path / "test_install.py"
    path.write_text(
        'def test_install(bench):\n    """Install is healthy on a fresh box.\n\nDetails."""\n',
        encoding="utf-8",
    )
    return path


def test_bench_run_prints_claim_and_returns_result_exit_code(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.cli import bench_cli

    scenario = _scenario(tmp_path)
    store_root = tmp_path / "runs" / "v1"
    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda repository: [])

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]) -> int:
        runtime_home = Path(env["OT_BENCH_REAL_HOME"])
        assert env["PLAYWRIGHT_BROWSERS_PATH"] == str(
            bench_cli._playwright_browser_cache(runtime_home)
        )
        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        result = build_result(
            run_id=draft.run_id,
            claim="Install is healthy on a fresh box.",
            nodeid=target,
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at="2026-07-13T12:00:00Z",
            duration_ms=1,
            execution_status="complete",
            verdict="pass",
            reason=None,
            verifiers=[],
            evidence={"complete": True, "requirements": []},
            recordings={"rewatchable": False, "channels": []},
            artifacts=[],
            capture=None,
            pins={},
        )
        draft.stage_result(result)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    result = CliRunner().invoke(
        main,
        ["bench", "run", f"{scenario}::test_install", "--store-root", str(store_root)],
    )

    assert result.exit_code == 0, result.output
    assert "Install is healthy on a fresh box." in result.output
    assert "verdict: pass" in result.output
    finalized = next(path for path in store_root.iterdir() if path.name.startswith("run_"))
    assert json.loads((finalized / "result.json").read_text())["verdict"] == "pass"


def test_bench_run_returns_one_for_a_functional_failure(tmp_path: Path, monkeypatch) -> None:
    from opentraces.cli import bench_cli

    scenario = _scenario(tmp_path)
    store_root = tmp_path / "runs" / "v1"
    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda repository: [])

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]) -> int:
        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        result = build_result(
            run_id=draft.run_id,
            claim="Install is healthy on a fresh box.",
            nodeid=target,
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at="2026-07-13T12:00:00Z",
            duration_ms=1,
            execution_status="complete",
            verdict="fail",
            reason={"code": "assertion_failed", "message": "not healthy"},
            verifiers=[],
            evidence={"complete": True, "requirements": []},
            recordings={"rewatchable": False, "channels": []},
            artifacts=[],
            capture=None,
            pins={},
        )
        draft.stage_result(result)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    result = CliRunner().invoke(
        main,
        ["bench", "run", f"{scenario}::test_install", "--store-root", str(store_root)],
    )

    assert result.exit_code == 1
    assert "verdict: fail" in result.output


def test_bench_run_json_is_pure_and_persists_pytest_diagnostics(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.cli import bench_cli

    scenario = _scenario(tmp_path)
    store_root = tmp_path / "runs" / "v1"
    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda repository: [])

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]):
        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        result = build_result(
            run_id=draft.run_id,
            claim="Install is healthy on a fresh box.",
            nodeid=target,
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at="2026-07-13T12:00:00Z",
            duration_ms=1,
            execution_status="complete",
            verdict="pass",
            reason=None,
            verifiers=[],
            evidence={"complete": True, "requirements": []},
            recordings={"rewatchable": False, "channels": []},
            artifacts=[],
            capture=None,
            pins={},
        )
        draft.stage_result(result)
        return SimpleNamespace(returncode=0, stdout="pytest chatter\n", stderr="warning\n")

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "run",
            f"{scenario}::test_install",
            "--store-root",
            str(store_root),
            "--json",
        ],
    )

    assert invoked.exit_code == 0, invoked.output
    summary = json.loads(invoked.output)
    finalized = store_root / summary["run_id"]
    stored = json.loads((finalized / "result.json").read_text())
    diagnostic = next(item for item in stored["artifacts"] if item["kind"] == "pytest_diagnostics")
    assert diagnostic["stdout_ref"] == "artifacts/pytest/stdout.txt"
    assert diagnostic["stderr_ref"] == "artifacts/pytest/stderr.txt"
    assert (finalized / diagnostic["stdout_ref"]).read_text() == "pytest chatter\n"
    assert (finalized / diagnostic["stderr_ref"]).read_text() == "warning\n"


def test_nonzero_pytest_after_green_result_forces_error_null(tmp_path: Path, monkeypatch) -> None:
    from opentraces.cli import bench_cli

    scenario = _scenario(tmp_path)
    store_root = tmp_path / "runs" / "v1"
    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda repository: [])

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]):
        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        result = build_result(
            run_id=draft.run_id,
            claim="Install is healthy on a fresh box.",
            nodeid=target,
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at="2026-07-13T12:00:00Z",
            duration_ms=1,
            execution_status="complete",
            verdict="pass",
            reason=None,
            verifiers=[],
            evidence={"complete": True, "requirements": []},
            recordings={"rewatchable": False, "channels": []},
            artifacts=[],
            capture=None,
            pins={},
        )
        draft.stage_result(result)
        return SimpleNamespace(returncode=3, stdout="late output\n", stderr="late failure\n")

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "run",
            f"{scenario}::test_install",
            "--store-root",
            str(store_root),
            "--json",
        ],
    )

    assert invoked.exit_code == 1, invoked.output
    summary = json.loads(invoked.output)
    stored = json.loads((store_root / summary["run_id"] / "result.json").read_text())
    assert stored["execution_status"] == "error"
    assert stored["verdict"] is None
    assert stored["reason"]["code"] == "pytest_failed"
    assert stored["evidence"]["complete"] is False


def test_nonzero_pytest_preserves_an_existing_named_setup_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.cli import bench_cli

    scenario = _scenario(tmp_path)
    store_root = tmp_path / "runs" / "v1"
    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda repository: [])

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]):
        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        result = build_result(
            run_id=draft.run_id,
            claim="Install is healthy on a fresh box.",
            nodeid=target,
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at="2026-07-13T12:00:00Z",
            duration_ms=1,
            execution_status="error",
            verdict=None,
            reason={
                "code": "crabbox_version_mismatch",
                "message": "requires crabbox 0.38.0",
            },
            verifiers=[],
            evidence={
                "complete": False,
                "requirements": [
                    {
                        "name": "bench.adjudication",
                        "complete": False,
                        "evidence_refs": [],
                    }
                ],
            },
            recordings={"rewatchable": False, "channels": []},
            artifacts=[],
            capture=None,
            pins={},
        )
        draft.stage_result(result)
        return SimpleNamespace(returncode=1, stdout="setup error\n", stderr="")

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "run",
            f"{scenario}::test_install",
            "--store-root",
            str(store_root),
            "--json",
        ],
    )

    assert invoked.exit_code == 1, invoked.output
    summary = json.loads(invoked.output)
    stored = json.loads((store_root / summary["run_id"] / "result.json").read_text())
    assert stored["execution_status"] == "error"
    assert stored["verdict"] is None
    assert stored["reason"] == {
        "code": "crabbox_version_mismatch",
        "message": "requires crabbox 0.38.0",
    }


@pytest.mark.parametrize(
    ("verdict", "reason", "expected_exit"),
    [
        ("pass", None, 0),
        ("fail", {"code": "assertion_failed", "message": "product red"}, 1),
        ("skip", {"code": "absent_prerequisite", "message": "dependency absent"}, 0),
    ],
)
def test_teardown_only_failure_preserves_primary_outcome_and_names_cleanup_evidence(
    tmp_path: Path,
    monkeypatch,
    verdict: str,
    reason: dict[str, str] | None,
    expected_exit: int,
) -> None:
    from opentraces.cli import bench_cli

    scenario = _scenario(tmp_path)
    store_root = tmp_path / "runs" / "v1"
    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda repository: [])

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]):
        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        result = build_result(
            run_id=draft.run_id,
            claim="Install is healthy on a fresh box.",
            nodeid=target,
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
        draft.stage_result(result)
        return SimpleNamespace(
            returncode=1,
            stdout="teardown output\n",
            stderr="cleanup failed\n",
            phase_reports=[
                {"nodeid": target, "when": "call", "outcome": "passed"},
                {"nodeid": target, "when": "teardown", "outcome": "failed"},
            ],
        )

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "run",
            f"{scenario}::test_install",
            "--store-root",
            str(store_root),
            "--json",
        ],
    )

    assert invoked.exit_code == expected_exit, invoked.output
    summary = json.loads(invoked.output)
    stored = json.loads((store_root / summary["run_id"] / "result.json").read_text())
    assert stored["execution_status"] == "complete"
    assert stored["verdict"] == verdict
    assert stored["reason"] == reason
    assert stored["evidence"]["complete"] is False
    assert stored["evidence"]["requirements"][-1]["name"] == "pytest.cleanup"
    diagnostic = next(item for item in stored["artifacts"] if item["kind"] == "pytest_diagnostics")
    assert diagnostic["phase_report_ref"] == "artifacts/pytest/phases.json"


def test_call_phase_failure_after_adjudication_forces_error_null(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.cli import bench_cli

    scenario = _scenario(tmp_path)
    store_root = tmp_path / "runs" / "v1"
    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda repository: [])

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]):
        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        result = build_result(
            run_id=draft.run_id,
            claim="Install is healthy on a fresh box.",
            nodeid=target,
            source_ref="source/scenario.py",
            execution_mode="direct",
            started_at="2026-07-13T12:00:00Z",
            duration_ms=1,
            execution_status="complete",
            verdict="pass",
            reason=None,
            verifiers=[],
            evidence={"complete": True, "requirements": []},
            recordings={"rewatchable": False, "channels": []},
            artifacts=[],
            capture=None,
            pins={},
        )
        draft.stage_result(result)
        return SimpleNamespace(
            returncode=1,
            stdout="call output\n",
            stderr="call failed\n",
            phase_reports=[{"nodeid": target, "when": "call", "outcome": "failed"}],
        )

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "run",
            f"{scenario}::test_install",
            "--store-root",
            str(store_root),
            "--json",
        ],
    )

    assert invoked.exit_code == 1, invoked.output
    summary = json.loads(invoked.output)
    stored = json.loads((store_root / summary["run_id"] / "result.json").read_text())
    assert stored["execution_status"] == "error"
    assert stored["verdict"] is None
    assert stored["reason"]["code"] == "pytest_call_failed"


def test_run_pytest_captures_child_output(monkeypatch, tmp_path: Path) -> None:
    from opentraces.cli import bench_cli

    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        report_path = Path(kwargs["env"]["OT_BENCH_PYTEST_PHASE_REPORT"])
        report_path.write_text(
            '{"nodeid":"test_demo.py::test_demo","when":"call","outcome":"passed"}\n'
            '{"nodeid":"test_demo.py::test_demo","when":"teardown","outcome":"failed"}\n',
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=7, stdout="captured out", stderr="captured err")

    monkeypatch.setattr(bench_cli.subprocess, "run", fake_run)

    outcome = bench_cli.run_pytest("test_demo.py::test_demo", repository=tmp_path, env={})

    assert observed["capture_output"] is True
    assert observed["text"] is True
    assert outcome.returncode == 7
    assert outcome.stdout == "captured out"
    assert outcome.stderr == "captured err"
    assert outcome.phase_reports == [
        {"nodeid": "test_demo.py::test_demo", "when": "call", "outcome": "passed"},
        {"nodeid": "test_demo.py::test_demo", "when": "teardown", "outcome": "failed"},
    ]


def test_real_pytest_subprocess_reconciles_all_honesty_outcomes(tmp_path: Path) -> None:
    from opentraces.cli import bench_cli

    scenario = tmp_path / "test_runner_honesty_subprocess.py"
    scenario.write_text(
        '''from tests.core.arena.test_engine import FakeBoxRuntime


def verifier_passes(run):
    return {"evidence_refs": []}


def use_fake_runtime(bench):
    bench.box_runtime = FakeBoxRuntime()
    return bench


def test_runner_reports_pass(bench):
    """The real pytest seam preserves a passing adjudication."""
    with use_fake_runtime(bench).run(app_state="base-only") as run:
        run.verify(verifier_passes)


def test_runner_reports_fail(bench):
    """The real pytest seam preserves a functional failure."""
    with use_fake_runtime(bench).run(app_state="base-only"):
        assert False, "forced functional red"


def test_runner_reports_named_skip(bench):
    """The real pytest seam preserves a named prerequisite skip."""
    with use_fake_runtime(bench).run(app_state="base-only") as run:
        run.skip("absent_prerequisite", "the required dependency is absent")


def test_runner_reports_machinery_error(bench):
    """The real pytest seam preserves machinery that prevents adjudication."""
    with use_fake_runtime(bench).run(app_state="base-only"):
        raise RuntimeError("forced machinery red")
''',
        encoding="utf-8",
    )
    store = RunStore(tmp_path / "runs" / "v1")
    env = dict(os.environ)
    env.update(
        {
            "OT_BENCH_RUN_ROOT": str(store.root),
            "OT_BENCH_REPOSITORY": str(Path.cwd()),
            "OT_BENCH_DEFER_FINALIZE": "1",
        }
    )
    expected = {
        "test_runner_reports_pass": (0, "passed", "complete", "pass", None),
        "test_runner_reports_fail": (
            0,
            "passed",
            "complete",
            "fail",
            "assertion_failed",
        ),
        "test_runner_reports_named_skip": (
            0,
            "passed",
            "complete",
            "skip",
            "absent_prerequisite",
        ),
        "test_runner_reports_machinery_error": (
            1,
            "failed",
            "error",
            None,
            "machinery_error",
        ),
    }

    for test_name, (
        returncode,
        call_outcome,
        execution_status,
        verdict,
        reason_code,
    ) in expected.items():
        before = bench_cli._pending_ids(store) | bench_cli._finalized_ids(store)
        target = f"{scenario}::{test_name}"
        outcome = bench_cli.run_pytest(target, repository=Path.cwd(), env=env)

        assert outcome.returncode == returncode, outcome.stderr
        assert any(
            report["when"] == "call" and report["outcome"] == call_outcome
            for report in outcome.phase_reports
        )
        created = bench_cli._pending_ids(store) - before
        assert len(created) == 1
        run_id = created.pop()

        run_path, result = bench_cli._finalize_after_pytest(store, run_id, outcome)

        assert result["execution_status"] == execution_status
        assert result["verdict"] == verdict
        assert (result["reason"] or {}).get("code") == reason_code
        diagnostic = next(
            artifact
            for artifact in result["artifacts"]
            if artifact["kind"] == "pytest_diagnostics"
        )
        assert diagnostic["phase_report_ref"] == "artifacts/pytest/phases.json"
        assert store.verify(run_path) is True
        assert run_id not in bench_cli._pending_ids(store)
