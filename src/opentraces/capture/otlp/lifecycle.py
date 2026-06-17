"""OS-level auto-start lifecycle for the OTLP receiver.

macOS launchd plist + Linux systemd user unit. Pure Python; the only
external commands we ever shell out to are ``launchctl``, ``systemctl``,
and ``codesign`` (Ventura+ unsigned-binary detection per plan 078
§"Open questions" #3). Windows is out of scope for v1.

Public surface: install_autostart, uninstall_autostart, is_installed,
is_running, InstallResult dataclass. Plan 078 R9.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from ... import __version__
from ...core.integration_versions import read_version_stamp, stamp_xml
from ...core.paths import OPENTRACES_DIR

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


SHIM_PATH = OPENTRACES_DIR / "bin" / "ot-otlp-receiver"


def _receiver_flags(
    *,
    foreground: bool,
    port: int | None = None,
    bind: str | None = None,
    raw_bodies_dir: Path | None = None,
) -> list[str]:
    """``capture-otlp start`` flags. Each knob is added only when set, so an
    unset port/bind/raw-bodies-dir falls back to the receiver's own defaults
    (4318 / loopback / ~/.opentraces/raw-bodies)."""
    flags: list[str] = []
    if foreground:
        flags.append("--foreground")
    if port is not None:
        flags += ["--port", str(port)]
    if bind is not None:
        flags += ["--bind", str(bind)]
    if raw_bodies_dir is not None:
        flags += ["--raw-bodies-dir", str(raw_bodies_dir)]
    return flags


def _render_receiver_shim(
    *,
    port: int | None = None,
    bind: str | None = None,
    raw_bodies_dir: Path | None = None,
) -> str:
    """Render the receiver shim.

    The launchd/systemd unit points at THIS shim, not the binary — so the unit's
    program is the signed system shell (``/bin/sh``) running a script, which
    macOS Ventura+ loads even though the opentraces binary itself is unsigned
    (the watcher shim works the same way). The shim resolves the CLI at RUN time
    (probing well-known bins under the minimal launchd PATH, verifying the verb),
    so it also survives a ``brew upgrade`` that replaces the binary.
    """
    py = sys.executable or "python3"
    flags = " ".join(
        shlex.quote(f)
        for f in _receiver_flags(
            foreground=True, port=port, bind=bind, raw_bodies_dir=raw_bodies_dir
        )
    )
    return (
        "#!/bin/sh\n"
        f"# opentraces-version: {__version__}\n"
        "# opentraces OTLP receiver shim. Installed by 'setup capture-otlp'.\n"
        "# Resolves the CLI at RUN time and verifies the verb before exec.\n"
        "for c in /opt/homebrew/bin/opentraces /usr/local/bin/opentraces \\\n"
        '         "$HOME/.local/bin/opentraces" "$(command -v opentraces 2>/dev/null)"; do\n'
        '  if [ -n "$c" ] && [ -x "$c" ] \\\n'
        '     && "$c" capture-otlp start --help >/dev/null 2>&1; then\n'
        f'    exec "$c" capture-otlp start {flags} "$@"\n'
        "  fi\n"
        "done\n"
        "# Fallback: interpreter recorded at install time (same release as this).\n"
        f'exec "{py}" -m opentraces capture-otlp start {flags} "$@"\n'
    )


def _write_receiver_shim(
    *,
    port: int | None = None,
    bind: str | None = None,
    raw_bodies_dir: Path | None = None,
) -> Path:
    SHIM_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHIM_PATH.write_text(
        _render_receiver_shim(port=port, bind=bind, raw_bodies_dir=raw_bodies_dir)
    )
    mode = SHIM_PATH.stat().st_mode
    SHIM_PATH.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return SHIM_PATH


def _fallback(binary: Path) -> list[str]:
    return [str(SHIM_PATH)]


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
    # Point the unit at a shim, not the binary: launchd/systemd then run the
    # signed system shell executing a script, so an UNSIGNED opentraces binary
    # loads on macOS Ventura+ (the watcher already works this way) and survives
    # a brew upgrade that replaces the binary. No code-signing gate needed.
    shim = _write_receiver_shim(port=port, bind=bind, raw_bodies_dir=raw_bodies_dir)
    program_args_xml = f"        <string>{_xml_escape(str(shim))}</string>"
    exec_start = shlex.quote(str(shim))

    if plat == "darwin":
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
    """Remove the OS-level auto-start unit. Idempotent.

    On a *failed* unload/disable of an EXISTING unit, return ``ok=False`` with
    a ``reason`` and leave the unit file in place. Removing the file while the
    supervisor still has the job loaded (``KeepAlive``/``Restart=always``)
    would orphan a live daemon AND lose the file needed to retry the
    teardown, so ``setup uninstall`` must be able to see this as an error
    rather than a silent success. The absent-unit path stays ``ok=True``
    (nothing to do); callers gate on :func:`is_installed` to map that to a
    not-installed skip.
    """
    plat = _plat()
    if plat == "darwin":
        if LAUNCHD_PLIST_PATH.exists():
            rc, err = _run(["launchctl", "unload", "-w", str(LAUNCHD_PLIST_PATH)])
            if rc != 0:
                # Leave the plist for retry; the job may still be loaded.
                return InstallResult(
                    ok=False, platform="darwin", path=LAUNCHD_PLIST_PATH,
                    reason="launchctl-unload-failed", details=err or None,
                )
            _unlink(LAUNCHD_PLIST_PATH)
        return InstallResult(ok=True, platform="darwin", path=LAUNCHD_PLIST_PATH)
    if plat == "linux":
        if SYSTEMD_UNIT_PATH.exists():
            rc, err = _run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME])
            if rc != 0:
                # Leave the unit for retry; the service may still be active.
                return InstallResult(
                    ok=False, platform="linux", path=SYSTEMD_UNIT_PATH,
                    reason="systemctl-disable-failed", details=err or None,
                )
            _unlink(SYSTEMD_UNIT_PATH)
            _run(["systemctl", "--user", "daemon-reload"])
        return InstallResult(ok=True, platform="linux", path=SYSTEMD_UNIT_PATH)
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
