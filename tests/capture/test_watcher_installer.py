"""Unit tests for opentraces.watcher.installer."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from opentraces.watcher import installer as _inst


def test_current_platform_detects_host():
    plat = _inst.current_platform()
    assert plat in ("macos", "linux")


def test_render_launchd_plist_has_required_keys():
    out = _inst.render_launchd_plist(interval=300, throttle_on_battery=True)
    assert "ai.opentraces.watcher" in out
    assert "<integer>300</integer>" in out
    assert "ThrottleOnBatteryPower" in out
    assert "/.opentraces/bin/ot-watcher" in out


def test_render_systemd_service_and_timer():
    s = _inst.render_systemd_service()
    t = _inst.render_systemd_timer(interval=300)
    assert "[Unit]" in s and "[Service]" in s
    assert "Type=oneshot" in s
    assert "OnUnitActiveSec=300" in t
    assert "timers.target" in t


def test_install_writes_shim_and_unit(tmp_path, monkeypatch):
    # HOME is already redirected by the autouse conftest fixture — install
    # into that sandbox and verify files appear.
    monkeypatch.setattr(
        "opentraces.watcher.installer.current_platform",
        lambda: "macos",
    )
    with patch("opentraces.watcher.installer.subprocess.run") as pr:
        pr.return_value = subprocess.CompletedProcess([], 0, "", "")
        path = _inst.install(interval=300)
    assert path.is_file()
    # Shim was also written.
    home = Path(os.environ["HOME"])
    shim = home / ".opentraces" / "bin" / "ot-watcher"
    assert shim.is_file()
    assert shim.stat().st_mode & 0o100  # user-executable
    assert "opentraces.watcher.daemon" in shim.read_text()


def test_install_linux_writes_service_and_timer(monkeypatch):
    monkeypatch.setattr(
        "opentraces.watcher.installer.current_platform",
        lambda: "linux",
    )
    with patch("opentraces.watcher.installer.subprocess.run") as pr:
        pr.return_value = subprocess.CompletedProcess([], 0, "", "")
        svc = _inst.install(interval=300)
    assert svc.is_file()
    tmr = svc.with_suffix(".timer")
    assert tmr.is_file()


def test_install_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(
        "opentraces.watcher.installer.current_platform",
        lambda: "macos",
    )
    path = _inst.install(interval=300, dry_run=True)
    assert not path.is_file()


def test_status_reports_not_installed(monkeypatch):
    monkeypatch.setattr(
        "opentraces.watcher.installer.current_platform",
        lambda: "macos",
    )
    st = _inst.status()
    assert st.installed is False
    assert st.running is False


def test_status_running_detected_via_launchctl(monkeypatch):
    monkeypatch.setattr(
        "opentraces.watcher.installer.current_platform",
        lambda: "macos",
    )
    # First, install to create the unit file.
    with patch("opentraces.watcher.installer.subprocess.run") as pr:
        pr.return_value = subprocess.CompletedProcess([], 0, "", "")
        _inst.install(interval=300)
    # Now query status — mock launchctl list success.
    with patch("opentraces.watcher.installer.subprocess.run") as pr:
        pr.return_value = subprocess.CompletedProcess([], 0, "", "")
        st = _inst.status()
    assert st.installed is True
    assert st.running is True


def test_uninstall_removes_macos_plist(monkeypatch):
    monkeypatch.setattr(
        "opentraces.watcher.installer.current_platform",
        lambda: "macos",
    )
    with patch("opentraces.watcher.installer.subprocess.run") as pr:
        pr.return_value = subprocess.CompletedProcess([], 0, "", "")
        path = _inst.install(interval=300)
    assert path.is_file()
    with patch("opentraces.watcher.installer.subprocess.run") as pr:
        pr.return_value = subprocess.CompletedProcess([], 0, "", "")
        _inst.uninstall()
    assert not path.is_file()
