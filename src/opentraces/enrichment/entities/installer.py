"""Download + verify the entity-parser binary.

Pipeline:
    1. Detect platform (darwin-arm64 / linux-x86_64 / …)
    2. Read expected sha256 + url from manifest.json
    3. Stream-download to a tmp file under ~/.opentraces/bin/
    4. Verify sha256; reject and delete on mismatch
    5. chmod +x, atomic rename to ot-entities-<version>
    6. Prune prior versions beyond the last 3

Honours ``OPENTRACES_ENTITY_BIN``: if set, the installer skips download
entirely and just verifies the pre-placed binary (for airgapped setups).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform as _platform
import shutil
import stat
import subprocess
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


# --- manifest helpers ------------------------------------------------------

def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def expected_sha256(platform: str) -> str:
    manifest = _load_manifest()
    for src in manifest.get("sources", []):
        if src.get("platform") == platform:
            return src["sha256"]
    raise InstallError(f"no manifest entry for platform: {platform}")


def _source_url(platform: str) -> str:
    manifest = _load_manifest()
    for src in manifest.get("sources", []):
        if src.get("platform") == platform:
            return src["url"]
    raise InstallError(f"no manifest entry for platform: {platform}")


# --- verification ----------------------------------------------------------

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
    return dest_dir / f"ot-entities-{ENTITY_BINARY_VERSION}"


def _prune_old_versions(dest_dir: Path, keep: int = 3) -> list[Path]:
    """Keep the `keep` most recent `ot-entities-<ver>` binaries; remove the rest."""
    if not dest_dir.is_dir():
        return []
    candidates = sorted(
        (p for p in dest_dir.glob("ot-entities-*") if p.is_file()),
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
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:  # noqa: S310
        while True:
            chunk = resp.read(1 << 15)
            if not chunk:
                break
            out.write(chunk)
            if progress is not None:
                progress(len(chunk))


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
            path=p, version=ENTITY_BINARY_VERSION,
            platform=_safe_platform(), source="env-override",
        )

    dest_dir = Path(dest_dir) if dest_dir is not None else _BIN_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = _target_path(dest_dir)

    if target.is_file() and not force:
        if verify(target):
            return InstallResult(
                path=target, version=ENTITY_BINARY_VERSION,
                platform=_safe_platform(), source="already-present",
            )
        # binary exists but is broken — fall through and re-download

    platform = current_platform()
    expected = expected_sha256(platform)
    url = _source_url(platform)

    tmp = target.with_suffix(".tmp")
    try:
        _download(url, tmp, progress=progress)
    except Exception as e:  # noqa: BLE001
        if tmp.exists():
            tmp.unlink()
        raise InstallError(f"download failed: {e}") from e

    actual = _sha256_of(tmp)
    if expected and expected != "0" * 64 and actual != expected:
        tmp.unlink()
        raise InstallError(
            f"sha256 mismatch for {platform}: expected {expected}, got {actual}"
        )

    # chmod +x
    tmp.chmod(tmp.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(tmp, target)

    if not verify(target):
        target.unlink(missing_ok=True)
        raise InstallError(
            f"binary at {target} installed but --version check failed."
        )

    _prune_old_versions(dest_dir)

    return InstallResult(
        path=target, version=ENTITY_BINARY_VERSION,
        platform=platform, source="download",
    )


def _safe_platform() -> str:
    """current_platform() but never raises — used for reporting paths where
    we don't care if the host is supported."""
    try:
        return current_platform()
    except InstallError:
        return "unknown"
