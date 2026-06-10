"""Shared artifact-preferred Codex checkpoint helpers for plan 083."""

from __future__ import annotations

import json
import tarfile
from collections.abc import Mapping
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register
from ._captured_helpers import (
    _artifact_paths,
    artifact_exists,
    capture_metadata_from_artifact,
    read_state_json,
    restore_from_capture,
)

CODEX_AUDIT_KEY = "c_captured_codex_session_audit"
_PYTHON = Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python"


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


def _first_trace_record_payload_from_capture(capture_name: str) -> dict | None:
    archive, _metadata = _artifact_paths(capture_name)
    if not archive.exists():
        return None
    with tarfile.open(archive, "r:gz") as tar:
        members = sorted(
            member
            for member in tar.getmembers()
            if member.isfile()
            and "/home/.opentraces/projects/" in member.name
            and "/traces/" in member.name
            and member.name.endswith(".jsonl")
        )
        for member in members:
            handle = tar.extractfile(member)
            if handle is None:
                continue
            for raw_line in handle:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                payload = json.loads(line)
                return payload if isinstance(payload, dict) else None
    return None


def _project_slug_from_restored_box(driver: Driver, box: Box) -> str:
    state_dir, _state = read_state_json(driver, box)
    return Path(state_dir).name


def _inject_trace_record_into_bucket(
    driver: Driver,
    box: Box,
    *,
    project_slug: str,
    record_payload: dict,
) -> dict:
    trace_id = str(record_payload.get("trace_id") or "")
    if not trace_id:
        raise CheckpointError("mixed-agent composite record has no trace_id")
    payload_path = box.root / "tmp" / f"{trace_id}.trace.json"
    driver.put_text(box, str(payload_path), json.dumps(record_payload))
    script = """
import json
import time
from datetime import datetime
from pathlib import Path

from opentraces_schema import TraceRecord
from opentraces.core.bucket_store import bucket_manifest, project_per_trace_exports
from opentraces.core.paths import PROJECTS_DIR
from opentraces.core.state import StateManager, TraceStatus

record = TraceRecord.model_validate_json(Path(__PAYLOAD__).read_text(encoding="utf-8"))
summary = project_per_trace_exports(
    Path(__PROJECT__),
    project_slug=__PROJECT_SLUG__,
    trace_id=record.trace_id,
    record=record,
)

# Register the injected trace in the project state.json the same way real
# ingest does (core/ingest.py): write the staging JSONL under
# <projects>/<slug>/traces/ and record the entry through the real
# state-writer. The min_captured_traces probe deliberately counts
# state.json entries, so the bucket write alone would leave the world
# dishonest by the probe's definition.
project_state_dir = PROJECTS_DIR / __PROJECT_SLUG__
staging_dir = project_state_dir / "traces"
staging_dir.mkdir(parents=True, exist_ok=True)
staging_file = staging_dir / (record.trace_id + ".jsonl")
staging_file.write_text(record.to_jsonl_line() + "\\n")
created_at = time.time()
if record.timestamp_start:
    try:
        created_at = datetime.fromisoformat(
            record.timestamp_start.replace("Z", "+00:00")
        ).timestamp()
    except ValueError:
        pass
state = StateManager(state_path=project_state_dir / "state.json")
state.set_trace_status(
    record.trace_id,
    TraceStatus.STAGED,
    session_id=record.session_id,
    file_path=str(staging_file),
    created_at=created_at,
)

manifest = bucket_manifest(write=True, include_objects=False)
state_traces = json.loads(
    (project_state_dir / "state.json").read_text(encoding="utf-8")
).get("traces") or {}
print(json.dumps({
    "trace_id": record.trace_id,
    "agent_name": record.agent.name if record.agent else "",
    "project_slug": __PROJECT_SLUG__,
    "summary": summary,
    "trace_count": len(manifest.get("traces") or []),
    "state_trace_count": len(state_traces),
}, sort_keys=True))
""".replace("__PAYLOAD__", repr(str(payload_path))).replace(
        "__PROJECT__", repr(str(box.project))
    ).replace(
        "__PROJECT_SLUG__", repr(project_slug)
    )
    python = str(_PYTHON) if _PYTHON.exists() else "python3"
    result = driver.exec(box, [python, "-c", script])
    if not result.ok:
        raise CheckpointError(
            "failed to inject mixed-agent TraceRecord into bucket: "
            f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
        )
    try:
        injected = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise CheckpointError(
            f"mixed-agent injection emitted malformed JSON: {exc}"
        ) from None
    return injected if isinstance(injected, dict) else {}


def _repair_bucket_projection(driver: Driver, box: Box) -> None:
    result = driver.exec(box, [*driver.cli_argv(box), "bucket", "repair", "--json"])
    if result.ok:
        return
    raise CheckpointError(
        "failed to repair bucket projection before mixed-agent injection: "
        f"{result.stderr.strip() or result.stdout.strip() or '<no output>'}"
    )


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


def register_mixed_agent_bucket_checkpoint(
    *,
    name: str,
    codex_capture_name: str,
    claude_capture_name: str,
    description: str,
) -> None:
    """Register the plan-083 mixed Claude/Codex bucket checkpoint.

    The checkpoint stays default-CI safe: if either local artifact is missing,
    it declares no ``provides`` dimensions and pinned journeys SKIP. When both
    artifacts are present, it restores the Codex box and injects the frozen
    Claude TraceRecord into the same bucket so bucket status/manifest/verify
    exercise both agent summaries without requiring a live Claude run.
    """

    def _mixed_delta(driver: Driver, box: Box) -> None:
        cap_meta = restore_from_capture(driver, box, codex_capture_name)
        if cap_meta is None:
            box.notes[CODEX_AUDIT_KEY] = _missing_artifact_audit(codex_capture_name)
            return

        codex_audit = _derive_audit_from_restored_box(
            driver,
            box,
            cap_meta,
            checkpoint_name=name,
            capture_name=codex_capture_name,
        )
        claude_payload = _first_trace_record_payload_from_capture(claude_capture_name)
        if claude_payload is None:
            raise CheckpointError(
                f"{name} could not read TraceRecord from {claude_capture_name!r}"
            )
        project_slug = _project_slug_from_restored_box(driver, box)
        _repair_bucket_projection(driver, box)
        injected = _inject_trace_record_into_bucket(
            driver,
            box,
            project_slug=project_slug,
            record_payload=claude_payload,
        )
        box.notes[CODEX_AUDIT_KEY] = {
            **codex_audit,
            "scenario_name": "mixed-agent-bucket-parity",
            "mixed_agent_trace_ids": {
                "codex-cli": codex_audit.get("trace_id") or "",
                "claude-code": injected.get("trace_id") or "",
            },
            "mixed_agent_project_slug": project_slug,
            "mixed_agent_trace_count": injected.get("trace_count"),
            "capture_metadata": {
                "source": "composite_artifacts",
                "codex": capture_metadata_from_artifact(cap_meta),
                "claude_capture_name": claude_capture_name,
            },
        }

    provides = None
    if artifact_exists(codex_capture_name) and artifact_exists(claude_capture_name):
        provides = {"captured_traces": 2, "context_tree_built": True}

    register(
        Checkpoint(
            name=name,
            composed_from="c-installed-source",
            delta=_mixed_delta,
            cache=False,
            description=description,
            provides=provides,
        )
    )


def register_codex_full_parity_checkpoint(
    *,
    name: str,
    capture_names: list[str],
    representative_capture_name: str,
    description: str,
) -> None:
    """Register the aggregate plan-083 full-parity sentinel checkpoint.

    The journey runner does not execute nested ``includes``. This checkpoint
    therefore acts as a sentinel: it only provides the release-gate dimensions
    when every individual Codex artifact exists, then restores one
    representative Codex capture so the aggregate journey has a real project
    to smoke.
    """

    def _full_delta(driver: Driver, box: Box) -> None:
        cap_meta = restore_from_capture(driver, box, representative_capture_name)
        if cap_meta is None:
            box.notes[CODEX_AUDIT_KEY] = _missing_artifact_audit(
                representative_capture_name
            )
            return
        audit = _derive_audit_from_restored_box(
            driver,
            box,
            cap_meta,
            checkpoint_name=name,
            capture_name=representative_capture_name,
        )
        box.notes[CODEX_AUDIT_KEY] = {
            **audit,
            "scenario_name": "codex-full-parity-latest",
            "aggregate_capture_names": list(capture_names),
            "capture_metadata": {
                "source": "aggregate_artifacts",
                "representative": capture_metadata_from_artifact(cap_meta),
                "capture_count": len(capture_names),
            },
        }

    provides = None
    if all(artifact_exists(capture_name) for capture_name in capture_names):
        provides = {
            "captured_traces": 1,
            "context_tree_built": True,
            "skills": ["opentraces"],
            "mcp_servers_connected": 1,
            "has_security_findings": True,
        }

    register(
        Checkpoint(
            name=name,
            composed_from="c-installed-source",
            delta=_full_delta,
            cache=False,
            description=description,
            provides=provides,
        )
    )
