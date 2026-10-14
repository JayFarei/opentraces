"""Superseded raw-source blob garbage collection.

A still-growing session is re-captured many times. Each capture writes a new
content-addressed full copy to ``objects/raw/v1/blobs/<hh>/<hex><suffix>`` and
repoints the session's link ``objects/raw/v1/sources/<slug>/<trace>.json``.
Before this module the predecessor was never removed, so a session captured N
times kept N full copies (O(N^2) bytes).

A blob is garbage only if ALL hold:

* no reference names it — references are every link's ``content_digest`` /
  ``blob_path`` plus any entry in a per-trace ``sources.jsonl.gz`` sidecar
  (written empty today; read so a future writer stays protected);
* it is a strict byte prefix of a still-referenced blob, i.e. its content
  survives verbatim in the current capture of the growing session;
* (sweep only) it is older than :data:`SWEEP_GRACE_SECONDS`, so an in-flight
  capture that has written its blob but not yet repointed is never raced.

Superseded TraceRecord objects (``objects/traces/v1``) are deliberately NOT
collected: an older record is a re-derivation by an earlier parser and can
differ from the current one at earlier steps, so it is history, not a copy.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from . import paths
from .bucket_layout import _digest_hex, raw_sources_root, traces_v1_root

#: Sweep never touches blobs younger than this (in-flight captures).
SWEEP_GRACE_SECONDS = 60 * 60

_HEAD_BYTES = 4096
_CHUNK_BYTES = 8 * 1024 * 1024


def _rel(path: Path) -> str:
    return path.relative_to(paths.bucket_dir()).as_posix()


def _blob_hex(path: Path) -> str:
    return path.name.split(".", 1)[0]


def _ref_hexes(entry: dict[str, Any]) -> Iterator[str]:
    digest = entry.get("content_digest")
    if isinstance(digest, str) and digest:
        yield _digest_hex(digest)
    blob_path = entry.get("blob_path")
    if isinstance(blob_path, str) and blob_path:
        yield _blob_hex(Path(blob_path))


def raw_blob_refs() -> set[str] | None:
    """Return the hex digest of every raw blob something references.

    Returns ``None`` if any reference file is unreadable, so callers delete
    nothing rather than trust a partial set.
    """

    refs: set[str] = set()
    sources_root = raw_sources_root() / "sources"
    for link_path in sources_root.glob("*/*.json") if sources_root.exists() else ():
        try:
            link = json.loads(link_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if isinstance(link, dict):
            refs.update(_ref_hexes(link))
    traces_root = traces_v1_root()
    for sidecar in traces_root.glob("*/*/sources.jsonl.gz") if traces_root.exists() else ():
        try:
            if sidecar.stat().st_size == 0:
                continue
            lines = gzip.decompress(sidecar.read_bytes()).decode("utf-8").splitlines()
            entries = [json.loads(line) for line in lines if line.strip()]
        except (OSError, ValueError, EOFError):
            return None
        refs.update(h for e in entries if isinstance(e, dict) for h in _ref_hexes(e))
    return refs


def _is_strict_prefix(prefix: Path, full: Path) -> bool:
    if prefix.stat().st_size >= full.stat().st_size:
        return False
    with prefix.open("rb") as a, full.open("rb") as b:
        while True:
            chunk = a.read(_CHUNK_BYTES)
            if not chunk:
                return True
            if b.read(len(chunk)) != chunk:
                return False


def drop_superseded_raw_blob(previous_blob_path: str | None, current_blob_path: str) -> bool:
    """Write path: remove the blob a link named before its repoint, if garbage."""

    if not previous_blob_path or previous_blob_path == current_blob_path:
        return False
    bucket = paths.bucket_dir()
    previous = bucket / previous_blob_path
    current = bucket / current_blob_path
    if previous.parent.parent != raw_sources_root() / "blobs" or not previous.is_file():
        return False
    refs = raw_blob_refs()
    if refs is None or _blob_hex(previous) in refs:
        return False
    if not _is_strict_prefix(previous, current):
        return False
    previous.unlink(missing_ok=True)
    return True


def _category() -> dict[str, Any]:
    return {"count": 0, "bytes": 0, "paths": []}


def _add(category: dict[str, Any], path: Path, size: int) -> None:
    category["count"] += 1
    category["bytes"] += size
    category["paths"].append(_rel(path))


def sweep_superseded_raw(
    *, dry_run: bool, grace_seconds: int = SWEEP_GRACE_SECONDS
) -> dict[str, Any]:
    """Maintenance: sweep superseded raw blobs (see module docstring)."""

    deleted = _category()
    not_prefix = _category()
    skipped = {"recent": 0, "unreadable_refs": 0}
    result = {
        "dry_run": dry_run,
        "grace_seconds": grace_seconds,
        "raw_blobs": deleted,
        "kept_not_prefix": not_prefix,
        "skipped": skipped,
    }
    blobs_root = raw_sources_root() / "blobs"
    refs = raw_blob_refs()
    if refs is None:
        skipped["unreadable_refs"] = 1
        return result
    if not blobs_root.exists():
        return result

    referenced: list[tuple[Path, int, bytes]] = []
    candidates: list[tuple[Path, int]] = []
    now = time.time()
    for blob in sorted(blobs_root.glob("*/*")):
        if not blob.is_file() or blob.name.startswith("."):
            continue
        st = blob.stat()
        if _blob_hex(blob) in refs:
            with blob.open("rb") as handle:
                referenced.append((blob, st.st_size, handle.read(_HEAD_BYTES)))
        elif now - st.st_mtime < grace_seconds:
            skipped["recent"] += 1
        else:
            candidates.append((blob, st.st_size))

    by_head: dict[bytes, list[tuple[Path, int]]] = {}
    for path, size, head in referenced:
        if len(head) == _HEAD_BYTES:
            by_head.setdefault(hashlib.sha256(head).digest(), []).append((path, size))

    for blob, size in candidates:
        with blob.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
        if len(head) == _HEAD_BYTES:
            pool = by_head.get(hashlib.sha256(head).digest(), [])
            match = any(s > size and _is_strict_prefix(blob, p) for p, s in pool)
        else:
            match = any(s > size and h.startswith(head) for _, s, h in referenced)
        if not match:
            _add(not_prefix, blob, size)
            continue
        if not dry_run:
            blob.unlink(missing_ok=True)
        _add(deleted, blob, size)
    return result
