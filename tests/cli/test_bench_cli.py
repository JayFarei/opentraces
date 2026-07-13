from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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


def test_run_pytest_captures_child_output(monkeypatch, tmp_path: Path) -> None:
    from opentraces.cli import bench_cli

    observed = {}

    def fake_run(command, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(returncode=7, stdout="captured out", stderr="captured err")

    monkeypatch.setattr(bench_cli.subprocess, "run", fake_run)

    outcome = bench_cli.run_pytest("test_demo.py::test_demo", repository=tmp_path, env={})

    assert observed["capture_output"] is True
    assert observed["text"] is True
    assert outcome.returncode == 7
    assert outcome.stdout == "captured out"
    assert outcome.stderr == "captured err"
