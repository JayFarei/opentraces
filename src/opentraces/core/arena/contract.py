"""Frozen machine contract for bench.v0 results.

``opentraces.bench.result.v0`` is a downstream-consumer contract.  Its exact
top-level shape is intentionally assembled in one place; additions require a
schema-version decision rather than an incidental producer change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping


BENCH_RESULT_SCHEMA_VERSION = "opentraces.bench.result.v0"
EXECUTION_MODES = frozenset({"direct", "agent_replay", "agent_live"})
EXECUTION_STATUSES = frozenset({"complete", "error"})
VERDICTS = frozenset({"pass", "fail", "skip"})


class ResultContractError(ValueError):
    """A result cannot be represented honestly by the v0 status algebra."""


def _require_reason(reason: object, *, for_status: str) -> None:
    if not isinstance(reason, Mapping) or not reason:
        raise ResultContractError(f"{for_status} requires a named reason object")
    if not any(str(reason.get(key, "")).strip() for key in ("code", "name", "message")):
        raise ResultContractError(f"{for_status} reason must include code, name, or message")


def _validate_rfc3339(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ResultContractError("started_at must be a non-empty RFC3339 string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ResultContractError("started_at must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ResultContractError("started_at must include a timezone")


def _validate_status(
    *, execution_status: str, verdict: str | None, reason: Mapping[str, Any] | None
) -> None:
    if execution_status not in EXECUTION_STATUSES:
        raise ResultContractError(f"unknown execution_status: {execution_status!r}")
    if verdict is not None and verdict not in VERDICTS:
        raise ResultContractError(f"unknown verdict: {verdict!r}")
    if execution_status == "error":
        if verdict is not None:
            raise ResultContractError("machinery error requires verdict=null")
        _require_reason(reason, for_status="machinery error")
        return
    if verdict is None:
        raise ResultContractError("complete execution requires pass, fail, or skip verdict")
    if verdict in {"fail", "skip"}:
        _require_reason(reason, for_status=verdict)


def build_result(
    *,
    run_id: str,
    claim: str,
    nodeid: str,
    source_ref: str,
    execution_mode: str,
    started_at: str,
    duration_ms: int,
    execution_status: str,
    verdict: str | None,
    reason: Mapping[str, Any] | None,
    verifiers: list[dict[str, Any]],
    evidence: dict[str, Any],
    recordings: dict[str, Any],
    artifacts: list[dict[str, Any]],
    capture: dict[str, Any] | None,
    pins: dict[str, Any],
) -> dict[str, Any]:
    """Build and validate the exact ``opentraces.bench.result.v0`` envelope."""

    if not all(isinstance(value, str) and value for value in (run_id, claim, nodeid, source_ref)):
        raise ResultContractError("run_id, claim, nodeid, and source_ref must be non-empty strings")
    if execution_mode not in EXECUTION_MODES:
        raise ResultContractError(f"unknown execution_mode: {execution_mode!r}")
    _validate_rfc3339(started_at)
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0:
        raise ResultContractError("duration_ms must be a non-negative integer")
    _validate_status(execution_status=execution_status, verdict=verdict, reason=reason)
    if not isinstance(verifiers, list):
        raise ResultContractError("verifiers must be an array")
    if not isinstance(evidence, dict) or set(evidence) < {"complete", "requirements"}:
        raise ResultContractError("evidence requires complete and requirements")
    if not isinstance(evidence["complete"], bool) or not isinstance(evidence["requirements"], list):
        raise ResultContractError("evidence has invalid complete or requirements values")
    if not isinstance(recordings, dict) or set(recordings) < {"rewatchable", "channels"}:
        raise ResultContractError("recordings requires rewatchable and channels")
    if not isinstance(recordings["rewatchable"], bool) or not isinstance(
        recordings["channels"], list
    ):
        raise ResultContractError("recordings has invalid rewatchable or channels values")
    if not isinstance(artifacts, list) or not isinstance(pins, dict):
        raise ResultContractError("artifacts must be an array and pins an object")
    if capture is not None and not isinstance(capture, dict):
        raise ResultContractError("capture must be an object or null")

    return {
        "schema_version": BENCH_RESULT_SCHEMA_VERSION,
        "run_id": run_id,
        "scenario": {"claim": claim, "nodeid": nodeid, "source_ref": source_ref},
        "execution_mode": execution_mode,
        "started_at": started_at,
        "duration_ms": duration_ms,
        "execution_status": execution_status,
        "verdict": verdict,
        "reason": dict(reason) if reason is not None else None,
        "verifiers": verifiers,
        "evidence": evidence,
        "recordings": recordings,
        "artifacts": artifacts,
        "capture": capture,
        "pins": pins,
    }


def validate_result(result: Mapping[str, Any]) -> None:
    """Validate a deserialized result through the same frozen constructor."""

    scenario = result.get("scenario")
    if not isinstance(scenario, Mapping):
        raise ResultContractError("scenario must be an object")
    expected_fields = {
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
    if set(result) != expected_fields:
        raise ResultContractError("result field set does not match opentraces.bench.result.v0")
    if result.get("schema_version") != BENCH_RESULT_SCHEMA_VERSION:
        raise ResultContractError("unsupported bench result schema_version")
    build_result(
        run_id=result["run_id"],
        claim=scenario.get("claim"),
        nodeid=scenario.get("nodeid"),
        source_ref=scenario.get("source_ref"),
        execution_mode=result["execution_mode"],
        started_at=result["started_at"],
        duration_ms=result["duration_ms"],
        execution_status=result["execution_status"],
        verdict=result["verdict"],
        reason=result["reason"],
        verifiers=result["verifiers"],
        evidence=result["evidence"],
        recordings=result["recordings"],
        artifacts=result["artifacts"],
        capture=result["capture"],
        pins=result["pins"],
    )


def result_exit_code(result: Mapping[str, Any]) -> int:
    """Return the runner exit code for a validated result (usage errors live in Click)."""

    validate_result(result)
    if result["execution_status"] == "error" or result["verdict"] == "fail":
        return 1
    return 0
