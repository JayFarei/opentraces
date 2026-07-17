"""Return a finalized bench run through the ordinary TraceRecord read path."""

from __future__ import annotations

import json
import re
import shlex
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from opentraces_schema import Agent, Observation, Outcome, Step, ToolCall, TraceRecord

from ..bucket_trace_records import read_bucket_record_for_trace
from ..config import Config, load_config
from ..ingest import _keep_index_warm_after_ingest, write_trace_to_bucket
from ..pipeline import process_imported_trace
from .contract import validate_result
from .run_store import RunStore


# Frozen from UUID5(URL, "https://opentraces.ai/bench/run-trace/v1").  Freezing
# the value makes the identity independent of implementation spelling while the
# exact run_id remains the sole name input.
BENCH_RUN_TRACE_NAMESPACE = uuid.UUID("d9b6d6f9-b040-5792-a5cf-16f9b501cbf4")
_OUTPUT_LIMIT_CHARS = 4096
_PRODUCT_PIN_FIELDS = {"commit", "worktree", "dirty_diff_digest"}
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class TraceReturnError(ValueError):
    """A verified run cannot be represented as an ordinary trace."""


def trace_id_for_run(run_id: str) -> str:
    """Return the frozen UUID5 trace identity for an exact bench ``run_id``."""

    if not isinstance(run_id, str) or not run_id:
        raise TraceReturnError("run_id must be a non-empty string")
    return str(uuid.uuid5(BENCH_RUN_TRACE_NAMESPACE, run_id))


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TraceReturnError(f"{label} is missing or invalid JSON") from exc
    if not isinstance(value, dict):
        raise TraceReturnError(f"{label} must be a JSON object")
    return value


def _bounded_text(path: Path, remaining: int) -> tuple[str, int]:
    if remaining <= 0:
        return "", remaining
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            # Read only the prefix the bounded preview can hold, plus one extra
            # character to detect that the file exceeds the bound. Text-mode
            # ``read`` decodes exactly enough bytes to yield these characters,
            # so UTF-8 replacement behavior and the visible output are identical
            # to decoding the whole file, without loading an oversized stored
            # output into memory. RunStore.verify() still hashes the full bytes.
            prefix = handle.read(remaining + 1)
    except OSError as exc:
        raise TraceReturnError(f"action output cannot be read: {path.name}") from exc
    if len(prefix) <= remaining:
        return prefix, remaining - len(prefix)
    marker = "\n[output truncated; full bytes remain in the stored run]\n"
    keep = max(0, remaining - len(marker))
    return prefix[:keep] + marker[: remaining - keep], 0


def _action_output_path(
    run_path: Path,
    action_path: Path,
    observed: Mapping[str, Any],
    field: str,
) -> Path:
    reference = observed.get(field)
    if not isinstance(reference, str) or not reference:
        raise TraceReturnError(f"missing action output reference: {field}")
    target = (run_path / reference).resolve()
    if not target.is_relative_to(action_path.resolve()) or not target.is_file():
        raise TraceReturnError(f"missing action output: {reference}")
    return target


def _observation_text(
    run_path: Path,
    action_path: Path,
    observed: Mapping[str, Any],
) -> str | None:
    remaining = _OUTPUT_LIMIT_CHARS
    stdout_path = _action_output_path(run_path, action_path, observed, "stdout_ref")
    stderr_path = _action_output_path(run_path, action_path, observed, "stderr_ref")
    stdout, remaining = _bounded_text(stdout_path, remaining)
    stderr, _ = _bounded_text(stderr_path, remaining)
    parts: list[str] = []
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"[stderr]\n{stderr}")
    return "\n".join(parts) or None


def _action_steps(run_path: Path) -> list[Step]:
    actions_root = run_path / "actions"
    if not actions_root.is_dir():
        raise TraceReturnError("finalized run is missing its actions directory")

    steps: list[Step] = []
    action_paths = sorted(path for path in actions_root.iterdir() if path.is_dir())
    for expected_ordinal, action_path in enumerate(action_paths, start=1):
        invocation = _read_object(
            action_path / "invocation.json",
            label=f"{action_path.name}/invocation.json",
        )
        observed = _read_object(
            action_path / "result.json",
            label=f"{action_path.name}/result.json",
        )
        ordinal = invocation.get("ordinal")
        if ordinal != expected_ordinal or action_path.name != f"{expected_ordinal:04d}":
            raise TraceReturnError("stored actions must have contiguous 1-based ordinals")
        argv = invocation.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(part, str) and part for part in argv)
        ):
            raise TraceReturnError(f"{action_path.name}/invocation.json has invalid argv")
        duration_ms = observed.get("duration_ms")
        if (
            not isinstance(duration_ms, int)
            or isinstance(duration_ms, bool)
            or duration_ms < 0
        ):
            raise TraceReturnError(f"{action_path.name}/result.json has invalid duration_ms")
        returncode = observed.get("returncode")
        if returncode is not None and (
            not isinstance(returncode, int) or isinstance(returncode, bool)
        ):
            raise TraceReturnError(f"{action_path.name}/result.json has invalid returncode")
        env_pins = invocation.get("env_pins")
        if not isinstance(env_pins, dict):
            raise TraceReturnError(f"{action_path.name}/invocation.json has invalid env_pins")
        cwd = invocation.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise TraceReturnError(f"{action_path.name}/invocation.json has invalid cwd")
        started_at = invocation.get("started_at")
        if not isinstance(started_at, str) or not started_at:
            raise TraceReturnError(f"{action_path.name}/invocation.json has invalid started_at")

        call_id = f"bench-action-{expected_ordinal:04d}"
        reason = observed.get("reason")
        error = None
        if isinstance(reason, Mapping):
            error = str(reason.get("message") or reason.get("code") or "") or None
        steps.append(
            Step(
                step_index=expected_ordinal + 1,
                role="agent",
                timestamp=started_at,
                tool_calls=[
                    ToolCall(
                        tool_call_id=call_id,
                        tool_name="Bash",
                        input={
                            "command": shlex.join(argv),
                            "argv": argv,
                            "cwd": cwd,
                            "env_pins": env_pins,
                        },
                        duration_ms=duration_ms,
                    )
                ],
                observations=[
                    Observation(
                        source_call_id=call_id,
                        content=_observation_text(run_path, action_path, observed),
                        output_summary=f"rc={returncode}",
                        error=error,
                    )
                ],
            )
        )
    return steps


def _timestamp_end(started_at: str, duration_ms: int) -> str:
    normalized = started_at[:-1] + "+00:00" if started_at.endswith("Z") else started_at
    try:
        ended = datetime.fromisoformat(normalized) + timedelta(milliseconds=duration_ms)
    except ValueError as exc:
        raise TraceReturnError("result.json has invalid started_at") from exc
    return ended.isoformat().replace("+00:00", "Z")


def _validated_product_pin(result: Mapping[str, Any]) -> dict[str, Any]:
    pins = result.get("pins")
    product_pin = pins.get("product") if isinstance(pins, Mapping) else None
    if not isinstance(product_pin, dict) or set(product_pin) != _PRODUCT_PIN_FIELDS:
        raise TraceReturnError("product pin must contain commit, worktree, and dirty digest")

    commit = product_pin["commit"]
    worktree = product_pin["worktree"]
    dirty_digest = product_pin["dirty_diff_digest"]
    if not isinstance(commit, str) or not commit:
        raise TraceReturnError("product pin commit must be a non-empty string")
    if worktree not in {"clean", "dirty"}:
        raise TraceReturnError("product pin worktree must be clean or dirty")
    if worktree == "clean" and dirty_digest is not None:
        raise TraceReturnError("clean product pin must have a null dirty digest")
    if worktree == "dirty" and (
        not isinstance(dirty_digest, str) or _SHA256_DIGEST.fullmatch(dirty_digest) is None
    ):
        raise TraceReturnError("dirty product pin must have a sha256 dirty digest")
    return product_pin


def _record_from_run(run_path: Path, result: dict[str, Any]) -> TraceRecord:
    run_id = result["run_id"]
    if run_id != run_path.name:
        raise TraceReturnError("result run_id does not match the finalized run directory")
    scenario = result["scenario"]
    claim = scenario["claim"]
    action_steps = _action_steps(run_path)
    end = _timestamp_end(result["started_at"], result["duration_ms"])
    product_pin = _validated_product_pin(result)

    steps = [
        Step(
            step_index=1,
            role="user",
            content=claim,
            timestamp=result["started_at"],
        ),
        *action_steps,
        Step(
            step_index=len(action_steps) + 2,
            role="agent",
            content=f"Bench run {run_id} completed; its verdict remains in the stored run.",
            timestamp=end,
        ),
    ]
    return TraceRecord(
        trace_id=trace_id_for_run(run_id),
        session_id=run_id,
        timestamp_start=result["started_at"],
        timestamp_end=end,
        execution_context="runtime",
        task={
            "description": claim,
            "source": scenario["nodeid"],
            "base_commit": product_pin["commit"],
        },
        agent=Agent(name="opentraces-bench"),
        steps=steps,
        outcome=Outcome(success=None),
        metadata={
            "bench": {
                "run_id": run_id,
                "run_ref": f"runs/v1/{run_id}",
                "product_pin": {
                    "commit": product_pin["commit"],
                    "worktree": product_pin["worktree"],
                    "dirty_diff_digest": product_pin["dirty_diff_digest"],
                },
                "capture_limitations": [
                    "manufactured run trace has no model context unless captured separately",
                    "manufactured run trace has no trail events unless observed separately",
                ],
            }
        },
    )


def return_run_as_trace(
    run_path: Path | str,
    *,
    project_dir: Path | str,
    store: RunStore | None = None,
    cfg: Config | None = None,
) -> TraceRecord:
    """Verify, convert, secure, store, and index one finalized bench run.

    Repeating the operation for the same immutable run converges on the same
    trace identity and normalized record bytes.
    """

    run_path = Path(run_path).resolve()
    project_dir = Path(project_dir).resolve()
    resolved_store = store or RunStore(run_path.parent)
    resolved_store.verify(run_path)
    result = _read_object(run_path / "result.json", label="result.json")
    validate_result(result)

    record = _record_from_run(run_path, result)
    processed = process_imported_trace(record, cfg or load_config()).record
    # The claim is a contract key copied from the verified result, not free-form
    # imported content.  Security still processes every other record field; the
    # two agent-readable claim surfaces must remain byte-identical to result.json.
    canonical_claim = result["scenario"]["claim"]
    processed.task.description = canonical_claim
    processed.steps[0].content = canonical_claim
    # A bench action transcript is evidence, never a grade.  Keep the normal
    # imported-trace enrichment/security pass, then clear only the fields that
    # commit-shaped output can infer as an outcome.  Arena labels remain the
    # sole grading surface for this manufactured trace family.
    processed.outcome = Outcome(success=None)
    processed.content_hash = processed.compute_content_hash()
    write_trace_to_bucket(
        processed,
        project_dir,
        parser_name="bench",
        source_jsonl=run_path / "result.json",
        trace_record_only=False,
        source_layer="manufactured",
    )
    _keep_index_warm_after_ingest(
        processed,
        processed.trace_id,
        trace_record_only=False,
    )
    stored = read_bucket_record_for_trace(processed.trace_id)
    if stored is None:  # The load-bearing write above must make this impossible.
        raise TraceReturnError("returned trace did not resolve from the bucket")
    return stored.record


__all__ = [
    "BENCH_RUN_TRACE_NAMESPACE",
    "TraceReturnError",
    "return_run_as_trace",
    "trace_id_for_run",
]
