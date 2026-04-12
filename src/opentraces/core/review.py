"""Review lifecycle operations shared by CLI, TUI, and web clients.

Each function here preserves the exact behavior of the inline site it was
extracted from, including any pre-existing discrepancies between clients.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ..inbox import redact_step
from ..state import StateManager, TraceStatus

logger = logging.getLogger(__name__)


@dataclass
class RedactResult:
    """Outcome of a redact_step_and_persist call."""

    ok: bool
    error: Optional[str] = None
    error_code: Optional[str] = None
    step_index: int = -1


def redact_step_and_persist(
    staging_dir: Path,
    trace_id: str,
    step_index: int,
) -> RedactResult:
    """Redact a step in a staging JSONL file and atomically rewrite the file.

    Mirrors the duplicated implementation previously present in both
    ``cli.py::session_redact`` and ``web_server.py::api_redact_step``. Callers
    remain responsible for trace_id format validation and for emitting
    surface-specific user messages based on the returned RedactResult.
    """
    staging_file = staging_dir / f"{trace_id}.jsonl"
    if not staging_file.exists():
        return RedactResult(
            ok=False,
            error=f"Staging file not found for {trace_id}",
            error_code="NOT_FOUND",
            step_index=step_index,
        )

    text = staging_file.read_text().strip()
    if not text:
        return RedactResult(
            ok=False,
            error="Staging file is empty",
            error_code="EMPTY",
            step_index=step_index,
        )

    trace_data = json.loads(text.splitlines()[0])
    steps = trace_data.get("steps", [])
    if step_index < 0 or step_index >= len(steps):
        return RedactResult(
            ok=False,
            error=f"Step index {step_index} out of range",
            error_code="OUT_OF_RANGE",
            step_index=step_index,
        )

    redact_step(steps[step_index])

    # Atomic write: temp file + os.replace for crash safety
    new_line = json.dumps(trace_data, ensure_ascii=False)
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        dir=str(staging_dir),
        suffix=".jsonl.tmp",
        delete=False,
    )
    try:
        fd.write(new_line + "\n")
        fd.flush()
        os.fsync(fd.fileno())
        fd.close()
        os.replace(fd.name, str(staging_file))
    except BaseException:
        fd.close()
        try:
            os.unlink(fd.name)
        except OSError:
            logger.debug("Failed to clean up temp file: %s", fd.name)
        raise

    return RedactResult(ok=True, step_index=step_index)


def reject_trace(
    state: StateManager,
    trace_id: str,
    *,
    with_session_kwarg: bool,
) -> None:
    """Mark a trace as REJECTED.

    The CLI variant historically omitted the ``session_id`` kwarg; web_server
    and TUI both pass it. ``with_session_kwarg`` preserves that discrepancy.
    """
    if with_session_kwarg:
        state.set_trace_status(trace_id, TraceStatus.REJECTED, session_id=trace_id)
    else:
        state.set_trace_status(trace_id, TraceStatus.REJECTED)


def discard_trace(
    state: StateManager,
    trace_id: str,
    *,
    staging_file: Path,
) -> None:
    """CLI discard flow: delete staging file + pop trace from state dict.

    Mirrors ``cli.py::session_discard`` exactly, including direct mutation of
    ``state._state['traces']`` followed by ``state.save()``.
    """
    if staging_file.exists():
        staging_file.unlink()

    entry = state.get_trace(trace_id)
    if entry is not None:
        state._state["traces"].pop(trace_id, None)
        state.save()


def discard_trace_state_only(
    state: StateManager,
    trace_id: str,
    *,
    staging_file: Path,
) -> None:
    """TUI discard flow: best-effort unlink + remove from state dict.

    Mirrors ``tui.py::action_discard`` exactly, including the try/except
    around ``staging_file.unlink()`` with logger.warning on OSError.
    """
    if staging_file.exists():
        try:
            staging_file.unlink()
        except OSError:
            logger.warning(
                "Failed to delete staging file %s", staging_file, exc_info=True
            )

    if trace_id in state._state.get("traces", {}):
        del state._state["traces"][trace_id]
        state.save()


def commit_single(
    state: StateManager,
    trace_id: str,
    task_desc: str,
) -> str:
    """Create a single-trace commit group. Returns the commit_id.

    Does NOT redundantly set COMMITTED status, matching cli/tui behavior.
    """
    return state.create_commit_group([trace_id], task_desc)


def commit_bulk(
    state: StateManager,
    trace_ids: Iterable[str],
    message: str,
) -> str:
    """Create a commit group for multiple traces and loop COMMITTED status.

    Matches ``web_server.py::api_commit`` behavior exactly, including the
    redundant per-id ``set_trace_status(COMMITTED, session_id=...)`` loop that
    follows ``create_commit_group``.
    """
    ids = list(trace_ids)
    commit_id = state.create_commit_group(trace_ids=ids, message=message)
    for sid in ids:
        state.set_trace_status(sid, TraceStatus.COMMITTED, session_id=sid)
    return commit_id


def stage_trace(state: StateManager, trace_id: str) -> None:
    """Transition a trace to STAGED status (web stage route)."""
    state.set_trace_status(trace_id, TraceStatus.STAGED, session_id=trace_id)


def unstage_trace(state: StateManager, trace_id: str) -> None:
    """Transition a trace back to PARSED status (web unstage route)."""
    state.set_trace_status(trace_id, TraceStatus.PARSED, session_id=trace_id)


def reset_to_staged(state: StateManager, trace_id: str) -> None:
    """CLI reset flow: transition to STAGED (no session_id kwarg)."""
    state.set_trace_status(trace_id, TraceStatus.STAGED)
