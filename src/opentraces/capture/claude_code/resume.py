"""Resume a Claude Code session from a trace id.

Adapter-specific: resolves the ``claude --resume <session_id>`` invocation,
and for plan 048 can fork a fresh Claude session truncated at a specific step.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from opentraces_schema import TraceRecord

from ...core.config import get_projects_path, load_config
from ...core.repo_identity import encode_claude_path
from ...core.state import StateManager


class ResumeError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ResumeTarget:
    trace_id: str
    session_id: str
    argv: list[str]

    @property
    def command(self) -> str:
        return " ".join(self.argv)


@dataclass
class ResumeAtStepTarget(ResumeTarget):
    new_session_id: str
    new_session_path: Path
    parent_session_id: str
    parent_step_id: str
    truncated_at_line: int
    source_path: Path


def resolve(trace_id_prefix: str, staging: Path) -> ResumeTarget:
    """Look up the Claude session_id behind a trace id (or short prefix)."""
    rec = _load_trace_record(trace_id_prefix, staging)
    session_id = getattr(rec, "session_id", None)
    if not session_id:
        raise ResumeError(
            "NO_SESSION",
            f"trace {rec.trace_id} has no session_id recorded.",
        )

    return ResumeTarget(
        trace_id=rec.trace_id,
        session_id=session_id,
        argv=["claude", "--resume", session_id],
    )


def resolve_at_step(
    trace_id_prefix: str,
    step_id: str,
    staging: Path,
    *,
    project_cwd: Path,
    state: StateManager,
    materialize: bool = True,
) -> ResumeAtStepTarget:
    rec = _load_trace_record(trace_id_prefix, staging)
    session_id = getattr(rec, "session_id", None)
    if not session_id:
        raise ResumeError(
            "NO_SESSION",
            f"trace {rec.trace_id} has no session_id recorded.",
        )

    step = _find_step(rec, step_id)
    if step.call_type == "subagent":
        raise ResumeError(
            "SUBAGENT_STEP",
            f"step {step_id} is a subagent step; resume the parent branch step instead.",
        )

    # Anchors live in local state, not on the trace record — they point
    # into ~/.claude/projects/ on the capture machine and are adapter-
    # specific, so keeping them out of the portable schema. A missing
    # anchor means this trace was captured elsewhere (or before the
    # anchor store existed) and cannot be forked on this machine.
    anchor = state.get_step_anchor(rec.trace_id, step.step_index)
    if anchor is None:
        raise ResumeError(
            "NO_ANCHOR",
            f"step {step_id} has no local source anchor on this machine.",
        )
    entry_uuid = anchor.get("entry_uuid")
    line_no = anchor.get("line_no")
    session_file_relpath = anchor.get("session_file_relpath")
    if entry_uuid is None and line_no is None:
        raise ResumeError("NO_ANCHOR", f"step {step_id} anchor is empty.")

    source_path = _resolve_source_path(
        session_id=session_id,
        session_file_relpath=session_file_relpath,
        project_cwd=project_cwd,
        state=state,
    )

    raw_lines = source_path.read_text().splitlines()
    cutoff_index = _resolve_cutoff_index(raw_lines, entry_uuid, line_no)
    if cutoff_index is None:
        raise ResumeError("NO_ANCHOR", f"could not resolve anchor for {step_id}.")

    kept_lines = raw_lines[: cutoff_index + 1]
    # Dashed UUID — ``claude --resume`` matches on this exact filename
    # under ~/.claude/projects/<encoded-cwd>/; the no-dash ``.hex`` form
    # is invisible to its session discovery.
    new_session_id = str(uuid.uuid4())
    new_session_path = (
        get_projects_path(load_config())
        / encode_claude_path(project_cwd)
        / f"{new_session_id}.jsonl"
    )
    target = ResumeAtStepTarget(
        trace_id=rec.trace_id,
        session_id=new_session_id,
        argv=["claude", "--resume", new_session_id],
        new_session_id=new_session_id,
        new_session_path=new_session_path,
        parent_session_id=session_id,
        parent_step_id=step_id,
        truncated_at_line=len(kept_lines),
        source_path=source_path,
    )

    if materialize:
        _materialize_resume_target(
            target,
            kept_lines,
            state=state,
        )

    return target


def _load_trace_record(trace_id_prefix: str, staging: Path) -> TraceRecord:
    from ...core.inbox import load_trace_records

    traces = list(load_trace_records(staging))
    matches = [t for t in traces if t.trace_id.startswith(trace_id_prefix)]
    if not matches:
        raise ResumeError("NO_MATCH", f"no trace matches '{trace_id_prefix}'")
    if len(matches) > 1:
        ids = ", ".join(t.trace_id for t in matches[:5])
        raise ResumeError(
            "AMBIGUOUS",
            f"'{trace_id_prefix}' is ambiguous ({len(matches)} matches): {ids}",
        )
    return matches[0]


def _find_step(rec: TraceRecord, step_id: str):
    if not step_id.startswith("s") or not step_id[1:].isdigit():
        raise ResumeError("NO_ANCHOR", f"invalid step id: {step_id}")
    step_index = int(step_id[1:])
    for step in rec.steps:
        if step.step_index == step_index:
            return step
    raise ResumeError("NO_ANCHOR", f"step {step_id} not found.")


def _resolve_cutoff_index(
    raw_lines: list[str],
    entry_uuid: str | None,
    line_no: int | None,
) -> int | None:
    if entry_uuid:
        for idx, raw_line in enumerate(raw_lines):
            try:
                if json.loads(raw_line).get("uuid") == entry_uuid:
                    return idx
            except Exception:
                continue
    if line_no is not None and 0 <= line_no < len(raw_lines):
        return line_no
    return None


def _resolve_source_path(
    *,
    session_id: str,
    session_file_relpath: str | None,
    project_cwd: Path,
    state: StateManager,
) -> Path:
    session = state.get_session(session_id)
    if session is not None and session.source_path:
        source_path = Path(session.source_path)
        if source_path.exists():
            return source_path

    projects_path = get_projects_path(load_config())
    fallback_candidates: list[Path] = []
    if session_file_relpath:
        fallback_candidates.append(projects_path / session_file_relpath)
    fallback_candidates.append(
        projects_path / encode_claude_path(project_cwd) / f"{session_id}.jsonl"
    )

    for candidate in fallback_candidates:
        if candidate.exists():
            return candidate

    if session is None or not session.source_path:
        raise ResumeError("SOURCE_MISSING", f"source session for {session_id} is not recorded.")

    raise ResumeError("SOURCE_MISSING", f"source session file is missing: {session.source_path}")


def _materialize_resume_target(
    target: ResumeAtStepTarget,
    kept_lines: list[str],
    *,
    state: StateManager,
) -> None:
    target.new_session_path.parent.mkdir(parents=True, exist_ok=True)
    preamble = json.dumps({
        "type": "opentraces_resume_parent",
        "parentSessionId": target.parent_session_id,
        "parentStepId": target.parent_step_id,
        "opentraces_version": "plan-048",
    })
    target.new_session_path.write_text("\n".join([preamble, *kept_lines]) + "\n")
    state.upsert_session(
        session_id=target.new_session_id,
        source_path=str(target.new_session_path),
        observed_size=target.new_session_path.stat().st_size,
        observed_mtime=target.new_session_path.stat().st_mtime,
        parent_session_id=target.parent_session_id,
        parent_step_id=target.parent_step_id,
    )

    backup_path = state._state_path.parent / f"{target.new_session_id}.backup"
    if backup_path.exists() or backup_path.is_symlink():
        backup_path.unlink()
    backup_path.symlink_to(target.source_path)
