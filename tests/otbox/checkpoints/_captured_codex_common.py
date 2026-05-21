"""Shared artifact-preferred Codex checkpoint helpers for plan 083."""

from __future__ import annotations

import json
from collections.abc import Mapping

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    artifact_exists,
    capture_metadata_from_artifact,
    read_state_json,
    restore_from_capture,
)

CODEX_AUDIT_KEY = "c_captured_codex_session_audit"


def _trace_agent_name(trace: dict) -> str:
    agent = trace.get("agent") or {}
    if isinstance(agent, dict):
        return str(agent.get("name") or "")
    return ""


def _first_codex_trace(state: dict) -> dict | None:
    traces = list((state.get("traces") or {}).values())
    for trace in traces:
        if _trace_agent_name(trace) in {"codex-cli", "codex"}:
            return trace
    return traces[0] if traces else None


def _load_trace_record(driver: Driver, box: Box, trace: dict) -> dict:
    path = trace.get("file_path")
    if not isinstance(path, str) or not path:
        return {}
    raw = driver.exec(box, ["cat", path])
    if not raw.ok:
        return {}
    first = next((line for line in raw.stdout.splitlines() if line.strip()), "")
    if not first:
        return {}
    try:
        data = json.loads(first)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _derive_audit_from_restored_box(
    driver: Driver,
    box: Box,
    cap_meta: dict,
    *,
    checkpoint_name: str,
    capture_name: str,
) -> dict:
    state_dir, state = read_state_json(driver, box)
    trace = _first_codex_trace(state)
    if not trace:
        raise CheckpointError(
            f"{checkpoint_name} artifact restore produced no traces; "
            f"state at {state_dir}/state.json contained "
            f"{len(state.get('traces') or {})} entries"
        )

    record = _load_trace_record(driver, box, trace)
    paths = driver.paths(box)
    project = paths["project"]
    head = driver.exec(box, ["git", "-C", project, "rev-parse", "HEAD"])
    commit_sha = head.stdout.strip() if head.ok else ""
    steps = record.get("steps") if isinstance(record.get("steps"), list) else []
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    function_names = (
        metadata.get("function_names")
        if isinstance(metadata.get("function_names"), dict)
        else {}
    )
    context_tree_built = bool(
        record.get("context_tree_summary")
        or any(isinstance(step, dict) and step.get("context_node_id") for step in steps)
    )
    step_count = int(trace.get("step_count") or len(steps))

    return {
        "scenario_name": capture_name,
        "session_id": trace.get("session_id") or "",
        "trace_id": trace.get("trace_id") or "",
        "agent_name": _trace_agent_name(record) or _trace_agent_name(trace),
        "step_count": step_count,
        "commit_sha": commit_sha,
        "state_dir": state_dir,
        "context_tree_built": context_tree_built,
        "function_names": function_names,
        "capture_metadata": capture_metadata_from_artifact(cap_meta),
    }


def _missing_artifact_audit(capture_name: str) -> dict:
    return {
        "scenario_name": capture_name,
        "session_id": "",
        "trace_id": "",
        "agent_name": "codex-cli",
        "step_count": 0,
        "commit_sha": "",
        "context_tree_built": False,
        "capture_metadata": {
            "source": "missing_artifact",
            "capture_name": capture_name,
        },
    }


def register_codex_capture_checkpoint(
    *,
    name: str,
    capture_name: str,
    description: str,
    extra_provides: Mapping[str, object] | None = None,
) -> None:
    """Register an artifact-preferred Codex checkpoint.

    The checkpoint is intentionally inert when its capture artifact is
    absent: it records a missing-artifact audit and declares no
    ``provides`` dimensions, so pinned journeys SKIP on preconditions.
    Cache is disabled because artifact presence and freshness live
    outside the checkpoint source hash.
    """

    def _captured_codex_delta(driver: Driver, box: Box) -> None:
        cap_meta = restore_from_capture(driver, box, capture_name)
        if cap_meta is None:
            box.notes[CODEX_AUDIT_KEY] = _missing_artifact_audit(capture_name)
            return
        box.notes[CODEX_AUDIT_KEY] = _derive_audit_from_restored_box(
            driver,
            box,
            cap_meta,
            checkpoint_name=name,
            capture_name=capture_name,
        )

    provides: dict[str, object] | None = None
    if artifact_exists(capture_name):
        provides = {"captured_traces": 1, "context_tree_built": True}
        if extra_provides:
            provides.update(extra_provides)

    register(
        Checkpoint(
            name=name,
            composed_from="c-installed-source",
            delta=_captured_codex_delta,
            cache=False,
            description=description,
            provides=provides,
        )
    )
