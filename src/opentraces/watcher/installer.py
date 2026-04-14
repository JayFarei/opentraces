"""Install / uninstall / status for the watcher service.

macOS  -> launchd user agent at ~/Library/LaunchAgents/ai.opentraces.watcher.plist
Linux  -> systemd user unit + timer under ~/.config/systemd/user/

The install step also writes a worker shim at ~/.opentraces/bin/ot-watcher
that invokes ``python3 -m opentraces.watcher.daemon run-forever``.

All shell-outs to ``launchctl`` / ``systemctl`` are mockable via
``subprocess.run``; tests never actually manipulate the host.
"""

from __future__ import annotations

import os
import platform as _platform
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..core.paths import OPENTRACES_DIR

LAUNCHD_LABEL = "ai.opentraces.watcher"
SYSTEMD_UNIT_NAME = "opentraces-watcher"


# --- platform detection ----------------------------------------------------

def current_platform() -> str:
    sys_name = _platform.system().lower()
    if sys_name == "darwin":
        return "macos"
    if sys_name == "linux":
        return "linux"
    raise RuntimeError(f"watcher: unsupported platform {sys_name!r}")


def _real_home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def _opentraces_home() -> Path:
    """Root of the opentraces state tree, respecting HOME overrides."""
    # In tests we override HOME; but OPENTRACES_DIR was resolved at import
    # time. Recompute on each call so tests that set HOME still work.
    return _real_home() / ".opentraces"


def _shim_path() -> Path:
    return _opentraces_home() / "bin" / "ot-watcher"


def _launchd_plist_path() -> Path:
    return _real_home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _systemd_dir() -> Path:
    return _real_home() / ".config" / "systemd" / "user"


def _systemd_service_path() -> Path:
    return _systemd_dir() / f"{SYSTEMD_UNIT_NAME}.service"


def _systemd_timer_path() -> Path:
    return _systemd_dir() / f"{SYSTEMD_UNIT_NAME}.timer"


# --- template rendering ----------------------------------------------------

def render_launchd_plist(interval: int = 300,
                         throttle_on_battery: bool = True,
                         home_placeholder: str = "{HOME}") -> str:
    throttle = "<true/>" if throttle_on_battery else "<false/>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '<dict>\n'
        '  <key>Label</key>\n'
        f'  <string>{LAUNCHD_LABEL}</string>\n'
        '  <key>ProgramArguments</key>\n'
        '  <array>\n'
        f'    <string>{home_placeholder}/.opentraces/bin/ot-watcher</string>\n'
        '  </array>\n'
        '  <key>StartInterval</key>\n'
        f'  <integer>{int(interval)}</integer>\n'
        '  <key>RunAtLoad</key>\n'
        '  <true/>\n'
        '  <key>ThrottleInterval</key>\n'
        '  <integer>30</integer>\n'
        '  <key>ThrottleOnBatteryPower</key>\n'
        f'  {throttle}\n'
        '  <key>StandardOutPath</key>\n'
        f'  <string>{home_placeholder}/.opentraces/logs/watcher.stdout.log</string>\n'
        '  <key>StandardErrorPath</key>\n'
        f'  <string>{home_placeholder}/.opentraces/logs/watcher.stderr.log</string>\n'
        '</dict>\n'
        '</plist>\n'
    )


def render_systemd_service(home_placeholder: str = "{HOME}") -> str:
    return (
        "[Unit]\n"
        "Description=opentraces background attribution watcher\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={home_placeholder}/.opentraces/bin/ot-watcher\n"
        f"StandardOutput=append:{home_placeholder}/.opentraces/logs/watcher.stdout.log\n"
        f"StandardError=append:{home_placeholder}/.opentraces/logs/watcher.stderr.log\n"
    )


def render_systemd_timer(interval: int = 300) -> str:
    return (
        "[Unit]\n"
        f"Description=opentraces watcher timer (every {int(interval)}s)\n"
        "\n"
        "[Timer]\n"
        "OnBootSec=60\n"
        f"OnUnitActiveSec={int(interval)}\n"
        "AccuracySec=30\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


# --- shim ------------------------------------------------------------------

def _render_shim() -> str:
    py = sys.executable or "python3"
    return (
        "#!/bin/sh\n"
        "# opentraces watcher shim — invokes the Python daemon service loop.\n"
        f'exec "{py}" -m opentraces.watcher.daemon run-forever "$@"\n'
    )


def _write_shim() -> Path:
    path = _shim_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_shim())
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


# --- install / uninstall ---------------------------------------------------

@dataclass
class WatcherStatus:
    platform: str
    installed: bool
    running: bool
    last_run_at: datetime | None
    interval_seconds: int | None
    unit_path: Path | None


def install(interval: int = 300, *, throttle_on_battery: bool = True,
            dry_run: bool = False) -> Path:
    """Write the platform-appropriate unit file + shim. Returns the unit path."""
    plat = current_platform()
    home = str(_real_home())
    home_opentraces = _opentraces_home()

    if not dry_run:
        (home_opentraces / "logs").mkdir(parents=True, exist_ok=True)
        _write_shim()

    if plat == "macos":
        unit_path = _launchd_plist_path()
        content = render_launchd_plist(interval, throttle_on_battery,
                                       home_placeholder=home)
        if dry_run:
            return unit_path
        unit_path.parent.mkdir(parents=True, exist_ok=True)
        unit_path.write_text(content)
        # Best-effort load; failures don't break install (tests mock this).
        _launchctl("unload", str(unit_path), check=False)
        _launchctl("load", str(unit_path), check=False)
        return unit_path

    # linux / systemd --user
    svc_path = _systemd_service_path()
    tmr_path = _systemd_timer_path()
    svc_content = render_systemd_service(home_placeholder=home)
    tmr_content = render_systemd_timer(interval)
    if dry_run:
        return svc_path
    _systemd_dir().mkdir(parents=True, exist_ok=True)
    svc_path.write_text(svc_content)
    tmr_path.write_text(tmr_content)
    _systemctl("daemon-reload", check=False)
    _systemctl("enable", "--now", f"{SYSTEMD_UNIT_NAME}.timer", check=False)
    return svc_path


def uninstall(*, dry_run: bool = False) -> None:
    plat = current_platform()
    if plat == "macos":
        unit_path = _launchd_plist_path()
        if not dry_run:
            _launchctl("unload", str(unit_path), check=False)
            if unit_path.is_file():
                unit_path.unlink()
        return
    svc_path = _systemd_service_path()
    tmr_path = _systemd_timer_path()
    if dry_run:
        return
    _systemctl("disable", "--now", f"{SYSTEMD_UNIT_NAME}.timer", check=False)
    for p in (svc_path, tmr_path):
        if p.is_file():
            p.unlink()
    _systemctl("daemon-reload", check=False)


def status() -> WatcherStatus:
    from .daemon import DEFAULT_INTERVAL
    plat = current_platform()
    if plat == "macos":
        unit = _launchd_plist_path()
        installed = unit.is_file()
        running = False
        if installed:
            r = _launchctl("list", LAUNCHD_LABEL, check=False, capture=True)
            running = bool(r and r.returncode == 0)
        return WatcherStatus(
            platform=plat, installed=installed, running=running,
            last_run_at=None, interval_seconds=DEFAULT_INTERVAL if installed else None,
            unit_path=unit if installed else None,
        )
    unit = _systemd_service_path()
    timer = _systemd_timer_path()
    installed = unit.is_file() and timer.is_file()
    running = False
    if installed:
        r = _systemctl("is-active", f"{SYSTEMD_UNIT_NAME}.timer",
                       check=False, capture=True)
        running = bool(r and r.returncode == 0 and "active" in (r.stdout or ""))
    return WatcherStatus(
        platform=plat, installed=installed, running=running,
        last_run_at=None, interval_seconds=DEFAULT_INTERVAL if installed else None,
        unit_path=unit if installed else None,
    )


# --- shelling helpers (mockable) ------------------------------------------

def _launchctl(*args: str, check: bool = True, capture: bool = False):
    exe = shutil.which("launchctl") or "launchctl"
    return _run([exe, *args], check=check, capture=capture)


def _systemctl(*args: str, check: bool = True, capture: bool = False):
    exe = shutil.which("systemctl") or "systemctl"
    return _run([exe, "--user", *args], check=check, capture=capture)


def _run(cmd: list[str], *, check: bool, capture: bool):
    try:
        return subprocess.run(
            cmd,
            check=check,
            capture_output=capture,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def start() -> None:
    plat = current_platform()
    if plat == "macos":
        _launchctl("load", str(_launchd_plist_path()), check=False)
    else:
        _systemctl("start", f"{SYSTEMD_UNIT_NAME}.timer", check=False)


def stop() -> None:
    plat = current_platform()
    if plat == "macos":
        _launchctl("unload", str(_launchd_plist_path()), check=False)
    else:
        _systemctl("stop", f"{SYSTEMD_UNIT_NAME}.timer", check=False)
