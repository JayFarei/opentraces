"""Adapter contract for agent session parsers.

Uses typing.Protocol (structural typing, not inheritance) so new adapters
only need to implement the interface without importing this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Protocol, runtime_checkable

from opentraces_schema import TraceRecord


@runtime_checkable
class SessionParser(Protocol):
    """Protocol that all agent parsers must satisfy."""

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
