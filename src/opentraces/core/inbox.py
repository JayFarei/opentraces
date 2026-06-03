"""Shared data access for all review clients (CLI, TUI, web).

Provides the common operations that every client needs: loading staged
traces from disk and resolving their visible review stage.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from .state import StateManager

logger = logging.getLogger(__name__)
from .trace_stage import resolve_visible_stage


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

    `since_iso` prunes to traces whose `timestamp_end` is at/after the cutoff.
    This streams file-by-file (never materialising the whole staging dir at
    once) and, when `since_iso` is set, skips files whose mtime is clearly older
    than the window via a cheap stat — without it the post-commit hot path
    loaded a 1000+ row inbox (~GBs of dicts) into memory on every commit just to
    return the handful inside a 2h window. `timestamp_end` remains the
    authoritative filter; the mtime gate is a conservative (1-day-margin)
    optimisation that can only skip files too old to qualify.
    """
    from opentraces_schema import TraceRecord

    if not staging_dir.exists():
        return []

    mtime_floor: float | None = None
    if since_iso is not None:
        from datetime import datetime, timedelta

        try:
            # A trace's file is written at ~timestamp_end, so a file modified
            # more than a day before the window cannot hold a qualifying record.
            mtime_floor = (datetime.fromisoformat(since_iso) - timedelta(days=1)).timestamp()
        except ValueError:
            mtime_floor = None

    records: list = []
    for jsonl_file in sorted(staging_dir.glob("*.jsonl")):
        if mtime_floor is not None:
            try:
                if jsonl_file.stat().st_mtime < mtime_floor:
                    continue
            except OSError:
                continue
        try:
            text = jsonl_file.read_text().strip()
        except OSError as e:
            logger.debug("Skipping unreadable trace file %s: %s", jsonl_file, e)
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                logger.debug("Skipping malformed trace line in %s: %s", jsonl_file, e)
                continue
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
    ``/api/trace/<id>/step/<idx>/redact`` endpoint.
    """
    step["content"] = "[REDACTED]"
    step["reasoning_content"] = None
    step["tool_calls"] = []
    step["observations"] = []
    step["snippets"] = []


# Default scan targets when no explicit ``field`` is passed to
# ``redact_pattern``. Dotted paths walk into dicts/lists — e.g. for each
# ``observations`` entry we scan its ``stdout`` string. Keep this list in
# sync with the step fields ``redact_step`` whole-blanks above.
_DEFAULT_REDACT_FIELDS: tuple[str, ...] = (
    "content",
    "reasoning_content",
    "tool_calls.input",
    "observations.stdout",
)

_REDACTED = "[REDACTED]"


def _apply_replacement(value: Any, replacer) -> Any:
    """Apply ``replacer`` (a ``re.sub`` partial) to any string leaves inside value."""
    if isinstance(value, str):
        return replacer(value)
    if isinstance(value, list):
        return [_apply_replacement(v, replacer) for v in value]
    if isinstance(value, dict):
        return {k: _apply_replacement(v, replacer) for k, v in value.items()}
    return value


def _redact_at_path(container: Any, path: list[str], replacer) -> Any:
    """Walk ``container`` along dotted ``path``, applying ``replacer`` at leaves.

    When an intermediate element is a list, we recurse into each item; this
    mirrors the trace shape where e.g. ``observations`` is a list of dicts.
    Returns the (possibly new) container — callers must re-assign to preserve
    replaced strings inside dicts/lists.
    """
    if not path:
        return _apply_replacement(container, replacer)
    head, rest = path[0], path[1:]
    if isinstance(container, list):
        return [_redact_at_path(item, path, replacer) for item in container]
    if isinstance(container, dict):
        if head not in container:
            return container
        container[head] = _redact_at_path(container[head], rest, replacer)
        return container
    return container


def redact_pattern(
    trace: dict[str, Any],
    pattern: str,
    *,
    regex: bool = False,
    field: str | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    """Find-and-replace matched substrings with ``[REDACTED]`` inside a trace.

    Unlike :func:`redact_step`, which blanks an entire step, this helper
    performs inline replacement so non-matching content survives. Scope is
    controlled by ``field`` (dotted path like ``observations.stdout``) and
    ``step`` (index into ``trace['steps']``); either/both may be omitted to
    broaden scope.

    The trace dict is mutated in place and also returned, matching the
    ``redact_step`` convention.

    Raises:
        ValueError: if ``pattern`` is empty, ``regex=True`` but the pattern
            fails to compile, or ``step`` is out of range.
    """
    if not pattern:
        raise ValueError("pattern must be non-empty")

    if regex:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"invalid regex pattern: {exc}") from exc
    else:
        compiled = re.compile(re.escape(pattern))

    def _replacer(s: str) -> str:
        return compiled.sub(_REDACTED, s)

    steps = trace.get("steps") or []
    if step is not None:
        if step < 0 or step >= len(steps):
            raise ValueError(f"step index {step} out of range")
        target_indices: list[int] = [step]
    else:
        target_indices = list(range(len(steps)))

    fields = [field] if field is not None else list(_DEFAULT_REDACT_FIELDS)

    for idx in target_indices:
        step_dict = steps[idx]
        if not isinstance(step_dict, dict):
            continue
        for fpath in fields:
            parts = fpath.split(".") if fpath else []
            if not parts:
                continue
            head, rest = parts[0], parts[1:]
            if head not in step_dict:
                continue
            step_dict[head] = _redact_at_path(step_dict[head], rest, _replacer)

    return trace
