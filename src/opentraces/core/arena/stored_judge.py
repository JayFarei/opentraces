"""Read-only re-observation of the scenario-4 stored run exhaust."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .run_store import RunStore


SCENARIO_4_CLAIM = "The agent makes a small change and commits it."
SCENARIO_4_PATH = ".arena/scenario-4.txt"
SCENARIO_4_CONTENT = "scenario-4-agent-change"


class StoredJudgeError(AssertionError):
    """Stored evidence does not independently establish the scenario claim."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StoredJudgeError(message)


def _stored_path(run_path: Path, reference: object) -> Path:
    _require(isinstance(reference, str) and bool(reference), "evidence ref is missing")
    target = (run_path / str(reference)).resolve()
    _require(target.is_relative_to(run_path), "evidence ref escapes the stored run")
    _require(target.is_file(), f"stored evidence is missing: {reference}")
    return target


def _json_bytes(payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(payload):
            if character not in "[{":
                continue
            try:
                value, _end = decoder.raw_decode(payload[index:])
                return value
            except json.JSONDecodeError:
                continue
    raise StoredJudgeError("stored command output is not JSON")


def _contains_value(value: object, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, Mapping):
        return any(_contains_value(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains_value(child, expected) for child in value)
    return False


def _action_observations(
    run_path: Path, references: list[str]
) -> list[tuple[list[str], str, str]]:
    observations: list[tuple[list[str], str, str]] = []
    for reference in references:
        result_path = _stored_path(run_path, reference)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        _require(result.get("returncode") == 0, f"stored command failed: {reference}")
        action_path = result_path.parent
        invocation = json.loads(
            (action_path / "invocation.json").read_text(encoding="utf-8")
        )
        argv = invocation.get("argv")
        _require(isinstance(argv, list), f"stored action has no public argv: {reference}")
        stdout_ref = result.get("stdout_ref")
        stdout = _stored_path(run_path, stdout_ref).read_text(encoding="utf-8")
        observations.append(([str(part) for part in argv], stdout, reference))
    return observations


def _find_command(
    observations: list[tuple[list[str], str, str]],
    predicate,
    name: str,
) -> tuple[list[str], str, str]:
    matches = [observation for observation in observations if predicate(observation[0])]
    _require(len(matches) == 1, f"stored run requires exactly one {name} observation")
    return matches[0]


def judge_agent_commit_run(run_path: Path) -> dict[str, Any]:
    """Rejudge scenario 4 from immutable bytes without executing product code."""

    run_path = Path(run_path).resolve()
    RunStore(run_path.parent).verify(run_path)
    result = json.loads((run_path / "result.json").read_text(encoding="utf-8"))
    _require(result.get("scenario", {}).get("claim") == SCENARIO_4_CLAIM, "wrong claim")
    _require(result.get("execution_mode") == "agent_live", "run is not agent_live")
    _require(result.get("execution_status") == "complete", "execution is incomplete")
    _require(result.get("verdict") == "pass", "stored verdict is not pass")
    _require(result.get("evidence", {}).get("complete") is True, "evidence is incomplete")
    _require(result.get("recordings", {}).get("rewatchable") is True, "run is not rewatchable")

    pins = result.get("pins") or {}
    harness = pins.get("harness") or {}
    _require(harness.get("name") == "claude", "stored harness is not Claude")
    _require(bool(harness.get("version")), "stored harness version is missing")
    _require((pins.get("model_wire") or {}).get("mode") == "live", "model wire is not live")
    _require(bool((pins.get("product") or {}).get("commit")), "product commit pin is missing")

    requirements = {
        item.get("name"): item
        for item in result.get("evidence", {}).get("requirements", [])
        if isinstance(item, dict)
    }
    for name in (
        "capture.trace",
        "capture.context",
        "capture.trail",
        "capture.storage",
        "capture.lifecycle",
        "agent.live_key_absence",
    ):
        _require(
            (requirements.get(name) or {}).get("complete") is True,
            f"stored requirement is incomplete: {name}",
        )

    custody_ref = requirements["agent.live_key_absence"]["evidence_refs"][0]
    custody = json.loads(_stored_path(run_path, custody_ref).read_text(encoding="utf-8"))
    _require(custody.get("absent") is True, "live key absence was not proven")
    _require(custody.get("matches") == [], "live key appeared in stored evidence")
    _require(
        int(custody.get("capture_files_checked") or 0) > 0,
        "collected capture tree was not included in the key scan",
    )

    capture = result.get("capture") or {}
    _require(capture.get("completeness") == "complete", "capture lifecycle is partial")
    trace_refs = capture.get("trace_refs") or []
    _require(len(trace_refs) == 1, "capture must name exactly one trace")
    trace_id = str(trace_refs[0])
    sources = {
        source.get("name"): source
        for source in capture.get("sources") or []
        if isinstance(source, dict)
    }
    for source_name in ("session_jsonl", "telemetry", "git", "bucket"):
        source = sources.get(source_name) or {}
        _require(
            source.get("status") == "finalized" and source.get("completeness") == "full",
            f"capture source is incomplete: {source_name}",
        )

    world_verifier = next(
        (
            verifier
            for verifier in result.get("verifiers") or []
            if verifier.get("name") == "scenario_4_world_state"
        ),
        None,
    )
    _require(bool(world_verifier), "scenario-4 world verifier is missing")
    _require(world_verifier.get("status") == "pass", "scenario-4 world verifier failed")
    evidence_refs = [str(ref) for ref in world_verifier.get("evidence_refs") or []]
    observations = _action_observations(run_path, evidence_refs)

    _argv, commit_stdout, _ref = _find_command(
        observations,
        lambda argv: argv == ["git", "rev-parse", "HEAD"],
        "git commit",
    )
    commit = commit_stdout.strip()
    _require(len(commit) == 40 and all(char in "0123456789abcdef" for char in commit), "invalid commit")
    _argv, content_stdout, _ref = _find_command(
        observations,
        lambda argv: argv == ["git", "show", f"{commit}:{SCENARIO_4_PATH}"],
        "committed file",
    )
    _require(content_stdout.strip() == SCENARIO_4_CONTENT, "committed content is wrong")

    _argv, blame_stdout, _ref = _find_command(
        observations,
        lambda argv: argv[:5] == ["opentraces", "trail", "blame", "commit", commit],
        "public blame",
    )
    blame = _json_bytes(blame_stdout)
    trail_rows = blame.get("trailEvidence") if isinstance(blame, dict) else None
    _require(bool(trail_rows), "public blame has no Trail evidence")
    _require(
        any(row.get("trace_id") == trace_id for row in trail_rows if isinstance(row, dict)),
        "public blame does not attribute the captured trace",
    )

    _argv, trace_stdout, _ref = _find_command(
        observations,
        lambda argv: argv == ["opentraces", "trace", "get", trace_id, "--json"],
        "public trace get",
    )
    _require(_contains_value(_json_bytes(trace_stdout), trace_id), "trace get returned another trace")
    _argv, context_stdout, _ref = _find_command(
        observations,
        lambda argv: argv == ["opentraces", "ctx", trace_id, "--json"],
        "public context read",
    )
    _require(_contains_value(_json_bytes(context_stdout), trace_id), "ctx returned another trace")
    _argv, bucket_stdout, _ref = _find_command(
        observations,
        lambda argv: argv == ["opentraces", "bucket", "verify", "--json"],
        "public bucket verify",
    )
    bucket = _json_bytes(bucket_stdout)
    _require(isinstance(bucket, dict) and bucket.get("ok") is True, "bucket verify is not ok")

    return {
        "verdict": "works",
        "commit": commit,
        "trace_id": trace_id,
        "evidence_refs": evidence_refs,
    }
