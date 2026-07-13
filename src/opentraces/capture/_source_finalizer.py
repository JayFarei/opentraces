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
    _atomic_write_json(args.report, report)
    return 0 if report.get("status") in {"finalized", "partial"} else 1


def _finalize(request: dict[str, Any]) -> dict[str, Any]:
    source = request["source"]
    if source == "session_jsonl":
        return _finalize_session(request)
    if source == "telemetry":
        return _finalize_telemetry(request)
    if source == "watcher":
        return _finalize_watcher(request)
    if source == "git":
        return _finalize_git(request)
    if source == "bucket":
        return _finalize_bucket(request)
    raise ValueError(f"no isolated finalizer for {source!r}")


def _finalize_session(request: dict[str, Any]) -> dict[str, Any]:
    path = request.get("session_path")
    if not path:
        return _missing("session_path was not supplied")
    from ..core.ingest import ingest_one_session

    result = ingest_one_session(
        Path(path),
        Path(request["project"]),
        parser_name=request.get("actor") or "claude-code",
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
    return {
        "status": "finalized",
        "completeness": "full",
        "evidence_refs": [str(Path(path))],
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
    remaining = max(0.0, float(request.get("remaining_seconds") or 0.0))
    deadline = time.monotonic() + remaining
    while not snapshot_path.is_file() and time.monotonic() < deadline:
        time.sleep(min(0.03, max(0.0, deadline - time.monotonic())))
    if not snapshot_path.is_file():
        return _missing("telemetry receiver produced no session snapshot")
    snapshot = load_snapshot_from_disk(snapshot_path)
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
    return {
        "status": "finalized",
        "completeness": "full",
        "evidence_refs": [str(snapshot_path)],
        "limitations": [],
        "details": report,
        "trace_id": trace_id,
    }


def _finalize_watcher(request: dict[str, Any]) -> dict[str, Any]:
    from .fs_watcher.runtime import poll_project_once
    from ..core.trails.reconciler import reconcile_watcher_observations

    project = Path(request["project"])
    open_details = request.get("open_details") or {}
    state_path = open_details.get("state_path")
    poll_succeeded = open_details.get("poll_succeeded") is True
    baseline_proven = open_details.get("baseline_proven") is True
    if not state_path or not poll_succeeded or not baseline_proven:
        return _missing(
            "watcher baseline was not proven at Capture.open",
            details={
                "open_poll_succeeded": poll_succeeded,
                "open_baseline_proven": baseline_proven,
                "open_baseline_initialized": open_details.get(
                    "baseline_initialized"
                ),
                "state_path": state_path,
            },
        )
    poll = poll_project_once(project, state_path=Path(state_path))
    final_state_present = Path(state_path).is_file()
    if poll.baseline_initialized or not final_state_present:
        return _missing(
            "watcher state was absent at final drain; lifecycle coverage is unproven",
            details={
                "open_poll_succeeded": True,
                "open_baseline_proven": True,
                "open_baseline_initialized": open_details.get(
                    "baseline_initialized"
                ),
                "final_baseline_initialized": poll.baseline_initialized,
                "final_state_present": final_state_present,
                "state_path": state_path,
            },
        )
    reconciled = reconcile_watcher_observations(project)
    return {
        "status": "finalized",
        "completeness": "full",
        "evidence_refs": [],
        "limitations": [],
        "details": {
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
    from ..core.trails.maturation import mature_trails

    remaining = max(0.0, float(request.get("remaining_seconds") or 0.0))
    summary = mature_trails(
        Path(request["project"]),
        deadline=time.monotonic() + remaining,
    )
    details = summary.to_dict()
    limitations = list(summary.errors)
    if summary.truncated:
        limitations.append("anchor maturation reached its deadline")
    return {
        "status": "partial" if summary.truncated or summary.errors else "finalized",
        "completeness": "partial" if summary.truncated or summary.errors else "full",
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
    missing = [
        str(path)
        for path in (trace_path, manifest_path)
        if not path.is_file()
    ]
    if missing:
        return _missing(
            "ingest did not materialize required bucket projection",
            details={"missing": missing, "trace_id": trace_id},
        )
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    present = any(
        row.get("trace_id") == trace_id for row in manifest.get("traces") or []
    )
    if not present:
        return _missing(
            "manifest does not name the finalized trace",
            details={"trace_id": trace_id, "trace_path": str(trace_path)},
        )
    return {
        "status": "finalized",
        "completeness": "full",
        "evidence_refs": [str(trace_path), str(manifest_path)],
        "limitations": [],
        "details": {
            "trace_id": trace_id,
            "trace_path": str(trace_path),
            "manifest_path": str(manifest_path),
            "project_slug": project_slug,
            "security": trace.get("security") or {},
            "events_mirror": mirror,
            "context_projection": context_projection,
            "companion_projection": companion_projection,
            "manifest_row_written": manifest_row is not None,
        },
    }


def _missing(reason: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "completeness": "missing",
        "evidence_refs": [],
        "limitations": [reason],
        "details": details or {},
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


if __name__ == "__main__":
    raise SystemExit(main())
