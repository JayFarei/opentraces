from __future__ import annotations

import json
import threading
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.bench_cli import PytestOutcome
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.run_store import RunStore


def test_public_fleet_command_runs_two_selected_nodes_with_private_recipes(
    tmp_path: Path, monkeypatch
) -> None:
    from opentraces.cli import bench_cli

    store_root = tmp_path / "bucket" / "runs" / "v1"
    wheel = tmp_path / "dist" / "opentraces.whl"
    wheel.parent.mkdir()
    wheel.write_bytes(b"clean wheel")
    barrier = threading.Barrier(2)
    observed_recipe_roots: list[Path] = []
    observed_lock = threading.Lock()
    selected = (
        "tests/arena/test_alpha.py::test_alpha",
        "tests/arena/test_beta.py::test_beta",
    )

    monkeypatch.setattr(bench_cli, "build_local_wheels", lambda _repository: [wheel])
    monkeypatch.setattr(
        bench_cli,
        "collect_selected_nodeids",
        lambda **_kwargs: selected,
    )

    def fake_pytest(target: str, *, repository: Path, env: dict[str, str]) -> PytestOutcome:
        recipe_root = Path(env["OT_BENCH_RECIPE_ROOT"])
        with observed_lock:
            observed_recipe_roots.append(recipe_root)
        private_wheel = recipe_root / wheel.name
        assert private_wheel.read_bytes() == b"clean wheel"
        if target == selected[0]:
            private_wheel.write_bytes(b"attempt-one mutation")
        barrier.wait(timeout=5)
        if target == selected[1]:
            assert private_wheel.read_bytes() == b"clean wheel"

        store = RunStore(Path(env["OT_BENCH_RUN_ROOT"]))
        draft = store.begin()
        draft.stage_result(
            build_result(
                run_id=draft.run_id,
                claim=f"{target} remains isolated.",
                nodeid=target,
                source_ref="source/scenario.py",
                execution_mode="direct",
                started_at="2026-07-15T12:00:00Z",
                duration_ms=1,
                execution_status="complete",
                verdict="pass",
                reason=None,
                verifiers=[],
                evidence={"complete": True, "requirements": []},
                recordings={"rewatchable": False, "channels": []},
                artifacts=[],
                capture=None,
                pins={
                    "environment": {
                        "provider": "local-container",
                        "image": "ubuntu:24.04",
                        "sandbox_tier": "container",
                    }
                },
            )
        )
        return PytestOutcome(
            returncode=0,
            stdout="",
            stderr="",
            phase_reports=[{"nodeid": target, "when": "call", "outcome": "passed"}],
            run_ids=(draft.run_id,),
        )

    monkeypatch.setattr(bench_cli, "run_pytest", fake_pytest)

    invoked = CliRunner().invoke(
        main,
        [
            "bench",
            "fleet",
            "tests/arena",
            "--concurrency",
            "2",
            "--placement",
            "local-container",
            "--store-root",
            str(store_root),
            "--json",
        ],
    )

    assert invoked.exit_code == 0, invoked.output
    summary = json.loads(invoked.output)
    assert [row["nodeid"] for row in summary["attempts"]] == list(selected)
    assert len(set(observed_recipe_roots)) == 2
    assert wheel.read_bytes() == b"clean wheel"
    store = RunStore(store_root)
    for row in summary["attempts"]:
        run_path = Path(row["run_path"])
        assert run_path.parent == store_root
        assert store.verify(run_path) is True
    assert [row["code"] for row in summary["coverage_holes"]] == [
        "remote_rented_glibc_lease_unproven",
        "x86_64_hf_emulator_unproven",
    ]
