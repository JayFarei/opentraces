"""Single-source UTC timestamp helpers used across core/."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now_str() -> str:
    """Return the current UTC time as an ISO-8601 string ending in 'Z'."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
