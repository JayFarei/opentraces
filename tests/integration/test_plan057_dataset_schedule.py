from __future__ import annotations

import json

from click.testing import CliRunner

import yaml

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import create_dataset, dataset_path
from opentraces.core.schedules import read_schedule


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
            "script",
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


def test_dataset_schedule_cli_persists_publish_automation_trigger():
    create_dataset(
        "scheduled-auto-publish",
        workflow_skill="scheduled-curator",
        workflow_digest="sha256:workflow",
    )
    runner = CliRunner()

    added = runner.invoke(
        dataset_group,
        [
            "schedule",
            "add",
            "scheduled-auto-publish",
            "--every",
            "30m",
            "--executor",
            "script",
            "--approve-new",
            "--publish-check-only",
            "--json",
        ],
    )
    assert added.exit_code == 0, added.output
    add_payload = json.loads(added.output)
    assert add_payload["schedule"]["trigger"]["approve_new"] is True
    assert add_payload["schedule"]["trigger"]["publish"] == "check_only"

    trigger_file = dataset_path("scheduled-auto-publish") / ".opentraces" / "schedule.trigger"
    trigger = trigger_file.read_text()
    assert "dataset run scheduled-auto-publish --scheduled" in trigger
    assert "--approve-new" in trigger
    assert "--publish-check-only" in trigger

    resumed = runner.invoke(
        dataset_group,
        ["schedule", "resume", "scheduled-auto-publish", "--json"],
    )
    assert resumed.exit_code == 0, resumed.output
    resume_payload = json.loads(resumed.output)
    assert resume_payload["schedule"]["trigger"]["approve_new"] is True
    assert resume_payload["schedule"]["trigger"]["publish"] == "check_only"
    assert "--approve-new" in trigger_file.read_text()


def test_legacy_headless_schedule_still_loads_and_coerces_to_script():
    """A schedule serialized before the M1 engine collapse (#185) carried
    ``executor: claude-code-headless``. It must still deserialize (schema keeps
    the value readable) and coerce onto the surviving ``script`` executor on
    read, so the regenerated trigger names a valid executor rather than a
    removed one."""
    create_dataset(
        "legacy-headless-schedule",
        workflow_skill="legacy-curator",
        workflow_digest="sha256:workflow",
    )
    root = dataset_path("legacy-headless-schedule") / ".opentraces"
    root.mkdir(parents=True, exist_ok=True)
    # Hand-write a pre-collapse schedule.yaml with the removed executor value.
    (root / "schedule.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset": "legacy-headless-schedule",
                "enabled": True,
                "every": "2h",
                "executor": "claude-code-headless",
                "trigger": {"backend": "local-file"},
                "last_run_status": None,
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    state = read_schedule("legacy-headless-schedule")
    assert state.enabled is True
    assert state.every == "2h"
    # Accept-and-coerce: the legacy value maps onto the automated executor.
    assert state.executor == "script"
