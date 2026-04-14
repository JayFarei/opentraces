"""Phase-3 watcher installer: rendered unit files match golden fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from opentraces.watcher import installer as _inst


FIXTURES = Path(__file__).parent.parent / "fixtures" / "watcher"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_launchd_plist_matches_fixture():
    actual = _inst.render_launchd_plist(interval=300,
                                        throttle_on_battery=True)
    assert actual == _read_fixture("launchd.plist.expected")


def test_systemd_service_matches_fixture():
    actual = _inst.render_systemd_service()
    assert actual == _read_fixture("opentraces-watcher.service.expected")


def test_systemd_timer_matches_fixture():
    actual = _inst.render_systemd_timer(interval=300)
    assert actual == _read_fixture("opentraces-watcher.timer.expected")


@pytest.mark.parametrize("interval", [60, 600, 1800])
def test_launchd_interval_roundtrips(interval):
    rendered = _inst.render_launchd_plist(interval=interval)
    assert f"<integer>{interval}</integer>" in rendered


def test_launchd_throttle_off():
    rendered = _inst.render_launchd_plist(interval=300,
                                          throttle_on_battery=False)
    assert "<key>ThrottleOnBatteryPower</key>\n  <false/>" in rendered


def test_systemd_timer_interval_roundtrips():
    rendered = _inst.render_systemd_timer(interval=900)
    assert "OnUnitActiveSec=900" in rendered
    assert "every 900s" in rendered
