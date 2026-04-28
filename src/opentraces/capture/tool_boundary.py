"""Adapter-facing worktree observation at agent tool boundaries.

This module is the stable capture-layer interface for agent integrations.
Adapters should not reach into ``capture.fs_watcher.runtime`` directly when
they want Trace Trails worktree observation. The filesystem watcher owns
path/blob scanning; this module owns the agent-tool lifecycle policy around
when that scanner should run.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Iterable

from .fs_watcher.runtime import PollResult, poll_project_once

DEFAULT_BOUNDARY_WRITER = "hook-fs-watcher"
DEFAULT_MUTATING_TOOL_NAMES = frozenset(
    {
        "bash",
        "edit",
        "write",
        "multiedit",
        "notebookedit",
    }
)


@dataclass(frozen=True)
class ToolBoundaryObservationSummary:
    """Small, adapter-safe summary of a boundary observation pass."""

    baseline_initialized: bool
    paths_seen: int
    observations: int
    skipped_paths: int

    @classmethod
    def from_poll_result(cls, result: PollResult) -> "ToolBoundaryObservationSummary":
        return cls(
            baseline_initialized=result.baseline_initialized,
            paths_seen=result.paths_seen,
            observations=len(result.observations),
            skipped_paths=result.skipped_paths,
        )

    def to_hook_payload(self) -> dict[str, int | bool]:
        return {
            "baseline_initialized": self.baseline_initialized,
            "paths_seen": self.paths_seen,
            "observations": self.observations,
            "skipped_paths": self.skipped_paths,
        }


def tool_name_may_mutate(tool_name: str | None) -> bool:
    return (
        isinstance(tool_name, str)
        and tool_name.strip().lower() in DEFAULT_MUTATING_TOOL_NAMES
    )


def observe_tool_boundary(
    cwd: str | Path | None,
    *,
    tool_name: str | None = None,
    may_mutate: bool | None = None,
    exclude_paths: Iterable[str | Path] | None = None,
    writer: str = DEFAULT_BOUNDARY_WRITER,
    strict: bool = False,
) -> ToolBoundaryObservationSummary | None:
    """Observe one agent tool lifecycle boundary if the tool can mutate files.

    ``tool_name`` covers common Claude-style tool names. New adapters with
    different vocabularies can pass ``may_mutate=True`` after applying their
    own policy, without teaching fs_watcher about every agent's tool taxonomy.

    By default errors are swallowed because this is intended for runtime hooks
    that must never block an agent session. Tests or batch callers can set
    ``strict=True`` to surface observer failures.
    """
    if not cwd:
        return None
    should_observe = tool_name_may_mutate(tool_name) if may_mutate is None else may_mutate
    if not should_observe:
        return None
    root = _worktree_root(Path(cwd).resolve())
    if root is None:
        return None
    try:
        result = poll_project_once(
            root,
            exclude_paths=exclude_paths,
            writer=writer,
        )
    except Exception:
        if strict:
            raise
        return None
    return ToolBoundaryObservationSummary.from_poll_result(result)


def _worktree_root(cwd: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return Path(proc.stdout.strip()).resolve()
