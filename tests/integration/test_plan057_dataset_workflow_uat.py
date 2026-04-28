from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import dataset_path


def _create_dataset(runner: CliRunner) -> None:
    result = runner.invoke(
        dataset_group,
        [
            "new",
            "grill-me-intents",
            "--workflow",
            "grill-me-intent-curator",
            "--workflow-digest",
            "sha256:workflow",
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


def test_dataset_run_dry_run_real_run_and_current_agent_modes(monkeypatch):
    runner = CliRunner()
    _create_dataset(runner)
    monkeypatch.setenv("OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS", _fake_rows())

    dry = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--dry-run",
            "--executor",
            "claude-code-headless",
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
            "claude-code-headless",
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
            "claude-code-headless",
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


def test_dataset_run_lock_prevents_overlapping_runs(monkeypatch):
    runner = CliRunner()
    _create_dataset(runner)
    monkeypatch.setenv("OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS", _fake_rows())
    lock = dataset_path("grill-me-intents") / ".opentraces" / ".lock"
    lock.write_text("run_existing")

    result = runner.invoke(
        dataset_group,
        ["run", "grill-me-intents", "--executor", "claude-code-headless", "--json"],
    )

    assert result.exit_code == 3
    assert "already in progress" in result.output


def test_successful_scheduled_zero_row_run_advances_cursor(monkeypatch):
    runner = CliRunner()
    _create_dataset(runner)
    monkeypatch.setenv("OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS", "")

    result = runner.invoke(
        dataset_group,
        [
            "run",
            "grill-me-intents",
            "--executor",
            "claude-code-headless",
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
