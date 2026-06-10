"""Faultpoint seam contract (otbox 2.0 phase 6).

The seam itself: armed only under BOTH env vars; unknown sites raise; the
otbox mutation layer recognizes faultpoint: faults and refuses to cache a
faulted world. The first faultpoint KILL (redaction) is deferred (see
guarantees.toml) pending an ingest-arming investigation — this pins the
infrastructure so that kill can land without re-deriving the safety rails.
"""

from __future__ import annotations

import pytest

from opentraces.core.faultpoints import SITES, armed, armed_site
from .mutations import is_faultpoint, known_fault


def test_disarmed_by_default(monkeypatch):
    monkeypatch.delenv("OT_FAULTPOINT", raising=False)
    monkeypatch.delenv("OT_OTBOX_FAULT_OK", raising=False)
    assert armed_site() is None
    for site in SITES:
        assert not armed(site)


def test_requires_both_env_vars(monkeypatch):
    site = next(iter(SITES))
    monkeypatch.setenv("OT_FAULTPOINT", site)
    monkeypatch.delenv("OT_OTBOX_FAULT_OK", raising=False)
    assert not armed(site), "OT_FAULTPOINT alone must not arm"
    monkeypatch.setenv("OT_OTBOX_FAULT_OK", "1")
    assert armed(site)
    assert armed_site() == site


def test_only_named_site_arms(monkeypatch):
    sites = sorted(SITES)
    assert len(sites) >= 2
    monkeypatch.setenv("OT_FAULTPOINT", sites[0])
    monkeypatch.setenv("OT_OTBOX_FAULT_OK", "1")
    assert armed(sites[0])
    assert not armed(sites[1])


def test_unknown_site_raises_loudly(monkeypatch):
    monkeypatch.setenv("OT_FAULTPOINT", "no-such-site")
    monkeypatch.setenv("OT_OTBOX_FAULT_OK", "1")
    with pytest.raises(RuntimeError, match="not a registered faultpoint"):
        armed(next(iter(SITES)))


def test_mutations_recognizes_faultpoint_faults():
    assert is_faultpoint("faultpoint:redaction-skipped") == "redaction-skipped"
    assert is_faultpoint("disable-security-tools") is None
    assert known_fault("disable-security-tools")
    assert known_fault("faultpoint:redaction-skipped")
    assert not known_fault("nonsense")
    with pytest.raises(KeyError):
        is_faultpoint("faultpoint:unknown-site")
