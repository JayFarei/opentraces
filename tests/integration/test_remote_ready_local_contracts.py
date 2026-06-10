from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from opentraces_schema import Agent, Step, TraceRecord

from opentraces.cli import main
from opentraces.core.bucket_store import iter_trace_record_objects, write_trace_record
from opentraces.core.datasets import (
    dataset_path,
    read_row_index,
    read_row_provenance,
)
from opentraces.security import SECURITY_VERSION


def _trace(trace_id: str, content: str = "Analyze Hugging Face bucket sync") -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": content},
        steps=[
            Step(step_index=1, role="user", content=content),
            Step(
                step_index=2,
                role="agent",
                content="I will inspect the bucket and dataset workflow.",
            ),
        ],
        dependencies=["huggingface_hub"],
        outcome={"success": True, "committed": True},
    )


def _scanned_trace(trace_id: str) -> TraceRecord:
    record = _trace(trace_id)
    record.security.scanned = True
    record.security.classifier_version = SECURITY_VERSION
    return record


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


def _fake_dataset_rows(*, trace_id: str = "trace-hf-1") -> str:
    return json.dumps(
        {
            "source_trace_id": trace_id,
            "source_unit_id": f"tu:{trace_id}:burst:1",
            "source_slice_id": f"slice:{trace_id}:hf-sync",
            "summary": "The trace captures local bucket design before Hugging Face sync.",
            "step_range": {"start": 12, "end": 28},
        },
        sort_keys=True,
    )


def _current_trail_freshness() -> list[dict[str, object]]:
    return [
        {
            "kind": "trail_projection_freshness",
            "severity": "info",
            "state": "current",
            "project_slug": "community-traces-hf",
            "last_synced_at": "2026-05-07T10:00:00Z",
        }
    ]


def test_remote_ready_bucket_round_trip_and_sync_blockers(monkeypatch, tmp_path):
    runner = CliRunner()
    remote_root = tmp_path / "remote-bucket"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    write_trace_record(
        _scanned_trace("trace-hf-bucket-1"),
        project_slug="community-traces-hf",
        source_layer="canonical",
        legacy_mirror=False,
        privacy_tier="medium",
    )

    status = runner.invoke(main, ["bucket", "status", "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    bucket = status_payload["bucket"]
    assert bucket["trace_records"]["object_count"] == 1
    assert bucket["trace_records"]["syncable_count"] == 1
    assert bucket["trace_records"]["last_write_at"].endswith("Z")
    assert bucket["sync"]["eligible"] is True
    assert bucket["digest"].startswith("sha256:")

    before = runner.invoke(main, ["bucket", "remote", "status", "--json"])
    assert before.exit_code == 0, before.output
    assert json.loads(before.output)["remote"]["state"] == "missing"

    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.output)["remote"]["state"] == "pushed"

    clean_diff = runner.invoke(main, ["bucket", "remote", "diff", "--json"])
    assert clean_diff.exit_code == 0, clean_diff.output
    assert json.loads(clean_diff.output)["remote"]["different"] is False

    write_trace_record(
        _scanned_trace("trace-local-only"),
        project_slug="community-traces-hf",
        source_layer="canonical",
        legacy_mirror=False,
    )
    dirty_diff = runner.invoke(main, ["bucket", "remote", "diff", "--json"])
    assert dirty_diff.exit_code == 0, dirty_diff.output
    dirty_payload = json.loads(dirty_diff.output)
    assert dirty_payload["remote"]["different"] is True
    assert dirty_payload["remote"]["state"] == "local_ahead"

    pulled = runner.invoke(main, ["bucket", "remote", "pull", "--force", "--json"])
    assert pulled.exit_code == 0, pulled.output
    assert [obj.trace_id for obj in iter_trace_record_objects()] == ["trace-hf-bucket-1"]

    write_trace_record(
        _trace("trace-unfiltered"),
        project_slug="community-traces-hf",
        source_layer="canonical",
        legacy_mirror=False,
        privacy_tier="off",
    )
    blocked = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert blocked.exit_code == 3
    assert "not eligible for remote sync" in blocked.output


def test_remote_push_refreshes_unscanned_records_when_tools_enabled(monkeypatch, tmp_path):
    """Push self-heals records captured before security tools were enabled.

    A capture made with zero enabled security tools lands unscanned
    (``security.scanned = False``) and blocks ``bucket remote push`` with
    ``unfiltered_records``. Once tools are enabled (what ``setup bucket``
    does), push must re-scan those records itself — parity with the daemon
    path, whose index-sync refresh does this implicitly. Surfaced by the
    first real-HF run of the live-hf-* otbox journeys (Phase B4).
    """
    from opentraces.core import paths
    from opentraces.core.config import load_config, save_config

    runner = CliRunner()
    remote_root = tmp_path / "remote-bucket"
    monkeypatch.setenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(remote_root))

    record = _trace("trace-unscanned-refresh")
    assert record.security.scanned is False
    traces_dir = paths.PROJECTS_DIR / "community-traces-hf" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / "trace-unscanned-refresh.jsonl").write_text(
        record.model_dump_json() + "\n", encoding="utf-8"
    )

    # Zero tools enabled: the gate blocks and the error names the remedy.
    blocked = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert blocked.exit_code == 3
    assert "unfiltered_records" in blocked.output
    assert "setup bucket" in blocked.output

    cfg = load_config()
    cfg.security.regex.enabled = True
    cfg.security.entropy.enabled = True
    save_config(cfg)

    pushed = runner.invoke(main, ["bucket", "remote", "push", "--json"])
    assert pushed.exit_code == 0, pushed.output
    assert json.loads(pushed.output)["remote"]["state"] == "pushed"
    refreshed = [
        obj
        for obj in iter_trace_record_objects()
        if obj.trace_id == "trace-unscanned-refresh"
    ]
    assert refreshed, "record should be mirrored into the bucket"
    assert refreshed[0].envelope["security"]["syncable"] is True


def test_remote_ready_dataset_run_review_and_row_provenance(monkeypatch, tmp_path):
    runner = CliRunner()
    schema_path = _write_dataset_schema(tmp_path / "hf-sync-row.schema.json")
    write_trace_record(
        _scanned_trace("trace-hf-dataset-1"),
        project_slug="community-traces-hf",
        source_layer="canonical",
        legacy_mirror=False,
    )
    monkeypatch.setattr(
        "opentraces.core.workflow_runner._trail_freshness_for_dataset",
        lambda _dataset, _scope: _current_trail_freshness(),
    )
    monkeypatch.setenv(
        "OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS",
        _fake_dataset_rows(trace_id="trace-hf-dataset-1"),
    )

    created = runner.invoke(
        main,
        [
            "dataset",
            "new",
            "hf-sync-intents",
            "--workflow",
            "hf-sync-curator",
            "--workflow-digest",
            "sha256:hf-sync-workflow",
            "--query-semantic",
            "hugging face bucket sync",
            "--schema",
            str(schema_path),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.output)
    assert created_payload["dataset"]["manifest"]["remotes"] == {}
    assert created_payload["dataset"]["manifest"].get("active_remote") is None

    result = runner.invoke(
        main,
        [
            "dataset",
            "run",
            "hf-sync-intents",
            "--executor",
            "claude-code-headless",
            "--privacy-tier",
            "medium",
            "--trail-freshness",
            "warn",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run"]["appended_count"] == 1
    assert payload["cursor_advanced"] is True

    run_packet = json.loads(
        (
            dataset_path("hf-sync-intents")
            / ".opentraces"
            / "runs"
            / payload["run_id"]
            / "run_packet.json"
        ).read_text(encoding="utf-8")
    )
    assert run_packet["candidate_query"]["args"]["semantic"] == "hugging face bucket sync"
    assert run_packet["source_provenance"]["bucket_manifest"]["digest"].startswith("sha256:")
    assert run_packet["source_provenance"]["bucket_manifest"]["trace_records"]["object_count"] == 1
    assert run_packet["privacy_tier"] == "medium"
    assert run_packet["trail_freshness_policy"] == "warn"
    assert run_packet["trail_freshness"][0]["state"] == "current"

    row_index = read_row_index("hf-sync-intents")
    assert len(row_index) == 1
    entry = row_index[0]
    assert entry.source_trace_id == "trace-hf-dataset-1"
    assert entry.source_unit_id == "tu:trace-hf-dataset-1:burst:1"
    assert entry.source_slice_id == "slice:trace-hf-dataset-1:hf-sync"
    assert entry.provenance["workflow"]["digest"] == "sha256:hf-sync-workflow"
    assert entry.provenance["source_refs"]["step_range"] == {"start": 12, "end": 28}

    provenance = read_row_provenance("hf-sync-intents")[entry.row_id]
    assert provenance["bucket"]["manifest_digest"].startswith("sha256:")
    assert provenance["bucket"]["source_trace_record"]["record_hash"].startswith("sha256:")
    assert provenance["bucket"]["source_trace_record"]["trace_id"] == "trace-hf-dataset-1"
    assert provenance["privacy"]["privacy_tier"] == "medium"
    assert provenance["trail"]["freshness"][0]["last_synced_at"] == "2026-05-07T10:00:00Z"
    assert provenance["run"]["executor"] == "claude-code-headless"

    review = runner.invoke(main, ["dataset", "review", "hf-sync-intents", "--json"])
    assert review.exit_code == 0, review.output
    review_payload = json.loads(review.output)
    review_row = review_payload["rows"][entry.row_id]
    assert review_payload["schema"]["json_schema"]["required"] == [
        "source_trace_id",
        "source_unit_id",
        "summary",
        "step_range",
    ]
    assert review_row["data"]["summary"].startswith("The trace captures local bucket design")
    assert review_row["provenance"]["source_refs"]["trace_id"] == "trace-hf-dataset-1"


def test_remote_ready_dataset_run_fail_policy_blocks_stale_trails(monkeypatch, tmp_path):
    runner = CliRunner()
    schema_path = _write_dataset_schema(tmp_path / "hf-stale-row.schema.json")
    monkeypatch.setattr(
        "opentraces.core.workflow_runner._trail_freshness_for_dataset",
        lambda _dataset, _scope: [
            {
                "kind": "trail_projection_freshness",
                "severity": "warning",
                "state": "stale",
                "project_slug": "community-traces-hf",
                "last_synced_at": "2026-05-06T09:00:00Z",
            }
        ],
    )
    monkeypatch.setenv(
        "OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS",
        _fake_dataset_rows(trace_id="trace-stale-trail"),
    )

    created = runner.invoke(
        main,
        [
            "dataset",
            "new",
            "hf-stale-intents",
            "--workflow",
            "hf-sync-curator",
            "--schema",
            str(schema_path),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output

    result = runner.invoke(
        main,
        [
            "dataset",
            "run",
            "hf-stale-intents",
            "--executor",
            "claude-code-headless",
            "--trail-freshness",
            "fail",
            "--json",
        ],
    )

    assert result.exit_code == 3
    assert "Trace Trail projection is stale" in result.output
    assert (dataset_path("hf-stale-intents") / "data" / "train.jsonl").read_text(
        encoding="utf-8"
    ) == ""
