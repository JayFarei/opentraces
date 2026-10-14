"""Lossless packing of superseded TraceRecord objects.

Every re-capture of a growing session writes a new full TraceRecord object
``objects/traces/v1/<project>/<trace>/<digest>.json`` and repoints
``current.json`` at it. The earlier objects stay on disk. They are not
duplicates — each one is the record as an earlier parser version derived it —
so they are kept, but consecutive versions share almost all of their bytes.

``pack_superseded_trace_records`` moves every superseded object of a trace into
one per-trace archive, ``superseded.otpack``, next to ``current.json``.
The current object stays a loose ``.json`` file, so the hot read path
(``current.json`` -> object) is unchanged.

Pack format: a header naming the codec, then a compressed POSIX pax tar (see
``bucket_pack_codecs``). Members are the original ``<digest>.json`` files,
byte-exact, in mtime order so consecutive versions sit next to each other
inside the compression window. Each member carries pax headers
``OPENTRACES.sha256`` (sha256 of the member bytes) and ``OPENTRACES.mtime_ns``.
Codecs are an optional extra: without one, ``bucket pack`` refuses with an
install hint, and nothing on the current-record read path needs a codec.

Safety:

- A loose file is removed only after the new pack is written to a tempfile,
  fsynced, renamed into place, and re-read with every member's sha256 matching
  the sha256 of the loose bytes it replaces.
- Re-packing merges the existing pack with newly superseded loose objects, so
  the verb is idempotent. A loose file that is already in the pack with the
  same bytes (a crash after rename, before unlink) is simply removed.
- Interrupted writes leave ``.superseded.otpack.*.tmp`` files that every
  reader ignores and the next pack run removes once they are an hour old.
- The current object and objects younger than ``min_age_seconds`` (default one
  hour) are never packed. ``current.json`` is re-read right before each unlink.
"""

from __future__ import annotations

import hashlib
import heapq
import io
import json
import os
import re
import tarfile
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths
from .bucket_layout import trace_records_root
from .bucket_models import TRACE_RECORD_POINTER_SCHEMA
from .bucket_pack_codecs import (
    CODEC_ERRORS,
    PACK_SCHEMA,
    CodecUnavailable,
    open_reader,
    open_writer,
    window_log,
    write_codec,
)

PACK_NAME = "superseded.otpack"
DEFAULT_MIN_AGE_SECONDS = 60 * 60
_TMP_PREFIX = f".{PACK_NAME}."
_TMP_MAX_AGE_SECONDS = 60 * 60
_OBJECT_NAME = re.compile(r"^[0-9a-f]{64}\.json$")
_SHA_KEY = "OPENTRACES.sha256"
_MTIME_KEY = "OPENTRACES.mtime_ns"


class PackError(RuntimeError):
    """A pack could not be written or read back losslessly."""


PACK_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    ValueError,
    EOFError,
    PackError,
    CodecUnavailable,
    tarfile.TarError,
    *CODEC_ERRORS,
)


@dataclass(frozen=True)
class PackMember:
    name: str
    data: bytes
    mtime_ns: int
    mode: int
    sha256: str


def pack_path(trace_dir: Path) -> Path:
    return trace_dir / PACK_NAME


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _current_object_name(trace_dir: Path) -> str | None:
    try:
        raw = json.loads((trace_dir / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if raw.get("schema_version") != TRACE_RECORD_POINTER_SCHEMA:
        return None
    object_path = raw.get("object_path")
    if not isinstance(object_path, str) or not object_path:
        return None
    return object_path.rsplit("/", 1)[-1]


def iter_pack_members(path: Path) -> Iterator[PackMember]:
    """Stream the members of a pack in stored (mtime) order."""

    with open_reader(path) as raw, tarfile.open(fileobj=raw, mode="r|") as tar:
        for info in tar:
            if not info.isfile():
                continue
            handle = tar.extractfile(info)
            data = handle.read() if handle is not None else b""
            sha = info.pax_headers.get(_SHA_KEY) or _sha256(data)
            mtime_ns = int(info.pax_headers.get(_MTIME_KEY) or int(info.mtime) * 1_000_000_000)
            yield PackMember(info.name, data, mtime_ns, info.mode, sha)


def list_pack(path: Path) -> dict[str, str]:
    """Return ``{member name: stored sha256}`` for a pack."""

    return {m.name: m.sha256 for m in iter_pack_members(path)}


def read_packed_object(object_path: Path) -> bytes | None:
    """Return the bytes of ``object_path`` from its trace's pack, if packed."""

    pack = pack_path(object_path.parent)
    if not pack.is_file():
        return None
    try:
        for member in iter_pack_members(pack):
            if member.name == object_path.name:
                return member.data
    except PACK_ERRORS:
        return None
    return None


def iter_trace_record_versions(trace_dir: Path) -> Iterator[tuple[str, bytes, int]]:
    """Yield ``(name, bytes, mtime_ns)`` for every stored version, oldest first.

    Includes packed and loose objects; a name present in both is yielded once.
    """

    items: dict[str, tuple[bytes, int]] = {}
    pack = pack_path(trace_dir)
    if pack.is_file():
        for member in iter_pack_members(pack):
            items[member.name] = (member.data, member.mtime_ns)
    for path in trace_dir.iterdir():
        if _OBJECT_NAME.match(path.name) and path.is_file():
            items[path.name] = (path.read_bytes(), path.stat().st_mtime_ns)
    for name, (data, mtime_ns) in sorted(items.items(), key=lambda kv: (kv[1][1], kv[0])):
        yield name, data, mtime_ns


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _write_pack(
    target: Path, members: Iterator[PackMember], wlog: int, codec: str | None = None
) -> dict[str, str]:
    """Write ``members`` to ``target`` atomically; return ``{name: sha256}``."""

    written: dict[str, str] = {}
    fd, tmp_name = tempfile.mkstemp(prefix=_TMP_PREFIX, suffix=".tmp", dir=str(target.parent))
    tmp = Path(tmp_name)
    codec = codec or write_codec()
    try:
        with os.fdopen(fd, "wb") as fh:
            with (
                open_writer(fh, codec, wlog) as zfh,
                tarfile.open(fileobj=zfh, mode="w|", format=tarfile.PAX_FORMAT) as tar,
            ):
                for member in members:
                    info = tarfile.TarInfo(member.name)
                    info.size = len(member.data)
                    info.mtime = member.mtime_ns // 1_000_000_000
                    info.mode = member.mode
                    info.pax_headers = {
                        _SHA_KEY: member.sha256,
                        _MTIME_KEY: str(member.mtime_ns),
                    }
                    tar.addfile(info, io.BytesIO(member.data))
                    written[member.name] = member.sha256
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, target)
        _fsync_dir(target.parent)
    finally:
        if tmp.exists():
            tmp.unlink()
    return written


def _verify_pack(path: Path, expected: dict[str, str]) -> None:
    """Re-read ``path`` and check every member's bytes hash to ``expected``."""

    seen: dict[str, str] = {}
    for member in iter_pack_members(path):
        actual = _sha256(member.data)
        if actual != member.sha256:
            raise PackError(f"{path}: member {member.name} sha mismatch")
        seen[member.name] = actual
    if seen != expected:
        raise PackError(f"{path}: pack contents differ from what was written")


def _sweep_stale_tmp(trace_dir: Path, now: float) -> int:
    removed = 0
    for tmp in trace_dir.glob(f"{_TMP_PREFIX}*.tmp"):
        try:
            if now - tmp.stat().st_mtime >= _TMP_MAX_AGE_SECONDS:
                tmp.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed


def _loose_member(path: Path) -> PackMember:
    data = path.read_bytes()
    st = path.stat()
    return PackMember(path.name, data, st.st_mtime_ns, st.st_mode & 0o7777, _sha256(data))


def pack_trace_dir(
    trace_dir: Path,
    *,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
    now: float | None = None,
) -> dict[str, Any]:
    """Pack the superseded objects of one trace directory."""

    now = time.time() if now is None else now
    out: dict[str, Any] = {
        "records_packable": 0,
        "records_packed": 0,
        "skipped_current": 0,
        "skipped_young": 0,
        "loose_bytes_packable": 0,
        "pack_bytes": 0,
        "tmp_removed": 0,
        "pack_written": False,
    }
    current = _current_object_name(trace_dir)
    if current is None:
        # No valid pointer: every object would look superseded. Leave it alone.
        out["skipped_no_pointer"] = 1
        return out
    candidates: list[Path] = []
    for path in sorted(trace_dir.iterdir()):
        if not _OBJECT_NAME.match(path.name) or not path.is_file():
            continue
        if path.name == current:
            out["skipped_current"] += 1
            continue
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        if now - st.st_mtime < min_age_seconds:
            out["skipped_young"] += 1
            continue
        candidates.append(path)
        out["loose_bytes_packable"] += st.st_size
    out["records_packable"] = len(candidates)
    if dry_run:
        return out
    out["tmp_removed"] = _sweep_stale_tmp(trace_dir, now)
    if not candidates:
        return out

    pack = pack_path(trace_dir)
    existing = list_pack(pack) if pack.is_file() else {}
    loose = [_loose_member(path) for path in candidates]
    already_packed: list[PackMember] = []
    new: list[PackMember] = []
    for member in loose:
        if member.name in existing:
            if existing[member.name] != member.sha256:
                raise PackError(f"{trace_dir}: {member.name} differs from its packed copy")
            already_packed.append(member)
        else:
            new.append(member)
    if new:
        new.sort(key=lambda m: (m.mtime_ns, m.name))
        old_sizes = [len(m.data) for m in new]
        stream: Iterator[PackMember] = iter(new)
        if existing:
            stream = heapq.merge(
                iter_pack_members(pack), iter(new), key=lambda m: (m.mtime_ns, m.name)
            )
        written = _write_pack(pack, stream, window_log(max(old_sizes)))
        _verify_pack(pack, written)
        out["pack_written"] = True
    else:
        written = existing
    for member in loose:
        if written.get(member.name) != member.sha256:
            raise PackError(f"{trace_dir}: {member.name} missing from pack after write")
        if _current_object_name(trace_dir) == member.name:
            # Repointed at this object while we were packing; keep it loose.
            continue
        (trace_dir / member.name).unlink()
        out["records_packed"] += 1
    _fsync_dir(trace_dir)
    out["pack_bytes"] = pack.stat().st_size
    return out


def _trace_dirs(project_slug: str | None) -> list[Path]:
    root = trace_records_root()
    if not root.exists():
        return []
    pattern = f"{project_slug}/*" if project_slug else "*/*"
    return sorted(p for p in root.glob(pattern) if p.is_dir())


def _tree_bytes(root: Path) -> tuple[int, int]:
    """Return ``(object file count, total bytes)`` under ``root``."""

    count = 0
    total = 0
    if not root.exists():
        return 0, 0
    for path in root.rglob("*"):
        if path.is_file():
            total += path.stat().st_size
            if _OBJECT_NAME.match(path.name):
                count += 1
    return count, total


def pack_superseded_trace_records(
    *,
    dry_run: bool = False,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    project_slug: str | None = None,
) -> dict[str, Any]:
    """Pack superseded TraceRecord objects for every trace (or one project).

    Raises ``CodecUnavailable`` (unless ``dry_run``) when no codec is installed.
    """

    codec = None if dry_run else write_codec()
    root = trace_records_root()
    records_before, bytes_before = _tree_bytes(root)
    totals: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    traces = _trace_dirs(project_slug)
    now = time.time()
    for trace_dir in traces:
        try:
            result = pack_trace_dir(
                trace_dir, min_age_seconds=min_age_seconds, dry_run=dry_run, now=now
            )
        except PACK_ERRORS as exc:
            errors.append({"path": _rel(trace_dir), "detail": str(exc)})
            continue
        for key, value in result.items():
            totals[key] = totals.get(key, 0) + int(value)
    records_after, bytes_after = (
        (records_before, bytes_before) if dry_run else _tree_bytes(root)
    )
    return {
        "schema_version": PACK_SCHEMA,
        "dry_run": dry_run,
        "codec": codec,
        "min_age_seconds": min_age_seconds,
        "traces_scanned": len(traces),
        "packs_written": totals.pop("pack_written", 0),
        **totals,
        "loose_records_before": records_before,
        "loose_records_after": records_after,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "errors": errors,
        "ok": not errors,
    }


def unpack_trace_dir(trace_dir: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Restore every packed object of one trace as a loose file, byte-exact."""

    out = {"records_restored": 0, "records_present": 0, "packs_removed": 0}
    pack = pack_path(trace_dir)
    if not pack.is_file():
        return out
    for member in iter_pack_members(pack):
        if _sha256(member.data) != member.sha256:
            raise PackError(f"{pack}: member {member.name} sha mismatch")
        target = trace_dir / member.name
        if target.exists():
            if _sha256(target.read_bytes()) != member.sha256:
                raise PackError(f"{target} differs from its packed copy")
            out["records_present"] += 1
            continue
        out["records_restored"] += 1
        if dry_run:
            continue
        fd, tmp_name = tempfile.mkstemp(prefix=f".{member.name}.", suffix=".tmp", dir=str(trace_dir))
        with os.fdopen(fd, "wb") as fh:
            fh.write(member.data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, member.mode)
        os.utime(tmp_name, ns=(member.mtime_ns, member.mtime_ns))
        os.replace(tmp_name, target)
    if not dry_run:
        _fsync_dir(trace_dir)
        pack.unlink()
        _fsync_dir(trace_dir)
        out["packs_removed"] = 1
    return out


def unpack_superseded_trace_records(
    *,
    dry_run: bool = False,
    project_slug: str | None = None,
) -> dict[str, Any]:
    root = trace_records_root()
    records_before, bytes_before = _tree_bytes(root)
    totals = {"records_restored": 0, "records_present": 0, "packs_removed": 0}
    errors: list[dict[str, str]] = []
    for trace_dir in _trace_dirs(project_slug):
        try:
            result = unpack_trace_dir(trace_dir, dry_run=dry_run)
        except PACK_ERRORS as exc:
            errors.append({"path": _rel(trace_dir), "detail": str(exc)})
            continue
        for key, value in result.items():
            totals[key] += value
    records_after, bytes_after = (
        (records_before, bytes_before) if dry_run else _tree_bytes(root)
    )
    return {
        "schema_version": PACK_SCHEMA,
        "dry_run": dry_run,
        **totals,
        "loose_records_before": records_before,
        "loose_records_after": records_after,
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "errors": errors,
        "ok": not errors,
    }


def verify_trace_packs(
    packs: list[Path] | None = None,
) -> tuple[int, list[dict[str, str]], list[str]]:
    """Check pack integrity; return ``(packs checked, errors, packs skipped)``.

    A pack whose codec is not installed is skipped (reported, not an error):
    ``bucket verify`` must keep working on an install without the extra.

    For each pack: every member decodes, its bytes match the stored sha256,
    its envelope's ``record_hash`` matches its file name, and the trace's
    ``current.json`` still resolves to a loose object (the current record is
    never packed).
    """

    if packs is None:
        packs = all_packs()
    errors: list[dict[str, str]] = []
    skipped: list[str] = []
    for pack in packs:
        rel = _rel(pack)
        try:
            for member in iter_pack_members(pack):
                if _sha256(member.data) != member.sha256:
                    errors.append({"kind": "pack_member_sha", "path": f"{rel}:{member.name}", "detail": "bytes do not match stored sha256"})
                    continue
                try:
                    envelope = json.loads(member.data)
                except ValueError as exc:
                    errors.append({"kind": "pack_member_json", "path": f"{rel}:{member.name}", "detail": str(exc)})
                    continue
                digest = str(envelope.get("record_hash") or "").removeprefix("sha256:")
                if f"{digest}.json" != member.name:
                    errors.append({"kind": "pack_member_hash", "path": f"{rel}:{member.name}", "detail": f"record_hash {digest!r} does not match name"})
        except CodecUnavailable:
            skipped.append(rel)
            continue
        except PACK_ERRORS as exc:
            errors.append({"kind": "pack_unreadable", "path": rel, "detail": str(exc)})
            continue
        current = _current_object_name(pack.parent)
        if current is not None and not (pack.parent / current).is_file():
            errors.append({"kind": "pack_current_missing", "path": rel, "detail": f"current object {current} is not a loose file"})
    return len(packs) - len(skipped), errors, skipped


def all_packs() -> list[Path]:
    root = trace_records_root()
    if not root.exists():
        return []
    return sorted(root.glob(f"*/*/{PACK_NAME}"))


def _rel(path: Path) -> str:
    try:
        return path.relative_to(paths.bucket_dir()).as_posix()
    except ValueError:
        return str(path)
