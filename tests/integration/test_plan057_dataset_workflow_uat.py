from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import dataset_path

from tests._dataset_egress import neutralize_dataset_egress
from tests.integration._script_workflow import (
    install_rows_workflow,
    install_scriptless_workflow,
)


@pytest.fixture(autouse=True)
def _clear_dataset_egress(monkeypatch):
    # The --publish automation path predates the #194 egress clearance gate and
    # projects synthetic trace ids that have no bucket entry.
    neutralize_dataset_egress(monkeypatch)


_ROWS_UNSET = object()


def _create_dataset(
    runner: CliRunner,
    *,
    rows: object = _ROWS_UNSET,
    scriptless: bool = False,
) -> None:
    # #190: `dataset new --workflow <name>` resolves the bare name BEFORE the
    # dataset is created, so the workflow must already be installed. Installing
    # the FINAL content here (not a stub) also keeps the pinned digest equal to
    # what `dataset run --executor script` recomputes at run time, so no
    # digest-drift warning is emitted.
    if scriptless:
        install_scriptless_workflow("grill-me-intent-curator")
    else:
        install_rows_workflow(
            "grill-me-intent-curator", _fake_rows() if rows is _ROWS_UNSET else rows
        )
    result = runner.invoke(
        dataset_group,
        [
            "new",
            "grill-me-intents",
            "--workflow",
            "grill-me-intent-curator",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output


def _fake_rows() -> str:
    return "\n".join(
        [
            json.dumps(
                {
                    "source_trace_id": "trace-1",
                    "source_unit_id": "tu:trace-1:trace",
                    "summary": "The user wanted a stricter design review.",
                },
                sort_keys=True,
            ),
            json.dumps(
                {
                    "source_trace_id": "trace-1",
                    "source_unit_id": "tu:trace-1:trace",
                    "summary": "The user wanted a stricter design review.",
                },
                sort_keys=True,
            ),
            json.dumps(
                {
                    "source_trace_id": "trace-2",
                    "source_unit_id": "tu:trace-2:trace",
                    "summary": "Invalid extra field.",
                    "extra": True,
                },
                sort_keys=True,
            ),
        ]
    )


def _single_fake_row() -> str:
    return json.dumps(
        {
            "source_trace_id": "trace-auto-publish",
            "source_unit_id": "tu:trace-auto-publish:trace",
            "summary": "A workflow row can be approved and published by automation.",
        },
        sort_keys=True,
    )


def test_dataset_run_dry_run_real_run_and_current_agent_modes():
    runner = CliRunner()
    _create_dataset(runner)

    dry = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--dry-run",
            "--executor",
            "script",
            "--limit",
            "5",
            "--verbose",
            "--json",
        ],
    )
    assert dry.exit_code == 0, dry.output
    dry_payload = json.loads(dry.output)
    assert dry_payload["run"]["dry_run"] is True
    assert dry_payload["run"]["appended_count"] == 0
    assert dry_payload["run"]["would_append_count"] == 1
    assert dry_payload["run"]["duplicate_count"] == 1
    assert dry_payload["run"]["validation_error_count"] == 1
    assert (dataset_path("grill-me-intents") / "data" / "train.jsonl").read_text() == ""
    assert not dry_payload["cursor_advanced"]
    assert (dataset_path("grill-me-intents") / ".opentraces" / "runs" / dry_payload["run_id"] / "RUN.md").exists()

    real = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--executor",
            "script",
            "--json",
        ],
    )
    assert real.exit_code == 0, real.output
    real_payload = json.loads(real.output)
    assert real_payload["run"]["dry_run"] is False
    assert real_payload["run"]["appended_count"] == 1
    assert real_payload["run"]["duplicate_count"] == 1
    assert real_payload["run"]["validation_error_count"] == 1
    assert real_payload["cursor_advanced"]

    repeated = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--executor",
            "script",
            "--json",
        ],
    )
    assert repeated.exit_code == 0, repeated.output
    repeated_payload = json.loads(repeated.output)
    assert repeated_payload["run"]["appended_count"] == 0
    assert repeated_payload["run"]["duplicate_count"] == 2

    current_agent = runner.invoke(
        dataset_group,
        ["run", "grill-me-intents", "--executor", "current-agent", "--json"],
    )
    assert current_agent.exit_code == 0, current_agent.output
    current_payload = json.loads(current_agent.output)
    assert current_payload["status"] == "instructions"
    assert current_payload["run"]["appended_count"] == 0
    assert "OT_DATASET_OUTPUT" in (
        dataset_path("grill-me-intents")
        / ".opentraces"
        / "runs"
        / current_payload["run_id"]
        / "RUN.md"
    ).read_text()


def test_dataset_run_lock_prevents_overlapping_runs():
    runner = CliRunner()
    _create_dataset(runner)
    lock = dataset_path("grill-me-intents") / ".opentraces" / ".lock"
    lock.write_text("run_existing")

    result = runner.invoke(
        dataset_group,
        ["run", "grill-me-intents", "--executor", "script", "--json"],
    )

    assert result.exit_code == 3
    assert "already in progress" in result.output


def test_dataset_run_packet_carries_query_source_provenance():
    runner = CliRunner()
    # #190: the bare `curator` name must resolve to an installed workflow before
    # `dataset new` can bind it. The current-agent executor resolves no package
    # at run time, so any loadable workflow suffices here.
    install_rows_workflow("curator", "")

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "mongodb-intents",
            "--workflow",
            "curator",
            "--query-semantic",
            "mongodb atlas",
            "--query-source",
            "projection",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output

    result = runner.invoke(
        dataset_group,
        [
            "run",
            "mongodb-intents",
            "--executor",
            "current-agent",
            "--privacy-tier",
            "high",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    run_packet = json.loads(
        (
            dataset_path("mongodb-intents")
            / ".opentraces"
            / "runs"
            / payload["run_id"]
            / "run_packet.json"
        ).read_text()
    )

    assert run_packet["candidate_query"]["args"]["semantic"] == "mongodb atlas"
    assert run_packet["privacy_tier"] == "high"
    assert run_packet["trail_freshness_policy"] == "warn"
    assert "trail_freshness" in run_packet
    assert run_packet["source_provenance"]["schema_version"] == (
        "opentraces.dataset.source_provenance.v1"
    )
    # `dataset new` defers the bucket snapshot for fast creation, but `dataset
    # run` is the moment the bucket is projected into rows, so the run packet
    # carries the real captured bucket snapshot (digest), not the deferred
    # placeholder.
    assert run_packet["source_provenance"]["bucket_snapshot"]["digest"].startswith(
        "sha256:"
    )
    assert run_packet["source_provenance"]["bucket_manifest"]["digest"].startswith(
        "sha256:"
    )
    assert run_packet["source_provenance"]["query_fingerprint"]


def test_dataset_run_packet_carries_resolved_security_policy(tmp_path):
    """Plan 092 R9: the run packet exposes the dataset's resolved security
    policy so the executor knows which tools are required/enabled."""
    md = tmp_path / "secure.md"
    md.write_text(
        "---\nname: secure\nsecurity:\n"
        "  required_tools: [regex, entropy]\n"
        "  optional_tools: [business_logic]\n"
        "  default_enabled_tools: [business_logic]\n"
        "---\n\n# secure\n",
        encoding="utf-8",
    )
    runner = CliRunner()
    assert runner.invoke(
        dataset_group, ["new", "secure-ds", "--workflow", str(md), "--json"]
    ).exit_code == 0
    result = runner.invoke(
        dataset_group,
        ["run", "secure-ds", "--executor", "current-agent", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    run_packet = json.loads(
        (
            dataset_path("secure-ds")
            / ".opentraces"
            / "runs"
            / payload["run_id"]
            / "run_packet.json"
        ).read_text()
    )
    assert run_packet["security"]["source"] == "workflow"
    assert run_packet["security"]["required_tools"] == ["regex", "entropy"]
    assert run_packet["security"]["enabled_tools"] == ["regex", "entropy", "business_logic"]


def test_dataset_run_can_fail_on_stale_trail_freshness(monkeypatch):
    runner = CliRunner()
    _create_dataset(runner)
    monkeypatch.setattr(
        "opentraces.core.workflow_runner._trail_freshness_for_dataset",
        lambda _dataset, _scope: [
            {
                "kind": "trail_projection_freshness",
                "severity": "warning",
                "state": "stale",
                "project_slug": "demo",
            }
        ],
    )

    result = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--executor",
            "current-agent",
            "--trail-freshness",
            "fail",
            "--json",
        ],
    )

    assert result.exit_code == 3
    assert "Trace Trail projection is stale" in result.output


def test_successful_scheduled_zero_row_run_advances_cursor():
    runner = CliRunner()
    _create_dataset(runner, rows="")

    result = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--executor",
            "script",
            "--scheduled",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run"]["appended_count"] == 0
    assert payload["cursor_advanced"] is True
    assert "last_successful_run_id" in (
        dataset_path("grill-me-intents") / ".opentraces" / "cursors.yaml"
    ).read_text()


def test_dataset_run_can_approve_new_rows_and_drive_publish_automation(
    monkeypatch,
    tmp_path,
) -> None:
    runner = CliRunner()
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    # #190: install the row emitter BEFORE binding so `dataset new` resolves the
    # workflow and pins its real digest, which the script executor then matches.
    install_rows_workflow("auto-publish-curator", _single_fake_row())

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "auto-published-intents",
            "--workflow",
            "auto-publish-curator",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output

    remote = runner.invoke(
        dataset_group,
        [
            "remote",
            "create",
            "auto-published-intents",
            "tester/auto-published-intents",
            "--json",
        ],
    )
    assert remote.exit_code == 0, remote.output
    assert json.loads(remote.output)["remote"]["visibility"] == "private"

    checked = runner.invoke(
        dataset_group,
        [
            "run",
            "auto-published-intents",
            "--executor",
            "script",
            "--approve-new",
            "--publish-check-only",
            "--json",
        ],
    )
    assert checked.exit_code == 0, checked.output
    checked_payload = json.loads(checked.output)
    assert checked_payload["run"]["appended_count"] == 1
    assert checked_payload["review"]["approved_new_count"] == 1
    assert checked_payload["publish"]["uploaded"] is False
    assert checked_payload["publish"]["check_only"] is True
    assert checked_payload["publish"]["new_row_count"] == 1
    assert "README.md" in checked_payload["publish"]["staged_files"]
    assert "dataset_infos.json" in checked_payload["publish"]["staged_files"]
    assert any(
        path.startswith("data/") for path in checked_payload["publish"]["staged_files"]
    )

    published = runner.invoke(
        dataset_group,
        [
            "run",
            "auto-published-intents",
            "--executor",
            "script",
            "--publish",
            "--json",
        ],
    )
    assert published.exit_code == 0, published.output
    published_payload = json.loads(published.output)
    assert published_payload["run"]["appended_count"] == 0
    assert published_payload["publish"]["uploaded"] is True
    assert published_payload["publish"]["check_only"] is False
    assert published_payload["publish"]["new_row_count"] == 1

    remote_root = tmp_path / "remotes" / "tester" / "auto-published-intents"
    remote_rows = "\n".join(path.read_text() for path in (remote_root / "data").glob("*.jsonl"))
    assert "A workflow row can be approved and published by automation." in remote_rows
    assert not (remote_root / ".opentraces").exists()


def test_failed_scheduled_run_does_not_advance_cursor_and_writes_failed_summary():
    """A failing executor must leave cursors untouched and record status=failed."""

    runner = CliRunner()
    # A workflow whose script builder is missing fails the script executor with
    # ExecutorUnavailableError — the stand-in for the old "executor unavailable".
    _create_dataset(runner, scriptless=True)

    result = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--executor",
            "script",
            "--scheduled",
            "--json",
        ],
    )

    assert result.exit_code == 3, result.output
    cursors = (
        dataset_path("grill-me-intents") / ".opentraces" / "cursors.yaml"
    ).read_text()
    assert "last_successful_run_id" not in cursors

    runs_dir = dataset_path("grill-me-intents") / ".opentraces" / "runs"
    failed_runs = sorted(runs_dir.iterdir())
    assert failed_runs, "expected a run directory to be persisted on failure"
    summary_path = failed_runs[-1] / "summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text())
    assert summary["run"]["status"] == "failed"
    assert summary["cursor_advanced"] is False
    log_text = (failed_runs[-1] / "log.txt").read_text()
    assert "ExecutorUnavailableError" in log_text
