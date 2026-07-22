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
import zlib
from pathlib import Path
from typing import Any, Iterable


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


def _write_streaming_gzip(path: Path, lines: Iterable[str]) -> bool:
    """Streaming counterpart to ``_atomic_write_gzip``: writes ``lines``
    (each WITHOUT its own trailing newline) as a deterministic (``mtime=0``)
    gzip file ONE LINE AT A TIME instead of joining a full body first
    (issue #358: ``_gzip_deterministic``'s ``gzip.compress(data, mtime=0,
    ...)`` call is, on every Python version, actually ``zlib.compress(data,
    wbits=31)`` under the hood — see that helper's own docstring — a
    single-shot call requiring the WHOLE body up front).

    ``zlib.compressobj`` is the streaming twin of that same call and
    produces BYTE-IDENTICAL compressed output for the SAME content
    regardless of how many ``.compress()`` calls it takes to feed it in:
    raw DEFLATE's output depends only on the byte STREAM and the codec's
    own internal buffering, never on caller-chosen chunk boundaries, as long
    as no intermediate flush is issued (this function issues exactly one,
    at the very end) — verified empirically for this exact
    level/wbits/OS-byte-patch combination, including the empty-input case,
    before this was wired into any caller.

    Returns ``True`` when the file was written (new or changed content),
    ``False`` when an existing file already held byte-identical output
    (matching ``_atomic_write_bytes``'s same-bytes-skip contract) — the
    compressed candidate is always fully produced first (bounded by the
    OUTPUT size, not the uncompressed input) so this compares actual bytes,
    never a size or hash proxy.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            for line in lines:
                handle.write(compressor.compress(line.encode("utf-8") + b"\n"))
            handle.write(compressor.flush())
        # OS-byte fix at offset 9, matching ``_gzip_deterministic`` exactly
        # (RFC 1952 "unknown" — see that helper's docstring for why CPython
        # itself is not self-consistent about this byte across versions).
        with open(tmp_path, "r+b") as handle:
            handle.seek(9)
            handle.write(b"\xff")
        if path.exists() and path.read_bytes() == tmp_path.read_bytes():
            return False
        tmp_path.replace(path)
        return True
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


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
