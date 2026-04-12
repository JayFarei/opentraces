"""Shared data access for all review clients (CLI, TUI, web).

Provides the common operations that every client needs: loading staged
traces from disk and resolving their visible review stage.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .state import StateManager

logger = logging.getLogger(__name__)
from .workflow import resolve_visible_stage


def load_traces(staging_dir: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load staged traces from JSONL files in the staging directory.

    With ``limit`` set, only the most recent ``limit`` files (by mtime) are
    read. This keeps clients responsive on large inboxes — big staging
    dirs (thousands of files) are common in long-running projects and
    loading them all up-front blocks startup for seconds.
    """
    traces: list[dict[str, Any]] = []
    if not staging_dir.exists():
        return traces
    files = list(staging_dir.glob("*.jsonl"))
    if limit is not None and len(files) > limit:
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        files = files[:limit]
    files.sort()  # deterministic order for downstream sorting
    for jsonl_file in files:
        try:
            text = jsonl_file.read_text().strip()
            for line in text.splitlines():
                line = line.strip()
                if line:
                    traces.append(json.loads(line))
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Skipping malformed trace file %s: %s", jsonl_file, e)
            continue
    return traces


def load_trace_records(staging_dir: Path, since_iso: str | None = None) -> list:
    """Load staged traces as validated TraceRecords, skipping malformed rows.

    `since_iso` optionally prunes by `timestamp_end` *before* the Pydantic
    validate step — useful on hot paths (e.g. the post-commit hook) where
    a staging dir of hundreds of historical rows would otherwise be
    validated on every commit.
    """
    from opentraces_schema import TraceRecord

    records: list = []
    for raw in load_traces(staging_dir):
        if since_iso is not None:
            ts = raw.get("timestamp_end")
            if not ts or ts < since_iso:
                continue
        try:
            records.append(TraceRecord.model_validate(raw))
        except Exception as e:
            logger.debug("Skipping invalid trace record: %s", e)
    return records


def get_stage(state: StateManager, trace_id: str) -> str:
    """Resolve the visible stage for a trace."""
    entry = state.get_trace(trace_id)
    return resolve_visible_stage(entry.status) if entry else "inbox"


def redact_step(step: dict[str, Any]) -> None:
    """Redact a single step dict in-place, clearing all sensitive fields.

    Used by both the CLI ``session redact`` command and the web server
    ``/api/session/<id>/step/<idx>/redact`` endpoint.
    """
    step["content"] = "[REDACTED]"
    step["reasoning_content"] = None
    step["tool_calls"] = []
    step["observations"] = []
    step["snippets"] = []
