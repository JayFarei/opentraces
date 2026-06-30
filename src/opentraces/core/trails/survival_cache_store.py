"""Local, evictable survival KV-cache (issue #116 A).

The survival cache used to be written as ``patch_survival_cached`` TrailEvents
appended to the immutable append-only canonical event log
(``refs/opentraces/local/events/v1``). Because the log is append-only, every
``trail track`` recompute that wrote a fresh row grew the ref permanently —
including on a stationary HEAD where the cache key never changes — so a
perf hint quietly inflated the durable, synced substrate.

This module relocates the cache WRITE into a local, evictable JSON store under
the COMMON git dir (``<git-common-dir>/opentraces/survival_cache.json``), keyed
by ``(trace_patch_id, observed_head_sha)`` — the same key the on-ref events
used. The store is a derived accelerator: it is safe to delete at any time, it
is never synced to a bucket or remote, and it is never part of the bucket
digest.

Back-compat: the survival read path overlays this store ON TOP of the on-ref
``build_survival_cache_index`` projection, so the ~505K legacy
``patch_survival_cached`` events already on live logs stay readable. No NEW
survival-cache events are appended to the ref.

Concurrency: like the old ``append_survival_cache_events``, writes are
best-effort. A read-modify-write race between two processes can drop a cache
entry (the loser's merge is overwritten), which only costs a later recompute —
it can never corrupt a survival answer, because correctness is owned by the
fresh-compute path, not the cache.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# Store identity + shape.
SCHEMA_VERSION = "opentraces.survival_cache.local.v1"
STORE_FILENAME = "survival_cache.json"
# NUL separates the two key components in the on-disk JSON object keys. It can
# never appear inside a trace-patch id (a hex digest) or a git sha.
_KEY_SEP = "\x00"
# Soft cap on retained rows. Stale-HEAD rows accumulate (the key embeds the
# observed HEAD sha), so the store is trimmed FIFO — the oldest-inserted rows
# are evicted first. Generous enough that a normal multi-thousand-patch repo
# never evicts a live-HEAD row mid-session.
DEFAULT_MAX_ENTRIES = 100_000


def _common_git_dir(repo: Path) -> Path | None:
    """Resolve the repo's COMMON git dir (shared across linked worktrees).

    Mirrors ``event_log._event_cache_path``: ``--git-common-dir`` (not
    ``--git-dir``) so N linked worktrees of the same repo share ONE store
    rather than a per-worktree copy. Returns ``None`` when the git dir can't
    be resolved (callers then skip the cache and fall back to fresh compute).
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    git_dir = Path(raw)
    if not git_dir.is_absolute():
        git_dir = (Path(repo) / git_dir).resolve()
    return git_dir


def store_path(repo: Path) -> Path | None:
    """Path to the local survival cache JSON, or ``None`` if unresolvable."""
    git_dir = _common_git_dir(Path(repo))
    if git_dir is None:
        return None
    return git_dir / "opentraces" / STORE_FILENAME


def _decode_entries(doc: Any) -> dict[str, Any]:
    if not isinstance(doc, dict):
        return {}
    entries = doc.get("entries")
    return entries if isinstance(entries, dict) else {}


def load_index(repo: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Return ``{(trace_patch_id, observed_head_sha): survival}`` from the store.

    The shape matches ``event_log.build_survival_cache_index`` so the survival
    read path can overlay the two indexes with a plain ``dict.update``. A
    missing or unreadable store yields ``{}`` (cache miss, fresh compute).
    """
    path = store_path(Path(repo))
    if path is None or not path.is_file():
        return {}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for composite, survival in _decode_entries(doc).items():
        if not isinstance(composite, str) or _KEY_SEP not in composite:
            continue
        patch_id, _, head_sha = composite.partition(_KEY_SEP)
        if patch_id and head_sha and isinstance(survival, dict):
            out[(patch_id, head_sha)] = survival
    return out


def write_entries(
    repo: Path,
    entries: dict[tuple[str, str], dict[str, Any]],
    *,
    max_entries: int = DEFAULT_MAX_ENTRIES,
) -> bool:
    """Merge ``entries`` into the store and persist atomically. Best-effort.

    ``entries`` maps ``(trace_patch_id, observed_head_sha)`` to the native
    survival row (the same dict ``sync_patch`` returns in ``current_survival``).
    Re-putting a key moves it to the most-recent position so FIFO eviction
    keeps the freshest rows. Returns ``True`` on a successful write, ``False``
    on any failure (a failed cache write never blocks the survival query).
    """
    if not entries:
        return False
    path = store_path(Path(repo))
    if path is None:
        return False

    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            existing = _decode_entries(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            existing = {}

    for (patch_id, head_sha), survival in entries.items():
        if not patch_id or not head_sha or not isinstance(survival, dict):
            continue
        composite = f"{patch_id}{_KEY_SEP}{head_sha}"
        # Move-to-end on re-put: drop then re-insert so insertion order tracks
        # recency for FIFO eviction.
        existing.pop(composite, None)
        existing[composite] = survival

    if max_entries > 0 and len(existing) > max_entries:
        drop = len(existing) - max_entries
        for composite in list(existing.keys())[:drop]:
            del existing[composite]

    doc = {"schema_version": SCHEMA_VERSION, "entries": existing}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".survival_cache-", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(doc, handle, ensure_ascii=False)
            os.replace(tmp_name, path)
        except OSError:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            return False
    except OSError:
        return False
    return True


def clear(repo: Path) -> bool:
    """Delete the local store (the evictability the relocation buys). Best-effort."""
    path = store_path(Path(repo))
    if path is None or not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True
