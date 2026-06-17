"""Issue #96 — doctor's machine contract must split the live search snapshot
from the deprecated legacy ``index.db``.

The human render was fixed under #89 (snapshot row first, legacy row labelled a
"deprecated compat cache"). This pins the unfinished JSON half: the top-level
``trace_index.state`` / ``rebuild_advice`` must reflect the snapshot that
``trace query`` actually serves, and a NEW ``legacy_index_state`` carries the
old legacy-driven concern. Otherwise an agent parsing ``trace_index.state``
sees ``missing`` and is told to run the deprecated ``--legacy`` rebuild even
when search is perfectly healthy.

The autouse ``_isolate_opentraces_global_state`` fixture (tests/conftest.py)
redirects ``~/.opentraces`` into a tmp HOME, so these are hermetic.
"""
from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.doctor import _trace_index_status
from opentraces.core.trace_index import default_index_path
from opentraces.core.trace_search_snapshot import build_trace_search_snapshot


SENTINEL = "---OPENTRACES_JSON---"


def _extract_json(output: str) -> dict:
    _, _, payload = output.rpartition(SENTINEL)
    return json.loads(payload)


def test_legacy_absent_snapshot_ok() -> None:
    """Healthy snapshot + NO legacy index.db: top-level state reflects the
    snapshot (ok), advice points at the snapshot rebuild (never ``--legacy``),
    and the legacy concern is carried separately as ``legacy_index_state``."""
    build_trace_search_snapshot()
    assert not default_index_path().exists()

    info = _trace_index_status()

    # Top-level = the live, query-serving snapshot.
    assert info["state"] == "ok"
    assert info["rebuild_advice"] == "opentraces trace index rebuild"
    assert "--legacy" not in info["rebuild_advice"]
    # The legacy concern is split out, not conflated with the live state.
    assert info["legacy_index_state"] == "missing"
    # Additive: every legacy/contract key is retained.
    assert info["legacy_rebuild_advice"] == "opentraces trace index rebuild --legacy"
    assert info["search_snapshot"]["state"] == "ok"


def test_snapshot_dirty_advises_snapshot_rebuild() -> None:
    """A dirty/stale snapshot drives ``state == 'stale'`` and the current
    snapshot rebuild advice, never the legacy ``--legacy`` string."""
    build_trace_search_snapshot()
    from opentraces.core.trace_search_state import mark_search_snapshot_dirty

    mark_search_snapshot_dirty("test_dirty")

    info = _trace_index_status()

    assert info["state"] == "stale"
    assert info["rebuild_advice"] == "opentraces trace index rebuild"
    assert "--legacy" not in info["rebuild_advice"]
    assert info["search_snapshot"]["state"] == "stale"


def test_command_local_doctor_json() -> None:
    """``opentraces doctor --json`` is accepted and emits the same doctor
    payload as the global ``opentraces --json doctor`` form."""
    build_trace_search_snapshot()
    runner = CliRunner()

    local = runner.invoke(main, ["doctor", "--json"])
    assert local.exit_code == 0, local.output
    global_form = runner.invoke(main, ["--json", "doctor"])
    assert global_form.exit_code == 0, global_form.output

    local_doctor = _extract_json(local.output)["doctor"]
    global_doctor = _extract_json(global_form.output)["doctor"]
    assert local_doctor["trace_index"]["state"] == global_doctor["trace_index"]["state"]
    assert (
        local_doctor["trace_index"]["rebuild_advice"]
        == global_doctor["trace_index"]["rebuild_advice"]
    )
    assert "legacy_index_state" in local_doctor["trace_index"]
