from __future__ import annotations

import json
from pathlib import Path

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
        draft.finalize(result)
        return 0

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
        draft.finalize(result)
        return 0

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    result = CliRunner().invoke(
        main,
        ["bench", "run", f"{scenario}::test_install", "--store-root", str(store_root)],
    )

    assert result.exit_code == 1
    assert "verdict: fail" in result.output
