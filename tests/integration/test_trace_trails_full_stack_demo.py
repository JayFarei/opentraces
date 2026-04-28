"""Executable full-stack Trace Trails demo coverage."""

from __future__ import annotations

import os
from pathlib import Path

from tests.integration.harness.trace_trails_full_stack_demo import run_demo


def test_trace_trails_full_stack_demo(tmp_path: Path) -> None:
    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    before_home = os.environ.get("HOME")
    before_paths = {
        mod: (
            mod.OPENTRACES_DIR,
            mod.CONFIG_PATH,
            mod.CREDENTIALS_PATH,
            mod.PROJECTS_DIR,
        )
        for mod in (paths_mod, config_mod)
    }

    summary = run_demo(tmp_path / "demo")

    assert summary["status"] == "ok"
    assert summary["post_commit_before_ingest_anchors"] == 0
    assert summary["trace_id"]
    assert summary["trace_patch_id"]
    assert summary["git_anchor_id"]
    assert summary["step_index"] >= 1
    assert summary["event_counts"]["filesystem_mutation_observed"] >= 1
    assert summary["event_counts"]["watcher_observation_attributed"] >= 1
    assert summary["event_counts"]["trace_patch_created"] >= 1
    assert summary["event_counts"]["git_anchor_search_completed"] >= 1
    assert summary["event_counts"]["git_anchor_created"] == 1
    assert os.environ.get("HOME") == before_home
    for mod, values in before_paths.items():
        assert (
            mod.OPENTRACES_DIR,
            mod.CONFIG_PATH,
            mod.CREDENTIALS_PATH,
            mod.PROJECTS_DIR,
        ) == values
