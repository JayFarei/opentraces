"""Pure I/O utilities shared across bucket_store, bucket_context_store, and bucket_events.

These helpers have no domain knowledge: they deal only with atomic file writes,
deterministic gzip, canonical JSON, and content-digest computation. Extracted
from bucket_store.py (plan 080) to eliminate circular imports when the store is
split into focused sub-modules.
"""

from __future__ import annotations

import gzip
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = _canonical_json(payload, pretty=True)
    _atomic_write_text(path, text)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == text:
        return
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp_name).replace(path)
    finally:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            tmp_path.unlink()


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == data:
        return
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        Path(tmp_name).replace(path)
    finally:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            tmp_path.unlink()


def _gzip_deterministic(data: bytes) -> bytes:
    """Deterministic gzip with ``mtime=0`` per plan 080 Resolution H.

    All gzipped surfaces (layer blobs, per-trace JSONL, event-log mirror)
    use this helper so two machines projecting the same content produce
    byte-identical output.
    """

    return gzip.compress(data, mtime=0, compresslevel=6)


def _atomic_write_gzip(path: Path, data: bytes) -> None:
    """Atomic write of ``data`` gzipped with deterministic settings."""

    _atomic_write_bytes(path, _gzip_deterministic(data))


def _read_gzip_bytes(path: Path) -> bytes:
    return gzip.decompress(path.read_bytes())


def _digest_payload(payload: Any) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(_canonical_json(payload).encode('utf-8')).hexdigest()}"


def _digest_bytes(payload: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _canonical_json(payload: Any, *, pretty: bool = False) -> str:
    if pretty:
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
