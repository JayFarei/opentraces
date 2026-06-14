"""Version stamps and update checks for installed opentraces glue."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .. import __version__

STAMP_PREFIX = "opentraces-version:"
_STAMP_RE = re.compile(r"^\s*#\s*opentraces-version:\s*([^\s]+)\s*$", re.MULTILINE)
_XML_STAMP_RE = re.compile(r"<!--\s*opentraces-version:\s*([^\s]+)\s*-->")
_PYPI_URL = "https://pypi.org/pypi/opentraces/json"
_CACHE_TTL_SECONDS = 24 * 60 * 60
_DEFAULT_CHECK_TIMEOUT_SECONDS = 0.5


def current_cli_version() -> str:
    return __version__


def stamp_script(text: str, version: str | None = None) -> str:
    """Insert or replace a shell/Python comment version stamp."""
    version = version or current_cli_version()
    stamp = f"# {STAMP_PREFIX} {version}\n"
    if _STAMP_RE.search(text):
        return _STAMP_RE.sub(stamp.rstrip("\n"), text, count=1)

    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        return "".join([lines[0], stamp, *lines[1:]])
    return stamp + text


def stamp_xml(text: str, version: str | None = None) -> str:
    version = version or current_cli_version()
    stamp = f"<!-- {STAMP_PREFIX} {version} -->"
    if _XML_STAMP_RE.search(text):
        return _XML_STAMP_RE.sub(stamp, text, count=1)
    lines = text.splitlines(keepends=True)
    if lines and lines[0].lstrip().startswith("<?xml"):
        return "".join([lines[0], stamp + "\n", *lines[1:]])
    return stamp + "\n" + text


def extract_version_stamp(text: str | None) -> str | None:
    if not text:
        return None
    match = _STAMP_RE.search(text) or _XML_STAMP_RE.search(text)
    return match.group(1) if match else None


def read_version_stamp(path: Path) -> str | None:
    try:
        return extract_version_stamp(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def version_drift(deployed_version: str | None, cli_version: str | None = None) -> list[str]:
    cli_version = cli_version or current_cli_version()
    if not deployed_version:
        return ["version-missing"]
    if deployed_version != cli_version:
        return ["version-drift"]
    return []


def _version_cache_path() -> Path:
    from . import paths

    return paths.OPENTRACES_DIR / "version-check.json"


def _version_key(version: str | None) -> tuple[int, ...] | None:
    if not version:
        return None
    parts = re.findall(r"\d+", str(version).split("+", 1)[0])
    if not parts:
        return None
    return tuple(int(part) for part in parts[:4])


def is_version_newer(latest: str | None, installed: str | None) -> bool:
    latest_key = _version_key(latest)
    installed_key = _version_key(installed)
    if latest_key is None or installed_key is None:
        return False
    return latest_key > installed_key


def _version_check_timeout() -> float:
    raw = os.environ.get("OPENTRACES_VERSION_CHECK_TIMEOUT")
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            return _DEFAULT_CHECK_TIMEOUT_SECONDS
    return _DEFAULT_CHECK_TIMEOUT_SECONDS


def _fetch_latest_pypi_version(timeout: float | None = None) -> str:
    timeout = _version_check_timeout() if timeout is None else timeout
    req = Request(_PYPI_URL, headers={"User-Agent": f"opentraces/{__version__}"})
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - fixed PyPI URL.
        payload = json.loads(response.read().decode("utf-8"))
    latest = ((payload.get("info") or {}).get("version") or "").strip()
    if not latest:
        raise ValueError("PyPI response did not include info.version")
    return latest


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def version_status(
    *,
    now: float | None = None,
    force: bool = False,
    cache_ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> dict[str, Any]:
    """Return an offline-safe, cached latest-version check."""
    installed = current_cli_version()
    disabled = os.environ.get("OPENTRACES_DISABLE_VERSION_CHECK") == "1"
    base: dict[str, Any] = {
        "installed_version": installed,
        "latest_version": None,
        "upgrade_available": False,
        "check_state": "disabled" if disabled else "unknown",
        "source": "pypi",
        "cache_path": str(_version_cache_path()),
    }
    if disabled:
        return base

    now = time.time() if now is None else now
    cache_path = _version_cache_path()
    cached = _read_cache(cache_path)
    if (
        not force
        and cached
        and isinstance(cached.get("checked_at"), (int, float))
        and now - float(cached["checked_at"]) < cache_ttl_seconds
    ):
        latest = cached.get("latest_version")
        return {
            **base,
            "latest_version": latest,
            "upgrade_available": is_version_newer(str(latest), installed),
            "check_state": "cached",
            "checked_at": cached.get("checked_at"),
        }

    try:
        latest = _fetch_latest_pypi_version()
    except Exception as exc:  # noqa: BLE001 - doctor must stay offline-safe.
        if cached and cached.get("latest_version"):
            latest = str(cached["latest_version"])
            return {
                **base,
                "latest_version": latest,
                "upgrade_available": is_version_newer(latest, installed),
                "check_state": "stale-cache",
                "checked_at": cached.get("checked_at"),
                "error": str(exc),
            }
        return {**base, "check_state": "unavailable", "error": str(exc)}

    payload = {"latest_version": latest, "checked_at": now, "source": "pypi"}
    _write_cache(cache_path, payload)
    return {
        **base,
        "latest_version": latest,
        "upgrade_available": is_version_newer(latest, installed),
        "check_state": "fresh",
        "checked_at": now,
    }
