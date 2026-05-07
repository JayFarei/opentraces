from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli.workflow import workflow_group


def test_workflow_cli_create_list_remove_json_round_trip():
    runner = CliRunner()

    created = runner.invoke(
        workflow_group,
        [
            "create",
            "grill-me-intent-curator",
            "--description",
            "Build rows for the grill-me-intents dataset",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.output)
    assert created_payload["workflow"]["name"] == "grill-me-intent-curator"
    assert Path(created_payload["workflow"]["path"]).name == "grill-me-intent-curator"

    listed = runner.invoke(workflow_group, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    list_payload = json.loads(listed.output)
    entries = list_payload["workflows"]
    assert [item["name"] for item in entries] == ["grill-me-intent-curator"]
    # Path and dataset bindings are the two cross-cutting facts the CLI
    # uniquely contributes — assert both surfaces are present.
    assert Path(entries[0]["path"]).name == "grill-me-intent-curator"
    assert entries[0]["datasets"] == []
    assert entries[0]["digest"].startswith("sha256:")

    removed = runner.invoke(workflow_group, ["remove", "grill-me-intent-curator", "--yes", "--json"])
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.output)["removed"]["name"] == "grill-me-intent-curator"


def test_workflow_cli_lists_and_creates_packaged_templates():
    runner = CliRunner()

    templates = runner.invoke(workflow_group, ["templates", "--json"])
    assert templates.exit_code == 0, templates.output
    assert "skill-command-trajectory-eval-v1" in json.loads(templates.output)["templates"]

    created = runner.invoke(
        workflow_group,
        [
            "create",
            "command-eval",
            "--template",
            "skill-command-trajectory-eval-v1",
            "--description",
            "Command eval workflow",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    assert payload["workflow"]["name"] == "command-eval"
    workflow_path = Path(payload["workflow"]["path"])
    assert (workflow_path / "schemas" / "row.schema.json").exists()
    assert (workflow_path / "scripts" / "build_rows.py").exists()


def test_workflow_list_reports_dataset_bindings():
    """A workflow that backs a dataset shows up in that dataset's binding list."""
    from opentraces.core.datasets import create_dataset

    runner = CliRunner()
    runner.invoke(
        workflow_group,
        ["create", "shared-curator", "--description", "shared", "--json"],
    )
    create_dataset(
        "alpha",
        workflow_skill="shared-curator",
        workflow_digest="sha256:w",
    )
    create_dataset(
        "beta",
        workflow_skill="shared-curator",
        workflow_digest="sha256:w",
    )

    listed = runner.invoke(workflow_group, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    payload = json.loads(listed.output)
    by_name = {entry["name"]: entry for entry in payload["workflows"]}
    assert by_name["shared-curator"]["datasets"] == ["alpha", "beta"]


def test_workflow_cli_rejects_removed_install_surface(tmp_path):
    runner = CliRunner()
    installed = runner.invoke(workflow_group, ["install", str(tmp_path), "--json"])

    assert installed.exit_code != 0
    assert "No such command" in installed.output


def test_workflow_cli_rejects_removed_show_and_edit_verbs():
    """``show`` and ``edit`` were folded into ``list`` and the shell — assert
    they no longer exist as commands so we don't quietly bring them back."""
    runner = CliRunner()
    for verb in ("show", "edit"):
        result = runner.invoke(workflow_group, [verb, "anything", "--json"])
        assert result.exit_code != 0
        assert "No such command" in result.output
