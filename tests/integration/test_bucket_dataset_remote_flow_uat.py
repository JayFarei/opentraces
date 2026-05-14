"""End-to-end bucket restore to dataset publication UAT.

This test keeps the product boundary explicit:

* the private bucket remote stores trace records, raw source artifacts, and
  portable Trace Trail exports;
* the dataset remote receives only the public HF-shaped dataset surface.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from opentraces.core.datasets import dataset_path, read_row_index, read_row_provenance

from tests.integration.harness.trace_trails_full_stack_demo import (
    _run_cli,
    _temporary_opentraces_home,
    run_installed_runtime_demo,
)


def _write_dataset_schema(path: Path) -> Path:
    schema = {
        "type": "object",
        "required": ["source_trace_id", "source_unit_id", "summary", "step_range"],
        "properties": {
            "source_trace_id": {"type": "string"},
            "source_unit_id": {"type": "string"},
            "source_slice_id": {"type": "string"},
            "summary": {"type": "string"},
            "step_range": {
                "type": "object",
                "required": ["start", "end"],
                "properties": {
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    }
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def _headless_row(trace_id: str) -> str:
    return json.dumps(
        {
            "source_trace_id": trace_id,
            "source_unit_id": f"tu:{trace_id}:restored-bucket",
            "source_slice_id": f"slice:{trace_id}:dataset-publication",
            "summary": "Restored private bucket evidence can seed a public dataset row.",
            "step_range": {"start": 1, "end": 2},
        },
        sort_keys=True,
    )


def _relative_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_restored_private_bucket_feeds_dataset_publish_without_leaking_bucket(
    monkeypatch,
    tmp_path: Path,
) -> None:
    bucket_remote_root = tmp_path / "fake-private-bucket"
    dataset_remote_root = tmp_path / "fake-dataset-remotes"
    dataset_repo = "tester/restored-bucket-dataset"

    summary = run_installed_runtime_demo(
        tmp_path / "installed-runtime-bucket-dataset",
        bucket_remote={
            "provider": "fake",
            "fake_root": bucket_remote_root,
            "sync_policy": "daemon",
        },
    )

    trace_id = summary["trace_id"]
    commit_sha = summary["commit_sha"]
    repo = Path(summary["repo"])
    opentraces_home = Path(summary["opentraces_home"])
    remote_manifest = json.loads((bucket_remote_root / "manifest.json").read_text())
    assert summary["watcher_tick"]["bucket_sync_state"] == "pushed"
    assert remote_manifest["sync"]["eligible"] is True
    assert remote_manifest["trace_records"]["object_count"] >= 1
    assert remote_manifest["raw_sources"]["object_count"] >= 1
    assert remote_manifest["trail_events"]["event_count"] >= 1
    assert any(bucket_remote_root.glob(f"objects/traces/v1/*/{trace_id}/*.json"))
    assert any(bucket_remote_root.glob(f"objects/raw/v1/sources/*/{trace_id}.json"))
    assert any(bucket_remote_root.glob("events/trail/v1/repositories/*/head.json"))

    shutil.rmtree(opentraces_home / "bucket")
    shutil.rmtree(opentraces_home / "index", ignore_errors=True)

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(dataset_remote_root))
    monkeypatch.setenv("OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS", _headless_row(trace_id))

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

        replay_repo = tmp_path / "fresh-replay-repo"
        subprocess.run(
            ["git", "clone", "--quiet", str(repo), str(replay_repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-C", str(replay_repo), "update-ref", "-d", "refs/opentraces/local/events/v1"],
            check=False,
            capture_output=True,
            text=True,
        )
        before_replay = _run_cli(
            [
                "trail",
                "search",
                "--commit",
                commit_sha,
                "--json",
                "--project",
                str(replay_repo),
            ]
        )
        assert before_replay["result_count"] == 0

        after_replay = _run_cli(
            [
                "trail",
                "search",
                "--commit",
                commit_sha,
                "--remote-bucket",
                "--force-remote-bucket",
                "--json",
                "--project",
                str(replay_repo),
            ]
        )
        assert after_replay["remote_bucket"]["remote"]["state"] == "pulled"
        assert after_replay["remote_bucket"]["replay"]["state"] == "imported"
        assert after_replay["remote_bucket"]["replay"]["events_imported"] >= 1
        assert after_replay["result_count"] >= 1

        schema_path = _write_dataset_schema(tmp_path / "restored-bucket-row.schema.json")
        created = _run_cli(
            [
                "dataset",
                "new",
                "restored-bucket-dataset",
                "--workflow",
                "restored-bucket-curator",
                "--workflow-digest",
                "sha256:restored-bucket-workflow",
                "--query-semantic",
                "restored private bucket dataset publication",
                "--schema",
                str(schema_path),
                "--json",
            ]
        )
        assert created["dataset"]["manifest"]["remotes"] == {}

        run = _run_cli(
            [
                "dataset",
                "run",
                "restored-bucket-dataset",
                "--executor",
                "claude-code-headless",
                "--privacy-tier",
                "medium",
                "--trail-freshness",
                "warn",
                "--json",
            ]
        )
        assert run["run"]["appended_count"] == 1

        row_index = read_row_index("restored-bucket-dataset")
        assert len(row_index) == 1
        row = row_index[0]
        assert row.source_trace_id == trace_id
        assert row.provenance["bucket"]["manifest_digest"].startswith("sha256:")
        assert row.provenance["bucket"]["source_trace_record"]["trace_id"] == trace_id

        provenance = read_row_provenance("restored-bucket-dataset")[row.row_id]
        assert provenance["bucket"]["manifest_digest"].startswith("sha256:")
        assert provenance["privacy"]["privacy_tier"] == "medium"
        assert provenance["run"]["executor"] == "claude-code-headless"

        review = _run_cli(["dataset", "review", "restored-bucket-dataset", "--json"])
        assert review["rows"][row.row_id]["provenance"]["bucket"]["source_trace_record"][
            "trace_id"
        ] == trace_id

        remote = _run_cli(
            [
                "dataset",
                "remote",
                "create",
                "restored-bucket-dataset",
                dataset_repo,
                "--json",
            ]
        )
        assert remote["remote"]["visibility"] == "private"

        approved = _run_cli(
            ["dataset", "approve", "restored-bucket-dataset", "--all", "--json"]
        )
        assert approved["counts"]["publishable"] == 1

        checked = _run_cli(
            ["dataset", "publish", "restored-bucket-dataset", "--check-only", "--json"]
        )
        assert checked["publish"]["uploaded"] is False
        assert checked["publish"]["new_row_count"] == 1
        assert "README.md" in checked["publish"]["staged_files"]
        assert "dataset_infos.json" in checked["publish"]["staged_files"]
        assert any(path.startswith("data/") for path in checked["publish"]["staged_files"])

        published = _run_cli(["dataset", "publish", "restored-bucket-dataset", "--json"])
        assert published["publish"]["uploaded"] is True
        assert published["publish"]["new_row_count"] == 1

        local_dataset_files = _relative_files(dataset_path("restored-bucket-dataset"))
        assert ".opentraces/row_provenance.jsonl" in local_dataset_files

    remote_root = dataset_remote_root / "tester" / "restored-bucket-dataset"
    remote_files = _relative_files(remote_root)
    public_files = {path for path in remote_files if not path.startswith(".fake_")}
    assert "README.md" in public_files
    assert "dataset_infos.json" in public_files
    assert "schemas/row.schema.json" in public_files
    assert any(path.startswith("data/") and path.endswith(".jsonl") for path in public_files)
    assert not any(path.startswith(".opentraces/") for path in public_files)
    assert not any(path.startswith("objects/") for path in public_files)
    assert not any(path.startswith("events/") for path in public_files)
    assert "manifest.json" not in public_files
