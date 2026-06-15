"""OS-level auto-start lifecycle for the OTLP receiver.

macOS launchd plist + Linux systemd user unit. Pure Python; the only
external commands we ever shell out to are ``launchctl``, ``systemctl``,
and ``codesign`` (Ventura+ unsigned-binary detection per plan 078
§"Open questions" #3). Windows is out of scope for v1.

Public surface: install_autostart, uninstall_autostart, is_installed,
is_running, InstallResult dataclass. Plan 078 R9.
"""

from __future__ import annotations

import logging
import os
import platform as _platmod
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from ... import __version__
from ...core.integration_versions import read_version_stamp, stamp_xml
from ...core.paths import OPENTRACES_DIR

logger = logging.getLogger("opentraces.otlp.lifecycle")

LAUNCHD_LABEL = "com.opentraces.otlp-receiver"
LAUNCHD_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
SYSTEMD_UNIT_NAME = "opentraces-otlp-receiver.service"
SYSTEMD_UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME
PID_FILE_PATH = OPENTRACES_DIR / "otlp-receiver.pid"

_PLIST = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<!-- opentraces-version: {version} -->
<dict>
    <key>Label</key><string>com.opentraces.otlp-receiver</string>
    <key>ProgramArguments</key>
    <array>
{program_args_xml}
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>StandardOutPath</key><string>{log_dir}/otlp-receiver.log</string>
    <key>StandardErrorPath</key><string>{log_dir}/otlp-receiver.err</string>
</dict>
</plist>
"""

_UNIT = """\
[Unit]
# opentraces-version: {version}
Description=opentraces OTLP receiver
After=network.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=always
RestartSec=5
StandardOutput=append:{log_dir}/otlp-receiver.log
StandardError=append:{log_dir}/otlp-receiver.err

[Install]
WantedBy=default.target
"""


@dataclass
class InstallResult:
    ok: bool
    platform: str
    path: Path | None = None
    reason: str | None = None
    fallback_command: list[str] | None = None
    details: str | None = None
    extra: dict = field(default_factory=dict)


def _plat() -> str:
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return "unsupported"


def _bin(b: Path | None) -> Path | None:
    if b is not None:
        return b
    found = shutil.which("opentraces")
    return Path(found) if found else None


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    """rc=-1 means the binary itself wasn't found on PATH."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stderr or p.stdout or "").strip()
    except FileNotFoundError:
        return -1, f"{cmd[0]} not found on PATH"


def _ventura_plus() -> bool:
    try:
        ver = _platmod.mac_ver()[0]
        return bool(ver) and int(ver.split(".", 1)[0]) >= 13
    except Exception:
        return False


def _signed(binary: Path) -> bool:
    rc, _ = _run(["codesign", "-dv", str(binary)], timeout=5)
    return True if rc == -1 else rc == 0  # no codesign tool => assume okay


def _receiver_args(
    binary: Path,
    *,
    foreground: bool,
    port: int | None = None,
    bind: str | None = None,
    raw_bodies_dir: Path | None = None,
) -> list[str]:
    """Build the ``capture-otlp start`` argv the unit should exec.

    Optional knobs are appended only when set, so the auto-start unit reflects
    the same port/bind/raw-bodies-dir the user passed to ``setup capture-otlp``;
    when unset, the receiver falls back to its own defaults (4318 / loopback /
    ~/.opentraces/raw-bodies). Keeps the no-arg caller (integration repair)
    byte-identical to the previous default unit.
    """
    args = [str(binary), "capture-otlp", "start"]
    if foreground:
        args.append("--foreground")
    if port is not None:
        args += ["--port", str(port)]
    if bind is not None:
        args += ["--bind", str(bind)]
    if raw_bodies_dir is not None:
        args += ["--raw-bodies-dir", str(raw_bodies_dir)]
    return args


def _fallback(binary: Path) -> list[str]:
    return [str(binary), "capture-otlp", "start"]


def install_autostart(
    opentraces_binary: Path | None = None,
    log_dir: Path | None = None,
    *,
    port: int | None = None,
    bind: str | None = None,
    raw_bodies_dir: Path | None = None,
) -> InstallResult:
    """Install an OS-level auto-start unit for the OTLP receiver.

    ``port`` / ``bind`` / ``raw_bodies_dir`` are baked into the unit's argv when
    provided (so a non-default ``setup capture-otlp`` is honored at boot); all
    are optional and default to the receiver's own defaults when omitted.
    """
    plat = _plat()
    binary = _bin(opentraces_binary)
    ld = log_dir or (OPENTRACES_DIR / "logs")
    if plat == "unsupported":
        return InstallResult(
            ok=False, platform="unsupported", reason="unsupported-platform",
            details=f"sys.platform={sys.platform!r}; only darwin and linux are supported",
        )
    if binary is None:
        return InstallResult(
            ok=False, platform=plat, reason="opentraces-binary-not-found",
            details="No opentraces binary on PATH; pass opentraces_binary=Path(...) explicitly.",
        )
    ld.mkdir(parents=True, exist_ok=True)
    args = _receiver_args(
        binary, foreground=True, port=port, bind=bind, raw_bodies_dir=raw_bodies_dir
    )
    program_args_xml = "\n".join(
        f"        <string>{_xml_escape(a)}</string>" for a in args
    )
    exec_start = " ".join(args)

    if plat == "darwin":
        if _ventura_plus() and not _signed(binary):
            logger.warning("opentraces binary at %s is unsigned on Ventura+", binary)
            return InstallResult(
                ok=False, platform="darwin", path=LAUNCHD_PLIST_PATH,
                reason="unsigned-binary-on-ventura-plus",
                fallback_command=_fallback(binary),
                details=(
                    "macOS Ventura+ requires signed binaries for LaunchAgents. "
                    "Run `opentraces capture-otlp start` manually, or sign the "
                    "opentraces binary with `codesign --force --sign - <path>` "
                    "for ad-hoc signing."
                ),
            )
        LAUNCHD_PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        LAUNCHD_PLIST_PATH.write_text(stamp_xml(_PLIST.format(
            program_args_xml=program_args_xml,
            log_dir=str(ld),
            version=__version__,
        )))
        rc, err = _run(["launchctl", "load", "-w", str(LAUNCHD_PLIST_PATH)])
        if rc == 0:
            return InstallResult(ok=True, platform="darwin", path=LAUNCHD_PLIST_PATH)
        return InstallResult(
            ok=False, platform="darwin", path=LAUNCHD_PLIST_PATH,
            reason="launchctl-missing" if rc == -1 else "launchctl-load-failed",
            fallback_command=_fallback(binary), details=err or None,
        )

    # linux
    SYSTEMD_UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SYSTEMD_UNIT_PATH.write_text(_UNIT.format(
        exec_start=exec_start,
        log_dir=str(ld),
        version=__version__,
    ))
    rc, err = _run(["systemctl", "--user", "daemon-reload"])
    if rc != 0:
        return InstallResult(
            ok=False, platform="linux", path=SYSTEMD_UNIT_PATH,
            reason="systemctl-missing" if rc == -1 else "systemctl-daemon-reload-failed",
            fallback_command=_fallback(binary), details=err or None,
        )
    rc, err = _run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT_NAME])
    if rc == 0:
        return InstallResult(ok=True, platform="linux", path=SYSTEMD_UNIT_PATH)
    return InstallResult(
        ok=False, platform="linux", path=SYSTEMD_UNIT_PATH,
        reason="systemctl-enable-failed",
        fallback_command=_fallback(binary), details=err or None,
    )


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except FileNotFoundError:
        pass


def uninstall_autostart() -> InstallResult:
    """Remove the OS-level auto-start unit. Idempotent."""
    plat = _plat()
    if plat == "darwin":
        details = None
        if LAUNCHD_PLIST_PATH.exists():
            rc, err = _run(["launchctl", "unload", "-w", str(LAUNCHD_PLIST_PATH)])
            if rc != 0:
                details = err or None
            _unlink(LAUNCHD_PLIST_PATH)
        return InstallResult(ok=True, platform="darwin", path=LAUNCHD_PLIST_PATH, details=details)
    if plat == "linux":
        rc, err = _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME])
        details = err if rc != 0 else None
        if SYSTEMD_UNIT_PATH.exists():
            _unlink(SYSTEMD_UNIT_PATH)
            _run(["systemctl", "--user", "daemon-reload"])
        return InstallResult(ok=True, platform="linux", path=SYSTEMD_UNIT_PATH, details=details)
    return InstallResult(
        ok=False, platform="unsupported", reason="unsupported-platform",
        details=f"sys.platform={sys.platform!r}",
    )


def is_installed() -> bool:
    plat = _plat()
    if plat == "darwin":
        return LAUNCHD_PLIST_PATH.exists()
    if plat == "linux":
        return SYSTEMD_UNIT_PATH.exists()
    return False


def autostart_path() -> Path | None:
    plat = _plat()
    if plat == "darwin":
        return LAUNCHD_PLIST_PATH
    if plat == "linux":
        return SYSTEMD_UNIT_PATH
    return None


def autostart_version() -> str | None:
    path = autostart_path()
    return read_version_stamp(path) if path else None


def _pid_from_file() -> int | None:
    try:
        raw = PID_FILE_PATH.read_text().strip()
        pid = int(raw) if raw else 0
        return pid if pid > 0 else None
    except (FileNotFoundError, ValueError):
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _pid_fallback() -> tuple[bool, int | None]:
    pid = _pid_from_file()
    return (True, pid) if pid is not None and _alive(pid) else (False, None)


def is_running() -> tuple[bool, int | None]:
    """Return (running?, pid). Falls back to PID file when OS query fails."""
    plat = _plat()
    if plat == "darwin":
        rc, out = _run(["launchctl", "list", LAUNCHD_LABEL], timeout=5)
        if rc == 0:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith('"PID"'):
                    token = line.split("=", 1)[-1].strip().rstrip(";").strip()
                    try:
                        pid = int(token)
                        if pid > 0:
                            return True, pid
                    except ValueError:
                        pass
        return _pid_fallback()
    if plat == "linux":
        rc, out = _run(["systemctl", "--user", "is-active", SYSTEMD_UNIT_NAME], timeout=5)
        if rc != 0 or out.strip() != "active":
            return _pid_fallback()
        pid: int | None = None
        rc2, out2 = _run(
            ["systemctl", "--user", "show", SYSTEMD_UNIT_NAME, "--property=MainPID"], timeout=5,
        )
        if rc2 == 0:
            _, _, value = out2.partition("=")
            try:
                candidate = int(value.strip())
                if candidate > 0:
                    pid = candidate
            except ValueError:
                pass
        return True, pid
    return _pid_fallback()
