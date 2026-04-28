"""Production-path Trace Trails watcher UAT.

This module complements ``test_trace_trails_full_stack_demo``. The existing
demo proves the attribution substrate through Claude hook-boundary observation.
This test drives the command/install surfaces a customer machine depends on:

* install the Git hook with ``opentraces --json setup git``;
* let a real ``git commit`` execute the installed post-commit shim;
* run ``opentraces watcher tick --project ... --json`` to ingest the session
  transcript, reconcile the boundary-observed filesystem mutation, and mature
  the Git Anchor.
"""

from __future__ import annotations

from pathlib import Path

from tests.integration.harness.trace_trails_full_stack_demo import (
    run_installed_runtime_demo,
)


def test_trace_trails_installed_runtime_uat(tmp_path: Path) -> None:
    summary = run_installed_runtime_demo(tmp_path / "installed-runtime")

    assert summary["status"] == "ok"
    assert summary["setup_git_installed"] is True
    assert summary["post_commit_hook_log"]["trail_anchors_created"] == 0
    assert summary["pre_tick_event_counts"]["filesystem_mutation_observed"] >= 1
    assert "watcher_observation_attributed" not in summary["pre_tick_event_counts"]
    assert "git_anchor_created" not in summary["pre_tick_event_counts"]
    assert summary["watcher_tick"]["error"] is None
    assert summary["watcher_tick"]["fs_observations"] == 0
    assert summary["watcher_tick"]["sessions_created"] >= 1
    assert summary["watcher_tick"]["trail_maturation_searches"] >= 1
    assert summary["watcher_tick"]["trail_maturation_anchors"] == 1
    assert summary["event_counts"]["filesystem_mutation_observed"] >= 1
    assert summary["event_counts"]["watcher_observation_attributed"] >= 1
    assert summary["event_counts"]["git_anchor_created"] == 1
