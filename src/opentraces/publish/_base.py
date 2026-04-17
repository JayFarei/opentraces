"""Adapter contract for trace format exporters.

Uses typing.Protocol (structural typing, not inheritance) so new exporters
only need to implement the interface without importing this module.

Each exporter projects TraceRecord (our superset) into a downstream schema.
All exports are lossy, the target format captures a subset of what
TraceRecord holds.
"""

from __future__ import annotations

from typing import Iterator, Literal, Protocol, runtime_checkable

from opentraces_schema import TraceRecord

FieldStatus = Literal["full", "partial", "dropped"]


@runtime_checkable
class FormatExporter(Protocol):
    """Protocol for trace format exporters."""

    format_name: str
    file_extension: str
    description: str

    def export_traces(self, records: list[TraceRecord]) -> Iterator[str]:
        """Convert TraceRecords to the target format.

        Yields one string per output unit (e.g., one JSONL line).
        Skips records that fail conversion and logs the error.
        """
        ...

    def field_coverage(self) -> dict[str, FieldStatus]:
        """Report which TraceRecord fields this exporter preserves.

        Returns a dict mapping field group names to their preservation status:
        - "full": all sub-fields preserved
        - "partial": some sub-fields preserved, some dropped
        - "dropped": field not included in export

        Enables 'opentraces export --format X --dry-run' to show
        what will be kept vs dropped before committing to an export.
        """
        ...
