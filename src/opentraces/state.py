"""Staging state machine and upload tracking.

Manages the lifecycle of traces through:
  discovered -> parsed -> staged -> reviewing -> approved -> uploading -> uploaded
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
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .config import OPENTRACES_DIR, STATE_PATH, STAGING_DIR, UPLOADED_DIR


class TraceStatus(str, Enum):
    DISCOVERED = "discovered"
    PARSED = "parsed"
    STAGED = "staged"
    REVIEWING = "reviewing"
    APPROVED = "approved"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    REJECTED = "rejected"
    FAILED = "failed"


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

    def __init__(self) -> None:
        self._state: dict[str, Any] = {"processed_files": {}, "traces": {}}
        self._load()

    def _load(self) -> None:
        if STATE_PATH.exists():
            try:
                self._state = json.loads(STATE_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                self._state = {"processed_files": {}, "traces": {}}

    def save(self) -> None:
        OPENTRACES_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(self._state, indent=2, default=str))

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
        self.save()

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
        self.save()

    def get_traces_by_status(self, status: TraceStatus) -> list[TraceStagingEntry]:
        return [
            TraceStagingEntry(**v)
            for v in self._state["traces"].values()
            if v.get("status") == status.value
        ]

    def get_pending_upload_traces(self) -> list[TraceStagingEntry]:
        """Get traces ready for upload (approved or previously failed)."""
        return [
            TraceStagingEntry(**v)
            for v in self._state["traces"].values()
            if v.get("status") in (TraceStatus.APPROVED.value, TraceStatus.FAILED.value)
        ]


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
