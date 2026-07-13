from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from opentraces.core.arena.box import (
    PINNED_CRABBOX_VERSION,
    PINNED_LOCAL_IMAGE,
    Box,
    CrabboxRefusal,
    CrabboxRuntime,
)


class ScriptedRunner:
    def __init__(self, responses: list[subprocess.CompletedProcess[str]]) -> None:
        self.responses = responses
        self.calls: list[tuple[list[str], Path | None, dict[str, str], float]] = []

    def __call__(self, argv, *, cwd=None, env, timeout):
        self.calls.append((list(argv), cwd, env, timeout))
        return self.responses.pop(0)


def _completed(argv: list[str], rc: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(argv, rc, stdout, stderr)


def _inspect() -> str:
    return json.dumps(
        {
            "id": "cbx_abc123",
            "slug": "steady-crab",
            "provider": "local-container",
            "state": "leased",
            "ready": True,
            "sshHost": "127.0.0.1",
            "sshUser": "crabbox",
            "sshPort": "32222",
            "sshKey": "/tmp/key",
            "labels": {"image": PINNED_LOCAL_IMAGE},
        }
    )


def test_lease_pins_version_image_tmpdir_and_runs_both_preflights(tmp_path: Path) -> None:
    runner = ScriptedRunner(
        [
            _completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n"),
            _completed(["crabbox", "warmup"], stdout="ready lease=cbx_abc123\n"),
            _completed(["crabbox", "inspect"], stdout=_inspect()),
            _completed(["ssh"], stdout=""),
        ]
    )
    runtime = CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=tmp_path / "missing")

    box = runtime.lease()

    warmup, inspect, ssh = (runner.calls[1][0], runner.calls[2][0], runner.calls[3][0])
    assert warmup == [
        "crabbox",
        "warmup",
        "--provider",
        "local-container",
        "--local-container-image",
        PINNED_LOCAL_IMAGE,
    ]
    assert runner.calls[1][2]["TMPDIR"] == str(tmp_path / "crabbox-tmp")
    assert inspect == ["crabbox", "inspect", "--id", "cbx_abc123", "--json"]
    assert ssh[:3] == ["ssh", "-F", "/dev/null"]
    assert box.sandbox_tier == "container"


def test_version_drift_is_a_loud_named_refusal(tmp_path: Path) -> None:
    runner = ScriptedRunner([_completed(["crabbox", "--version"], stdout="crabbox 0.39.0\n")])

    with pytest.raises(CrabboxRefusal, match="crabbox_version_mismatch") as caught:
        CrabboxRuntime(runner=runner, home=tmp_path).lease()

    assert "re-audit" in str(caught.value)
    assert len(runner.calls) == 1


def test_bad_ssh_config_is_refused_before_warmup(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.write_text("Host *\n  UseKeychain yes\n", encoding="utf-8")
    runner = ScriptedRunner(
        [_completed(["crabbox", "--version"], stdout=f"crabbox {PINNED_CRABBOX_VERSION}\n")]
    )

    with pytest.raises(CrabboxRefusal, match="ssh_config_incompatible"):
        CrabboxRuntime(runner=runner, home=tmp_path, ssh_config=config).lease()

    assert len(runner.calls) == 1


def test_exec_uses_pinned_lease_flags_and_propagates_remote_result(tmp_path: Path) -> None:
    timing = tmp_path / "timing.json"

    def runner(argv, *, cwd=None, env, timeout):
        timing.write_text(json.dumps({"schemaVersion": 1, "timing": {"exitCode": 7}}))
        return _completed(argv, rc=7, stdout="out", stderr="remote command exited 7")

    runtime = CrabboxRuntime(runner=runner, home=tmp_path)
    box = Box(
        id="cbx_abc123",
        slug="steady-crab",
        provider="local-container",
        sandbox_tier="container",
        ssh_host="127.0.0.1",
        ssh_user="crabbox",
        ssh_port="32222",
        ssh_key="/tmp/key",
    )

    result = runtime.exec(box, ["sh", "-c", "exit 7"], timing_path=timing)

    assert result.returncode == 7
    assert result.stdout == "out"
    assert result.timing["timing"]["exitCode"] == 7
    assert result.argv[:8] == [
        "crabbox",
        "run",
        "--id",
        "cbx_abc123",
        "--reclaim",
        "--no-sync",
        "--provider",
        "local-container",
    ]
