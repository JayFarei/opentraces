"""Index-based trace store with LRU cache for full trace loading.

Uses the lightweight ``TraceIndexEntry`` for the session list, loading
full trace data on demand.  Keeps at most 20 full traces in memory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...inbox import (
    TraceIndexEntry,
    get_stage,
    load_trace_full,
    load_trace_index,
    load_traces,
)
from ...state import StateManager
from ...workflow import VISIBLE_STAGE_ORDER


class TraceStore:
    """Index-based trace store with LRU full-trace cache."""

    def __init__(self, staging_dir: Path, state: StateManager) -> None:
        self._staging_dir = staging_dir
        self._state = state
        self._index: list[TraceIndexEntry] = []
        self._by_id: dict[str, TraceIndexEntry] = {}
        self._by_stage: dict[str, list[TraceIndexEntry]] = {}
        self._full_cache: dict[str, dict[str, Any]] = {}  # LRU, max 20
        self._cache_order: list[str] = []
        self._last_mtime: float = 0.0
        self.reload()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def index(self) -> list[TraceIndexEntry]:
        return self._index

    @property
    def traces(self) -> list[TraceIndexEntry]:
        """Alias kept for backward compat with screens that read .traces."""
        return self._index

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Alias for reload(), used by InboxScreen."""
        self.reload()

    def reload(self) -> None:
        """Reload from the lightweight index (fast)."""
        self._index = load_trace_index(self._staging_dir)

        # Sort by stage order then timestamp
        def _key(entry: TraceIndexEntry) -> tuple[int, str]:
            stage = get_stage(self._state, entry.trace_id) if entry.trace_id else "inbox"
            try:
                idx = VISIBLE_STAGE_ORDER.index(stage)
            except ValueError:
                idx = 0
            return (idx, entry.timestamp_start or "")

        self._index.sort(key=_key)

        # Rebuild lookup indices
        self._by_id = {}
        self._by_stage = {}
        for entry in self._index:
            if entry.trace_id:
                self._by_id[entry.trace_id] = entry
            stage = get_stage(self._state, entry.trace_id) if entry.trace_id else "inbox"
            self._by_stage.setdefault(stage, []).append(entry)

        # Record mtime
        try:
            self._last_mtime = self._staging_dir.stat().st_mtime
        except OSError:
            self._last_mtime = 0.0

    # ------------------------------------------------------------------
    # Full trace loading (lazy, LRU cached)
    # ------------------------------------------------------------------

    def get_full_trace(self, trace_id: str) -> dict[str, Any] | None:
        """Load full trace, using LRU cache."""
        if trace_id in self._full_cache:
            # Move to end of LRU order
            self._cache_order.remove(trace_id)
            self._cache_order.append(trace_id)
            return self._full_cache[trace_id]
        entry = self._by_id.get(trace_id)
        if not entry:
            return None
        data = load_trace_full(entry.file_path)
        if data:
            self._cache_full(trace_id, data)
        return data

    def _cache_full(self, trace_id: str, data: dict[str, Any]) -> None:
        """LRU cache with max 20 entries."""
        if trace_id in self._full_cache:
            self._cache_order.remove(trace_id)
        elif len(self._full_cache) >= 20:
            evict = self._cache_order.pop(0)
            del self._full_cache[evict]
        self._full_cache[trace_id] = data
        self._cache_order.append(trace_id)

    # ------------------------------------------------------------------
    # Dirty detection
    # ------------------------------------------------------------------

    def mark_dirty(self) -> None:
        """Force the next is_dirty() check to return True."""
        self._last_mtime = 0.0

    def is_dirty(self) -> bool:
        """Check staging dir mtime against last known. No watchdog dependency."""
        try:
            current = self._staging_dir.stat().st_mtime
        except OSError:
            return False
        return current != self._last_mtime

    def check_and_reload(self) -> bool:
        """If dirty, reload. Returns True if reloaded."""
        if self.is_dirty():
            self.reload()
            return True
        return False

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_by_id(self, trace_id: str) -> TraceIndexEntry | None:
        return self._by_id.get(trace_id)

    def get_by_stage(self, stage: str) -> list[TraceIndexEntry]:
        return self._by_stage.get(stage, [])

    def stage_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for stage in VISIBLE_STAGE_ORDER:
            counts[stage] = len(self._by_stage.get(stage, []))
        return counts

    def sorted_traces(self) -> list[TraceIndexEntry]:
        """Return index entries sorted by stage order then timestamp."""
        return list(self._index)
