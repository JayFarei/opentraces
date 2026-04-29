from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import (
    add_dataset_remote,
    append_rows,
    create_dataset,
    load_dataset,
    publish_dataset,
    read_publication_state,
    read_row_index,
)


def test_dataset_remote_lifecycle_updates_dataset_manifest(monkeypatch):
    runner = CliRunner()
    runner.invoke(
        dataset_group,
        ["new", "remote-ready", "--workflow", "curator", "--workflow-digest", "sha256:w"],
    )

    monkeypatch.setattr(
        "opentraces.cli.dataset._remote_probe",
        lambda repo_id, token: {"id": repo_id, "private": True},
    )
    created: list[tuple[str, bool]] = []
    deleted: list[str] = []
    visibility_changes: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "opentraces.cli.dataset._remote_create",
        lambda repo_id, private, token: created.append((repo_id, private)) or True,
    )
    monkeypatch.setattr(
        "opentraces.cli.dataset._remote_delete",
        lambda repo_id, token: deleted.append(repo_id),
    )
    monkeypatch.setattr(
        "opentraces.cli.dataset._remote_set_visibility",
        lambda repo_id, private, token: visibility_changes.append((repo_id, private)),
    )

    added = runner.invoke(
        dataset_group,
        ["remote", "add", "remote-ready", "me/existing", "--json"],
    )
    assert added.exit_code == 0, added.output
    assert json.loads(added.output)["remote"]["name"] == "me/existing"
    manifest = load_dataset("remote-ready").manifest
    assert manifest.remotes["me/existing"].url == "hf://me/existing"
    assert manifest.remotes["me/existing"].visibility == "private"
    assert manifest.active_remote == "me/existing"

    made = runner.invoke(
        dataset_group,
        ["remote", "create", "remote-ready", "me/new", "--public", "--json"],
    )
    assert made.exit_code == 0, made.output
    assert created == [("me/new", False)]
    assert load_dataset("remote-ready").manifest.remotes["me/new"].visibility == "public"

    changed = runner.invoke(
        dataset_group,
        ["remote", "visibility", "remote-ready", "me/existing", "--public", "--json"],
    )
    assert changed.exit_code == 0, changed.output
    assert visibility_changes == [("me/existing", False)]
    assert load_dataset("remote-ready").manifest.remotes["me/existing"].visibility == "public"

    listed = runner.invoke(dataset_group, ["remote", "list", "remote-ready", "--json"])
    assert listed.exit_code == 0, listed.output
    assert set(json.loads(listed.output)["remotes"]) == {"me/existing", "me/new"}

    removed = runner.invoke(
        dataset_group,
        ["remote", "remove", "remote-ready", "me/existing", "--delete-remote", "--yes", "--json"],
    )
    assert removed.exit_code == 0, removed.output
    assert deleted == ["me/existing"]
    assert set(load_dataset("remote-ready").manifest.remotes) == {"me/new"}


def test_dataset_review_commands_update_publication_state_without_row_mutation():
    runner = CliRunner()
    runner.invoke(
        dataset_group,
        ["new", "reviewable", "--workflow", "curator", "--workflow-digest", "sha256:w"],
    )
    summary = append_rows(
        "reviewable",
        [
            {
                "source_trace_id": "trace-1",
                "source_unit_id": "tu:trace-1:trace",
                "summary": "Review me.",
            }
        ],
        run_id="run-1",
    )
    row_id = summary.row_ids[0]

    review = runner.invoke(dataset_group, ["review", "reviewable", "--json"])
    assert review.exit_code == 0, review.output
    assert json.loads(review.output)["counts"]["needs_review"] == 1

    approved = runner.invoke(dataset_group, ["approve", "reviewable", row_id, "--json"])
    assert approved.exit_code == 0, approved.output
    assert read_publication_state("reviewable").rows[row_id].status == "publishable"

    rejected = runner.invoke(dataset_group, ["reject", "reviewable", "--all", "--json"])
    assert rejected.exit_code == 0, rejected.output
    assert read_publication_state("reviewable").rows[row_id].status == "rejected"

    reset = runner.invoke(dataset_group, ["review", "reset", "reviewable", "--all", "--json"])
    assert reset.exit_code == 0, reset.output
    assert read_publication_state("reviewable").rows[row_id].status == "needs_review"


def test_dataset_publish_check_only_and_status_json(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    runner.invoke(
        dataset_group,
        ["new", "publish-cli", "--workflow", "curator", "--workflow-digest", "sha256:w"],
    )
    add_dataset_remote("publish-cli", "me/publish-cli", visibility="private")
    append_rows(
        "publish-cli",
        [
            {
                "source_trace_id": "trace-1",
                "source_unit_id": "tu:trace-1:trace",
                "summary": "Publish through CLI.",
            }
        ],
        run_id="run-1",
    )
    runner.invoke(dataset_group, ["approve", "publish-cli", "--all"])

    checked = runner.invoke(dataset_group, ["publish", "publish-cli", "--check-only", "--json"])
    assert checked.exit_code == 0, checked.output
    checked_payload = json.loads(checked.output)
    assert checked_payload["publish"]["uploaded"] is False
    assert checked_payload["publish"]["new_row_count"] == 1

    published = runner.invoke(dataset_group, ["publish", "publish-cli", "--json"])
    assert published.exit_code == 0, published.output
    published_payload = json.loads(published.output)
    assert published_payload["publish"]["uploaded"] is True
    assert published_payload["publish"]["new_row_count"] == 1

    status = runner.invoke(dataset_group, ["status", "publish-cli", "--remote", "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.output)
    assert status_payload["publication"]["published"] == 1
    assert status_payload["remote"]["active_remote"] == "me/publish-cli"

    assert status_payload["remote"]["byte_identity"]["status"] == "ok"


def test_dataset_clone_and_pull_cli_use_hf_shaped_remote(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "origin-dataset",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("origin-dataset", "me/origin-dataset", visibility="private")
    append_rows(
        "origin-dataset",
        [
            {
                "source_trace_id": "trace-1",
                "source_unit_id": "tu:trace-1:trace",
                "summary": "Remote row.",
            }
        ],
        run_id="run-1",
    )
    publish_dataset("origin-dataset", contributor="tester")

    cloned = runner.invoke(
        dataset_group,
        ["clone", "hf://me/origin-dataset", "--as", "local-copy", "--read-only", "--json"],
    )
    assert cloned.exit_code == 0, cloned.output
    assert json.loads(cloned.output)["dataset"]["name"] == "local-copy"
    assert read_row_index("local-copy") == []

    pulled = runner.invoke(dataset_group, ["pull", "local-copy", "--data", "--json"])
    assert pulled.exit_code == 0, pulled.output
    assert json.loads(pulled.output)["pull"]["imported_count"] == 1
    assert len(read_row_index("local-copy")) == 1


def test_dataset_clone_with_data_imports_rows_in_one_step(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "origin-with-data",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("origin-with-data", "me/origin-with-data", visibility="private")
    append_rows(
        "origin-with-data",
        [
            {
                "source_trace_id": "trace-2",
                "source_unit_id": "tu:trace-2:trace",
                "summary": "Cloned in one step.",
            }
        ],
        run_id="run-1",
    )
    publish_dataset("origin-with-data", contributor="tester")

    cloned = runner.invoke(
        dataset_group,
        [
            "clone",
            "hf://me/origin-with-data",
            "--as",
            "one-step-copy",
            "--data",
            "--read-only",
            "--json",
        ],
    )
    assert cloned.exit_code == 0, cloned.output
    payload = json.loads(cloned.output)
    assert payload["dataset"]["name"] == "one-step-copy"
    assert payload["pull"]["imported_count"] == 1
    assert len(read_row_index("one-step-copy")) == 1


def test_dataset_apply_alias_still_works_and_warns(tmp_path, monkeypatch):
    runner = CliRunner()
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "origin-alias",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("origin-alias", "me/origin-alias", visibility="private")
    append_rows(
        "origin-alias",
        [
            {
                "source_trace_id": "trace-alias",
                "source_unit_id": "tu:trace-alias:trace",
                "summary": "Alias path.",
            }
        ],
        run_id="run-1",
    )
    publish_dataset("origin-alias", contributor="tester")

    applied = runner.invoke(
        dataset_group,
        ["apply", "hf://me/origin-alias", "--as", "alias-copy", "--read-only", "--json"],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["dataset"]["name"] == "alias-copy"
    assert "deprecated" in applied.stderr


