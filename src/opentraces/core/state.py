"""Staging state machine and upload tracking.

Manages the lifecycle of traces through:
  discovered -> parsed -> staged -> reviewing -> approved -> committed -> uploading -> uploaded
                                              -> rejected
                                    uploading -> failed -> staged (retry)

Tracks processed session files for incremental re-runs.
Uses file locks to prevent concurrent upload corruption.

Schema versioning: ``STATE_SCHEMA_VERSION`` is the on-disk schema version of
the state.json file. ``StateManager._load()`` migrates legacy v1 state files
(no ``state_version`` key) into the current shape.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# NOTE: config.py imports workflow.py which imports state.py — to avoid the
# import cycle we defer the config import inside TraceLock.

STATE_SCHEMA_VERSION = "3"


class UnknownRemoteError(KeyError):
    """Raised when a public lookup names a remote the project doesn't know.

    Typically thrown after ``rename_remote()`` or ``forget_remote()``: the
    name was once valid but is gone. Subclasses ``KeyError`` so existing
    ``except KeyError`` blocks catch it.
    """


def _utcnow_iso() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


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
    BLOCKED = "blocked"


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
    """State for a single trace in the staging pipeline.

    ``uploaded_to`` maps remote name -> ISO8601 upload timestamp. Each
    successful ``ot push`` to a remote writes one entry. Switching remotes
    and pushing replays history because a trace shows up in
    ``pending_for(<new_remote>)`` until it has an entry there.
    """

    trace_id: str
    session_id: str
    status: TraceStatus
    file_path: str | None = None
    error: str | None = None
    uploaded_at: str | None = None
    created_at: float = field(default_factory=time.time)
    block_reason: str | None = None
    uploaded_to: dict[str, str] = field(default_factory=dict)


class StateManager:
    """Manages persistent state for incremental processing and upload tracking."""

    def __init__(self, state_path: Path | None = None) -> None:
        if state_path is None:
            raise ValueError(
                "StateManager requires an explicit state_path; "
                "use get_project_state_path(project_dir)"
            )
        self._state_path = state_path
        self._state: dict[str, Any] = {"processed_files": {}, "traces": {}, "commit_groups": {},
             "last_backfilled_commit": None, "last_backfill_at": None}
        self._load()

    def _load(self) -> None:
        if self._state_path.exists():
            try:
                self._state = json.loads(self._state_path.read_text())
                # Ensure commit_groups key exists for older state files
                if "commit_groups" not in self._state:
                    self._state["commit_groups"] = {}
            except (json.JSONDecodeError, OSError):
                self._state = {"processed_files": {}, "traces": {}, "commit_groups": {},
             "last_backfilled_commit": None, "last_backfill_at": None}
        self._migrate()

    def _migrate(self) -> None:
        """Migrate state from any older schema to STATE_SCHEMA_VERSION.

        v1 -> v2: backfill ``uploaded_to`` on every trace with status=UPLOADED.
        We don't have access to the project's ``ProjectConfig.active_remote``
        here without an extra dependency, so we use ``"origin"`` as the
        canonical name — that is the remote name produced by the
        ProjectConfig migration too, so the two halves agree by convention.
        """
        version = self._state.get("state_version")
        if version == STATE_SCHEMA_VERSION:
            return

        # v1 (no version key) -> v2: backfill uploaded_to.
        if version is None or version == "1":
            for entry in self._state.get("traces", {}).values():
                if entry.get("status") == TraceStatus.UPLOADED.value:
                    if not entry.get("uploaded_to"):
                        ts = entry.get("uploaded_at") or "1970-01-01T00:00:00Z"
                        entry["uploaded_to"] = {"origin": ts}

        # v2 -> v3 (plan 043 phase 1): add last_backfilled_commit +
        # last_backfill_at at the top level. Both default to None; the
        # `ot backfill` verb writes them when it advances.
        self._state.setdefault("last_backfilled_commit", None)
        self._state.setdefault("last_backfill_at", None)

        self._state["state_version"] = STATE_SCHEMA_VERSION

    def save(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        # Always stamp the current schema version on save.
        self._state["state_version"] = STATE_SCHEMA_VERSION
        self._state_path.write_text(json.dumps(self._state, indent=2, default=str))

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

    def get_pending_upload_traces(
        self, remote_name: str | None = None
    ) -> list[TraceStagingEntry]:
        """Get traces ready for upload (committed or previously failed).

        BLOCKED traces are excluded — parse errors and security findings
        must never reach upload. When ``remote_name`` is given, also
        include UPLOADED traces that have not been uploaded to that
        specific remote yet (full per-remote replay; see ``pending_for``
        for the per-remote-only set).
        """
        if remote_name is not None:
            return self.pending_for(remote_name)
        return [
            TraceStagingEntry(**v)
            for v in self._state["traces"].values()
            if v.get("status") in (TraceStatus.COMMITTED.value, TraceStatus.FAILED.value)
        ]

    def pending_for(self, remote_name: str) -> list[TraceStagingEntry]:
        """Traces missing from ``remote_name``.

        The set is: every COMMITTED or FAILED trace, plus every UPLOADED
        trace whose ``uploaded_to`` does not include ``remote_name``.
        Switching remotes and pushing replays full local history because
        every previously-pushed trace shows up as pending on the new
        remote until it has an entry there. BLOCKED and REJECTED traces
        are always excluded.
        """
        pending: list[TraceStagingEntry] = []
        for v in self._state["traces"].values():
            status = v.get("status")
            if status in (TraceStatus.COMMITTED.value, TraceStatus.FAILED.value):
                pending.append(TraceStagingEntry(**v))
            elif status == TraceStatus.UPLOADED.value:
                if remote_name not in (v.get("uploaded_to") or {}):
                    pending.append(TraceStagingEntry(**v))
        return pending

    def mark_uploaded_to(
        self, trace_id: str, remote_name: str, *, when: str | None = None
    ) -> None:
        """Record that ``trace_id`` was uploaded to ``remote_name``.

        Sets ``status=UPLOADED``, writes ``uploaded_to[remote_name] = ts``,
        and refreshes ``uploaded_at``. Idempotent — calling twice with
        the same remote just refreshes the timestamp.
        """
        ts = when or _utcnow_iso()
        entry = self._state["traces"].get(trace_id)
        if entry is None:
            entry = {
                "trace_id": trace_id,
                "session_id": "",
                "status": TraceStatus.UPLOADED.value,
                "created_at": time.time(),
            }
            self._state["traces"][trace_id] = entry
        entry["status"] = TraceStatus.UPLOADED.value
        existing = entry.get("uploaded_to") or {}
        existing[remote_name] = ts
        entry["uploaded_to"] = existing
        entry["uploaded_at"] = ts
        self.save()

    def rename_remote(self, old: str, new: str) -> None:
        """Atomically rename a remote across every trace's ``uploaded_to``.

        Raises ``UnknownRemoteError`` if no trace references ``old``.
        Callers should also update ``ProjectConfig.remotes`` and
        ``active_remote`` in the same logical transaction.
        """
        touched = 0
        for entry in self._state["traces"].values():
            ut = entry.get("uploaded_to") or {}
            if old in ut:
                ut[new] = ut.pop(old)
                entry["uploaded_to"] = ut
                touched += 1
        if touched == 0:
            raise UnknownRemoteError(old)
        self.save()

    def forget_remote(self, remote_name: str) -> None:
        """Atomically drop ``remote_name`` from every trace's ``uploaded_to``.

        Raises ``UnknownRemoteError`` if no trace references it. Callers
        should also remove the entry from ``ProjectConfig.remotes`` and
        clear ``active_remote`` if it pointed there.
        """
        touched = 0
        for entry in self._state["traces"].values():
            ut = entry.get("uploaded_to") or {}
            if remote_name in ut:
                del ut[remote_name]
                entry["uploaded_to"] = ut
                touched += 1
        if touched == 0:
            raise UnknownRemoteError(remote_name)
        self.save()

    def block_trace(self, trace_id: str, reason: str, **kwargs: Any) -> None:
        """Move a trace to BLOCKED state with an explanatory reason.

        Used for parse errors, TruffleHog findings, and other hard-block
        conditions that must prevent upload. Idempotent — blocking an
        already-blocked trace updates the reason.
        """
        self.set_trace_status(
            trace_id, TraceStatus.BLOCKED, block_reason=reason, **kwargs
        )

    def get_blocked_traces(self) -> list[TraceStagingEntry]:
        """Return all traces currently in BLOCKED state."""
        return self.get_traces_by_status(TraceStatus.BLOCKED)

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
        self.save()
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

    # --- Backfill bookmark (plan 043 phase 1) ---

    def get_last_backfilled_commit(self) -> str | None:
        return self._state.get("last_backfilled_commit")

    def get_last_backfill_at(self) -> str | None:
        return self._state.get("last_backfill_at")

    def set_last_backfilled_commit(self, sha: str | None, *,
                                   when: str | None = None) -> None:
        self._state["last_backfilled_commit"] = sha
        self._state["last_backfill_at"] = when or _utcnow_iso()
        self.save()

    # --- Watcher bookmark (plan 043 phase 3) ---

    def get_last_watcher_run_at(self) -> str | None:
        return self._state.get("last_watcher_run_at")

    def set_last_watcher_run_at(self, when: str | None = None) -> None:
        self._state["last_watcher_run_at"] = when or _utcnow_iso()
        self.save()


class TraceLock:
    """File lock on a project's traces dir to prevent concurrent uploads."""

    def __init__(self, project_dir: Path) -> None:
        from .config import get_project_dir
        global_dir = get_project_dir(project_dir)
        global_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = global_dir / ".lock"
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

    def __enter__(self) -> TraceLock:
        if not self.acquire():
            raise RuntimeError(
                "Could not acquire trace lock. Another opentraces process may be uploading."
            )
        return self

    def __exit__(self, *args: Any) -> None:
        self.release()


# Back-compat alias for any external importers; new code should use TraceLock.
StagingLock = TraceLock
