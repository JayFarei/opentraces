"""Publish pipeline: serializers (formats) + destinations.

Collapses the former `exporters/` (format projections) and `upload/`
(destinations) packages into a single namespace.

Registries are lazy — submodules are imported on demand so that optional
dependencies (e.g., huggingface_hub) don't get pulled into light CLI paths.
"""

from __future__ import annotations

from typing import Any

SERIALIZERS: dict[str, str] = {
    "atif": "opentraces.publish.atif",
    "agent_trace": "opentraces.publish.agent_trace",
}

DESTINATIONS: dict[str, str] = {
    "huggingface": "opentraces.publish.huggingface",
}

_EXPORTERS: dict[str, type] = {}
_exporters_registered = False


def _register_default_exporters() -> None:
    from .atif import ATIFExporter

    _EXPORTERS["atif"] = ATIFExporter


def get_exporters() -> dict[str, type]:
    """Return the exporters registry, registering defaults on first call."""
    global _exporters_registered
    if not _exporters_registered:
        _register_default_exporters()
        _exporters_registered = True
    return _EXPORTERS


def get_serializer(name: str) -> Any:
    """Import and return the serializer module by name."""
    import importlib

    if name not in SERIALIZERS:
        raise KeyError(f"Unknown serializer: {name!r}")
    return importlib.import_module(SERIALIZERS[name])


def get_destination(name: str) -> Any:
    """Import and return the destination module by name."""
    import importlib

    if name not in DESTINATIONS:
        raise KeyError(f"Unknown destination: {name!r}")
    return importlib.import_module(DESTINATIONS[name])


__all__ = [
    "SERIALIZERS",
    "DESTINATIONS",
    "get_exporters",
    "get_serializer",
    "get_destination",
]
