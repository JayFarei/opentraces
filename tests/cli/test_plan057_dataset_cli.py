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


def test_dataset_new_accepts_markdown_workflow_path(tmp_path):
    workflow_file = tmp_path / "classic-intent-labels.md"
    workflow_file.write_text(
        "---\n"
        "name: classic-intent-labels\n"
        "description: Classic intent trajectory labels\n"
        "---\n\n"
        "# Classic Intent Labels\n\n"
        "Use `opentraces trace slice --template bursts` as the source packet.\n",
        encoding="utf-8",
    )
    runner = CliRunner()

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "classic-intents",
            "--workflow",
            str(workflow_file),
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    workflow = payload["dataset"]["manifest"]["workflow"]
    assert workflow["skill"] == "classic-intent-labels"
    assert workflow["digest"].startswith("sha256:")
    assert workflow["config"]["source"] == str(workflow_file.resolve())
    assert workflow["config"]["source_type"] == "file"
    assert workflow["config"]["entrypoint"] == str(workflow_file.resolve())


def test_dataset_new_accepts_workflow_schema_without_seed_rows(tmp_path):
    schema_file = tmp_path / "intent-trajectory.schema.json"
    schema_file.write_text(
        json.dumps(
            {
                "type": "object",
                "required": ["source_trace_id", "source_unit_id", "intent"],
                "properties": {
                    "source_trace_id": {"type": "string"},
                    "source_unit_id": {"type": "string"},
                    "intent": {"type": "string"},
                },
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    runner = CliRunner()

    created = runner.invoke(
        dataset_group,
        [
            "new",
            "hf-intents",
            "--workflow",
            "hf-intent-trajectory-outcome",
            "--schema",
            str(schema_file),
            "--query-semantic",
            "hugging face",
            "--query-source",
            "projection",
            "--json",
        ],
    )

    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    assert payload["dataset"]["manifest"]["workflow"]["skill"] == (
        "hf-intent-trajectory-outcome"
    )
    schema_payload = json.loads(
        (dataset_path("hf-intents") / "schemas" / "row.schema.json").read_text()
    )
    assert schema_payload["required"] == ["source_trace_id", "source_unit_id", "intent"]
    candidate_query = payload["dataset"]["manifest"]["candidate_query"]
    assert candidate_query["scope"] == "all-projects"
    assert candidate_query["args"]["semantic"] == "hugging face"
    assert candidate_query["args"]["source"] == "projection"


def test_dataset_and_workflow_groups_are_registered_on_root_cli():
    runner = CliRunner()

    result = runner.invoke(main, ["dataset", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["datasets"] == []

    result = runner.invoke(main, ["workflow", "list", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["workflows"] == []
