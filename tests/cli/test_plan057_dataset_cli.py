from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import dataset_path


def test_dataset_cli_new_list_remove_round_trip(tmp_path):
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
