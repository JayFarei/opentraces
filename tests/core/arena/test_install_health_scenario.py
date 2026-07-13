from __future__ import annotations

import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "tests" / "arena" / "scenarios" / "test_install_health.py"


@pytest.mark.parametrize("returncode", [3, 127])
def test_install_health_rejects_nonzero_doctor_exit(returncode: int) -> None:
    verifier = runpy.run_path(str(SCENARIO))["installed_cli_reports_health"]
    observed = SimpleNamespace(
        returncode=returncode,
        stderr="opentraces: command not found" if returncode == 127 else "hard doctor state",
        json={
            "status": "ok",
            "schema_version": "opentraces.doctor.v1",
            "doctor": {
                "cli": {"installed_version": "0.5.0"},
                "security_version": "0.8.0",
                "security": {"tools": [{"name": "regex", "state": "enabled"}]},
            },
        },
        result_ref="actions/0001/result.json",
    )
    run = SimpleNamespace(
        terminal=SimpleNamespace(exec=lambda *args, **kwargs: observed)
    )

    with pytest.raises(AssertionError, match="doctor"):
        verifier(run)
