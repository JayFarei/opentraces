"""Shared uniform read-verb envelope helper (ADR-0007 lint L5).

L5 wants every visible read verb's ``--json`` output to carry a uniform
``{status, schema_version, ...}`` header. This module offers the single
constructor family slices adopt to emit that header consistently. It is a
convenience, NOT a mass migration: F1 introduces the helper; each family
slice adopts it (and empties its L5 quarantine entry) on its own schedule.
"""
from __future__ import annotations

from typing import Any


def envelope(schema_version: str, status: str = "ok", **fields: Any) -> dict[str, Any]:
    """Build the uniform read-verb envelope.

    Returns a dict whose first two keys are always ``status`` then
    ``schema_version`` (the L5 header), followed by the caller's payload
    fields in declaration order. Extra ``status``/``schema_version`` values
    passed via ``**fields`` are ignored in favor of the positional args so
    the header is unambiguous.
    """
    fields.pop("status", None)
    fields.pop("schema_version", None)
    return {"status": status, "schema_version": schema_version, **fields}
