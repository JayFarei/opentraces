"""Shared data access for all review clients (CLI, TUI, web).

Provides the common operations that every client needs: loading staged
traces from disk and resolving their visible review stage.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state import StateManager

logger = logging.getLogger(__name__)
from .workflow import resolve_visible_stage


@dataclass
class TraceIndexEntry:
    """Lightweight metadata for the session list, without loading full steps."""

    trace_id: str
    session_id: str
    timestamp_start: str | None
    task_description: str
    agent_name: str
    agent_model: str
    total_steps: int
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost_usd: float | None
    security_flags_count: int
    file_path: str  # path to JSONL file for lazy loading


def load_traces(staging_dir: Path) -> list[dict[str, Any]]:
    """Load all staged traces from JSONL files in the staging directory."""
    traces: list[dict[str, Any]] = []
    if not staging_dir.exists():
        return traces
    for jsonl_file in sorted(staging_dir.glob("*.jsonl")):
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


# ------------------------------------------------------------------
# Index-based lazy loading (performance layer)
# ------------------------------------------------------------------


def build_trace_index(staging_dir: Path) -> list[TraceIndexEntry]:
    """Parse all JSONL files, extract metadata only, write index cache."""
    entries: list[TraceIndexEntry] = []
    for jsonl_file in sorted(staging_dir.glob("*.jsonl")):
        try:
            text = jsonl_file.read_text().strip()
            if not text:
                continue
            data = json.loads(text.splitlines()[0])
            entry = TraceIndexEntry(
                trace_id=data.get("trace_id", ""),
                session_id=data.get("session_id", ""),
                timestamp_start=data.get("timestamp_start"),
                task_description=(data.get("task", {}).get("description") or "No description"),
                agent_name=(data.get("agent", {}).get("name") or "unknown"),
                agent_model=(data.get("agent", {}).get("model") or "unknown"),
                total_steps=data.get("metrics", {}).get("total_steps", len(data.get("steps", []))),
                total_input_tokens=data.get("metrics", {}).get("total_input_tokens", 0),
                total_output_tokens=data.get("metrics", {}).get("total_output_tokens", 0),
                estimated_cost_usd=data.get("metrics", {}).get("estimated_cost_usd"),
                security_flags_count=len(data.get("_security_flags", [])),
                file_path=str(jsonl_file),
            )
            entries.append(entry)
        except (json.JSONDecodeError, OSError):
            continue

    # Write index cache
    index_path = staging_dir.parent / "trace_index.json"
    cache = [dataclasses.asdict(e) for e in entries]
    index_path.write_text(json.dumps(cache))
    return entries


def load_trace_index(staging_dir: Path) -> list[TraceIndexEntry]:
    """Load trace index from cache if fresh, otherwise rebuild."""
    index_path = staging_dir.parent / "trace_index.json"
    try:
        if index_path.exists():
            index_mtime = index_path.stat().st_mtime
            staging_mtime = staging_dir.stat().st_mtime
            if index_mtime >= staging_mtime:
                # Cache is fresh
                cache = json.loads(index_path.read_text())
                return [TraceIndexEntry(**entry) for entry in cache]
    except (json.JSONDecodeError, OSError, TypeError):
        pass
    # Rebuild
    return build_trace_index(staging_dir)


def load_trace_full(file_path: str | Path) -> dict[str, Any] | None:
    """Load a single trace JSONL file fully (including steps)."""
    try:
        text = Path(file_path).read_text().strip()
        if text:
            return json.loads(text.splitlines()[0])
    except (json.JSONDecodeError, OSError):
        pass
    return None
