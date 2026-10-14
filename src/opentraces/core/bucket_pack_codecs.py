"""Compression codecs for superseded TraceRecord packs.

A pack file is a fixed header followed by one compressed POSIX pax tar stream::

    OTPACK1\\n
    {"codec": "zstd", "format": "tar", "schema": "opentraces.trace_record_pack.v1"}\\n
    <compressed tar>

The header names the codec, so a pack written with one codec stays readable
when the preferred codec changes and new codecs can be added. Codecs are
optional — the core install needs none of them, and reading a current record
never touches a pack:

- ``zstd``: stdlib ``compression.zstd`` (Python >= 3.14) or ``zstandard``
  (``pip install 'opentraces[pack]'``). Preferred for writing.
- ``xz``: stdlib ``lzma``, when the interpreter was built with ``_lzma``.

Recover by hand: ``tail -n +3 superseded.otpack | zstd -d | tar -x``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

MAGIC = b"OTPACK1\n"
PACK_SCHEMA = "opentraces.trace_record_pack.v1"
WRITE_PREFERENCE = ("zstd", "xz")
# zstd level 9 with long-distance matching. On a real 385 MB, 80-version trace:
# level 9 = 51x at ~370 MB/s, level 16 = 84x at ~22 MB/s, xz preset 3 = 67x at
# ~24 MB/s. Re-packing rewrites a trace's whole pack, so speed wins.
_ZSTD_LEVEL = 9
_XZ_PRESET = 3
_MIN_WINDOW_LOG = 20
# 2**27 is zstd's default decoder window limit, so any zstd decoder (CLI or
# library) reads our packs without extra flags.
_MAX_WINDOW_LOG = 27

INSTALL_HINT = (
    "bucket pack needs a compression codec: install the extra with "
    "`pip install 'opentraces[pack]'` (zstandard), or run on Python >= 3.14"
)


class CodecUnavailable(RuntimeError):
    """No installed codec can write packs, or read this pack's codec."""


def _codec_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = []
    try:
        import lzma

        errors.append(lzma.LZMAError)
    except ImportError:
        pass
    try:
        from compression import zstd as _stdlib_zstd  # type: ignore[import-not-found]

        errors.append(_stdlib_zstd.ZstdError)
    except ImportError:
        pass
    try:
        import zstandard

        errors.append(zstandard.ZstdError)
    except ImportError:
        pass
    return tuple(errors)


CODEC_ERRORS = _codec_errors()


def _zstd_backend() -> str | None:
    try:
        from compression import zstd  # type: ignore[import-not-found]  # noqa: F401

        return "stdlib"
    except ImportError:
        pass
    try:
        import zstandard  # noqa: F401

        return "zstandard"
    except ImportError:
        return None


def _xz_available() -> bool:
    try:
        import lzma  # noqa: F401
    except ImportError:
        return False
    return True


def available_codecs() -> list[str]:
    out = []
    if _zstd_backend() is not None:
        out.append("zstd")
    if _xz_available():
        out.append("xz")
    return out


def write_codec() -> str:
    """Return the codec new packs are written with, or raise ``CodecUnavailable``."""

    available = available_codecs()
    for codec in WRITE_PREFERENCE:
        if codec in available:
            return codec
    raise CodecUnavailable(INSTALL_HINT)


def window_log(largest_member: int) -> int:
    """Window covering the previous version of the largest member, clamped."""

    return max(_MIN_WINDOW_LOG, min(_MAX_WINDOW_LOG, (2 * largest_member).bit_length()))


@contextmanager
def open_writer(fh: IO[bytes], codec: str, wlog: int) -> Iterator[IO[bytes]]:
    """Write the header to ``fh`` and yield a compressing writer over it."""

    header = {"codec": codec, "format": "tar", "schema": PACK_SCHEMA}
    fh.write(MAGIC + json.dumps(header, sort_keys=True).encode("utf-8") + b"\n")
    if codec == "zstd":
        backend = _zstd_backend()
        if backend == "stdlib":
            from compression import zstd  # type: ignore[import-not-found]

            options = {
                zstd.CompressionParameter.compression_level: _ZSTD_LEVEL,
                zstd.CompressionParameter.window_log: wlog,
                zstd.CompressionParameter.enable_long_distance_matching: 1,
            }
            with zstd.ZstdFile(fh, "wb", options=options) as out:
                yield out
            return
        if backend == "zstandard":
            import zstandard

            params = zstandard.ZstdCompressionParameters.from_level(
                _ZSTD_LEVEL, window_log=wlog, enable_ldm=True
            )
            cctx = zstandard.ZstdCompressor(compression_params=params)
            with cctx.stream_writer(fh, closefd=False) as out:
                yield out
            return
    elif codec == "xz" and _xz_available():
        import lzma

        filters = [{"id": lzma.FILTER_LZMA2, "preset": _XZ_PRESET, "dict_size": 1 << wlog}]
        with lzma.LZMAFile(fh, "wb", format=lzma.FORMAT_XZ, filters=filters) as out:
            yield out
        return
    raise CodecUnavailable(f"codec {codec!r} is not available; {INSTALL_HINT}")


def read_header(fh: IO[bytes]) -> dict[str, Any]:
    if fh.read(len(MAGIC)) != MAGIC:
        raise ValueError("not an OpenTraces pack (bad magic)")
    line = fh.readline(4096)
    header = json.loads(line)
    if not isinstance(header, dict) or header.get("format") != "tar":
        raise ValueError("unsupported pack header")
    return header


def pack_codec(path: Path) -> str:
    with path.open("rb") as fh:
        return str(read_header(fh).get("codec"))


@contextmanager
def open_reader(path: Path) -> Iterator[IO[bytes]]:
    """Yield a decompressing reader over the tar stream of the pack at ``path``."""

    with path.open("rb") as fh:
        codec = read_header(fh).get("codec")
        if codec == "zstd":
            backend = _zstd_backend()
            if backend == "stdlib":
                from compression import zstd  # type: ignore[import-not-found]

                options = {zstd.DecompressionParameter.window_log_max: _MAX_WINDOW_LOG}
                with zstd.ZstdFile(fh, "rb", options=options) as raw:
                    yield raw
                return
            if backend == "zstandard":
                import zstandard

                dctx = zstandard.ZstdDecompressor(max_window_size=1 << _MAX_WINDOW_LOG)
                with dctx.stream_reader(fh, closefd=False) as raw:
                    yield raw
                return
        elif codec == "xz" and _xz_available():
            import lzma

            with lzma.LZMAFile(fh, "rb") as raw:
                yield raw
            return
        raise CodecUnavailable(f"{path}: pack codec {codec!r} is not available; {INSTALL_HINT}")
