"""Publish-flow leaf helpers shared by the CLI push command.

The full push flow in ``cli.py`` is intentionally left inline: it interleaves
click/echo/sys.exit/interactive-prompt concerns with upload logic, and a
mechanical extraction risked behavior drift. Only leaf state mutations are
extracted here.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .state import StateManager, TraceStatus


def mark_uploaded(
    state: StateManager,
    trace_ids: Iterable[str],
    *,
    remote_name: Optional[str] = None,
) -> None:
    """Mark the given trace_ids as UPLOADED.

    When ``remote_name`` is supplied (the post-Step-3 path), each trace's
    ``uploaded_to[remote_name]`` is written via
    ``StateManager.mark_uploaded_to`` so the per-remote replay flow works.

    When called without ``remote_name`` (legacy / transitional callers),
    fall back to the old behavior of just flipping status to UPLOADED
    without recording per-remote provenance. This keeps in-flight code
    paths working while step 3 is finishing — they simply won't benefit
    from per-remote tracking until they thread the active remote name
    through.
    """
    for trace_id in trace_ids:
        if remote_name is None:
            state.set_trace_status(trace_id, TraceStatus.UPLOADED)
        else:
            state.mark_uploaded_to(trace_id, remote_name)


def mark_failed(
    state: StateManager,
    trace_id: str,
    error: Optional[str],
) -> None:
    """Mark a single trace as FAILED with the given error string."""
    state.set_trace_status(trace_id, TraceStatus.FAILED, error=error)
