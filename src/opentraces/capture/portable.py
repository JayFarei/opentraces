"""Portable, source-honest capture orchestration.

This module owns capture finalization for both long-running installations and
leased workspaces.  Callers open one session, inject its bindings, and call
``finish`` once; they never sequence source-specific flush or projection steps.
"""

from __future__ import annotations

import hashlib
import gzip
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.error import URLError
from urllib.request import urlopen

from .. import __version__
from ..security.config import SECURITY_TOOL_NAMES

CapturePlacement = Literal["persistent", "leased"]
CaptureSourceName = Literal[
    "session_jsonl",
    "telemetry",
    "watcher",
    "git",
    "bucket",
]
CaptureViewName = Literal["model_boundary", "harness", "world_effects", "storage"]
Completeness = Literal["full", "partial", "missing"]
SourceStatus = Literal["finalized", "partial", "unavailable", "timed_out"]

CAPTURE_RESULT_SCHEMA = "opentraces.capture.result.v1"
_KNOWN_SOURCES = frozenset({"session_jsonl", "telemetry", "watcher", "git", "bucket"})
_FINALIZATION_ORDER: tuple[CaptureSourceName, ...] = (
    "session_jsonl",
    "telemetry",
    "watcher",
    "git",
    "bucket",
)
_SOURCE_VIEW: dict[str, CaptureViewName] = {
    "telemetry": "model_boundary",
    "session_jsonl": "harness",
    "watcher": "world_effects",
    "git": "world_effects",
    "bucket": "storage",
}
_OBSERVATIONAL_SOURCES = frozenset({"session_jsonl", "telemetry", "watcher", "git"})
_FINALIZER_OUTCOMES = {
    "finalized": "full",
    "partial": "partial",
    "unavailable": "missing",
}


def _finalizer_report_error(report: Any, invocation_nonce: str) -> str | None:
    """Validate the complete child-to-parent evidence envelope."""

    if not isinstance(report, dict):
        return "expected a JSON object"
    required = {
        "invocation_nonce",
        "status",
        "completeness",
        "evidence_refs",
        "limitations",
        "details",
    }
    missing = sorted(required - set(report))
    if missing:
        return f"missing fields: {', '.join(missing)}"
    if report["invocation_nonce"] != invocation_nonce:
        return "invocation nonce does not match the request"
    status = report["status"]
    if status not in _FINALIZER_OUTCOMES:
        return f"invalid status: {status!r}"
    completeness = report["completeness"]
    if completeness != _FINALIZER_OUTCOMES[status]:
        return f"status {status!r} cannot claim completeness {completeness!r}"
    evidence_refs = report["evidence_refs"]
    if not isinstance(evidence_refs, list) or not all(
        isinstance(ref, str) and ref for ref in evidence_refs
    ):
        return "evidence_refs must be a list of non-empty strings"
    limitations = report["limitations"]
    if not isinstance(limitations, list) or not all(
        isinstance(item, str) and item for item in limitations
    ):
        return "limitations must be a list of non-empty strings"
    if not isinstance(report["details"], dict):
        return "details must be an object"
    return None


def _read_bucket_companion(path: Path) -> tuple[list[Any], str | None]:
    """Read one canonical gzip JSONL companion without accepting partial rows."""

    rows: list[Any] = []
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    rows.append(json.loads(line))
    except (OSError, EOFError, json.JSONDecodeError, UnicodeError) as exc:
        return [], f"unreadable: {type(exc).__name__}"
    return rows, None


def _bucket_companion_content_error(
    rows: list[Any],
    *,
    family: str,
    trace_id: str,
    required: bool,
) -> str | None:
    """Validate canonical, family-exact, trace-scoped bucket companion rows."""

    if not rows:
        return "is empty despite canonical trace state" if required else None
    if family == "sources":
        # sources.jsonl.gz is currently an explicitly empty placeholder.  No
        # non-empty source-reference envelope has shipped, so accepting an
        # arbitrary JSON object here would manufacture unsupported evidence.
        return "contains unsupported non-empty source-reference rows"
    if family not in {"context", "trail"}:
        return f"has unknown family {family!r}"

    from ..core.context_tree.contract import CONTEXT_EVENT_TYPES
    from ..core.trails.models import TrailEvent, expected_event_id, payload_content_hash
    from ..core.trails.search_records import summary_search_touches_trace

    canonical_fields = set(TrailEvent.model_fields)
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != canonical_fields:
            return f"row {index} is not the exact canonical TrailEvent envelope"
        try:
            event = TrailEvent.model_validate(row)
        except (TypeError, ValueError) as exc:
            return f"row {index} is not a canonical TrailEvent: {type(exc).__name__}"
        is_context = event.event_type in CONTEXT_EVENT_TYPES
        if (family == "context") != is_context:
            return f"row {index} belongs to the wrong companion family"
        scoped = event.trace_id == trace_id or (
            family == "trail" and summary_search_touches_trace(event, trace_id)
        )
        if not scoped:
            return f"row {index} is not scoped to the current trace"
        if event.content_hash != payload_content_hash(event.payload):
            return f"row {index} payload content hash is invalid"
        if event.event_id != expected_event_id(event):
            return f"row {index} event identity is invalid"
    return None


def _finalizer_semantic_error(
    report: dict[str, Any],
    *,
    source: str,
    request: dict[str, Any],
    deadline: float,
) -> str | None:
    """Validate source truth only after the child envelope is well typed."""

    if report["status"] in {"partial", "unavailable"} and not report["limitations"]:
        return f"{report['status']} reports must name at least one limitation"

    evidence_paths = [Path(ref) for ref in report["evidence_refs"]]
    missing = [str(path) for path in evidence_paths if not path.is_file()]
    if missing:
        return f"referenced evidence does not exist: {', '.join(missing)}"
    if report["status"] == "unavailable":
        return None

    details = report["details"]
    report_trace_id = report.get("trace_id")
    requested_trace_id = request.get("trace_id")
    capture_root_raw = request.get("capture_root")
    if not isinstance(capture_root_raw, str) or not capture_root_raw:
        return "finalizer request did not pin the capture root"
    capture_root = Path(capture_root_raw).resolve()

    if source == "session_jsonl":
        details_trace_id = details.get("trace_id")
        if not isinstance(report_trace_id, str) or not report_trace_id:
            return "session finalizer did not name a trace_id"
        if details_trace_id != report_trace_id:
            return "session report trace identities disagree"
        requested_session_id = request.get("session_id")
        if requested_session_id and details.get("session_id") != requested_session_id:
            return "session report session_id does not match the request"
        session_path = request.get("session_path")
        if not session_path or not _path_is_referenced(Path(session_path), evidence_paths):
            return "session input is not named as evidence"
        trace_record_path = details.get("trace_record_path")
        pointer_path = details.get("trace_record_pointer_path")
        if not isinstance(trace_record_path, str) or not trace_record_path:
            return "session finalizer did not name its canonical trace record"
        if not isinstance(pointer_path, str) or not pointer_path:
            return "session finalizer did not name its canonical current pointer"
        if not _path_is_referenced(Path(trace_record_path), evidence_paths):
            return "canonical trace record is not named as evidence"
        if not _path_is_referenced(Path(pointer_path), evidence_paths):
            return "canonical current pointer is not named as evidence"
        from ..core._bucket_io import _digest_payload
        from ..core.bucket_models import (
            TRACE_RECORD_BUCKET_SCHEMA,
            TRACE_RECORD_POINTER_SCHEMA,
        )
        from ..core.bucket_store import read_trace_record_object
        from ..core.config import get_project_dir

        expected_project_slug = get_project_dir(Path(request["project"])).name
        expected_pointer = (
            capture_root
            / "bucket"
            / "objects"
            / "traces"
            / "v1"
            / expected_project_slug
            / report_trace_id
            / "current.json"
        ).resolve()
        if Path(pointer_path).resolve() != expected_pointer:
            return "canonical current pointer is not at the exact bucket layout path"
        try:
            pointer = json.loads(Path(pointer_path).read_text(encoding="utf-8"))
            envelope = json.loads(Path(trace_record_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"canonical trace record evidence is invalid: {type(exc).__name__}"
        if pointer.get("schema_version") != TRACE_RECORD_POINTER_SCHEMA:
            return "canonical current pointer has the wrong schema"
        if envelope.get("schema_version") != TRACE_RECORD_BUCKET_SCHEMA:
            return "canonical trace record has the wrong schema"
        record_hash = _digest_payload(envelope.get("record") or {})
        if envelope.get("record_hash") != record_hash or pointer.get("record_hash") != record_hash:
            return "canonical trace record hash does not match stored record bytes"
        expected_object = (
            capture_root / "bucket" / str(pointer.get("object_path") or "")
        ).resolve()
        canonical_object = (
            expected_pointer.parent / f"{record_hash.removeprefix('sha256:')}.json"
        ).resolve()
        if expected_object != canonical_object:
            return "canonical current pointer object is not at its content-addressed path"
        if expected_object != Path(trace_record_path).resolve():
            return "canonical current pointer names another trace record object"
        if (
            pointer.get("trace_id") != report_trace_id
            or envelope.get("trace_id") != report_trace_id
        ):
            return "canonical trace record envelope names another trace"
        if (
            pointer.get("project_slug") != expected_project_slug
            or envelope.get("project_slug") != expected_project_slug
        ):
            return "canonical trace record envelope names another project"
        record = read_trace_record_object(Path(trace_record_path))
        if record is None or record.record.trace_id != report_trace_id:
            return "canonical trace record is invalid or names another trace"
        if record.record.content_hash != record.record.compute_content_hash():
            return "canonical TraceRecord content_hash does not match its content"
        if not record.path.resolve().is_relative_to(capture_root / "bucket"):
            return "canonical trace record is outside the pinned capture root"
        if record.project_slug != expected_project_slug:
            return "canonical trace record names another project"
        if requested_session_id and record.record.session_id != requested_session_id:
            return "canonical trace record names another session"
        return None

    if source == "telemetry":
        if not isinstance(requested_trace_id, str) or not requested_trace_id:
            return "telemetry request did not name the current trace"
        if report_trace_id != requested_trace_id or details.get("trace_id") != requested_trace_id:
            return "telemetry report trace_id does not match the current trace"
        requested_session_id = request.get("session_id")
        if not requested_session_id or details.get("session_id") != requested_session_id:
            return "telemetry report session_id does not match the request"
        if len(evidence_paths) != 1:
            return "telemetry report must name exactly one staging snapshot"
        expected_snapshot = capture_root / "staging" / "otel" / f"{requested_session_id}.json"
        if evidence_paths[0].resolve() != expected_snapshot:
            return "telemetry snapshot is outside the pinned session staging path"
        try:
            snapshot = json.loads(evidence_paths[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"telemetry snapshot is invalid: {type(exc).__name__}"
        if not isinstance(snapshot, dict) or snapshot.get("session_id") != requested_session_id:
            return "telemetry snapshot does not match the requested session"
        open_details = request.get("open_details") or {}
        opened_at = open_details.get("opened_at_unix")
        last_envelope_at = snapshot.get("last_envelope_at")
        if not _is_number(opened_at) or not _is_number(last_envelope_at):
            return "telemetry freshness timestamps are not numeric"
        if float(last_envelope_at) < float(opened_at):
            return "telemetry snapshot predates Capture.open"
        generation = snapshot.get("snapshot_generation")
        accepted = snapshot.get("accepted_envelopes")
        if not _is_nonnegative_int(generation) or not _is_nonnegative_int(accepted):
            return "telemetry generation counters are not typed integers"
        if generation < 1 or generation != accepted:
            return "telemetry snapshot generation is incomplete"
        for key in ("last_envelope_at", "snapshot_generation", "accepted_envelopes"):
            if details.get(key) != snapshot.get(key):
                return f"telemetry report {key} disagrees with its snapshot"
        if open_details.get("ingress_quiesced") is True:
            if snapshot.get("snapshot_quiescent") is not True:
                return "telemetry snapshot is not quiescent after ingress shutdown"
            if details.get("snapshot_quiescent") is not True:
                return "telemetry report does not attest its quiescent snapshot"
        for key in (
            "layers_count",
            "nodes_count",
            "full_nodes",
            "approximated_nodes",
            "stub_nodes",
            "drafts_appended",
        ):
            if key in details and not _is_nonnegative_int(details[key]):
                return f"telemetry report {key} is not a nonnegative integer"
        return None

    if source == "watcher":
        open_details = request.get("open_details") or {}
        project = Path(request["project"]).resolve()
        workspace = Path(request["workspace"]).resolve()
        state_path = open_details.get("state_path")
        if project != workspace:
            return "watcher project and workspace roots differ"
        if not isinstance(state_path, str) or not state_path:
            return "watcher request did not pin its open-time state path"
        if len(evidence_paths) != 1 or evidence_paths[0].resolve() != Path(state_path).resolve():
            return "watcher report did not name the exact lifecycle state artifact"
        if details.get("observed_root") != str(Path(request["project"])):
            return "watcher report observed another project root"
        if details.get("declared_workspace") != str(Path(request["workspace"])):
            return "watcher report names another declared workspace"
        for key in ("poll_succeeded", "baseline_proven", "root_binding_proven"):
            if open_details.get(key) is not True:
                return f"watcher open-time {key} was not proven"
        if details.get("open_poll_succeeded") is not True:
            return "watcher report did not preserve open poll success"
        if details.get("open_baseline_proven") is not True:
            return "watcher report did not preserve open baseline proof"
        if details.get("open_baseline_initialized") != open_details.get("baseline_initialized"):
            return "watcher report changed the open baseline state"
        if details.get("final_baseline_initialized") is not False:
            return "watcher final drain reinitialized its baseline"
        if (
            details.get("final_state_present") is not True
            or details.get("state_path") != state_path
        ):
            return "watcher final state artifact is not the pinned open-time state"
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"watcher state artifact is invalid: {type(exc).__name__}"
        if state.get("schema_version") != "opentraces.fs_watcher.state.v1":
            return "watcher state artifact has the wrong schema"
        if Path(str(state.get("repo") or "")).resolve() != project:
            return "watcher state artifact names another project"
        if not isinstance(state.get("paths"), dict):
            return "watcher state artifact has invalid path state"
        for key in ("paths_seen", "observations", "mutations"):
            if not _is_nonnegative_int(details.get(key)):
                return f"watcher report {key} is not a nonnegative integer"
        reconciled = details.get("reconciled")
        if not isinstance(reconciled, dict) or not reconciled:
            return "watcher report did not include its reconciliation result"
        if not all(_is_nonnegative_int(value) for value in reconciled.values()):
            return "watcher reconciliation counters are not typed integers"
        return None

    if source == "git":
        from ..core.trails._git_subprocess import run_git_until
        from ..core.trails.event_log import EVENT_LOG_REF, read_events

        project = Path(request["project"]).resolve()
        if details.get("schema_version") != "opentraces.capture.git_finalization.v1":
            return "git report has the wrong finalization schema"
        if details.get("project") != str(project):
            return "git report names another project"
        if details.get("event_log_ref") != EVENT_LOG_REF:
            return "git report names another canonical event ref"
        maturation = details.get("maturation")
        if not isinstance(maturation, dict):
            return "git report did not include maturation evidence"
        for key in ("commits_considered", "searches_completed", "anchors_created"):
            if not _is_nonnegative_int(maturation.get(key)):
                return f"git maturation {key} is not a nonnegative integer"
        if not isinstance(maturation.get("errors"), list) or not all(
            isinstance(value, str) for value in maturation["errors"]
        ):
            return "git maturation errors are not a string list"
        if not isinstance(maturation.get("truncated"), bool):
            return "git maturation truncated flag is not boolean"
        if not isinstance(details.get("evidence_read_timed_out", False), bool):
            return "git evidence-read timeout flag is not boolean"
        head = run_git_until(
            ["rev-parse", "--verify", EVENT_LOG_REF],
            cwd=project,
            deadline=deadline,
        )
        evidence_read_timed_out = details.get("evidence_read_timed_out") is True
        evidence_deferred = (
            report["status"] == "partial"
            and details.get("event_log_head") is None
            and details.get("events_count") is None
            and (
                (
                    maturation["truncated"] is True
                    and any(
                        "timed out" in error.lower()
                        for error in maturation["errors"]
                    )
                )
                or evidence_read_timed_out
            )
        )
        if head.returncode != 0:
            if not (
                evidence_deferred
                or (
                    report["status"] == "partial"
                    and details.get("event_log_head") is None
                    and details.get("events_count") == 0
                )
            ):
                return "git report event-log head is not current"
        else:
            if not evidence_deferred and details.get("event_log_head") != head.stdout.strip():
                return "git report event-log head is not current"
            count_deferred = (
                report["status"] == "partial"
                and details.get("events_count") is None
                and (maturation["truncated"] is True or evidence_read_timed_out)
            )
            if not count_deferred:
                try:
                    events = read_events(
                        project,
                        verify=True,
                        deadline=deadline,
                    )
                except subprocess.TimeoutExpired:
                    raise
                except Exception as exc:  # noqa: BLE001 - fail-closed evidence validator
                    return f"git canonical event evidence is unreadable: {type(exc).__name__}"
                if not events or details.get("events_count") != len(events):
                    return "git report event count does not match readable canonical evidence"
        return None

    if source == "bucket":
        if not isinstance(requested_trace_id, str) or not requested_trace_id:
            return "bucket request did not name the current trace"
        if report_trace_id != requested_trace_id or details.get("trace_id") != requested_trace_id:
            return "bucket report trace_id does not match the current trace"
        from opentraces_schema import TraceRecord

        from ..core._bucket_io import _digest_payload
        from ..core.bucket_models import (
            BUCKET_MANIFEST_SCHEMA,
            TRACE_RECORD_BUCKET_SCHEMA,
            TRACE_RECORD_POINTER_SCHEMA,
        )
        from ..core.bucket_store import _load_manifest
        from ..core.config import get_project_dir

        project_slug = get_project_dir(Path(request["project"])).name
        bucket_root = capture_root / "bucket"
        base = bucket_root / "traces" / "v1" / project_slug / requested_trace_id
        expected = {
            "trace_path": base / "trace.json",
            "manifest_path": bucket_root / "manifest.json",
            "trace_record_pointer_path": (
                bucket_root
                / "objects"
                / "traces"
                / "v1"
                / project_slug
                / requested_trace_id
                / "current.json"
            ),
            "context_path": base / "context.jsonl.gz",
            "trail_path": base / "trail.jsonl.gz",
            "sources_path": base / "sources.jsonl.gz",
        }
        for key, path in expected.items():
            value = details.get(key)
            if not isinstance(value, str) or Path(value).resolve() != path.resolve():
                return f"bucket {key} is not at the exact canonical layout path"
            if not _path_is_referenced(path, evidence_paths):
                return f"bucket {key} is not named as evidence"
        trace_path = str(expected["trace_path"])
        manifest_path = str(expected["manifest_path"])
        bucket_root = capture_root / "bucket"
        if not Path(trace_path).resolve().is_relative_to(bucket_root):
            return "bucket trace projection is outside the pinned capture root"
        if Path(manifest_path).resolve() != bucket_root / "manifest.json":
            return "bucket manifest is outside the pinned capture root"
        try:
            trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            pointer = json.loads(expected["trace_record_pointer_path"].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"bucket evidence is invalid: {type(exc).__name__}"
        try:
            trace_record = TraceRecord.model_validate(trace)
        except (TypeError, ValueError) as exc:
            return f"bucket trace projection is not a canonical TraceRecord: {type(exc).__name__}"
        if trace_record.trace_id != requested_trace_id:
            return "bucket trace projection names another trace"
        if manifest.get("schema_version") != BUCKET_MANIFEST_SCHEMA or not any(
            isinstance(row, dict) and row.get("trace_id") == requested_trace_id
            for row in manifest.get("traces") or []
        ):
            return "bucket manifest does not contain the current trace"
        try:
            canonical_manifest = _load_manifest(expected["manifest_path"])
        except Exception as exc:  # noqa: BLE001 - fail-closed evidence validator
            return f"bucket manifest canonical reader rejected evidence: {type(exc).__name__}"
        if canonical_manifest is None:
            return "bucket manifest canonical reader found no manifest"
        if pointer.get("schema_version") != TRACE_RECORD_POINTER_SCHEMA:
            return "bucket current pointer has the wrong schema"
        object_path = (bucket_root / str(pointer.get("object_path") or "")).resolve()
        reported_object = details.get("trace_record_path")
        if not isinstance(reported_object, str) or Path(reported_object).resolve() != object_path:
            return "bucket report does not name the current trace record object"
        if not _path_is_referenced(object_path, evidence_paths):
            return "bucket current trace record object is not named as evidence"
        try:
            envelope = json.loads(object_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"bucket trace record object is invalid: {type(exc).__name__}"
        if envelope.get("schema_version") != TRACE_RECORD_BUCKET_SCHEMA:
            return "bucket trace record object has the wrong schema"
        record_hash = _digest_payload(envelope.get("record") or {})
        if pointer.get("record_hash") != record_hash or envelope.get("record_hash") != record_hash:
            return "bucket trace record hash does not match stored record bytes"
        canonical_object = (
            expected["trace_record_pointer_path"].parent
            / f"{record_hash.removeprefix('sha256:')}.json"
        ).resolve()
        if object_path != canonical_object:
            return "bucket current record object is not at its content-addressed path"
        if (
            pointer.get("trace_id") != requested_trace_id
            or envelope.get("trace_id") != requested_trace_id
            or pointer.get("project_slug") != project_slug
            or envelope.get("project_slug") != project_slug
        ):
            return "bucket current record pointer or envelope names another identity"
        if envelope.get("record") != trace_record.model_dump(mode="json"):
            return "bucket trace projection differs from its canonical current record"
        context_summary = trace_record.context_tree_summary or {}
        context_required = any(
            _is_nonnegative_int(context_summary.get(key)) and context_summary[key] > 0
            for key in ("node_count", "layer_count")
        )
        companion_requirements = {
            "context": context_required,
            "trail": bool(trace_record.patches),
            # The v2 envelope currently writes this as an empty placeholder.
            "sources": False,
        }
        for family, required in companion_requirements.items():
            key = f"{family}_path"
            rows, read_error = _read_bucket_companion(expected[key])
            if read_error is not None:
                return f"bucket {key} companion is {read_error}"
            content_error = _bucket_companion_content_error(
                rows,
                family=family,
                trace_id=requested_trace_id,
                required=required,
            )
            if content_error is not None:
                return f"bucket {key} companion {content_error}"
    return None


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _path_is_referenced(path: Path, evidence_paths: list[Path]) -> bool:
    expected = path.resolve()
    return any(candidate.resolve() == expected for candidate in evidence_paths)


@dataclass(frozen=True)
class CapturePlan:
    """One placement-neutral request for observed capture evidence."""

    project: Path
    workspace: Path
    placement: CapturePlacement
    requested_sources: tuple[CaptureSourceName, ...]
    required_sources: tuple[CaptureSourceName, ...] = ()
    observer_version: str = __version__
    product_under_test_version: str = __version__
    result_dir: Path | None = None
    actor: str | None = None
    session_id: str | None = None
    session_path: Path | None = None
    trace_id: str | None = None
    security_tools: tuple[str, ...] | None = None
    raw_body_retention: str = "delete"
    require_observer_separation: bool = False
    product_under_test_pid: int | None = None
    product_under_test_version_probe: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        requested = tuple(dict.fromkeys(self.requested_sources))
        required = tuple(dict.fromkeys(self.required_sources))
        unknown = (set(requested) | set(required)) - _KNOWN_SOURCES
        if unknown:
            raise ValueError(f"unknown capture sources: {sorted(unknown)}")
        if not set(required).issubset(requested):
            raise ValueError("required_sources must be included in requested_sources")
        if self.placement not in ("persistent", "leased"):
            raise ValueError("placement must be 'persistent' or 'leased'")
        if not self.observer_version.strip():
            raise ValueError("observer_version must be pinned")
        if not self.product_under_test_version.strip():
            raise ValueError("product_under_test_version must be pinned")
        security_tools = (
            None
            if self.security_tools is None
            else tuple(dict.fromkeys(str(name) for name in self.security_tools))
        )
        unknown_security_tools = set(security_tools or ()) - set(SECURITY_TOOL_NAMES)
        if unknown_security_tools:
            raise ValueError(
                f"unknown security tool(s): {', '.join(sorted(unknown_security_tools))}"
            )
        if self.product_under_test_pid is not None and self.product_under_test_pid <= 0:
            raise ValueError("product_under_test_pid must be positive")
        version_probe = tuple(str(part) for part in self.product_under_test_version_probe)
        if any(not part for part in version_probe):
            raise ValueError("product_under_test_version_probe cannot contain empty argv")
        object.__setattr__(self, "project", Path(self.project).resolve())
        object.__setattr__(self, "workspace", Path(self.workspace).resolve())
        object.__setattr__(self, "requested_sources", requested)
        object.__setattr__(self, "required_sources", required)
        object.__setattr__(self, "security_tools", security_tools)
        object.__setattr__(self, "product_under_test_version_probe", version_probe)
        if self.result_dir is not None:
            object.__setattr__(self, "result_dir", Path(self.result_dir).resolve())
        if self.session_path is not None:
            object.__setattr__(self, "session_path", Path(self.session_path).resolve())


@dataclass(frozen=True)
class CaptureBindings:
    """Values a placement owner injects into the observed actor."""

    env: dict[str, str]
    otlp_endpoint: str | None = None
    raw_bodies_dir: str | None = None
    watched_roots: tuple[str, ...] = ()
    hook_commands: tuple[tuple[str, ...], ...] = ()
    hook_commands_status: Literal["observed", "unavailable", "not_requested"] = "not_requested"
    hook_commands_reason: str | None = None


@dataclass(frozen=True)
class CaptureSourceResult:
    name: str
    view: CaptureViewName
    requested: bool
    required: bool
    status: SourceStatus
    completeness: Completeness
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    duration_ms: int = 0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CaptureViewResult:
    name: CaptureViewName
    completeness: Completeness
    methods: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class CaptureResult:
    """Frozen capture outcome.

    ``sources`` preserves the caller's ``CapturePlan.requested_sources`` order
    even though finalization runs in dependency order internally.
    """

    schema_version: str
    module_version: str
    placement: CapturePlacement
    completeness: Literal["complete", "partial"]
    observer_version: str
    product_under_test_version: str
    sources: tuple[CaptureSourceResult, ...]
    views: tuple[CaptureViewResult, ...]
    limitations: tuple[str, ...]
    trace_refs: tuple[str, ...]
    security: dict[str, Any]
    result_path: str
    provenance: dict[str, Any] = field(default_factory=dict)

    def source(self, name: str) -> CaptureSourceResult:
        for source in self.sources:
            if source.name == name:
                return source
        raise KeyError(name)

    def view(self, name: str) -> CaptureViewResult:
        for view in self.views:
            if view.name == name:
                return view
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        # JSON is the frozen representation; normalise tuple-valued dataclass
        # fields to their persisted list form so an in-memory result compares
        # byte-for-byte with the record read back from disk.
        return json.loads(json.dumps(asdict(self), sort_keys=True))


@dataclass
class _OwnedProcess:
    source: str
    process: subprocess.Popen[bytes]
    stdout: Any
    stderr: Any


class CaptureSession:
    """An open capture lifecycle with internally owned finalization."""

    def __init__(
        self,
        plan: CapturePlan,
        bindings: CaptureBindings,
        *,
        result_dir: Path,
        capture_root: Path,
        processes: dict[str, _OwnedProcess],
        open_limitations: dict[str, list[str]],
        open_details: dict[str, dict[str, Any]],
        resolved_security_tools: tuple[str, ...],
        security_configuration: Literal["configured", "explicit"],
    ) -> None:
        self.plan = plan
        self.bindings = bindings
        self._result_dir = result_dir
        self._capture_root = capture_root
        self._processes = processes
        self._open_limitations = open_limitations
        self._open_details = open_details
        self._resolved_security_tools = resolved_security_tools
        self._security_configuration = security_configuration
        self._finished: CaptureResult | None = None
        self._trace_refs = list([plan.trace_id] if plan.trace_id is not None else [])
        self._interrupted_sources: set[str] = set()

    def interrupt(self, source: str) -> bool:
        """Terminate an owned leased source for an explicit fault injection."""
        owned = self._processes.get(source)
        if owned is None or owned.process.poll() is not None:
            return False
        owned.process.terminate()
        try:
            owned.process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            owned.process.kill()
            owned.process.wait(timeout=2.0)
        self._interrupted_sources.add(source)
        return True

    def finish(self, deadline: float | None = None) -> CaptureResult:
        """Finalize every requested source without exceeding ``deadline``.

        ``deadline`` is an absolute :func:`time.monotonic` value.  A source that
        cannot settle inside the remaining budget is recorded as timed out; it
        is never inferred complete from the request.
        """
        if self._finished is not None:
            return self._finished
        if deadline is None:
            deadline = time.monotonic() + 30.0

        if "telemetry" in self.plan.requested_sources:
            self._quiesce_telemetry(deadline)

        finalized_by_name: dict[str, CaptureSourceResult] = {}
        requested = set(self.plan.requested_sources)
        ordered_sources = [source for source in _FINALIZATION_ORDER if source in requested]
        for index, source in enumerate(ordered_sources):
            if time.monotonic() >= deadline:
                finalized_by_name[source] = self._timed_out(
                    source, "finalization deadline exhausted"
                )
                continue
            sources_left = len(ordered_sources) - index
            remaining = max(0.0, deadline - time.monotonic())
            # Reserve a cold-start allowance for every later child. A silent
            # early adapter must not consume the whole budget and leave a
            # later source only a process-start timeout report.
            if sources_left == 1:
                source_budget = remaining
            elif remaining < 3.0:
                source_budget = remaining * 0.15
            else:
                source_budget = remaining / sources_left
            source_deadline = time.monotonic() + max(0.0, source_budget)
            if source == "telemetry":
                finalized = self._finalize_telemetry(source_deadline)
            else:
                finalized = self._run_isolated_finalizer(source, source_deadline)
            finalized_by_name[source] = finalized
            trace_id = finalized.details.get("trace_id")
            if trace_id and trace_id not in self._trace_refs:
                self._trace_refs.append(str(trace_id))

        source_results = [finalized_by_name[source] for source in self.plan.requested_sources]

        self._stop_processes(deadline)
        views = _summarize_views(source_results)
        observer_pid = os.getpid()
        (
            product_attestation,
            separation_proven,
            separation_limitations,
            observed_product_version,
        ) = _attest_product_process(
            pid=self.plan.product_under_test_pid,
            observer_pid=observer_pid,
            caller_version=self.plan.product_under_test_version,
            version_probe=self.plan.product_under_test_version_probe,
            workspace=self.plan.workspace,
            deadline=deadline,
        )
        has_observational_source = any(
            source.name in _OBSERVATIONAL_SOURCES for source in source_results
        )
        complete = (
            bool(source_results)
            and has_observational_source
            and all(source.completeness == "full" for source in source_results)
            and (not self.plan.require_observer_separation or separation_proven)
        )
        source_limitations = tuple(
            limitation for source in source_results for limitation in source.limitations
        )
        provenance = {
            "observer": {
                "version": __version__,
                "derivation": "runtime",
                "caller_claim": self.plan.observer_version,
                "pid": observer_pid,
                "executable": sys.executable,
            },
            "product_under_test": product_attestation,
            "separation": {
                "required": self.plan.require_observer_separation,
                "proven": separation_proven,
                "reason": (None if separation_proven else separation_limitations[0]),
            },
        }
        provenance_limitations = () if separation_proven else tuple(separation_limitations)
        declared_security = [
            source.details.get("capture_security_policy")
            for source in source_results
            if isinstance(source.details.get("capture_security_policy"), dict)
        ]
        security_observations = [
            source.details.get("security_observation")
            for source in source_results
            if isinstance(source.details.get("security_observation"), dict)
            and source.details["security_observation"].get("observed") is True
        ]
        configured_tools = (
            list(declared_security[0].get("configured_tools") or [])
            if declared_security
            else list(self._resolved_security_tools)
        )
        tools_applied = list(
            dict.fromkeys(
                str(tool)
                for observation in security_observations
                for tool in observation.get("tools_applied") or []
            )
        )
        security_limitations = (
            ()
            if security_observations
            else (
                "security applied-state observation unavailable; "
                "no sanitized TraceRecord was finalized",
            )
        )
        observation_limitations = (
            ()
            if has_observational_source
            else ("no observational capture source was requested; storage alone proves custody",)
        )
        limitations = (
            *source_limitations,
            *provenance_limitations,
            *security_limitations,
            *observation_limitations,
        )
        result_path = self._result_dir / "capture_result.json"
        result = CaptureResult(
            schema_version=CAPTURE_RESULT_SCHEMA,
            module_version=__version__,
            placement=self.plan.placement,
            completeness="complete" if complete else "partial",
            observer_version=__version__,
            product_under_test_version=(
                observed_product_version or self.plan.product_under_test_version
            ),
            sources=tuple(source_results),
            views=tuple(views),
            limitations=limitations,
            trace_refs=tuple(self._trace_refs),
            security={
                "configuration": (
                    declared_security[0].get("configuration", "configured")
                    if declared_security
                    else self._security_configuration
                ),
                "configured_tools": configured_tools,
                "tools_applied": tools_applied,
                # Backward-compatible alias; its value now comes from the
                # observed record rather than from enabled configuration.
                "effective_tools": tools_applied,
                "observed": bool(security_observations),
                "raw_body_retention": self.plan.raw_body_retention,
            },
            provenance=provenance,
            result_path=str(result_path),
        )
        _atomic_write_json(result_path, result.to_dict())
        self._finished = result
        return result

    def _finalize_telemetry(self, deadline: float) -> CaptureSourceResult:
        started = time.monotonic()
        limitations = list(self._open_limitations.get("telemetry", ()))
        owned = self._processes.get("telemetry")
        if "telemetry" in self._interrupted_sources:
            limitations.append("source process exited after explicit interruption")
            return CaptureSourceResult(
                name="telemetry",
                view="model_boundary",
                requested=True,
                required="telemetry" in self.plan.required_sources,
                status="unavailable",
                completeness="missing",
                limitations=tuple(limitations),
                duration_ms=_duration_ms(started),
            )
        if owned is not None and owned.process.poll() is not None and owned.process.returncode != 0:
            limitations.append(f"source process exited with code {owned.process.returncode}")
            return CaptureSourceResult(
                name="telemetry",
                view="model_boundary",
                requested=True,
                required="telemetry" in self.plan.required_sources,
                status="unavailable",
                completeness="missing",
                limitations=tuple(limitations),
                duration_ms=_duration_ms(started),
            )
        if limitations:
            return CaptureSourceResult(
                name="telemetry",
                view="model_boundary",
                requested=True,
                required="telemetry" in self.plan.required_sources,
                status="unavailable",
                completeness="missing",
                limitations=tuple(limitations),
                duration_ms=_duration_ms(started),
            )
        if time.monotonic() >= deadline:
            return self._timed_out("telemetry", "telemetry finalization timed out")
        finalized = self._run_isolated_finalizer("telemetry", deadline)
        return CaptureSourceResult(
            name=finalized.name,
            view=finalized.view,
            requested=finalized.requested,
            required=finalized.required,
            status=finalized.status,
            completeness=finalized.completeness,
            evidence_refs=finalized.evidence_refs,
            limitations=finalized.limitations,
            duration_ms=_duration_ms(started),
            details=finalized.details,
        )

    def _quiesce_telemetry(self, deadline: float) -> None:
        owned = self._processes.get("telemetry")
        if owned is None or owned.process.poll() is not None:
            return
        owned.process.terminate()
        try:
            owned.process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            owned.process.kill()
            remaining = max(0.0, deadline - time.monotonic())
            if remaining > 0:
                try:
                    owned.process.wait(timeout=remaining)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._open_limitations.setdefault("telemetry", []).append(
                "telemetry receiver could not drain before the finalization deadline"
            )
        self._open_details.setdefault("telemetry", {})["ingress_quiesced"] = True

    def _run_isolated_finalizer(
        self,
        source: str,
        deadline: float,
    ) -> CaptureSourceResult:
        started = time.monotonic()
        finalizers = self._result_dir / "finalizers"
        finalizers.mkdir(parents=True, exist_ok=True)
        invocation_nonce = secrets.token_hex(16)
        request_path = finalizers / f"{source}.{invocation_nonce}.request.json"
        report_path = finalizers / f"{source}.{invocation_nonce}.report.json"
        request = {
            "invocation_nonce": invocation_nonce,
            "source": source,
            "placement": self.plan.placement,
            "project": str(self.plan.project),
            "workspace": str(self.plan.workspace),
            "actor": self.plan.actor,
            "session_id": self.plan.session_id,
            "session_path": str(self.plan.session_path) if self.plan.session_path else None,
            "trace_id": self._trace_refs[-1] if self._trace_refs else None,
            "remaining_seconds": max(0.0, deadline - time.monotonic() - 0.2),
            "deadline_monotonic": max(time.monotonic(), deadline - 0.2),
            "security_tools": list(self._resolved_security_tools),
            "security_configuration": self._security_configuration,
            "raw_body_retention": self.plan.raw_body_retention,
            "capture_root": str(self._capture_root),
            "open_details": self._open_details.get(source) or {},
            "requested_sources": list(self.plan.requested_sources),
        }
        _atomic_write_json(request_path, request)
        stdout = (finalizers / f"{source}.{invocation_nonce}.stdout").open("xb")
        stderr = (finalizers / f"{source}.{invocation_nonce}.stderr").open("xb")
        env = dict(os.environ)
        env["OT_OPENTRACES_DIR"] = str(self._capture_root)
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "opentraces.capture._source_finalizer",
                    "--request",
                    str(request_path),
                    "--report",
                    str(report_path),
                ],
                cwd=self.plan.workspace,
                env=env,
                stdout=stdout,
                stderr=stderr,
                start_new_session=os.name == "posix",
            )
        except OSError as exc:
            stdout.close()
            stderr.close()
            return self._unavailable(
                source,
                f"source finalizer launch failed: {type(exc).__name__}: {exc}",
            )
        try:
            remaining = max(0.0, deadline - time.monotonic())
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_finalizer_process_tree(process)
            return CaptureSourceResult(
                name=source,
                view=_SOURCE_VIEW[source],
                requested=True,
                required=source in self.plan.required_sources,
                status="timed_out",
                completeness="missing",
                evidence_refs=(request_path.relative_to(self._result_dir).as_posix(),),
                limitations=(self._finalizer_timeout_limitation(source),),
                duration_ms=_duration_ms(started),
            )
        finally:
            stdout.close()
            stderr.close()
        refs = (
            request_path.relative_to(self._result_dir).as_posix(),
            report_path.relative_to(self._result_dir).as_posix(),
        )
        if not report_path.is_file():
            return self._unavailable(
                source,
                f"source finalizer exited {process.returncode} without a report",
                evidence_refs=refs[:1],
            )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return self._unavailable(
                source,
                f"invalid finalizer report: {type(exc).__name__}: {exc}",
                evidence_refs=refs,
            )
        report_error = _finalizer_report_error(report, invocation_nonce)
        if report_error is not None:
            return self._unavailable(
                source,
                f"invalid finalizer report: {report_error}",
                evidence_refs=refs,
            )
        try:
            semantic_error = _finalizer_semantic_error(
                report,
                source=source,
                request=request,
                deadline=deadline,
            )
        except subprocess.TimeoutExpired as exc:
            from ..core.trails._git_subprocess import timeout_limitation

            report_refs = (*refs, *report["evidence_refs"])
            limitations = [*report["limitations"], timeout_limitation(exc)]
            limitations.append(
                f"{source} finalizer evidence validation reached the capture "
                "deadline; no post-deadline validation re-read was attempted"
            )
            return CaptureSourceResult(
                name=source,
                view=_SOURCE_VIEW[source],
                requested=True,
                required=source in self.plan.required_sources,
                status="partial",
                completeness="partial",
                evidence_refs=report_refs,
                limitations=tuple(dict.fromkeys(limitations)),
                duration_ms=_duration_ms(started),
                details=dict(report["details"]),
            )
        if semantic_error is not None:
            return self._unavailable(
                source,
                f"invalid {source} finalizer evidence: {semantic_error}",
                evidence_refs=refs,
            )
        report_refs = (*refs, *report["evidence_refs"])
        if process.returncode != 0:
            detail = "; ".join(report["limitations"])
            suffix = f": {detail}" if detail else ""
            return self._unavailable(
                source,
                f"source finalizer exited {process.returncode}{suffix}",
                evidence_refs=report_refs,
                details=dict(report["details"]),
            )
        return CaptureSourceResult(
            name=source,
            view=_SOURCE_VIEW[source],
            requested=True,
            required=source in self.plan.required_sources,
            status=report["status"],
            completeness=report["completeness"],
            evidence_refs=report_refs,
            limitations=tuple(report["limitations"]),
            duration_ms=_duration_ms(started),
            details=dict(report["details"]),
        )

    def _finalizer_timeout_limitation(self, source: str) -> str:
        """Retain known source truth when an isolated child misses its budget."""

        generic = f"{source} source finalizer exceeded the capture deadline"
        if source != "telemetry" or not self.plan.session_id:
            return generic
        snapshot_path = self._capture_root / "staging" / "otel" / f"{self.plan.session_id}.json"
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return generic
        opened_at = float(self._open_details.get("telemetry", {}).get("opened_at_unix") or 0.0)
        raw_last = snapshot.get("last_envelope_at")
        last_envelope_at = float(raw_last) if raw_last is not None else None
        if last_envelope_at is None or last_envelope_at < opened_at:
            return "telemetry receiver produced no fresh session snapshot after Capture.open"
        ingress_quiesced = self._open_details.get("telemetry", {}).get("ingress_quiesced") is True
        if ingress_quiesced and snapshot.get("snapshot_quiescent") is not True:
            return "telemetry snapshot is not a quiescent finish-time generation"
        return generic

    def _unavailable(
        self,
        source: str,
        reason: str,
        *,
        evidence_refs: tuple[str, ...] = (),
        details: dict[str, Any] | None = None,
    ) -> CaptureSourceResult:
        return CaptureSourceResult(
            name=source,
            view=_SOURCE_VIEW[source],
            requested=True,
            required=source in self.plan.required_sources,
            status="unavailable",
            completeness="missing",
            evidence_refs=evidence_refs,
            limitations=(reason,),
            details=dict(details or {}),
        )

    def _timed_out(self, source: str, reason: str) -> CaptureSourceResult:
        return CaptureSourceResult(
            name=source,
            view=_SOURCE_VIEW[source],
            requested=True,
            required=source in self.plan.required_sources,
            status="timed_out",
            completeness="missing",
            limitations=(reason,),
        )

    def _stop_processes(self, deadline: float) -> None:
        for owned in self._processes.values():
            try:
                if owned.process.poll() is None:
                    owned.process.terminate()
                    remaining = max(0.0, deadline - time.monotonic())
                    owned.process.wait(timeout=min(remaining, 1.0))
            except (OSError, subprocess.TimeoutExpired):
                if owned.process.poll() is None:
                    owned.process.kill()
            finally:
                owned.stdout.close()
                owned.stderr.close()


class Capture:
    """Public entry point for the portable capture lifecycle."""

    @staticmethod
    def open(plan: CapturePlan) -> CaptureSession:
        lifecycle_opened_at = time.time()
        if not plan.project.is_dir():
            raise FileNotFoundError(f"capture project does not exist: {plan.project}")
        if not plan.workspace.is_dir():
            raise FileNotFoundError(f"capture workspace does not exist: {plan.workspace}")

        if plan.security_tools is None:
            from ..core.config import load_config
            from ..security.config import enabled_security_tool_names

            resolved_security_tools = tuple(enabled_security_tool_names(load_config()))
            security_configuration: Literal["configured", "explicit"] = "configured"
        else:
            resolved_security_tools = plan.security_tools
            security_configuration = "explicit"

        result_dir = plan.result_dir or (plan.workspace / ".opentraces" / "capture")
        result_dir.mkdir(parents=True, exist_ok=True)
        if plan.placement == "persistent":
            from ..core import paths as capture_paths

            capture_root = Path(capture_paths.OPENTRACES_DIR).resolve()
            raw_bodies = capture_paths.raw_bodies_dir()
        else:
            capture_root = result_dir / "runtime"
            raw_bodies = capture_root / "raw-bodies"
        capture_root.mkdir(parents=True, exist_ok=True)
        raw_bodies.mkdir(parents=True, exist_ok=True)

        env = {"OT_OPENTRACES_DIR": str(capture_root)}
        endpoint: str | None = None
        processes: dict[str, _OwnedProcess] = {}
        limitations: dict[str, list[str]] = {}
        open_details: dict[str, dict[str, Any]] = {}
        if "telemetry" in plan.requested_sources:
            open_details["telemetry"] = {
                "opened_at_unix": lifecycle_opened_at,
            }
            if plan.placement == "leased":
                endpoint, owned, reason = _start_leased_receiver(
                    capture_root=capture_root,
                    raw_bodies=raw_bodies,
                    result_dir=result_dir,
                )
                if owned is not None:
                    processes["telemetry"] = owned
                if reason is not None:
                    limitations.setdefault("telemetry", []).append(reason)
            else:
                endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
            env.update(
                {
                    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
                    "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
                    "OTEL_LOGS_EXPORTER": "otlp",
                    "OTEL_METRICS_EXPORTER": "otlp",
                    "OTEL_TRACES_EXPORTER": "otlp",
                    "OTEL_LOG_RAW_API_BODIES": f"file:{raw_bodies}",
                }
            )

        if "watcher" in plan.requested_sources:
            from .fs_watcher.runtime import poll_project_once

            watcher_state_path = result_dir / "runtime" / "watcher-state.json"
            root_details = {
                "observed_root": str(plan.project),
                "declared_workspace": str(plan.workspace),
            }
            if plan.workspace != plan.project:
                open_details["watcher"] = {
                    **root_details,
                    "state_path": str(watcher_state_path),
                    "poll_succeeded": False,
                    "baseline_proven": False,
                    "root_binding_proven": False,
                }
                limitations.setdefault("watcher", []).append(
                    "declared workspace differs from the watcher observed root"
                )
            else:
                try:
                    baseline = poll_project_once(
                        plan.project,
                        state_path=watcher_state_path,
                        exclude_paths=[plan.session_path] if plan.session_path else None,
                        writer="portable-capture-watcher",
                    )
                    baseline_proven = watcher_state_path.is_file()
                    open_details["watcher"] = {
                        **root_details,
                        "state_path": str(watcher_state_path),
                        "poll_succeeded": True,
                        "baseline_proven": baseline_proven,
                        "baseline_initialized": baseline.baseline_initialized,
                        "paths_seen": baseline.paths_seen,
                        "skipped_paths": baseline.skipped_paths,
                        "root_binding_proven": True,
                    }
                    if not baseline_proven:
                        limitations.setdefault("watcher", []).append(
                            "watcher baseline could not be established at Capture.open"
                        )
                except Exception as exc:  # noqa: BLE001 - persisted honesty boundary
                    open_details["watcher"] = {
                        **root_details,
                        "state_path": str(watcher_state_path),
                        "poll_succeeded": False,
                        "baseline_proven": False,
                        "root_binding_proven": True,
                    }
                    limitations.setdefault("watcher", []).append(
                        f"watcher baseline failed at Capture.open: {type(exc).__name__}: {exc}"
                    )

        hook_commands, hook_status, hook_reason = _hook_bindings(plan)
        bindings = CaptureBindings(
            env=env,
            otlp_endpoint=endpoint,
            raw_bodies_dir=str(raw_bodies),
            watched_roots=(str(plan.project),),
            hook_commands=hook_commands,
            hook_commands_status=hook_status,
            hook_commands_reason=hook_reason,
        )
        return CaptureSession(
            plan,
            bindings,
            result_dir=result_dir,
            capture_root=capture_root,
            processes=processes,
            open_limitations=limitations,
            open_details=open_details,
            resolved_security_tools=resolved_security_tools,
            security_configuration=security_configuration,
        )


def _start_leased_receiver(
    *,
    capture_root: Path,
    raw_bodies: Path,
    result_dir: Path,
) -> tuple[str, _OwnedProcess | None, str | None]:
    port = _free_port()
    endpoint = f"http://127.0.0.1:{port}"
    logs = result_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stdout = (logs / "telemetry.stdout").open("ab")
    stderr = (logs / "telemetry.stderr").open("ab")
    env = dict(os.environ)
    env["OT_OPENTRACES_DIR"] = str(capture_root)
    command = [
        sys.executable,
        "-m",
        "opentraces.capture._leased_receiver",
        "--port",
        str(port),
        "--raw-bodies-dir",
        str(raw_bodies),
    ]
    try:
        process = subprocess.Popen(
            command,
            cwd=capture_root,
            env=env,
            stdout=stdout,
            stderr=stderr,
        )
    except OSError as exc:
        stdout.close()
        stderr.close()
        return endpoint, None, f"receiver launch failed: {type(exc).__name__}: {exc}"
    owned = _OwnedProcess("telemetry", process, stdout, stderr)
    if not _wait_for_health(endpoint, process, timeout=3.0):
        return endpoint, owned, "receiver did not become healthy"
    return endpoint, owned, None


def _hook_bindings(
    plan: CapturePlan,
) -> tuple[
    tuple[tuple[str, ...], ...],
    Literal["observed", "unavailable", "not_requested"],
    str | None,
]:
    if "session_jsonl" not in plan.requested_sources:
        return (), "not_requested", None
    actor = plan.actor or "claude-code"
    if plan.placement != "persistent":
        return (), "unavailable", "leased hook integration was not observed"
    if actor != "claude-code":
        return (), "unavailable", f"persistent hook observation is unavailable for {actor}"
    from .claude_code.install import registered_hook_commands

    observed = registered_hook_commands()
    if not observed:
        return (), "unavailable", "Claude Code OpenTraces hooks are not installed"
    return tuple(tuple(shlex.split(command)) for command in observed), "observed", None


def _wait_for_health(
    endpoint: str,
    process: subprocess.Popen[bytes],
    *,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with urlopen(f"{endpoint}/health", timeout=0.2) as response:  # noqa: S310
                return response.status == 200
        except (OSError, URLError):
            time.sleep(0.03)
    return False


def _summarize_views(
    sources: list[CaptureSourceResult],
) -> list[CaptureViewResult]:
    rows: list[CaptureViewResult] = []
    for name in ("model_boundary", "harness", "world_effects", "storage"):
        members = [source for source in sources if source.view == name]
        if not members:
            completeness: Completeness = "missing"
        elif all(source.completeness == "full" for source in members):
            completeness = "full"
        elif any(source.completeness in ("full", "partial") for source in members):
            completeness = "partial"
        else:
            completeness = "missing"
        rows.append(
            CaptureViewResult(
                name=name,  # type: ignore[arg-type]
                completeness=completeness,
                methods=tuple(source.name for source in members),
                evidence_refs=tuple(ref for source in members for ref in source.evidence_refs),
                limitations=tuple(
                    limitation for source in members for limitation in source.limitations
                ),
            )
        )
    return rows


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _duration_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _file_identity(
    path: Path | None,
    *,
    deadline: float,
) -> dict[str, str] | None:
    """Resolve and hash a file without outliving the capture deadline."""

    remaining = max(0.0, deadline - time.monotonic())
    if path is None or remaining <= 0:
        return None
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import hashlib,json,sys; from pathlib import Path; "
                    "p=Path(sys.argv[1]).resolve(strict=True); "
                    "h=hashlib.sha256(); f=p.open('rb'); "
                    "[h.update(c) for c in iter(lambda: f.read(1048576), b'')]; "
                    "f.close(); print(json.dumps({'path':str(p),'digest':'sha256:'+h.hexdigest()}))"
                ),
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=remaining,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        identity = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(identity, dict):
        return None
    identity_path = identity.get("path")
    digest = identity.get("digest")
    if not isinstance(identity_path, str) or not isinstance(digest, str):
        return None
    return {"path": identity_path, "digest": digest}


def _command_path(command: str, *, workspace: Path) -> Path | None:
    candidate: str | None
    if os.sep in command:
        path = Path(command).expanduser()
        candidate = str(path if path.is_absolute() else workspace / path)
    else:
        candidate = shutil.which(command)
    if candidate is None:
        return None
    try:
        return Path(candidate).resolve(strict=True)
    except OSError:
        return None


def _launcher_runtime_command(launcher: Path) -> Path:
    """Return the interpreter command named by a simple shebang launcher."""

    try:
        with launcher.open("rb") as handle:
            first_line = handle.readline(4096).decode("utf-8", errors="replace")
    except OSError:
        return launcher
    if not first_line.startswith("#!"):
        return launcher
    try:
        words = shlex.split(first_line[2:].strip())
    except ValueError:
        return launcher
    if not words:
        return launcher
    interpreter = words[0]
    if Path(interpreter).name == "env":
        candidates = [word for word in words[1:] if not word.startswith("-")]
        if not candidates:
            return launcher
        resolved = shutil.which(candidates[0])
        return Path(resolved) if resolved else launcher
    return Path(interpreter).expanduser()


def _launcher_runtime_path(launcher: Path) -> Path:
    """Resolve the OS executable for a binary or a simple shebang launcher."""

    command = _launcher_runtime_command(launcher)
    try:
        return command.resolve(strict=True)
    except OSError:
        return launcher


def _observe_process_identity(
    pid: int | None,
    *,
    deadline: float,
) -> dict[str, Any]:
    alive = _pid_is_alive(pid)
    observed: dict[str, Any] = {
        "pid": pid,
        "pid_alive": alive,
        "command": None,
        "executable": None,
    }
    if pid is None or not alive or time.monotonic() >= deadline:
        return observed

    executable_path: Path | None = None
    proc_executable = Path(f"/proc/{pid}/exe")
    try:
        if time.monotonic() < deadline and proc_executable.exists():
            executable_path = Path(os.readlink(proc_executable)).resolve()
    except OSError:
        executable_path = None

    proc_command = Path(f"/proc/{pid}/cmdline")
    command: str | None = None
    try:
        if time.monotonic() < deadline and proc_command.is_file():
            argv = [
                part.decode("utf-8", errors="replace")
                for part in proc_command.read_bytes().split(b"\0")
                if part
            ]
            command = shlex.join(argv) if argv else None
    except OSError:
        command = None

    if executable_path is None and time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            completed = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                check=False,
                timeout=remaining,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0 and completed.stdout.strip():
            raw_executable = completed.stdout.strip()
            candidate = raw_executable if os.sep in raw_executable else shutil.which(raw_executable)
            try:
                executable_path = (
                    Path(candidate).resolve(strict=True) if candidate is not None else None
                )
            except OSError:
                executable_path = None
    if command is None and time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            completed = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
                timeout=remaining,
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
        if completed is not None and completed.returncode == 0 and completed.stdout.strip():
            command = completed.stdout.strip()

    observed["command"] = command
    observed["executable"] = _file_identity(executable_path, deadline=deadline)
    return observed


def _command_executes_launcher(
    command: str | None,
    *,
    launcher: Path,
    runtimes: tuple[Path, ...],
) -> bool:
    """Prove the launcher occupies the executable/script slot in argv."""

    if not command:
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    if not words:
        return False

    def matches(word: str, expected: Path) -> bool:
        candidate = word if os.sep in word else shutil.which(word)
        if candidate is None:
            return False
        try:
            return Path(candidate).resolve(strict=True) == expected
        except OSError:
            return False

    if matches(words[0], launcher):
        return True
    # A shebang script is represented by the interpreter followed immediately
    # by the executed script.  A later occurrence is data (for example the
    # inert argv after ``python -c``) and cannot bind process identity.
    return (
        len(words) >= 2
        and any(matches(words[0], runtime) for runtime in runtimes)
        and matches(words[1], launcher)
    )


def _deterministic_probe_environment() -> tuple[dict[str, str], dict[str, Any]]:
    """Return the pinned minimal environment used for product-side probes."""

    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
    }
    encoded = json.dumps(
        environment,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return environment, {
        "policy": "deterministic_minimal_v1",
        "digest": f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        "keys": sorted(environment),
    }


def _declared_python_runtime_matches_observed(
    runtime_command: Path,
    observed_executable: dict[str, str] | None,
    *,
    deadline: float,
) -> bool:
    """Independently bind a Python shebang to its live OS executable."""

    if (
        observed_executable is None
        or "python" not in runtime_command.name.lower()
        or time.monotonic() >= deadline
    ):
        return False
    try:
        witness_env, _environment_record = _deterministic_probe_environment()
        witness = subprocess.Popen(
            [
                str(runtime_command),
                "-c",
                "import time; time.sleep(1)",
            ],
            env=witness_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    try:
        time.sleep(min(0.03, max(0.0, deadline - time.monotonic())))
        identity = _observe_process_identity(
            witness.pid,
            deadline=deadline,
        ).get("executable")
        return bool(identity and identity == observed_executable)
    finally:
        if witness.poll() is None:
            witness.terminate()
            remaining = max(0.0, deadline - time.monotonic())
            try:
                witness.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                witness.kill()
                remaining = max(0.0, deadline - time.monotonic())
                if remaining > 0:
                    try:
                        witness.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        pass


_VERSION_RE = re.compile(r"(?<![A-Za-z0-9])\d+\.\d+\.\d+(?:[A-Za-z0-9.+-]*)")


def _attest_product_process(
    *,
    pid: int | None,
    observer_pid: int,
    caller_version: str,
    version_probe: tuple[str, ...],
    workspace: Path,
    deadline: float,
) -> tuple[dict[str, Any], bool, list[str], str | None]:
    observed = _observe_process_identity(pid, deadline=deadline)
    limitations: list[str] = []
    if pid is None:
        limitations.append("non-self-observation separation has no product process identity")
    elif not observed["pid_alive"]:
        limitations.append("product process is not alive at capture finalization")
    elif pid == observer_pid:
        limitations.append("observer and product process use the same pid")
    if observed["executable"] is None:
        limitations.append("product executable identity could not be observed")
    if observed["command"] is None:
        limitations.append("product process command identity could not be observed")

    probe_record: dict[str, Any] = {
        "argv": list(version_probe),
        "declared_argv": list(version_probe),
        "execution": "declared_launcher",
        "status": "unavailable",
        "returncode": None,
        "version": None,
        "executable": None,
    }
    probe_env, environment_record = _deterministic_probe_environment()
    probe_record["environment"] = environment_record
    launcher_identity: dict[str, str] | None = None
    observed_version: str | None = None
    identity_bound = False
    if not version_probe:
        limitations.append("product version probe was not provided")
    else:
        launcher_path = _command_path(version_probe[0], workspace=workspace)
        launcher_identity = _file_identity(launcher_path, deadline=deadline)
        if launcher_path is None or launcher_identity is None:
            limitations.append("product version probe executable could not be resolved")
        else:
            runtime_command = _launcher_runtime_command(launcher_path)
            runtime_path = _launcher_runtime_path(launcher_path)
            runtime_identity = _file_identity(runtime_path, deadline=deadline)
            probe_record["executable"] = runtime_identity
            observed_executable = observed["executable"]
            native_launcher_matches = bool(
                observed_executable
                and observed_executable["digest"] == launcher_identity["digest"]
                and observed_executable["path"] == launcher_identity["path"]
            )
            executed_launcher = _command_executes_launcher(
                observed["command"],
                launcher=launcher_path,
                runtimes=(
                    runtime_path,
                    *((Path(observed_executable["path"]),) if observed_executable else ()),
                ),
            )
            identity_bound = native_launcher_matches or executed_launcher
            if not identity_bound:
                limitations.append(
                    "product executable process is not bound to the executed launcher "
                    "used by the version probe"
                )

            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                limitations.append("product version probe deadline was exhausted")
            else:
                probe_argv = list(version_probe)
                if (
                    executed_launcher
                    and runtime_path != launcher_path
                    and observed_executable is not None
                ):
                    runtime_matches = _declared_python_runtime_matches_observed(
                        runtime_command,
                        observed_executable,
                        deadline=deadline,
                    )
                    probe_record["runtime_relationship"] = (
                        "independently_attested" if runtime_matches else "unproven"
                    )
                    if runtime_matches:
                        probe_argv = [
                            str(runtime_command),
                            str(launcher_path),
                            *version_probe[1:],
                        ]
                        probe_record["execution"] = "independently_attested_runtime_launcher"
                        probe_record["executable"] = runtime_identity
                    else:
                        probe_argv = [
                            observed_executable["path"],
                            str(launcher_path),
                            *version_probe[1:],
                        ]
                        probe_record["execution"] = "observed_runtime_launcher"
                        probe_record["executable"] = observed_executable
                    probe_record["argv"] = probe_argv
                remaining = max(0.0, deadline - time.monotonic())
                if remaining <= 0:
                    limitations.append("product version probe deadline was exhausted")
                    completed = None
                else:
                    try:
                        completed = subprocess.run(
                            probe_argv,
                            cwd=workspace,
                            env=probe_env,
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=min(5.0, remaining),
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        completed = None
                        limitations.append(f"product version probe failed: {type(exc).__name__}")
                if completed is not None:
                    probe_record["returncode"] = completed.returncode
                    match = _VERSION_RE.search(f"{completed.stdout}\n{completed.stderr}")
                    observed_version = match.group(0) if match else None
                    probe_record["version"] = observed_version
                    if completed.returncode != 0:
                        limitations.append(f"product version probe exited {completed.returncode}")
                    elif observed_version is None:
                        limitations.append("product version probe emitted no version")
                    else:
                        probe_record["status"] = "observed"
                        if observed_version != caller_version:
                            limitations.append(
                                "product version probe mismatch: "
                                f"observed {observed_version}, caller claimed {caller_version}"
                            )

    proven = bool(
        pid is not None
        and observed["pid_alive"]
        and pid != observer_pid
        and observed["executable"] is not None
        and observed["command"] is not None
        and identity_bound
        and probe_record["status"] == "observed"
        and observed_version == caller_version
    )
    if not proven and not limitations:
        limitations.append("non-self-observation separation is not proven for this capture")
    provenance = {
        "version": observed_version or caller_version,
        "caller_claim": caller_version,
        "derivation": (
            "runtime_attestation"
            if proven
            else ("runtime_attestation_unproven" if pid is not None else "caller_claim")
        ),
        "pid": pid,
        "pid_alive": observed["pid_alive"],
        "command": observed["command"],
        "executable": observed["executable"],
        "launcher": launcher_identity,
        "version_probe": probe_record,
    }
    return provenance, proven, limitations, observed_version


def _pid_is_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_finalizer_process_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill an isolated finalizer and every subprocess it launched."""

    killed_group = False
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            killed_group = True
        except (OSError, AttributeError):
            pass
    if not killed_group:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=0.5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)
