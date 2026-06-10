"""Small state markers for the read-only trace search snapshot."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths


def dirty_marker_path() -> Path:
    return paths.OPENTRACES_DIR / "index" / "search_snapshot.dirty.json"


def current_dirty_token() -> str | None:
    path = dirty_marker_path()
    try:
        return path.read_text(encoding="utf-8") if path.exists() else None
    except OSError:
        return "__unreadable__"


def mark_search_snapshot_dirty(reason: str, *, trace_id: str | None = None) -> None:
    path = dirty_marker_path()
    payload: dict[str, Any] = {
        "schema_version": "opentraces.trace_search_snapshot_dirty.v1",
        "reason": reason,
        "trace_id": trace_id,
        "marked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "token": str(time.time_ns()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def clear_dirty_marker_if_unchanged(token: str | None) -> None:
    if token is None:
        return
    try:
        path = dirty_marker_path()
        if path.exists() and path.read_text(encoding="utf-8") == token:
            path.unlink()
    except OSError:
        pass
