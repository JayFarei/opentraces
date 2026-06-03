"""Polling runtime for Layer C filesystem mutation observation.

This module is intentionally conservative: it records path/blob transitions
and leaves attribution to ``core.trails.reconciler``. The first scan establishes
a baseline only; subsequent scans emit ``filesystem_mutation_observed`` events
for paths whose blob identity changed or disappeared.
"""
from __future__ import annotations

import contextlib
import errno
import json
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from ...core.trails.event_log import append_event_batch
from ...core._time import utc_now_str as _utc_now
from ...core.trails.models import GitObjectID, TrailEvent
from .observations import filesystem_mutation_observed_draft

DEFAULT_STATE_BASENAME = "opentraces-fs-watcher-state.json"
DEFAULT_LOCK_BASENAME = "opentraces-fs-watcher.lock"
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024
INTERNAL_PREFIXES = (
    ".git/",
    ".opentraces/",
    ".agent-trace/",
    ".claude/",
)


@dataclass(frozen=True)
class ObservedMutation:
    path: str
    before_blob_id: GitObjectID | None
    after_blob_id: GitObjectID | None
    observed_at_start: str
    observed_at_end: str


@dataclass(frozen=True)
class PollResult:
    baseline_initialized: bool = False
    paths_seen: int = 0
    observations: list[TrailEvent] = field(default_factory=list)
    mutations: list[ObservedMutation] = field(default_factory=list)
    skipped_paths: int = 0


def poll_project_once(
    repo: Path,
    *,
    state_path: Path | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    exclude_paths: Iterable[str | Path] | None = None,
    writer: str = "fs-watcher",
) -> PollResult:
    """Scan one project and append observation events for changed files.

    The baseline scan records current blob identities without emitting events.
    This avoids turning pre-existing dirty worktrees into false mutations.
    """
    repo = Path(repo).resolve()
    root = _worktree_root(repo)
    if root is None:
        return PollResult()
    repo = root

    with _observer_lock(repo):
        return _poll_project_once_unlocked(
            repo,
            state_path=state_path,
            max_file_bytes=max_file_bytes,
            exclude_paths=exclude_paths,
            writer=writer,
        )


def _poll_project_once_unlocked(
    repo: Path,
    *,
    state_path: Path | None,
    max_file_bytes: int,
    exclude_paths: Iterable[str | Path] | None,
    writer: str,
) -> PollResult:
    state_path = state_path or _default_state_path(repo)
    excluded = _normalize_excluded_paths(repo, exclude_paths)
    now = _utc_now()
    prior = _read_state(state_path)
    prior_paths = _state_paths(prior)
    has_baseline = isinstance(prior.get("paths"), dict)
    prior_scanned_at = prior.get("scanned_at") if isinstance(prior, dict) else None

    current_paths = _list_candidate_paths(repo)
    current: dict[str, dict[str, str]] = {}
    skipped_current: set[str] = set()
    skipped = 0
    for rel_path in current_paths:
        if _is_internal_path(rel_path) or rel_path in excluded:
            continue
        blob, status = _hash_worktree_file(
            repo,
            rel_path,
            max_file_bytes=max_file_bytes,
        )
        if blob is None:
            if status != "missing":
                skipped += 1
                skipped_current.add(rel_path)
            continue
        current[rel_path] = blob.model_dump(mode="json")

    baseline = not has_baseline
    mutations: list[ObservedMutation] = []
    observations: list[TrailEvent] = []
    if not baseline:
        drafts = []
        all_paths = sorted(set(prior_paths) | set(current))
        for rel_path in all_paths:
            if rel_path in skipped_current:
                continue
            before_raw = prior_paths.get(rel_path)
            after_raw = current.get(rel_path)
            if before_raw == after_raw:
                continue
            before = _object_id(before_raw)
            after = _object_id(after_raw)
            mutation = ObservedMutation(
                path=rel_path,
                before_blob_id=before,
                after_blob_id=after,
                observed_at_start=_timestamp_or_now(prior_scanned_at, now),
                observed_at_end=now,
            )
            mutations.append(mutation)
            drafts.append(
                filesystem_mutation_observed_draft(
                    path=rel_path,
                    observed_at_start=mutation.observed_at_start,
                    observed_at_end=mutation.observed_at_end,
                    before_blob_id=before,
                    after_blob_id=after,
                )
            )
        if drafts:
            observations = append_event_batch(repo, drafts, writer=writer)

    state_paths = dict(current)
    for rel_path in skipped_current:
        if rel_path in prior_paths:
            state_paths[rel_path] = prior_paths[rel_path]

    _write_state(
        state_path,
        {
            "schema_version": "opentraces.fs_watcher.state.v1",
            "repo": str(repo),
            "scanned_at": now,
            "paths": state_paths,
        },
    )
    return PollResult(
        baseline_initialized=baseline,
        paths_seen=len(current),
        observations=observations,
        mutations=mutations,
        skipped_paths=skipped,
    )


def _normalize_excluded_paths(
    repo: Path,
    paths: Iterable[str | Path] | None,
) -> set[str]:
    if paths is None:
        return set()
    excluded: set[str] = set()
    for raw in paths:
        try:
            path = Path(raw)
            if path.is_absolute():
                rel_path = path.resolve().relative_to(repo)
            else:
                rel_path = path
        except Exception:
            continue
        normalized = str(rel_path).replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized and normalized != ".":
            excluded.add(normalized)
    return excluded


def _timestamp_or_now(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _default_state_path(repo: Path) -> Path:
    git_dir = _git_dir(repo)
    if git_dir is not None:
        return git_dir / DEFAULT_STATE_BASENAME
    return repo / ".git" / DEFAULT_STATE_BASENAME


def _worktree_root(repo: Path) -> Path | None:
    out = _git(repo, "rev-parse", "--show-toplevel", check=False).strip()
    if not out:
        return None
    return Path(out).resolve()


def _git_dir(repo: Path) -> Path | None:
    git_dir = _git(repo, "rev-parse", "--git-dir", check=False).strip()
    if not git_dir:
        return None
    path = Path(git_dir)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


@contextlib.contextmanager
def _observer_lock(repo: Path) -> Iterator[None]:
    git_dir = _git_dir(repo)
    if git_dir is None:
        yield
        return
    lock_path = git_dir / DEFAULT_LOCK_BASENAME
    try:
        import fcntl  # type: ignore[import-not-found]
    except ImportError:
        yield
        return
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as exc:
            if exc.errno not in {errno.ENOLCK, errno.ENOTSUP}:
                raise
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _read_state(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _state_paths(state: dict[str, Any]) -> dict[str, dict[str, str]]:
    raw = state.get("paths") if isinstance(state, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for path, value in raw.items():
        if isinstance(path, str) and isinstance(value, dict):
            try:
                out[path] = GitObjectID.model_validate(value).model_dump(mode="json")
            except Exception:
                continue
    return out


def _write_state(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, sort_keys=True, separators=(",", ":"))
            f.write("\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _list_candidate_paths(repo: Path) -> list[str]:
    out = _git(
        repo,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        check=False,
    )
    if not out:
        return []
    return sorted(
        path.replace("\\", "/")
        for path in out.split("\0")
        if path
    )


def _is_internal_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return any(
        normalized == prefix[:-1] or normalized.startswith(prefix)
        for prefix in INTERNAL_PREFIXES
    )


def _hash_worktree_file(
    repo: Path,
    rel_path: str,
    *,
    max_file_bytes: int,
) -> tuple[GitObjectID | None, str | None]:
    path = repo / rel_path
    try:
        stat_result = path.lstat()
    except OSError:
        return None, "missing"
    if not stat.S_ISREG(stat_result.st_mode):
        return None, "not_regular"
    if stat_result.st_size > max_file_bytes:
        return None, "too_large"
    proc = subprocess.run(
        ["git", "hash-object", "-w", "--no-filters", "--", rel_path],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None, "hash_failed"
    oid = proc.stdout.strip()
    if not oid:
        return None, "hash_failed"
    algo = "sha256" if len(oid) == 64 else "sha1"
    return GitObjectID(algo=algo, hex=oid), None


def _object_id(value: dict[str, str] | None) -> GitObjectID | None:
    if value is None:
        return None
    try:
        return GitObjectID.model_validate(value)
    except Exception:
        return None
