"""Opt-in live UAT for private bucket sync.

This test drives the full expensive path:

* real Claude Code session in tmux;
* real hook/session ingestion through the watcher;
* real private HuggingFace dataset repo used as the bucket remote;
* local bucket restore from that remote.

It is intentionally skipped unless every gate is explicit. Use a disposable
private HF dataset repo; the test uploads a full bucket mirror and does not
delete remote objects afterwards.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Live Claude/tmux UAT must see the developer's real HOME."""
    yield


def _require_live_bucket_env() -> tuple[str, str]:
    if os.environ.get("OT_BUCKET_LIVE_HF") != "1":
        pytest.skip("set OT_BUCKET_LIVE_HF=1 to run live HF bucket UAT")
    if os.environ.get("OT_TRAIL_REAL_REPL") != "1":
        pytest.skip("set OT_TRAIL_REAL_REPL=1 to drive real Claude/tmux")
    if not shutil.which("tmux"):
        pytest.skip("tmux not on PATH")

    token = os.environ.get("OPENTRACES_LIVE_HF_TOKEN") or os.environ.get("HF_TOKEN")
    repo_id = os.environ.get("OPENTRACES_LIVE_HF_BUCKET_REPO_ID")
    if not token or not repo_id:
        pytest.skip(
            "set OPENTRACES_LIVE_HF_TOKEN and "
            "OPENTRACES_LIVE_HF_BUCKET_REPO_ID for live HF bucket UAT"
        )
    return token, repo_id


def _patch_opentraces_state(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    opentraces_dir = root / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    staging_dir = opentraces_dir / "staging"
    projects_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    for mod in (paths_mod, config_mod):
        monkeypatch.setattr(mod, "OPENTRACES_DIR", opentraces_dir)
        monkeypatch.setattr(mod, "CONFIG_PATH", opentraces_dir / "config.json")
        monkeypatch.setattr(mod, "CREDENTIALS_PATH", opentraces_dir / "credentials")
        monkeypatch.setattr(mod, "PROJECTS_DIR", projects_dir)
        if hasattr(mod, "STAGING_DIR"):
            monkeypatch.setattr(mod, "STAGING_DIR", staging_dir)
    return opentraces_dir


def _configure_live_bucket(repo_id: str) -> None:
    from opentraces.core.config import (
        BucketConfig,
        BucketRemoteConfig,
        load_config,
        save_config,
    )

    cfg = load_config()
    cfg.bucket = BucketConfig(
        storage="remote",
        local_cache=True,
        remote=BucketRemoteConfig(
            enabled=True,
            provider="huggingface",
            url=f"hf://{repo_id}",
            visibility="private",
            sync_policy="daemon",
        ),
    )
    save_config(cfg)


def _run_live_claude_scenario(
    monkeypatch: pytest.MonkeyPatch,
    project_dir: Path,
) -> tuple[str, object]:
    from tests.integration.harness import attribution_v2_harness as harness

    override = os.environ.get("OT_BUCKET_LIVE_CLAUDE_CMD") or os.environ.get(
        "OT_TRAIL_CLAUDE_CMD"
    )
    if override:
        monkeypatch.setattr(harness, "CLAUDE", override)

    marker = uuid.uuid4().hex[:10]
    state = harness.RunState(project_dir=project_dir, verbose=False)
    steps = [
        {
            "type": "reset",
            "initial_readme": "# Live HF bucket sync UAT\n",
        },
        {"type": "init_opentraces"},
        {
            "type": "trail_preflight",
            "setup_claude_code": True,
            "setup_git": True,
        },
        {
            "type": "claude",
            "label": "bucket",
            "timeout": 300,
            "quiesce": 8,
            "prompts": [
                (
                    "Create src/live_hf_bucket_uat.py containing exactly "
                    f"LIVE_HF_BUCKET_UAT = '{marker}' and no other behavior."
                )
            ],
        },
        {
            "type": "commit",
            "message": f"live hf bucket sync uat {marker}",
        },
        {"type": "watcher_run_once"},
    ]
    try:
        for step in steps:
            harness.execute_step(state, step)
        harness.run_assertions(
            state,
            [
                {"kind": "tick_field", "name": "sessions_created", "min": 1},
                {"kind": "tick_field", "name": "trail_maturation_anchors", "min": 1},
                {"kind": "tick_field", "name": "bucket_sync_state", "equals": "pushed"},
            ],
        )
        trace_ids = harness._canonical_trace_ids_for_label(state, "bucket")
        if not trace_ids:
            raise AssertionError("live Claude scenario produced no canonical trace id")
        return sorted(trace_ids)[-1], state
    finally:
        if getattr(state, "watcher_proc", None):
            harness._step_stop_watcher(state, {})
        for ctx in state.contexts:
            harness._run([harness.TMUX, "kill-session", "-t", ctx.name], check=False)


def _assert_live_hf_bucket_contains_trace(
    *,
    token: str,
    repo_id: str,
    project_slug: str,
    trace_id: str,
) -> dict:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    info = api.repo_info(repo_id=repo_id, repo_type="dataset")
    assert info.private is True

    files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    assert "manifest.json" in files
    assert f"objects/traces/v1/{project_slug}/{trace_id}/current.json" in files
    assert any(
        path.startswith(f"objects/traces/v1/{project_slug}/{trace_id}/")
        and path.endswith(".json")
        and not path.endswith("/current.json")
        for path in files
    )
    assert f"objects/raw/v1/sources/{project_slug}/{trace_id}.json" in files
    assert f"events/trail/v1/repositories/{project_slug}/head.json" in files

    manifest_path = api.hf_hub_download(
        repo_id=repo_id,
        repo_type="dataset",
        filename="manifest.json",
    )
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    assert manifest["sync"]["eligible"] is True
    assert manifest["trace_records"]["object_count"] >= 1
    assert manifest["raw_sources"]["object_count"] >= 1
    assert manifest["trail_events"]["event_count"] >= 1
    return manifest


@pytest.mark.real_repl
@pytest.mark.trail_real_repl
def test_live_hf_bucket_sync_with_real_claude_tmux(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token, repo_id = _require_live_bucket_env()
    monkeypatch.setenv("HF_TOKEN", token)
    opentraces_dir = _patch_opentraces_state(monkeypatch, tmp_path / "home")
    _configure_live_bucket(repo_id)

    project_dir = tmp_path / "live-hf-bucket-project"
    trace_id, _state = _run_live_claude_scenario(monkeypatch, project_dir)

    from opentraces.core import paths
    from opentraces.core.bucket_remote import remote_pull
    from opentraces.core.bucket_store import bucket_manifest, read_trace_record_object
    from opentraces.core.config import get_project_dir
    from opentraces.core.trace_index import get_trace_path, rebuild_index

    local_manifest = bucket_manifest(write=True, include_objects=False)
    assert local_manifest["sync"]["eligible"] is True
    project_slug = get_project_dir(project_dir).name
    remote_manifest = _assert_live_hf_bucket_contains_trace(
        token=token,
        repo_id=repo_id,
        project_slug=project_slug,
        trace_id=trace_id,
    )
    assert remote_manifest["digest"] == local_manifest["digest"]

    shutil.rmtree(paths.bucket_dir())
    shutil.rmtree(opentraces_dir / "index", ignore_errors=True)
    pulled = remote_pull()
    assert pulled["state"] == "pulled"

    rebuilt = rebuild_index()
    assert rebuilt.trace_count >= 1
    trace_path = get_trace_path(trace_id)
    assert trace_path is not None
    restored = read_trace_record_object(trace_path)
    assert restored is not None
    assert restored.record.trace_id == trace_id
