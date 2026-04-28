from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli.workflow import workflow_group


def test_workflow_cli_new_list_show_rm_json_round_trip():
    runner = CliRunner()

    created = runner.invoke(
        workflow_group,
        [
            "new",
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
    assert [item["name"] for item in list_payload["workflows"]] == [
        "grill-me-intent-curator"
    ]

    shown = runner.invoke(workflow_group, ["show", "grill-me-intent-curator", "--json"])
    assert shown.exit_code == 0, shown.output
    show_payload = json.loads(shown.output)
    assert show_payload["workflow"]["digest"].startswith("sha256:")
    assert show_payload["workflow"]["description"] == (
        "Build rows for the grill-me-intents dataset"
    )

    removed = runner.invoke(workflow_group, ["rm", "grill-me-intent-curator", "--yes", "--json"])
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.output)["removed"]["name"] == "grill-me-intent-curator"


def test_workflow_cli_install_copies_skill_package(tmp_path):
    source = tmp_path / "curator"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\n"
        "name: installed-curator\n"
        "description: Installed curator\n"
        "mode: agent-skill\n"
        "---\n"
        "# Installed curator\n"
    )

    runner = CliRunner()
    installed = runner.invoke(workflow_group, ["install", str(source), "--json"])

    assert installed.exit_code == 0, installed.output
    payload = json.loads(installed.output)
    assert payload["workflow"]["name"] == "installed-curator"
    assert payload["workflow"]["digest"].startswith("sha256:")
