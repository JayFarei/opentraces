"""Isolated source finalizers used by :mod:`opentraces.capture.portable`.

Each invocation owns one existing capture primitive and writes one small report.
The parent enforces the wall-clock deadline and records a timeout if this child
does not settle, so an unbounded legacy primitive cannot hang ``finish``.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    invocation_nonce = request.get("invocation_nonce")
    if not isinstance(invocation_nonce, str) or not invocation_nonce:
        raise ValueError("finalizer request is missing its invocation nonce")
    try:
        report = _finalize(request)
    except Exception as exc:  # noqa: BLE001 - the report is the honesty seam
        report = {
            "status": "unavailable",
            "completeness": "missing",
            "evidence_refs": [],
            "limitations": [f"{type(exc).__name__}: {exc}"],
            "details": {},
        }
    report["invocation_nonce"] = invocation_nonce
    _atomic_write_json(args.report, report)
    return 0 if report.get("status") in {"finalized", "partial"} else 1


def _finalize(request: dict[str, Any]) -> dict[str, Any]:
    security_cfg, security_state = _resolve_security_policy(request)
    request["_security_cfg"] = security_cfg
    source = request["source"]
    if source == "session_jsonl":
        report = _finalize_session(request)
    elif source == "telemetry":
        report = _finalize_telemetry(request)
    elif source == "watcher":
        report = _finalize_watcher(request)
    elif source == "git":
        report = _finalize_git(request)
    elif source == "bucket":
        report = _finalize_bucket(request)
    else:
        raise ValueError(f"no isolated finalizer for {source!r}")
    report.setdefault("details", {})["capture_security_policy"] = security_state
    return report


def _finalize_session(request: dict[str, Any]) -> dict[str, Any]:
    path = request.get("session_path")
    if not path:
        return _missing("session_path was not supplied")
    from ..core.ingest import ingest_one_session

    result = ingest_one_session(
        Path(path),
        Path(request["project"]),
        parser_name=request.get("actor") or "claude-code",
        cfg=request["_security_cfg"],
        reconcile_watcher=False,
    )
    details = {
        "action": result.action,
        "session_id": result.session_id,
        "trace_id": result.trace_id,
        "error": result.error,
    }
    if result.error or not result.trace_id:
        return _missing(result.error or "ingest produced no trace", details=details)
    from ..core.bucket_store import read_trace_record_object, trace_record_path
    from ..core.config import get_project_dir

    project = Path(request["project"])
    project_slug = get_project_dir(project).name
    record = read_trace_record_object(trace_record_path(project_slug, result.trace_id))
    if record is None:
        return _missing(
            "ingest produced no independently readable canonical trace record",
            details=details,
        )
    current_pointer = trace_record_path(project_slug, result.trace_id)
    details["trace_record_path"] = str(record.path)
    details["trace_record_pointer_path"] = str(current_pointer)
    details["security_observation"] = _security_observation_from_trace(
        record.record.model_dump(mode="json")
    )
    return {
        "status": "finalized",
        "completeness": "full",
        "evidence_refs": [str(Path(path)), str(record.path), str(current_pointer)],
        "limitations": [],
        "details": details,
        "trace_id": result.trace_id,
    }


def _finalize_telemetry(request: dict[str, Any]) -> dict[str, Any]:
    session_id = request.get("session_id")
    trace_id = request.get("trace_id")
    if not session_id or not trace_id:
        return _missing("telemetry finalization requires session_id and trace_id")
    from .otlp.emitter import flush_session_to_project, load_snapshot_from_disk
    from ..core.paths import otel_staging_dir, raw_bodies_dir

    snapshot_path = otel_staging_dir() / f"{session_id}.json"
    opened_at = float((request.get("open_details") or {}).get("opened_at_unix") or 0.0)
    ingress_quiesced = (request.get("open_details") or {}).get("ingress_quiesced") is True
    deadline = _request_deadline(request)
    snapshot: dict[str, Any] | None = None
    last_envelope_at: float | None = None
    while time.monotonic() < deadline:
        if snapshot_path.is_file():
            candidate = load_snapshot_from_disk(snapshot_path)
            raw_last = candidate.get("last_envelope_at")
            last_envelope_at = float(raw_last) if raw_last is not None else None
            if last_envelope_at is not None and last_envelope_at >= opened_at:
                snapshot = candidate
                break
            if ingress_quiesced:
                return _missing(
                    "telemetry receiver produced no fresh session snapshot after Capture.open",
                    details={
                        "snapshot_path": str(snapshot_path),
                        "lifecycle_opened_at": opened_at,
                        "last_envelope_at": last_envelope_at,
                        "ingress_quiesced": True,
                    },
                )
        time.sleep(min(0.03, max(0.0, deadline - time.monotonic())))
    if snapshot is None:
        return _missing(
            "telemetry receiver produced no fresh session snapshot after Capture.open",
            details={
                "snapshot_path": str(snapshot_path),
                "lifecycle_opened_at": opened_at,
                "last_envelope_at": last_envelope_at,
            },
        )
    generation = int(snapshot.get("snapshot_generation") or 0)
    accepted = int(snapshot.get("accepted_envelopes") or 0)
    quiescent = snapshot.get("snapshot_quiescent") is True
    if generation < 1 or generation != accepted:
        return _missing(
            "telemetry snapshot generation is incomplete",
            details={
                "snapshot_quiescent": quiescent,
                "snapshot_generation": generation,
                "accepted_envelopes": accepted,
            },
        )
    if ingress_quiesced and not quiescent:
        return _missing(
            "telemetry snapshot is not a quiescent finish-time generation",
            details={
                "snapshot_quiescent": False,
                "snapshot_generation": generation,
                "accepted_envelopes": accepted,
                "ingress_quiesced": True,
            },
        )
    snapshot_semantics = (
        "leased_quiescent_generation" if ingress_quiesced else "persistent_unbarriered_generation"
    )
    report = flush_session_to_project(
        project_dir=Path(request["project"]),
        trace_id=trace_id,
        session_id=session_id,
        snapshot=snapshot,
        raw_bodies_dir=raw_bodies_dir(),
        raw_body_retention=request.get("raw_body_retention") or "delete",
    )
    if not report.get("ok"):
        return _missing(
            str(report.get("reason") or "telemetry flush failed"),
            details=report,
        )
    report.update(
        {
            "snapshot_quiescent": quiescent,
            "snapshot_semantics": snapshot_semantics,
            "snapshot_generation": generation,
            "accepted_envelopes": accepted,
            "last_envelope_at": last_envelope_at,
        }
    )
    limitations: list[str] = []
    if not ingress_quiesced:
        limitations.append(
            "persistent telemetry finish-tail coverage is unproven without a "
            "daemon barrier acknowledgment"
        )
    approximated_nodes = int(report.get("approximated_nodes") or 0)
    stub_nodes = int(report.get("stub_nodes") or 0)
    if approximated_nodes or stub_nodes:
        limitations.append(
            "OTel evidence contains approximated or stub context nodes; "
            "model-boundary capture is not full"
        )
    partial = bool(limitations)
    return {
        "status": "partial" if partial else "finalized",
        "completeness": "partial" if partial else "full",
        "evidence_refs": [str(snapshot_path)],
        "limitations": limitations,
        "details": report,
        "trace_id": trace_id,
    }


def _finalize_watcher(request: dict[str, Any]) -> dict[str, Any]:
    from .fs_watcher.runtime import poll_project_once
    from ..core.trails.reconciler import reconcile_watcher_observations

    project = Path(request["project"])
    open_details = request.get("open_details") or {}
    root_details = {
        "observed_root": str(open_details.get("observed_root") or project),
        "declared_workspace": str(
            open_details.get("declared_workspace") or request.get("workspace") or ""
        ),
    }
    if open_details.get("root_binding_proven") is False:
        return _missing(
            "declared workspace differs from the watcher observed root",
            details=root_details,
        )
    state_path = open_details.get("state_path")
    poll_succeeded = open_details.get("poll_succeeded") is True
    baseline_proven = open_details.get("baseline_proven") is True
    if not state_path or not poll_succeeded or not baseline_proven:
        return _missing(
            "watcher baseline was not proven at Capture.open",
            details={
                **root_details,
                "open_poll_succeeded": poll_succeeded,
                "open_baseline_proven": baseline_proven,
                "open_baseline_initialized": open_details.get("baseline_initialized"),
                "state_path": state_path,
            },
        )
    poll = poll_project_once(project, state_path=Path(state_path))
    final_state_present = Path(state_path).is_file()
    if poll.baseline_initialized or not final_state_present:
        return _missing(
            "watcher state was absent at final drain; lifecycle coverage is unproven",
            details={
                **root_details,
                "open_poll_succeeded": True,
                "open_baseline_proven": True,
                "open_baseline_initialized": open_details.get("baseline_initialized"),
                "final_baseline_initialized": poll.baseline_initialized,
                "final_state_present": final_state_present,
                "state_path": state_path,
            },
        )
    reconciled = reconcile_watcher_observations(project)
    return {
        "status": "finalized",
        "completeness": "full",
        "evidence_refs": [str(Path(state_path))],
        "limitations": [],
        "details": {
            **root_details,
            "open_poll_succeeded": True,
            "open_baseline_proven": True,
            "open_baseline_initialized": open_details.get("baseline_initialized"),
            "final_baseline_initialized": poll.baseline_initialized,
            "final_state_present": final_state_present,
            "state_path": state_path,
            "paths_seen": poll.paths_seen,
            "observations": len(poll.observations),
            "mutations": len(poll.mutations),
            "reconciled": reconciled,
        },
    }


def _finalize_git(request: dict[str, Any]) -> dict[str, Any]:
    import subprocess

    from ..core.trails._git_subprocess import run_git_until, timeout_limitation
    from ..core.trails.event_log import EVENT_LOG_REF, read_events
    from ..core.trails.maturation import mature_trails

    project = Path(request["project"]).resolve()
    # Leave the isolated child enough time to serialize its honest partial
    # report after a Git process-group timeout. The parent already reserves a
    # smaller outer cushion; this inner reserve covers cleanup plus report I/O.
    deadline = max(time.monotonic(), _request_deadline(request) - 0.15)
    summary = mature_trails(
        project,
        deadline=deadline,
    )
    limitations = list(summary.errors)
    try:
        head = run_git_until(
            ["rev-parse", "--verify", EVENT_LOG_REF],
            cwd=project,
            deadline=deadline,
        )
    except subprocess.TimeoutExpired as exc:
        limitations.append(timeout_limitation(exc))
        event_log_head = None
    else:
        event_log_head = head.stdout.strip() if head.returncode == 0 else None
    events = read_events(project, verify=True) if event_log_head else []
    details = {
        "schema_version": "opentraces.capture.git_finalization.v1",
        "project": str(project),
        "event_log_ref": EVENT_LOG_REF,
        "event_log_head": event_log_head,
        "events_count": len(events),
        "maturation": summary.to_dict(),
    }
    if summary.truncated:
        limitations.append("anchor maturation reached its deadline")
    if event_log_head is None or not events:
        limitations.append("canonical trail event log is unavailable or empty")
    partial = summary.truncated or bool(summary.errors) or not event_log_head or not events
    return {
        "status": "partial" if partial else "finalized",
        "completeness": "partial" if partial else "full",
        "evidence_refs": [],
        "limitations": limitations,
        "details": details,
    }


def _finalize_bucket(request: dict[str, Any]) -> dict[str, Any]:
    trace_id = request.get("trace_id")
    if not trace_id:
        return _missing("bucket projection requires a finalized trace id")
    from ..core.bucket_store import (
        bucket_manifest_path,
        project_context_tree_to_bucket,
        project_per_trace_exports,
        read_trace_record_object,
        sync_trail_events_from_repo,
        trace_record_path,
        trace_v1_json_path,
        upsert_manifest_trace_row,
    )
    from ..core.config import get_project_dir

    project = Path(request["project"])
    project_slug = get_project_dir(project).name
    record_obj = read_trace_record_object(trace_record_path(project_slug, trace_id))
    if record_obj is None:
        return _missing(
            "bucket projection requires the canonical trace record",
            details={"trace_id": trace_id, "project_slug": project_slug},
        )

    mirror = sync_trail_events_from_repo(project, repo_id=project_slug)
    context_projection = project_context_tree_to_bucket(
        project,
        project_slug=project_slug,
        trace_id=trace_id,
    )
    companion_projection = project_per_trace_exports(
        project,
        project_slug=project_slug,
        trace_id=trace_id,
        record=record_obj.record,
    )
    manifest_row = upsert_manifest_trace_row(
        project,
        project_slug=project_slug,
        trace_id=trace_id,
        record=record_obj.record,
    )
    trace_path = trace_v1_json_path(project_slug, trace_id)
    manifest_path = bucket_manifest_path()
    pointer_path = trace_record_path(project_slug, trace_id)
    record_path = record_obj.path
    from ..core.bucket_layout import (
        trace_v1_context_path,
        trace_v1_sources_path,
        trace_v1_trail_path,
    )

    context_path = trace_v1_context_path(project_slug, trace_id)
    trail_path = trace_v1_trail_path(project_slug, trace_id)
    sources_path = trace_v1_sources_path(project_slug, trace_id)
    missing = [str(path) for path in (trace_path, manifest_path) if not path.is_file()]
    if missing:
        return _missing(
            "ingest did not materialize required bucket projection",
            details={"missing": missing, "trace_id": trace_id},
        )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    present = any(row.get("trace_id") == trace_id for row in manifest.get("traces") or [])
    if not present:
        return _missing(
            "manifest does not name the finalized trace",
            details={"trace_id": trace_id, "trace_path": str(trace_path)},
        )
    return {
        "status": "finalized",
        "completeness": "full",
        "evidence_refs": [
            str(trace_path),
            str(manifest_path),
            str(pointer_path),
            str(record_path),
            str(context_path),
            str(trail_path),
            str(sources_path),
        ],
        "limitations": [],
        "details": {
            "trace_id": trace_id,
            "trace_path": str(trace_path),
            "manifest_path": str(manifest_path),
            "trace_record_pointer_path": str(pointer_path),
            "trace_record_path": str(record_path),
            "context_path": str(context_path),
            "trail_path": str(trail_path),
            "sources_path": str(sources_path),
            "project_slug": project_slug,
            "security": trace.get("security") or {},
            "security_observation": _security_observation_from_trace(trace),
            "events_mirror": mirror,
            "context_projection": context_projection,
            "companion_projection": companion_projection,
            "manifest_row_written": manifest_row is not None,
        },
        "trace_id": trace_id,
    }


def _missing(reason: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "completeness": "missing",
        "evidence_refs": [],
        "limitations": [reason],
        "details": details or {},
    }


def _request_deadline(request: dict[str, Any]) -> float:
    absolute = request.get("deadline_monotonic")
    if absolute is not None:
        return float(absolute)
    remaining = max(0.0, float(request.get("remaining_seconds") or 0.0))
    return time.monotonic() + remaining


def _resolve_security_policy(request: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Resolve configured or explicit per-tool flags for this child."""

    from ..core.config import load_config
    from ..security.config import (
        enabled_security_tool_names,
        set_security_tools_exact,
    )

    cfg = load_config()
    requested = request.get("security_tools")
    configuration = request.get("security_configuration")
    if configuration not in {"configured", "explicit"}:
        configuration = "configured" if requested is None else "explicit"
    if requested is not None:
        set_security_tools_exact(cfg, requested)
    return cfg, {
        "configuration": configuration,
        "configured_tools": enabled_security_tool_names(cfg),
        "observed": False,
    }


def _security_observation_from_trace(trace: dict[str, Any]) -> dict[str, Any]:
    metadata = trace.get("metadata") or {}
    manifest = metadata.get("security") if isinstance(metadata, dict) else None
    tools = manifest.get("tools_applied") if isinstance(manifest, dict) else None
    if not isinstance(tools, list):
        return {
            "observed": False,
            "tools_applied": [],
            "reason": "TraceRecord has no security tools_applied manifest",
        }
    return {
        "observed": True,
        "tools_applied": [str(tool) for tool in tools],
        "scanned": bool((trace.get("security") or {}).get("scanned")),
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    raise SystemExit(main())
