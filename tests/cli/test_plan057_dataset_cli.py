from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import append_rows, dataset_path


def test_dataset_cli_new_list_show_export_remove_round_trip(tmp_path):
    runner = CliRunner()

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "grill-me-intents",
            "--description",
            "Intent summaries",
            "--workflow",
            "grill-me-intent-curator",
            "--workflow-digest",
            "sha256:workflow",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.output)
    assert created_payload["dataset"]["name"] == "grill-me-intents"
    assert created_payload["dataset"]["path"].endswith("grill-me-intents")

    listed = runner.invoke(dataset_group, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert [item["name"] for item in json.loads(listed.output)["datasets"]] == [
        "grill-me-intents"
    ]

    shown = runner.invoke(dataset_group, ["show", "grill-me-intents", "--json"])
    assert shown.exit_code == 0, shown.output
    show_payload = json.loads(shown.output)
    assert show_payload["dataset"]["manifest"]["workflow"]["skill"] == (
        "grill-me-intent-curator"
    )

    row = {
        "source_trace_id": "trace-1",
        "source_unit_id": "tu:trace-1:trace",
        "summary": "CLI export row.",
    }
    append_rows("grill-me-intents", [row], run_id="run_cli")

    output = tmp_path / "rows.jsonl"
    exported = runner.invoke(
        dataset_group,
        ["export", "grill-me-intents", "--format", "jsonl", "--output", str(output), "--json"],
    )
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["export"]["row_count"] == 1
    assert json.loads(output.read_text()) == row

    removed = runner.invoke(dataset_group, ["remove", "grill-me-intents", "--yes", "--json"])
    assert removed.exit_code == 0, removed.output
    assert not dataset_path("grill-me-intents").exists()


def test_dataset_and_workflow_groups_are_registered_on_root_cli():
    runner = CliRunner()

    result = runner.invoke(main, ["dataset", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["datasets"] == []

    result = runner.invoke(main, ["workflow", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["workflows"] == []


def test_dataset_cli_show_row_reads_public_row_by_row_id():
    runner = CliRunner()
    runner.invoke(
        dataset_group,
        ["new", "row-show", "--workflow", "curator", "--workflow-digest", "sha256:w"],
    )
    row = {
        "source_trace_id": "trace-1",
        "source_unit_id": "tu:trace-1:trace",
        "summary": "Show this row.",
    }
    summary = append_rows("row-show", [row], run_id="run_row")
    result = runner.invoke(dataset_group, ["show", "row-show", "--row", summary.row_ids[0], "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["row"] == row
