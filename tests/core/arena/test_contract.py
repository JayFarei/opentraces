from __future__ import annotations

import pytest

from opentraces.core.arena.contract import (
    BENCH_RESULT_SCHEMA_VERSION,
    ResultContractError,
    build_result,
    result_exit_code,
)


EXPECTED_FIELDS = {
    "schema_version",
    "run_id",
    "scenario",
    "execution_mode",
    "started_at",
    "duration_ms",
    "execution_status",
    "verdict",
    "reason",
    "verifiers",
    "evidence",
    "recordings",
    "artifacts",
    "capture",
    "pins",
}


def _result(**overrides):
    values = {
        "run_id": "run-01",
        "claim": "Install is healthy on a fresh box",
        "nodeid": "tests/arena/test_install.py::test_install_is_healthy",
        "source_ref": "source/scenario.py",
        "execution_mode": "direct",
        "started_at": "2026-07-13T12:00:00Z",
        "duration_ms": 7,
        "execution_status": "complete",
        "verdict": "pass",
        "reason": None,
        "verifiers": [],
        "evidence": {"complete": True, "requirements": []},
        "recordings": {"rewatchable": False, "channels": []},
        "artifacts": [],
        "capture": None,
        "pins": {},
    }
    values.update(overrides)
    return build_result(**values)


def test_result_envelope_has_the_frozen_v0_shape() -> None:
    result = _result()

    assert set(result) == EXPECTED_FIELDS
    assert result["schema_version"] == BENCH_RESULT_SCHEMA_VERSION
    assert result["scenario"] == {
        "claim": "Install is healthy on a fresh box",
        "nodeid": "tests/arena/test_install.py::test_install_is_healthy",
        "source_ref": "source/scenario.py",
    }
    assert result["capture"] is None


@pytest.mark.parametrize(
    ("execution_status", "verdict", "reason", "expected_exit"),
    [
        ("complete", "pass", None, 0),
        ("complete", "fail", {"code": "assertion_failed", "message": "no"}, 1),
        (
            "complete",
            "skip",
            {"code": "missing_prerequisite", "message": "crabbox is absent"},
            0,
        ),
        ("error", None, {"code": "lease_failed", "message": "no box"}, 1),
    ],
)
def test_status_algebra_keeps_outcomes_distinguishable(
    execution_status: str,
    verdict: str | None,
    reason: dict[str, str] | None,
    expected_exit: int,
) -> None:
    result = _result(
        execution_status=execution_status,
        verdict=verdict,
        reason=reason,
    )

    assert result["execution_status"] == execution_status
    assert result["verdict"] == verdict
    assert result_exit_code(result) == expected_exit


@pytest.mark.parametrize(
    ("execution_status", "verdict", "reason"),
    [
        ("error", "fail", {"code": "machinery", "message": "boom"}),
        ("complete", None, {"code": "unknown", "message": "unknown"}),
        ("complete", "skip", None),
        ("error", None, None),
    ],
)
def test_invalid_status_combinations_are_refused(
    execution_status: str,
    verdict: str | None,
    reason: dict[str, str] | None,
) -> None:
    with pytest.raises(ResultContractError):
        _result(
            execution_status=execution_status,
            verdict=verdict,
            reason=reason,
        )


def test_recording_failure_does_not_rewrite_a_functional_pass() -> None:
    result = _result(
        recordings={
            "rewatchable": False,
            "channels": [
                {
                    "kind": "terminal",
                    "complete": False,
                    "path": None,
                    "reason": "cast process was killed",
                }
            ],
        }
    )

    assert result["verdict"] == "pass"
    assert result["execution_status"] == "complete"
    assert result["recordings"]["rewatchable"] is False
