"""Trace format exporters.

Registries map format names to exporter classes (not instances)
for lazy instantiation. The CLI imports these registries inside
command functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import FormatExporter

EXPORTERS: dict[str, type] = {}

_registered = False


def _register_defaults() -> None:
    from .atif import ATIFExporter

    EXPORTERS["atif"] = ATIFExporter


def get_exporters() -> dict[str, type]:
    """Get the EXPORTERS registry, registering defaults on first call."""
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return EXPORTERS
