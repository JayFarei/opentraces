"""Publish-flow leaf helpers shared by the CLI push command.

The full push flow in ``cli.py`` is intentionally left inline: it interleaves
click/echo/sys.exit/interactive-prompt concerns with upload logic, and a
mechanical extraction risked behavior drift. Only leaf state mutations are
extracted here.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..state import StateManager, TraceStatus


def mark_uploaded(state: StateManager, trace_ids: Iterable[str]) -> None:
    """Mark the given trace_ids as UPLOADED, matching cli.py push behavior."""
    for trace_id in trace_ids:
        state.set_trace_status(trace_id, TraceStatus.UPLOADED)


def mark_failed(
    state: StateManager,
    trace_id: str,
    error: Optional[str],
) -> None:
    """Mark a single trace as FAILED with the given error string."""
    state.set_trace_status(trace_id, TraceStatus.FAILED, error=error)
