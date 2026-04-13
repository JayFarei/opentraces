"""Shared test fixtures.

Autouse isolation: every test runs with a tmp HOME and a redirected
``~/.opentraces/`` so nothing leaks into the developer's real config.
Any test that invokes ``opentraces init`` would otherwise register the
pytest tmpdir into the global opted-in registry — this fixture prevents
that.

Tests that deliberately want to exercise the real config can override
the fixture locally.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home_isolated")
    monkeypatch.setenv("HOME", str(home))

    opentraces_dir = home / ".opentraces"
    opentraces_dir.mkdir()

    from opentraces.core import paths as _paths
    from opentraces.core import config as _config

    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "STAGING_DIR", opentraces_dir / "staging")
        monkeypatch.setattr(mod, "STATE_PATH", opentraces_dir / "state.json")
        monkeypatch.setattr(mod, "UPLOADED_DIR", opentraces_dir / "uploaded")
    yield opentraces_dir
