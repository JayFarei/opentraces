"""Portable Trace Workspace and snapshot resume helpers."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord

from ...core.config import (
    get_project_dir,
    get_project_traces_dir,
    get_projects_path,
    load_config,
    project_is_opted_in,
)
from ...core.repo_identity import encode_claude_path
from ...core.state import StateManager
from .event_log import EVENT_LOG_REF, read_events
from .ids import normalize_id
from .models import TrailEvent
from .slices import trace_slice_for_event

TRACE_WORKSPACE_SCHEMA_VERSION = "opentraces.trace_workspace.v1"
TRACE_PLAY_SCHEMA_VERSION = "opentraces.trail_play.v1"
SNAPSHOT_REWIND_SCHEMA_VERSION = "opentraces.snapshot_rewind.v1"
SNAPSHOT_RESUME_SCHEMA_VERSION = "opentraces.snapshot_resume.v1"
WORKSPACE_META_FILENAME = ".opentraces-trace-workspace.json"

NON_PORTABLE_DEPENDENCIES = [
    "ignored_files_not_captured",
    "external_services_not_captured",
    "environment_variables_not_captured",
    "secrets_not_captured",
    "databases_not_captured",
    "caches_not_captured",
    "running_processes_not_captured",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _git(
    repo: Path,
    args: list[str],
    *,
    input: str | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        input=input,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def _object_type(repo: Path, oid_hex: str) -> str | None:
    out = _git(repo, ["cat-file", "-t", oid_hex], check=False)
    return out or None


def _workspace_meta(repo: Path) -> dict[str, Any] | None:
    path = repo / WORKSPACE_META_FILENAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _workspace_source(repo: Path) -> dict[str, Any]:
    meta = _workspace_meta(repo)
    if meta:
        return {
            "type": "trace_workspace",
            "trace_id": meta.get("trace_id"),
            "workspace_id": meta.get("workspace_id"),
            "opened_at": meta.get("opened_at"),
        }
    return {"type": "local_project"}


def _adapter_slots() -> dict[str, dict[str, Any]]:
    return {
        "claude_code": {"implemented": True, "status": "available"},
        "codex": {
            "implemented": False,
            "status": "schema_ready_unimplemented",
        },
    }


def _source_event(event: TrailEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_sequence": event.event_sequence,
        "event_type": event.event_type,
    }


def _snapshot_row(event: TrailEvent) -> dict[str, Any]:
    payload = event.payload
    return {
        "snapshot_id": payload.get("snapshot_id"),
        "snapshot_ref": payload.get("snapshot_ref"),
        "trace_id": event.trace_id,
        "generation_index": event.generation_index or 0,
        "step_index": event.step_index,
        "step_id": f"s{event.step_index}" if event.step_index is not None else None,
        "role": payload.get("snapshot_role") or "after",
        "tree_id": payload.get("tree_id"),
        "base_commit": payload.get("base_commit"),
        "git_head": payload.get("git_head"),
        "capture_status": payload.get("capture_status") or "unknown",
        "limitations": payload.get("limitations") or [],
        "source_event": _source_event(event),
    }


def list_trace_snapshots(repo: Path, trace_id: str) -> dict[str, Any]:
    """List captured Trace Snapshot rewind candidates for a trace."""
    repo = repo.resolve()
    snapshots = [
        _snapshot_row(event)
        for event in read_events(repo)
        if event.event_type == "trace_snapshot_created" and event.trace_id == trace_id
    ]
    snapshots.sort(
        key=lambda item: (
            item.get("step_index") if item.get("step_index") is not None else -1,
            item.get("role") != "before",
            item["source_event"]["event_sequence"],
        )
    )
    return {
        "schema_version": "opentraces.trail_snapshots.v1",
        "trace_id": trace_id,
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
        "event_log_ref": EVENT_LOG_REF,
        "workspace_source": _workspace_source(repo),
    }


def _snapshot_event_for_ref(repo: Path, snapshot_ref: str) -> TrailEvent | None:
    snapshot_id = normalize_id(snapshot_ref)
    candidates = [
        event
        for event in read_events(repo)
        if event.event_type == "trace_snapshot_created"
        and normalize_id(event.payload.get("snapshot_id")) == snapshot_id
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda event: event.event_sequence)


def _parse_step_index(step_id: str | int) -> int:
    raw = str(step_id).strip()
    if raw.startswith("step_"):
        raw = raw[5:]
    elif raw.startswith("s"):
        raw = raw[1:]
    if not raw.isdigit():
        raise ValueError(f"invalid step id: {step_id}")
    return int(raw)


def _snapshot_event_for_step(
    repo: Path,
    trace_id: str,
    step_id: str | int,
) -> TrailEvent | None:
    step_index = _parse_step_index(step_id)
    candidates = [
        event
        for event in read_events(repo)
        if event.event_type == "trace_snapshot_created"
        and event.trace_id == trace_id
        and event.step_index == step_index
        and (event.payload.get("snapshot_role") or "after") == "after"
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda event: event.event_sequence)


def _materialization_base(repo: Path) -> Path:
    try:
        if project_is_opted_in(repo):
            return get_project_dir(repo) / "trace-worktrees"
    except Exception:
        pass
    common = _git(repo, ["rev-parse", "--git-common-dir"], check=False)
    if common:
        common_path = Path(common)
        if not common_path.is_absolute():
            common_path = repo / common_path
        return common_path / "opentraces-worktrees"
    return repo.parent / ".opentraces-trace-worktrees"


def _default_materialization_path(repo: Path, snapshot_id: str) -> Path:
    return _materialization_base(repo) / f"{snapshot_id[:16]}-{uuid.uuid4().hex[:8]}"


def materialize_snapshot_tree(
    repo: Path,
    snapshot_event: TrailEvent,
    *,
    target: Path | None = None,
) -> Path:
    """Materialize a snapshot tree into an isolated Git worktree."""
    repo = repo.resolve()
    payload = snapshot_event.payload
    snapshot_id = normalize_id(payload.get("snapshot_id"))
    tree_id = payload.get("tree_id") or {}
    tree_hex = tree_id.get("hex")
    if not tree_hex or _object_type(repo, tree_hex) != "tree":
        raise ValueError(f"missing_snapshot_tree:{snapshot_id or 'unknown'}")

    target = (target or _default_materialization_path(repo, snapshot_id)).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "opentraces")
    env.setdefault("GIT_AUTHOR_EMAIL", "opentraces@local")
    env.setdefault("GIT_COMMITTER_NAME", "opentraces")
    env.setdefault("GIT_COMMITTER_EMAIL", "opentraces@local")
    commit = _git(
        repo,
        ["commit-tree", tree_hex, "-m", f"opentraces snapshot {snapshot_id[:12]}"],
        env=env,
    )
    _git(repo, ["worktree", "add", "--detach", str(target), commit])
    return target


def snapshot_checkout_packet(
    repo: Path,
    snapshot_ref: str,
    *,
    dry_run: bool,
    target: Path | None = None,
) -> dict[str, Any]:
    repo = repo.resolve()
    event = _snapshot_event_for_ref(repo, snapshot_ref)
    if event is None:
        return {
            "schema_version": SNAPSHOT_REWIND_SCHEMA_VERSION,
            "relation": "unknown",
            "snapshot_ref": snapshot_ref,
            "limitations": [f"missing_snapshot:{normalize_id(snapshot_ref)}"],
            "event_log_ref": EVENT_LOG_REF,
        }

    row = _snapshot_row(event)
    snapshot_id = normalize_id(row.get("snapshot_id"))
    materialization_path = target or _default_materialization_path(repo, snapshot_id)
    materialized = False
    if not dry_run:
        materialization_path = materialize_snapshot_tree(repo, event, target=target)
        materialized = True

    return {
        "schema_version": SNAPSHOT_REWIND_SCHEMA_VERSION,
        "relation": "snapshot_rewind",
        "snapshot": row,
        "snapshot_id": row.get("snapshot_id"),
        "tree_id": row.get("tree_id"),
        "base_commit": row.get("base_commit"),
        "materialization": {
            "path": str(materialization_path),
            "isolated": True,
            "materialized": materialized,
            "mode": "git_worktree",
        },
        "limitations": row.get("limitations") or [],
        "event_log_ref": EVENT_LOG_REF,
        "workspace_source": _workspace_source(repo),
    }


def _find_trace_file(repo: Path, trace_id: str) -> Path | None:
    try:
        traces_dir = get_project_traces_dir(repo)
    except Exception:
        return None
    exact = traces_dir / f"{trace_id}.jsonl"
    if exact.exists():
        return exact
    for path in sorted(traces_dir.glob("*.jsonl")):
        try:
            raw = path.read_text().splitlines()[0]
            record = json.loads(raw)
        except Exception:
            continue
        if record.get("trace_id") == trace_id or record.get("session_id") == trace_id:
            return path
    return None


def _trace_events(repo: Path, trace_id: str) -> list[TrailEvent]:
    return [
        event
        for event in read_events(repo)
        if event.trace_id == trace_id or event.payload.get("trace_id") == trace_id
    ]


def export_trace_workspace(repo: Path, trace_id: str, output: Path) -> dict[str, Any]:
    """Write a portable Trace Workspace directory for a single trace."""
    repo = repo.resolve()
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"output path is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "traces").mkdir(exist_ok=True)

    events = _trace_events(repo, trace_id)
    snapshots_payload = list_trace_snapshots(repo, trace_id)
    if not events:
        raise ValueError(f"no TrailEvents found for trace {trace_id}")

    bundle_rel = "git.bundle"
    _git(repo, ["bundle", "create", str(output / bundle_rel), EVENT_LOG_REF])

    limitations: list[str] = []
    trace_file = _find_trace_file(repo, trace_id)
    if trace_file is None:
        limitations.append("source_trace_record_missing")
        trace_record_rel = None
    else:
        trace_record_rel = f"traces/{trace_id}.jsonl"
        shutil.copyfile(trace_file, output / trace_record_rel)

    manifest = {
        "schema_version": TRACE_WORKSPACE_SCHEMA_VERSION,
        "workspace_id": f"trace-workspace-{uuid.uuid4().hex}",
        "trace_id": trace_id,
        "created_at": _utc_now(),
        "event_log_ref": EVENT_LOG_REF,
        "git_bundle": bundle_rel,
        "trace_record": trace_record_rel,
        "event_count": len(events),
        "events": [_source_event(event) for event in events],
        "snapshots": snapshots_payload["snapshots"],
        "environment_manifest": {
            "target_agent": "claude-code",
            "adapter_slots": _adapter_slots(),
            "non_portable_dependencies": NON_PORTABLE_DEPENDENCIES,
        },
        "limitations": limitations,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return {
        "schema_version": TRACE_WORKSPACE_SCHEMA_VERSION,
        "status": "ok",
        "trace_id": trace_id,
        "output": str(output),
        "manifest": str(output / "manifest.json"),
        "event_count": len(events),
        "snapshot_count": len(manifest["snapshots"]),
        "git_bundle": str(output / bundle_rel),
        "trace_record": trace_record_rel,
        "limitations": limitations,
    }


def open_trace_workspace(workspace: Path, project: Path) -> dict[str, Any]:
    """Open a portable Trace Workspace into a blank project directory."""
    workspace = workspace.resolve()
    project = project.resolve()
    manifest_path = workspace / "manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"missing workspace manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    bundle = workspace / manifest.get("git_bundle", "git.bundle")
    if not bundle.exists():
        raise ValueError(f"missing workspace git bundle: {bundle}")

    if project.exists() and any(project.iterdir()):
        raise ValueError(f"project directory is not blank: {project}")
    project.mkdir(parents=True, exist_ok=True)
    _git(project, ["init", "-q"])
    _git(project, ["config", "user.email", "opentraces@local"])
    _git(project, ["config", "user.name", "opentraces"])
    _git(
        project,
        ["fetch", str(bundle), f"{EVENT_LOG_REF}:{EVENT_LOG_REF}"],
    )

    missing: list[str] = []
    for snapshot in manifest.get("snapshots") or []:
        tree_id = snapshot.get("tree_id") or {}
        tree_hex = tree_id.get("hex")
        if not tree_hex or _object_type(project, tree_hex) != "tree":
            missing.append(str(snapshot.get("snapshot_id") or tree_hex or "unknown"))
    if missing:
        raise ValueError("missing_snapshot_tree:" + ",".join(missing))

    project_id = f"trace-workspace-{normalize_id(manifest.get('trace_id'))[:12]}"
    (project / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id}, indent=2) + "\n"
    )

    traces_dir = get_project_traces_dir(project)
    traces_dir.mkdir(parents=True, exist_ok=True)
    trace_record_rel = manifest.get("trace_record")
    if trace_record_rel:
        src = workspace / trace_record_rel
        if src.exists():
            shutil.copyfile(src, traces_dir / Path(trace_record_rel).name)

    meta = {
        "schema_version": TRACE_WORKSPACE_SCHEMA_VERSION,
        "workspace_id": manifest.get("workspace_id"),
        "trace_id": manifest.get("trace_id"),
        "opened_at": _utc_now(),
    }
    (project / WORKSPACE_META_FILENAME).write_text(json.dumps(meta, indent=2) + "\n")
    return {
        "schema_version": TRACE_WORKSPACE_SCHEMA_VERSION,
        "status": "ok",
        "trace_id": manifest.get("trace_id"),
        "project": str(project),
        "event_log_ref": EVENT_LOG_REF,
        "event_count": manifest.get("event_count", 0),
        "snapshot_count": len(manifest.get("snapshots") or []),
        "workspace_source": _workspace_source(project),
    }


def _timeline_entry(event: TrailEvent) -> dict[str, Any]:
    payload = event.payload
    entry: dict[str, Any] = {
        "event_sequence": event.event_sequence,
        "event_time": event.event_time,
        "event_type": event.event_type,
        "trace_id": event.trace_id,
        "generation_index": event.generation_index or 0,
        "step_index": event.step_index,
        "step_id": f"s{event.step_index}" if event.step_index is not None else None,
        "capture_method": event.capture_method,
    }
    if event.event_type == "trace_snapshot_created":
        entry.update(
            {
                "snapshot_id": payload.get("snapshot_id"),
                "snapshot_ref": payload.get("snapshot_ref"),
                "snapshot_role": payload.get("snapshot_role") or "after",
                "tree_id": payload.get("tree_id"),
                "base_commit": payload.get("base_commit"),
                "capture_status": payload.get("capture_status"),
                "limitations": payload.get("limitations") or [],
            }
        )
    elif event.event_type == "trace_patch_created":
        entry.update(
            {
                "trace_patch_id": payload.get("trace_patch_id"),
                "trace_patch_ref": payload.get("trace_patch_ref"),
                "file_path": payload.get("file_path"),
                "snapshot_before_id": payload.get("snapshot_before_id"),
                "snapshot_after_id": payload.get("snapshot_after_id"),
                "limitations": payload.get("limitations") or [],
            }
        )
    elif event.event_type == "git_anchor_created":
        entry.update(
            {
                "trace_patch_id": payload.get("trace_patch_id"),
                "git_anchor_id": payload.get("git_anchor_id"),
                "commit_id": payload.get("commit_id"),
                "path": payload.get("path"),
                "relation": payload.get("relation") or "anchored_in_git",
                "evidence_tier": payload.get("evidence_tier"),
                "evidence_firmness": payload.get("evidence_firmness"),
                "limitations": payload.get("limitations") or [],
            }
        )
    return entry


def play_trace_timeline(repo: Path, trace_id: str) -> dict[str, Any]:
    """Return the observed Trace Trails timeline for a trace."""
    repo = repo.resolve()
    events = sorted(_trace_events(repo, trace_id), key=lambda event: event.event_sequence)
    timeline = [_timeline_entry(event) for event in events]
    return {
        "schema_version": TRACE_PLAY_SCHEMA_VERSION,
        "trace_id": trace_id,
        "event_log_ref": EVENT_LOG_REF,
        "event_count": len(timeline),
        "timeline": timeline,
        "workspace_source": _workspace_source(repo),
        "limitations": [] if timeline else ["missing_trace_events"],
    }


def _step_context(record: TraceRecord, step_index: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for step in record.steps:
        if step.step_index > step_index:
            continue
        out.append(
            {
                "step_index": step.step_index,
                "step_id": f"s{step.step_index}",
                "role": step.role,
                "content": step.content,
                "call_type": step.call_type,
                "parent_step": step.parent_step,
                "tool_call_count": len(step.tool_calls),
            }
        )
    return out


def _write_claude_resume_session(
    *,
    materialized_path: Path,
    record: TraceRecord,
    selected_step_id: str,
    new_session_id: str,
    preamble: str,
    context: list[dict[str, Any]],
) -> Path:
    session_path = (
        get_projects_path(load_config())
        / encode_claude_path(materialized_path)
        / f"{new_session_id}.jsonl"
    )
    session_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        {
            "type": "opentraces_resume_preamble",
            "uuid": str(uuid.uuid4()),
            "cwd": str(materialized_path),
            "message": {"role": "user", "content": preamble},
            "opentraces": {
                "trace_id": record.trace_id,
                "parent_session_id": record.session_id,
                "parent_step_id": selected_step_id,
            },
        }
    ]
    for step in context:
        lines.append(
            {
                "type": "opentraces_resume_context_step",
                "uuid": str(uuid.uuid4()),
                "message": {
                    "role": "assistant" if step["role"] == "agent" else "user",
                    "content": step.get("content") or "",
                },
                "opentraces": {
                    "trace_id": record.trace_id,
                    "step_id": step["step_id"],
                    "step_index": step["step_index"],
                },
            }
        )
    session_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return session_path


def snapshot_resume_packet(
    repo: Path,
    record: TraceRecord,
    step_id: str,
    *,
    state: StateManager | None = None,
) -> dict[str, Any]:
    """Build a snapshot-backed Claude Code resume packet."""
    repo = repo.resolve()
    step_index = _parse_step_index(step_id)
    snapshot_event = _snapshot_event_for_step(repo, record.trace_id, step_index)
    normalized_step_id = f"s{step_index}"
    if snapshot_event is None:
        return {
            "schema_version": SNAPSHOT_RESUME_SCHEMA_VERSION,
            "resume_mode": "unknown",
            "relation": "unknown",
            "trace_id": record.trace_id,
            "step_id": normalized_step_id,
            "target_agent": "claude-code",
            "limitations": [f"missing_snapshot:step_{step_index}"],
            "adapter_slots": _adapter_slots(),
            "workspace_source": _workspace_source(repo),
        }

    selected_step = next((step for step in record.steps if step.step_index == step_index), None)
    if selected_step is None:
        return {
            "schema_version": SNAPSHOT_RESUME_SCHEMA_VERSION,
            "resume_mode": "unknown",
            "relation": "unknown",
            "trace_id": record.trace_id,
            "step_id": normalized_step_id,
            "target_agent": "claude-code",
            "limitations": [f"missing_trace_step:{normalized_step_id}"],
            "adapter_slots": _adapter_slots(),
            "workspace_source": _workspace_source(repo),
        }
    if selected_step.call_type == "subagent":
        return {
            "schema_version": SNAPSHOT_RESUME_SCHEMA_VERSION,
            "resume_mode": "unknown",
            "relation": "unknown",
            "trace_id": record.trace_id,
            "step_id": normalized_step_id,
            "target_agent": "claude-code",
            "limitations": ["subagent_step_not_resumable"],
            "adapter_slots": _adapter_slots(),
            "workspace_source": _workspace_source(repo),
        }

    materialized_path = materialize_snapshot_tree(repo, snapshot_event)
    context = _step_context(record, step_index)
    snapshot = _snapshot_row(snapshot_event)
    limitations = sorted(
        set((snapshot.get("limitations") or []) + NON_PORTABLE_DEPENDENCIES)
    )
    slice_info = trace_slice_for_event(
        snapshot_event,
        relation="contains_resume_snapshot",
    )
    preamble = (
        "OpenTraces snapshot-backed resume. "
        f"Resume trace {record.trace_id} at {normalized_step_id}. "
        f"The worktree has been materialized from Trace Snapshot "
        f"{normalize_id(snapshot.get('snapshot_id'))[:12]}. "
        "This is trajectory forking from observed state, not deterministic "
        "agent re-execution. Non-portable dependencies are declared in the "
        "resume packet."
    )
    new_session_id = str(uuid.uuid4())
    session_path = _write_claude_resume_session(
        materialized_path=materialized_path,
        record=record,
        selected_step_id=normalized_step_id,
        new_session_id=new_session_id,
        preamble=preamble,
        context=context,
    )
    if state is not None:
        state.upsert_session(
            session_id=new_session_id,
            source_path=str(session_path),
            observed_size=session_path.stat().st_size,
            observed_mtime=session_path.stat().st_mtime,
            parent_session_id=record.session_id,
            parent_step_id=normalized_step_id,
        )

    return {
        "schema_version": SNAPSHOT_RESUME_SCHEMA_VERSION,
        "resume_mode": "snapshot_backed",
        "relation": "snapshot_resume",
        "trace_id": record.trace_id,
        "step_id": normalized_step_id,
        "containing_segment_id": (slice_info or {}).get("containing_segment_id"),
        "target_agent": "claude-code",
        "launch": {
            "argv": ["claude", "--resume", new_session_id],
            "cwd": str(materialized_path),
            "dry_run_safe": True,
        },
        "session": {
            "new_session_id": new_session_id,
            "new_session_path": str(session_path),
            "parent_session_id": record.session_id,
            "filtered_step_count": len(context),
        },
        "snapshot": snapshot,
        "materialization": {
            "path": str(materialized_path),
            "isolated": True,
            "materialized": True,
            "mode": "git_worktree",
        },
        "session_context": {
            "preamble": preamble,
            "steps": context,
        },
        "fork_lineage": {
            "source_trace_id": record.trace_id,
            "source_session_id": record.session_id,
            "source_step_id": normalized_step_id,
            "source_snapshot_id": snapshot.get("snapshot_id"),
        },
        "workspace_source": _workspace_source(repo),
        "adapter_slots": _adapter_slots(),
        "non_portable_dependencies": NON_PORTABLE_DEPENDENCIES,
        "limitations": limitations,
        "event_log_ref": EVENT_LOG_REF,
        "source_events": [_source_event(snapshot_event)],
    }
