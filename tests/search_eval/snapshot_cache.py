"""U9 - content-addressed snapshot cache for the eval corpus (standing gate).

Building the real-scale corpus + warm index/projection costs minutes; a standing
regression gate must not pay that on every run. This caches the built corpus in
a content-addressed directory keyed by the plan's ``snapshot_key`` (which already
folds in profile + seed + tier + generator/code version). On a cache hit the
eval restores the corpus by pointing ``HOME`` at the cached directory — a pure
read, because the 087 query hot path never rebuilds and its cheap-sync probe
no-ops on an unchanged corpus, so the cached projection is queried in place.

The projection sqlites are WAL-checkpointed before the cache is marked ready
(Decision Audit #8): a stale ``-wal`` sidecar would trip the 087
malformed->self-heal->rebuild path and dominate the measurement.

Reuses otbox's ``_rewrite_sqlite_absolute_paths`` for the (optional) portable
tar export. Same-machine reuse needs no rewrite because the cache directory path
is stable, so the baked-in absolute paths stay valid.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = REPO_ROOT / "tests" / "search_eval" / ".cache"
_READY_MARKER = ".ready"


def _safe_key(key: str) -> str:
    return key.replace("sha256:", "").replace(":", "_")


def cache_path(key: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> Path:
    return cache_dir / _safe_key(key)


def is_cached(key: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> bool:
    return (cache_path(key, cache_dir) / _READY_MARKER).is_file()


def _projection_sqlites(home_dir: Path) -> list[Path]:
    out: list[Path] = []
    idx = home_dir / ".opentraces" / "index" / "index.db"
    if idx.exists():
        out.append(idx)
    proj = home_dir / ".opentraces" / "bucket" / "projections" / "search" / "v1"
    if proj.exists():
        out.extend(proj.rglob("*.sqlite"))
    return out


def wal_checkpoint(home_dir: Path) -> None:
    """TRUNCATE-checkpoint every projection sqlite so the cache is sidecar-free."""

    for sq in _projection_sqlites(home_dir):
        try:
            with sqlite3.connect(sq) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.commit()
        except sqlite3.DatabaseError:
            pass


def mark_ready(key: str, home_dir: Path, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
    """WAL-checkpoint and mark the cache directory ready for reuse."""

    wal_checkpoint(home_dir)
    base = cache_path(key, cache_dir)
    base.mkdir(parents=True, exist_ok=True)
    (base / _READY_MARKER).write_text(key + "\n", encoding="utf-8")


def clear(key: str, cache_dir: Path = DEFAULT_CACHE_DIR) -> None:
    shutil.rmtree(cache_path(key, cache_dir), ignore_errors=True)
