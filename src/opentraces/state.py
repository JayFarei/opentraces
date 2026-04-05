"""Staging state machine and upload tracking.

Manages the lifecycle of traces through:
  discovered -> parsed -> staged -> reviewing -> approved -> committed -> uploading -> uploaded
                                              -> rejected
                                    uploading -> failed -> staged (retry)

Tracks processed session files for incremental re-runs.
Uses file locks to prevent concurrent upload corruption.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .paths import STATE_PATH, STAGING_DIR


class TraceStatus(str, Enum):
    DISCOVERED = "discovered"
    PARSED = "parsed"
    STAGED = "staged"
    REVIEWING = "reviewing"
    COMMITTED = "committed"
    APPROVED = "approved"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class CommitGroup:
    """A group of traces committed together for push."""

    commit_id: str
    trace_ids: list[str]
    message: str
    created_at: str


@dataclass
class ProcessedFile:
    """Tracks a processed session file for incremental re-runs."""

    file_path: str
    inode: int
    mtime: float
    last_byte_offset: int


@dataclass
class TraceStagingEntry:
    """State for a single trace in the staging pipeline."""

    trace_id: str
    session_id: str
    status: TraceStatus
    file_path: str | None = None
    error: str | None = None
    uploaded_at: str | None = None
    created_at: float = field(default_factory=time.time)


class StateManager:
    """Manages persistent state for incremental processing and upload tracking."""

    def __init__(self, state_path: Path | None = None) -> None:
        self._state_path = state_path or STATE_PATH
        self._state: dict[str, Any] = {"processed_files": {}, "traces": {}, "commit_groups": {}}
        self._load()

    def _load(self) -> None:
        if self._state_path.exists():
            try:
                self._state = json.loads(self._state_path.read_text())
                # Ensure commit_groups key exists for older state files
                if "commit_groups" not in self._state:
                    self._state["commit_groups"] = {}
            except (json.JSONDecodeError, OSError):
                self._state = {"processed_files": {}, "traces": {}, "commit_groups": {}}

    def save(self) -> None:
        """Mark state as needing a save (buffered).

        Call ``flush()`` to actually write to disk.  For backward compat,
        callers that previously relied on immediate writes should add a
        ``flush()`` call or use a periodic timer.
        """
        self._save_dirty = True

    def flush(self) -> None:
        """Actually write state to disk if dirty."""
        if not getattr(self, "_save_dirty", False):
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2, default=str))
        self._save_dirty = False

    def save_immediate(self) -> None:
        """Write state to disk immediately (bypass buffering)."""
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2, default=str))
        self._save_dirty = False

    # --- Processed files tracking ---

    def get_processed_file(self, file_path: str) -> ProcessedFile | None:
        entry = self._state["processed_files"].get(file_path)
        if entry is None:
            return None
        return ProcessedFile(**entry)

    def mark_file_processed(self, pf: ProcessedFile) -> None:
        self._state["processed_files"][pf.file_path] = {
            "file_path": pf.file_path,
            "inode": pf.inode,
            "mtime": pf.mtime,
            "last_byte_offset": pf.last_byte_offset,
        }
        self.save_immediate()

    def should_reprocess(self, file_path: str) -> tuple[bool, int]:
        """Check if a file needs reprocessing. Returns (should_reprocess, byte_offset)."""
        path = Path(file_path)
        if not path.exists():
            return False, 0

        stat = path.stat()
        prev = self.get_processed_file(file_path)

        if prev is None:
            return True, 0

        # File replaced (different inode) or modified
        if stat.st_ino != prev.inode or stat.st_mtime > prev.mtime:
            # If same inode but newer mtime, we can resume from offset
            if stat.st_ino == prev.inode:
                return True, prev.last_byte_offset
            # Different inode means file was replaced, start from 0
            return True, 0

        return False, 0

    # --- Trace staging ---

    def get_trace(self, trace_id: str) -> TraceStagingEntry | None:
        entry = self._state["traces"].get(trace_id)
        if entry is None:
            return None
        return TraceStagingEntry(**entry)

    def set_trace_status(self, trace_id: str, status: TraceStatus, **kwargs: Any) -> None:
        if trace_id not in self._state["traces"]:
            self._state["traces"][trace_id] = {
                "trace_id": trace_id,
                "session_id": "",
                "status": status.value,
                "created_at": time.time(),
            }
        self._state["traces"][trace_id]["status"] = status.value
        self._state["traces"][trace_id].update(kwargs)
        self.save_immediate()

    def delete_trace(self, trace_id: str) -> None:
        """Remove a trace from state entirely (used by discard action)."""
        self._state["traces"].pop(trace_id, None)
        self.save()

    def get_traces_by_status(self, status: TraceStatus) -> list[TraceStagingEntry]:
        return [
            TraceStagingEntry(**v)
            for v in self._state["traces"].values()
            if v.get("status") == status.value
        ]

    def get_pending_upload_traces(self) -> list[TraceStagingEntry]:
        """Get traces ready for upload (committed or previously failed)."""
        return [
            TraceStagingEntry(**v)
            for v in self._state["traces"].values()
            if v.get("status") in (TraceStatus.COMMITTED.value, TraceStatus.FAILED.value)
        ]

    # --- Commit groups ---

    def create_commit_group(self, trace_ids: list[str], message: str) -> str:
        """Create a commit group from approved traces. Returns commit_id."""
        import datetime

        commit_id = uuid.uuid4().hex[:12]
        created_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._state["commit_groups"][commit_id] = {
            "commit_id": commit_id,
            "trace_ids": trace_ids,
            "message": message,
            "created_at": created_at,
        }
        for trace_id in trace_ids:
            self.set_trace_status(trace_id, TraceStatus.COMMITTED)
        self.save_immediate()
        return commit_id

    def get_committed_traces(self) -> dict[str, dict]:
        """Get all traces with COMMITTED status."""
        return {
            tid: info for tid, info in self._state.get("traces", {}).items()
            if info.get("status") == TraceStatus.COMMITTED.value
        }

    def get_commit_group(self, commit_id: str) -> CommitGroup | None:
        entry = self._state["commit_groups"].get(commit_id)
        if entry is None:
            return None
        return CommitGroup(**entry)

    def get_commit_groups(self) -> list[CommitGroup]:
        return [CommitGroup(**v) for v in self._state["commit_groups"].values()]


class StagingLock:
    """File lock on the staging directory to prevent concurrent uploads."""

    def __init__(self) -> None:
        STAGING_DIR.mkdir(parents=True, exist_ok=True)
        self._lock_path = STAGING_DIR / ".lock"
        self._lock_fd: int | None = None

    def acquire(self) -> bool:
        """Acquire exclusive lock. Returns False if already locked."""
        try:
            self._lock_fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, BlockingIOError):
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            return False

    def release(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> StagingLock:
        if not self.acquire():
            raise RuntimeError(
                "Could not acquire staging lock. Another opentraces process may be uploading."
            )
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()
