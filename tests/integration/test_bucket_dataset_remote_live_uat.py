"""Opt-in live UAT for bucket restore into dataset publication.

This test uses real HuggingFace dataset repositories for both sides of the
product boundary:

* a private bucket remote that receives trace records, raw source artifacts, and
  Trace Trail exports;
* a separate private dataset remote that receives only the public dataset
  surface.

It is skipped unless explicitly enabled. Use disposable repos; this test creates
private HuggingFace dataset repositories and does not delete remote objects.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pytest

from opentraces.core.datasets import dataset_path, read_row_index, read_row_provenance

from tests.integration.harness.trace_trails_full_stack_demo import (
    _run_cli,
    _temporary_opentraces_home,
    run_installed_runtime_demo,
)
from tests.integration.test_bucket_dataset_remote_flow_uat import (
    _headless_row,
    _relative_files,
    _write_dataset_schema,
)


def _require_live_hf_env() -> tuple[str, str]:
    if os.environ.get("OT_BUCKET_DATASET_LIVE_HF") != "1":
        pytest.skip("set OT_BUCKET_DATASET_LIVE_HF=1 to run live bucket/dataset HF UAT")
    try:
        from huggingface_hub import HfApi, get_token
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"huggingface_hub is unavailable: {exc}")

    token = os.environ.get("OPENTRACES_LIVE_HF_TOKEN") or os.environ.get("HF_TOKEN")
    token = token or get_token()
    if not token:
        pytest.skip("set OPENTRACES_LIVE_HF_TOKEN or run hf auth login")
    who = HfApi(token=token).whoami()
    owner = str(who.get("name") or "")
    if not owner:
        pytest.skip("authenticated HuggingFace account did not return a username")
    return token, owner


def _repo_pair(owner: str) -> tuple[str, str]:
    prefix = os.environ.get("OPENTRACES_LIVE_HF_REPO_PREFIX", "opentraces-live-full-e2e")
    suffix = uuid.uuid4().hex[:10]
    bucket_repo = os.environ.get("OPENTRACES_LIVE_HF_BUCKET_REPO_ID")
    dataset_repo = os.environ.get("OPENTRACES_LIVE_HF_DATASET_REPO_ID")
    return (
        bucket_repo or f"{owner}/{prefix}-bucket-{suffix}",
        dataset_repo or f"{owner}/{prefix}-dataset-{suffix}",
    )


def _assert_remote_public_dataset_surface(repo_id: str, token: str) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    assert api.dataset_info(repo_id).private is True
    files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    assert "README.md" in files
    assert "dataset_infos.json" in files
    assert "schemas/row.schema.json" in files
    assert any(path.startswith("data/") and path.endswith(".jsonl") for path in files)
    assert not any(path.startswith(".opentraces/") for path in files)
    assert not any(path.startswith("objects/") for path in files)
    assert not any(path.startswith("events/") for path in files)
    assert "manifest.json" not in files


def _assert_remote_private_bucket_surface(
    *,
    repo_id: str,
    token: str,
    trace_id: str,
) -> None:
    from huggingface_hub import HfApi

    api = HfApi(token=token)
    assert api.dataset_info(repo_id).private is True
    files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset"))
    assert "manifest.json" in files
    assert any(
        path.startswith("objects/traces/v1/") and f"/{trace_id}/" in path
        for path in files
    )
    assert any(
        path.startswith("objects/raw/v1/sources/") and path.endswith(f"/{trace_id}.json")
        for path in files
    )
    assert any(
        path.startswith("events/trail/v1/repositories/") and path.endswith("/head.json")
        for path in files
    )


def test_live_hf_bucket_restore_feeds_private_dataset_publish(tmp_path: Path) -> None:
    token, owner = _require_live_hf_env()
    bucket_repo, dataset_repo = _repo_pair(owner)
    os.environ["HF_TOKEN"] = token
    os.environ["HUGGINGFACE_TOKEN"] = token

    summary = run_installed_runtime_demo(
        tmp_path / "installed-runtime-live-hf-bucket-dataset",
        bucket_remote={
            "provider": "huggingface",
            "repo": bucket_repo,
            "sync_policy": "daemon",
        },
    )

    trace_id = summary["trace_id"]
    commit_sha = summary["commit_sha"]
    repo = Path(summary["repo"])
    opentraces_home = Path(summary["opentraces_home"])
    _assert_remote_private_bucket_surface(
        repo_id=bucket_repo,
        token=token,
        trace_id=trace_id,
    )

    shutil.rmtree(opentraces_home / "bucket")
    shutil.rmtree(opentraces_home / "index", ignore_errors=True)
    os.environ["OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS"] = _headless_row(trace_id)

    with _temporary_opentraces_home(opentraces_home.parent):
        restored = _run_cli(
            [
                "trace",
                "get",
                trace_id,
                "--remote-bucket",
                "--force-remote-bucket",
                "--json",
            ],
            cwd=repo,
        )
        assert restored["trace"]["trace_id"] == trace_id
        assert restored["remote_bucket"]["remote"]["state"] == "pulled"
        assert restored["remote_bucket"]["index"]["trace_count"] >= 1

        query = _run_cli(
            [
                "trace",
                "query",
                "--cwd",
                "--limit",
                "10",
                "--remote-bucket",
                "--force-remote-bucket",
                "--json",
            ],
            cwd=repo,
        )
        assert query["remote_bucket"]["remote"]["state"] == "pulled"
        assert query["remote_bucket"]["index"]["trace_count"] >= 1
        assert any(candidate["trace_id"] == trace_id for candidate in query["candidates"])

        trail = _run_cli(
            [
                "trail",
                "search",
                "--commit",
                commit_sha,
                "--remote-bucket",
                "--force-remote-bucket",
                "--json",
                "--project",
                str(repo),
            ]
        )
        assert trail["remote_bucket"]["remote"]["state"] == "pulled"
        assert trail["remote_bucket"]["replay"]["state"] in {"current", "imported"}
        assert trail["result_count"] >= 1

        schema_path = _write_dataset_schema(tmp_path / "live-hf-restored-row.schema.json")
        created = _run_cli(
            [
                "dataset",
                "new",
                "live-hf-restored-dataset",
                "--workflow",
                "live-hf-restored-curator",
                "--workflow-digest",
                "sha256:live-hf-restored-workflow",
                "--query-semantic",
                "live private bucket restore to dataset publication",
                "--schema",
                str(schema_path),
                "--json",
            ]
        )
        assert created["dataset"]["manifest"]["remotes"] == {}

        remote = _run_cli(
            [
                "dataset",
                "remote",
                "create",
                "live-hf-restored-dataset",
                dataset_repo,
                "--json",
            ]
        )
        assert remote["remote"]["visibility"] == "private"

        run = _run_cli(
            [
                "dataset",
                "run",
                "live-hf-restored-dataset",
                "--executor",
                "claude-code-headless",
                "--privacy-tier",
                "medium",
                "--trail-freshness",
                "warn",
                "--approve-new",
                "--publish-check-only",
                "--json",
            ]
        )
        assert run["run"]["appended_count"] == 1
        assert run["review"]["approved_new_count"] == 1
        assert run["publish"]["uploaded"] is False
        assert run["publish"]["check_only"] is True
        assert run["publish"]["new_row_count"] == 1

        row = read_row_index("live-hf-restored-dataset")[0]
        assert row.source_trace_id == trace_id
        assert row.provenance["bucket"]["manifest_digest"].startswith("sha256:")
        provenance = read_row_provenance("live-hf-restored-dataset")[row.row_id]
        assert provenance["bucket"]["source_trace_record"]["trace_id"] == trace_id
        assert provenance["privacy"]["privacy_tier"] == "medium"

        published = _run_cli(
            [
                "dataset",
                "run",
                "live-hf-restored-dataset",
                "--executor",
                "claude-code-headless",
                "--publish",
                "--json",
            ]
        )
        assert published["run"]["appended_count"] == 0
        assert published["publish"]["uploaded"] is True
        assert published["publish"]["check_only"] is False
        assert published["publish"]["new_row_count"] == 1

        local_dataset_files = _relative_files(dataset_path("live-hf-restored-dataset"))
        assert ".opentraces/row_provenance.jsonl" in local_dataset_files

    _assert_remote_public_dataset_surface(dataset_repo, token)
