"""Adapter contracts for agent session parsers and file-based importers.

Uses typing.Protocol (structural typing, not inheritance) so new adapters
only need to implement the interface without importing this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from opentraces_schema import TraceRecord


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

    For importing traces from external file formats (e.g. ADP trajectories)
    into TraceRecord format.
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
