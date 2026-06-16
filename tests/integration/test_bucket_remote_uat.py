"""Bucket remote UAT coverage.

The fake-provider test is intentionally deterministic and safe for normal
regression runs. The live HuggingFace + real Claude/tmux variant lives in
``test_bucket_remote_live_uat.py`` behind explicit opt-in flags.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from tests.integration.harness.trace_trails_full_stack_demo import (
    _run_cli,
    _temporary_opentraces_home,
    run_installed_runtime_demo,
)


def _manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_installed_runtime_syncs_bucket_to_fake_remote_and_restores(tmp_path: Path) -> None:
    remote_root = tmp_path / "fake-private-bucket"
    summary = run_installed_runtime_demo(
        tmp_path / "installed-runtime-bucket",
        bucket_remote={
            "provider": "fake",
            "fake_root": remote_root,
            "sync_policy": "daemon",
        },
    )

    tick = summary["watcher_tick"]
    assert tick["bucket_sync_state"] == "pushed"
    assert tick["bucket_sync_error"] is None
    assert tick["bucket_sync_digest"]

    manifest = _manifest(remote_root / "manifest.json")
    assert manifest["digest"] == tick["bucket_sync_digest"]
    assert manifest["trace_records"]["object_count"] >= 1
    assert manifest["trace_records"]["syncable_count"] >= 1
    assert manifest["sync"]["eligible"] is True
    assert manifest["raw_sources"]["object_count"] >= 1
    assert manifest["trail_events"]["event_count"] >= 1

    trace_id = summary["trace_id"]
    assert any(remote_root.glob(f"objects/traces/v1/*/{trace_id}/*.json"))
    assert any(remote_root.glob(f"objects/raw/v1/sources/*/{trace_id}.json"))
    # Plan 080 §20 Resolution B: events mirror moved to
    # bucket/events/v1/batches/<seq>-<batch-id>.jsonl.gz + index.json.
    assert (remote_root / "events" / "v1" / "index.json").exists() or any(
        remote_root.glob("events/v1/batches/*.jsonl.gz")
    )

    opentraces_home = Path(summary["opentraces_home"])
    local_bucket = opentraces_home / "bucket"
    shutil.rmtree(local_bucket)
    shutil.rmtree(opentraces_home / "index", ignore_errors=True)

    with _temporary_opentraces_home(opentraces_home.parent):
        pulled = _run_cli(["bucket", "remote", "pull", "--json"])
        assert pulled["remote"]["state"] == "pulled"

        rebuilt = _run_cli(["trace", "index", "rebuild", "--json"])
        # `trace index rebuild` is snapshot-only by default (issue #89); the
        # read-model trace count lives under `search_snapshot`, not `index`.
        assert rebuilt["search_snapshot"]["trace_count"] >= 1

        restored = _run_cli(
            ["trace", "get", trace_id, "--json"],
            cwd=Path(summary["repo"]),
        )
        assert restored["trace"]["trace_id"] == trace_id
