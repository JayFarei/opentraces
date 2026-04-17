"""Adapter contracts for agent session parsers and file-based importers.

Uses typing.Protocol (structural typing, not inheritance) so new adapters
only need to implement the interface without importing this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from opentraces_schema import TraceRecord


@dataclass
class ParseOutcome:
    """Result of a parse attempt, carrying both record and any errors.

    A parser that encounters errors mid-parse still populates ``errors``
    even when a partial ``record`` was produced. Any non-empty ``errors``
    blocks the trace from upload via the state machine — parse errors
    are a hard block, never a warning.
    """

    record: object | None = None
    errors: list[str] = field(default_factory=list)

    def is_blocked(self) -> bool:
        """Return True if this outcome must block upload."""
        return bool(self.errors)

    def block_reason(self) -> str:
        """Canonical block reason string for the state machine."""
        return "parse_error"


@runtime_checkable
class SessionParser(Protocol):
    """Protocol that all agent parsers must satisfy.

    For live discovery and incremental parsing of agent session files on disk.
    """

    agent_name: str

    def discover_sessions(self, projects_path: Path) -> Iterator[Path]:
        """Yield paths to session files for this agent."""
        ...

    def parse_session(self, session_path: Path, byte_offset: int = 0) -> TraceRecord | None:
        """Parse a session file into a TraceRecord.

        Args:
            session_path: Path to the session JSONL file.
            byte_offset: Resume from this byte offset for incremental processing.

        Returns:
            TraceRecord if session meets quality thresholds, None otherwise.
        """
        ...


@runtime_checkable
class FormatImporter(Protocol):
    """Protocol for file-based trace importers.

    For importing traces from external file formats (e.g. ADP trajectories,
    HuggingFace datasets) into TraceRecord format.
    """

    format_name: str
    file_extensions: list[str]

    def import_traces(self, input_path: Path, max_records: int = 0) -> list[TraceRecord]:
        """Read a file and produce TraceRecords.

        Args:
            input_path: Path to the source file.
            max_records: Maximum records to import (0 = unlimited).

        Returns:
            List of TraceRecords. May be empty if file has no valid records.
        """
        ...

    def map_record(self, row: dict, index: int, source_info: dict | None = None) -> TraceRecord | None:
        """Convert one dataset row to a TraceRecord.

        Used by CLI commands that stream rows from external sources
        (e.g. HuggingFace datasets) and need per-row conversion.

        Args:
            row: Raw dataset row as a dict.
            index: Row index in the source dataset.
            source_info: Provenance metadata (dataset_id, revision, etc.).

        Returns:
            TraceRecord if row is valid, None to skip invalid rows.
        """
        ...


# ---------------------------------------------------------------------------
# HookInstaller protocol — unified contract for wiring opentraces into an
# external tool's hook system (Claude Code settings.json, git post-commit,
# and future adapters: Codex, Cursor, etc.).
#
# Each adapter implements this by binding to a target at construction (e.g.
# the claude_dir for Claude Code, the repo root for git). The CLI, `doctor`,
# and test harnesses then treat every installer uniformly.
# ---------------------------------------------------------------------------


@dataclass
class HookInstallResult:
    """Outcome of install() / remove()."""

    ok: bool = True
    installed: dict[str, str] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    config_files: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class HookInstallError(Exception):
    """Typed failure from a HookInstaller. Code is stable for CLI/JSON output."""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


@runtime_checkable
class HookInstaller(Protocol):
    """Contract for installing/removing opentraces integration points.

    Implementations should be idempotent and should never leave partial
    state on failure (validate first, then write). Raise HookInstallError
    for user-actionable failures; reserve exceptions for programmer errors.
    """

    installer_name: str  # stable id, e.g. "claude-code", "git"

    def plan(self) -> list[dict]:
        """Return a list of {event, source, dest} dicts describing install()."""
        ...

    def install(self) -> HookInstallResult:
        """Install hooks. Idempotent."""
        ...

    def remove(self) -> HookInstallResult:
        """Remove hooks. Idempotent."""
        ...

    def status(self) -> dict:
        """Report current install state (for `opentraces doctor`)."""
        ...


