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


def _fsync_directory(directory: Path) -> None:
    """fsync a directory so a rename into it survives power loss (best-effort).

    Directory fsync is not supported on every platform/filesystem; a failure to
    open or sync the directory must not defeat the write itself.
    """

    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        try:
            os.close(dir_fd)
        except OSError:
            # The rename already committed; a failing close on the directory
            # fd must never surface as a write failure (#302 review B).
            pass


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == text:
        return
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            # fsync the temp content before the rename so a crash cannot leave a
            # renamed-but-empty file (issue #302 F6).
            handle.flush()
            os.fsync(handle.fileno())
        Path(tmp_name).replace(path)
        _fsync_directory(path.parent)
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
            handle.flush()
            os.fsync(handle.fileno())
        Path(tmp_name).replace(path)
        _fsync_directory(path.parent)
    finally:
        tmp_path = Path(tmp_name)
        if tmp_path.exists():
            tmp_path.unlink()


def _gzip_deterministic(data: bytes) -> bytes:
    """Deterministic gzip with ``mtime=0`` per plan 080 Resolution H.

    All gzipped surfaces (layer blobs, per-trace JSONL, event-log mirror)
    use this helper so two machines projecting the same content produce
    byte-identical output — across machines AND across Python versions.

    The OS byte (offset 9 of the gzip header) is explicitly forced to
    ``0xff`` ("unknown", RFC 1952) because CPython's stdlib is not
    self-consistent about it: on Python >= 3.13 ``gzip.compress(...,
    mtime=0)`` normalizes the OS byte to 255, but on Python < 3.13 the
    ``mtime == 0`` fast path returns ``zlib.compress(data, wbits=31)``
    raw, leaking whatever OS id the platform's zlib build stamps (0x03 on
    Linux, 0x13 elsewhere) — so the "same" call produced three different
    bytes across CI, macOS dev, and 3.13+ machines. The single-byte
    surgery below makes the output identical everywhere.
    """

    out = gzip.compress(data, mtime=0, compresslevel=6)
    return out[:9] + b"\xff" + out[10:]


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
