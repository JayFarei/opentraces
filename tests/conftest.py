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

import os
import sys
from pathlib import Path

import pytest


_SCHEMA_SRC = Path(__file__).resolve().parents[1] / "packages" / "opentraces-schema" / "src"
if str(_SCHEMA_SRC) not in sys.path:
    sys.path.insert(0, str(_SCHEMA_SRC))


# Eagerly import the modules we monkeypatch below so their module-init
# values capture the real HOME. If deferred until inside the fixture body,
# the first fixture invocation patches HOME first, then triggers the import,
# which bakes the tmpdir path into the module's "original" state. monkeypatch
# then reverts to that tmpdir path instead of the real HOME, and every later
# test inherits the stale value — including scenario tests that override
# this fixture with a no-op.
from opentraces.core import paths as _paths  # noqa: E402
from opentraces.core import config as _config  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _guard_real_home_enlistments():
    """Fail the run if tests leak enlistments into the REAL ~/.opentraces.

    #23/#65 guard: leaked test-fixture enlistments (``ot-h-*``, ``ot-otbox-*``)
    multiplied the live watcher's sweep surface to 869 projects. Per-test HOME
    isolation below covers in-process code, but subprocess paths that rebuild
    their own environment can still escape — this session-level belt catches
    any escape loudly instead of letting it accumulate on dev machines.
    """
    real_home = Path(os.environ.get("HOME") or Path.home())
    real_projects = real_home / ".opentraces" / "projects"

    def _names() -> set[str]:
        if not real_projects.is_dir():
            return set()
        try:
            return {entry.name for entry in real_projects.iterdir()}
        except OSError:
            return set()

    before = _names()
    yield
    leaked = _names() - before
    if leaked:
        pytest.fail(
            "tests leaked enlistments into the real ~/.opentraces/projects/: "
            f"{sorted(leaked)[:10]}{' …' if len(leaked) > 10 else ''} — "
            "every test must run against an isolated HOME "
            "(see _isolate_opentraces_global_state)",
            pytrace=False,
        )


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state(tmp_path_factory, monkeypatch):
    home = tmp_path_factory.mktemp("home_isolated")
    monkeypatch.setenv("HOME", str(home))

    opentraces_dir = home / ".opentraces"
    opentraces_dir.mkdir()
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir()
    staging_dir = opentraces_dir / "staging"
    staging_dir.mkdir()

    for mod in (_paths, _config):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
        if hasattr(mod, "STAGING_DIR"):
            monkeypatch.setattr(mod, "STAGING_DIR", staging_dir)
    yield opentraces_dir
