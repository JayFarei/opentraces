"""Download + verify the entity-parser binary.

Pipeline:
    1. Detect platform (darwin-arm64 / linux-x86_64 / …)
    2. Read expected sha256, url, and archive layout from manifest.json
    3. Stream-download to a tmp file under ~/.opentraces/bin/
    4. Verify sha256 against the downloaded payload
    5. If the payload is a tarball (manifest says so, or magic bytes sniff),
       extract the inner binary; otherwise treat the payload as a raw binary
    6. chmod +x, atomic rename to ``<binary_name>-<version>``
    7. Prune prior versions beyond the last 3

Honours ``OPENTRACES_ENTITY_BIN``: if set, the installer skips download
entirely and just verifies the pre-placed binary (for airgapped setups).

Upstream note: during development we point at ``Ataraxy-Labs/sem`` releases
(the upstream project that superseded ``ot-entities``). The manifest keeps
this swappable — bump ``version``, ``binary_name``, and ``sources`` in
``manifest.json`` to target a different release channel.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform as _platform
import shutil
import stat
import subprocess
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .version import ENTITY_BINARY_VERSION, MANIFEST_PATH

_BIN_DIR = Path.home() / ".opentraces" / "bin"
_ENV_OVERRIDE = "OPENTRACES_ENTITY_BIN"


class InstallError(RuntimeError):
    """Raised when download / verification cannot succeed.

    Callers (CLI setup flow) catch this and render a human-friendly error.
    """


@dataclass
class InstallResult:
    path: Path
    version: str
    platform: str
    source: str  # "download" | "env-override" | "already-present"


# --- platform detection ----------------------------------------------------

def current_platform() -> str:
    """Normalize uname into one of the platform keys our manifest uses."""
    system = _platform.system().lower()
    machine = _platform.machine().lower()

    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
        if machine in ("x86_64", "amd64"):
            return "darwin-x86_64"
    elif system == "linux":
        if machine in ("x86_64", "amd64"):
            return "linux-x86_64"
        if machine in ("arm64", "aarch64"):
            return "linux-arm64"

    raise InstallError(
        f"unsupported platform: system={system!r} machine={machine!r}. "
        f"Set {_ENV_OVERRIDE}=<path> to point at a pre-built binary."
    )


def is_platform_supported() -> bool:
    """True iff the running platform has a downloadable manifest source.

    False for hosts the binary isn't published for (e.g. Intel macOS or
    Windows today) — callers should report a clean "file-level attribution
    fallback" rather than a failed download.
    """
    try:
        return bool(_source_url(current_platform()))
    except InstallError:
        return False


# --- manifest helpers ------------------------------------------------------

def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def _binary_name() -> str:
    return _load_manifest().get("binary_name") or "sem"


def _archive_format() -> str | None:
    """'tar.gz' / 'zip' / None (raw binary)."""
    return _load_manifest().get("archive_format")


def _inner_path() -> str:
    """Relative path inside the archive to the executable binary."""
    return _load_manifest().get("inner_path") or _binary_name()


def expected_sha256(platform: str) -> str:
    manifest = _load_manifest()
    for src in manifest.get("sources", []):
        if src.get("platform") == platform:
            return src["sha256"]
    raise InstallError(
        f"no manifest entry for platform: {platform} "
        f"(set {_ENV_OVERRIDE}=<path> to bypass)"
    )


def _source_url(platform: str) -> str:
    manifest = _load_manifest()
    for src in manifest.get("sources", []):
        if src.get("platform") == platform:
            return src["url"]
    raise InstallError(f"no manifest entry for platform: {platform}")


# --- verification ----------------------------------------------------------

def detected_version(binary_path: Path) -> str | None:
    """The version the binary ACTUALLY reports (vs the pinned manifest version).

    Lets callers surface what's really installed — so a stale or bogus
    env-override binary shows its real (mismatched) version instead of a
    confident pinned one.
    """
    from .runner import EntityRunner

    return EntityRunner(binary_path=binary_path).version()


def verify(binary_path: Path) -> bool:
    """Run --version against the binary. True iff exit 0 and non-empty output."""
    if not binary_path.is_file():
        return False
    try:
        r = subprocess.run(
            [str(binary_path), "--version"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool((r.stdout or r.stderr).strip())


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --- install ---------------------------------------------------------------

def _target_path(dest_dir: Path) -> Path:
    return dest_dir / f"{_binary_name()}-{ENTITY_BINARY_VERSION}"


def _prune_old_versions(dest_dir: Path, keep: int = 3) -> list[Path]:
    """Keep the ``keep`` most recent ``<binary_name>-<ver>`` binaries."""
    if not dest_dir.is_dir():
        return []
    prefix = f"{_binary_name()}-"
    candidates = sorted(
        (p for p in dest_dir.glob(f"{prefix}*") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    pruned: list[Path] = []
    for p in candidates[keep:]:
        try:
            p.unlink()
            pruned.append(p)
        except OSError:
            pass
    return pruned


def _download(url: str, dest: Path, *, progress=None) -> None:
    """Stream ``url`` to ``dest``. ``progress`` is called with bytes_read."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    # pre-create fresh tmp
    if dest.exists():
        dest.unlink()
    # Bounded timeout so a stalled connection can't hang init / a watcher tick
    # (urlopen otherwise blocks forever). 30s is generous for a few-MB binary.
    with urllib.request.urlopen(url, timeout=30) as resp, open(dest, "wb") as out:  # noqa: S310
        while True:
            chunk = resp.read(1 << 15)
            if not chunk:
                break
            out.write(chunk)
            if progress is not None:
                progress(len(chunk))


def _looks_like_gzip(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _extract_binary_from_tar_gz(archive: Path, inner: str, dest: Path) -> None:
    """Extract ``inner`` (matched by basename) from ``archive`` to ``dest``."""
    try:
        with tarfile.open(archive, "r:gz") as tf:
            member = None
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                if Path(m.name).name == inner or m.name == inner:
                    member = m
                    break
            if member is None:
                raise InstallError(
                    f"tarball {archive.name} has no member named {inner!r}"
                )
            extracted = tf.extractfile(member)
            if extracted is None:
                raise InstallError(
                    f"could not read {inner!r} from {archive.name}"
                )
            dest.write_bytes(extracted.read())
    except tarfile.TarError as e:
        raise InstallError(f"tarball extraction failed: {e}") from e


def install(
    dest_dir: Path | None = None,
    *,
    force: bool = False,
    progress=None,
) -> InstallResult:
    """Install or verify the entity-parser binary.

    Env-override path: if ``OPENTRACES_ENTITY_BIN`` is set, just ``verify()``
    the pointed-at binary and return — never downloads. Handy for airgapped
    installs, CI fixture runs (see ``tests/fixtures/ot-entities/``), and
    devs hacking on the upstream binary.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        p = Path(override)
        if not verify(p):
            raise InstallError(
                f"{_ENV_OVERRIDE} is set to {p}, but --version didn't succeed."
            )
        return InstallResult(
            path=p, version=detected_version(p) or ENTITY_BINARY_VERSION,
            platform=_safe_platform(), source="env-override",
        )

    dest_dir = Path(dest_dir) if dest_dir is not None else _BIN_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _target_path(dest_dir)

    if target.is_file() and not force:
        if verify(target):
            return InstallResult(
                path=target, version=detected_version(target) or ENTITY_BINARY_VERSION,
                platform=_safe_platform(), source="already-present",
            )
        # binary exists but is broken — fall through and re-download

    platform = current_platform()
    expected = expected_sha256(platform)
    url = _source_url(platform)

    # Raw download → tmp1. If archive, extract inner → tmp2 and swap.
    tmp_download = target.with_suffix(".dl")
    try:
        _download(url, tmp_download, progress=progress)
    except Exception as e:  # noqa: BLE001
        if tmp_download.exists():
            tmp_download.unlink()
        raise InstallError(f"download failed: {e}") from e

    # sha256 is always computed against the downloaded payload (matches the
    # checksums.txt GitHub Releases publishes alongside tarballs).
    actual = _sha256_of(tmp_download)
    if expected and expected != "0" * 64 and actual != expected:
        tmp_download.unlink()
        raise InstallError(
            f"sha256 mismatch for {platform}: expected {expected}, got {actual}"
        )

    # Archive vs raw binary: trust the bytes. GitHub Releases for sem ship
    # .tar.gz; older ``ot-entities`` releases and test doubles ship raw
    # binaries. Magic-byte sniff handles both without needing the manifest
    # to stay perfectly in sync with the test fixtures.
    is_archive = _looks_like_gzip(tmp_download)

    tmp_bin = target.with_suffix(".tmp")
    if tmp_bin.exists():
        tmp_bin.unlink()
    if is_archive:
        try:
            _extract_binary_from_tar_gz(tmp_download, _inner_path(), tmp_bin)
        finally:
            tmp_download.unlink(missing_ok=True)
    else:
        os.replace(tmp_download, tmp_bin)

    # chmod +x
    tmp_bin.chmod(tmp_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(tmp_bin, target)

    if not verify(target):
        target.unlink(missing_ok=True)
        raise InstallError(
            f"binary at {target} installed but --version check failed."
        )

    _prune_old_versions(dest_dir)

    return InstallResult(
        path=target, version=detected_version(target) or ENTITY_BINARY_VERSION,
        platform=platform, source="download",
    )


def _safe_platform() -> str:
    """current_platform() but never raises — used for reporting paths where
    we don't care if the host is supported."""
    try:
        return current_platform()
    except InstallError:
        return "unknown"


_PROVISION_MARKER = _BIN_DIR / ".entity-provision-attempt"


def ensure_installed(
    *, best_effort: bool = True, force: bool = False, retry_failed: bool = False
) -> InstallResult | None:
    """Auto-provision the entity-parser binary, transparently and safely.

    Called from ``init`` (the opt-in moment) and ``setup upgrade`` so the
    binary is provisioned + kept current WITHOUT the user having to discover
    the explicit ``setup entity-parser`` step. Designed for hot/offline paths:

      * env-override or an already-present valid binary -> cheap re-verify.
      * unsupported platform -> returns None (file-level attribution stands).
      * offline / download failure under ``best_effort`` -> returns None and
        drops a one-shot attempt marker so we don't re-download on every
        subsequent ``init``. ``force=True`` (explicit refresh) ignores the
        marker and clears it on success.

    Never raises under ``best_effort`` (the default): entity enrichment is
    optional and degrades gracefully, so provisioning must never break the
    caller (init / upgrade / a background tick).
    """
    override = os.environ.get(_ENV_OVERRIDE)
    already = _target_path(_BIN_DIR).is_file()
    try:
        if override or already:
            # Cheap path: verify (and, if forced, re-download a broken one).
            return install(force=force)
        if not is_platform_supported():
            return None
        if not force and not retry_failed:
            try:
                if (
                    _PROVISION_MARKER.exists()
                    and _PROVISION_MARKER.read_text().strip() == ENTITY_BINARY_VERSION
                ):
                    return None  # already tried + failed for this version
            except OSError:
                pass
        result = install(force=force)
        try:
            _PROVISION_MARKER.unlink(missing_ok=True)  # success: clear marker
        except OSError:
            pass
        return result
    except InstallError:
        try:
            _BIN_DIR.mkdir(parents=True, exist_ok=True)
            _PROVISION_MARKER.write_text(ENTITY_BINARY_VERSION)
        except OSError:
            pass
        if best_effort:
            return None
        raise
