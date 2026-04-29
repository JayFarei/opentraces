from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import create_dataset, dataset_path


def test_dataset_schedule_cli_manages_local_state_without_remote_publish():
    create_dataset(
        "scheduled-intents",
        workflow_skill="scheduled-curator",
        workflow_digest="sha256:workflow",
    )
    runner = CliRunner()

    added = runner.invoke(
        dataset_group,
        [
            "schedule",
            "add",
            "scheduled-intents",
            "--every",
            "2h",
            "--executor",
            "claude-code-headless",
            "--json",
        ],
    )
    assert added.exit_code == 0, added.output
    add_payload = json.loads(added.output)
    assert add_payload["schedule"]["enabled"] is True
    assert add_payload["schedule"]["every"] == "2h"
    assert add_payload["schedule"]["trigger"]["backend"] == "local-file"

    trigger_file = dataset_path("scheduled-intents") / ".opentraces" / "schedule.trigger"
    assert "dataset run scheduled-intents --scheduled" in trigger_file.read_text()

    listed = runner.invoke(dataset_group, ["schedule", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)["schedules"][0]["dataset"] == "scheduled-intents"

    paused = runner.invoke(dataset_group, ["schedule", "pause", "scheduled-intents", "--json"])
    assert paused.exit_code == 0, paused.output
    assert json.loads(paused.output)["schedule"]["enabled"] is False

    resumed = runner.invoke(dataset_group, ["schedule", "resume", "scheduled-intents", "--json"])
    assert resumed.exit_code == 0, resumed.output
    assert json.loads(resumed.output)["schedule"]["enabled"] is True

    logs = runner.invoke(dataset_group, ["schedule", "logs", "scheduled-intents", "--json"])
    assert logs.exit_code == 0, logs.output
    assert "schedule added" in "\n".join(json.loads(logs.output)["logs"])

    removed = runner.invoke(dataset_group, ["schedule", "remove", "scheduled-intents", "--json"])
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.output)["removed"]["dataset"] == "scheduled-intents"
    assert not trigger_file.exists()
