from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import (
    DatasetPublishSummary,
    add_dataset_remote,
    append_rows,
    load_dataset,
    read_publication_state,
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

    reset_after_approve = runner.invoke(
        dataset_group,
        ["review", "reset", "reviewable", "--all", "--json"],
    )
    assert reset_after_approve.exit_code == 0, reset_after_approve.output
    assert read_publication_state("reviewable").rows[row_id].status == "needs_review"

    reviewed_approve = runner.invoke(
        dataset_group,
        ["review", "approve", "reviewable", row_id, "--json"],
    )
    assert reviewed_approve.exit_code == 0, reviewed_approve.output
    assert read_publication_state("reviewable").rows[row_id].status == "publishable"

    rejected = runner.invoke(dataset_group, ["review", "reject", "reviewable", "--all", "--json"])
    assert rejected.exit_code == 0, rejected.output
    assert read_publication_state("reviewable").rows[row_id].status == "rejected"

    reset = runner.invoke(dataset_group, ["review", "reset", "reviewable", "--all", "--json"])
    assert reset.exit_code == 0, reset.output
    assert read_publication_state("reviewable").rows[row_id].status == "needs_review"


def test_dataset_publish_check_only_then_publish(tmp_path, monkeypatch):
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


def test_dataset_publish_passes_saved_hf_token(monkeypatch):
    runner = CliRunner()
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        "opentraces.cli.dataset._hf_auth",
        lambda: ("hf_saved_token", "me"),
    )

    def fake_publish_dataset(name: str, **kwargs):
        observed["name"] = name
        observed.update(kwargs)
        return DatasetPublishSummary(
            dataset_name=name,
            remote_name="me/private-ds",
            repo_id="me/private-ds",
            run_id="pub-test",
            uploaded=False,
            check_only=True,
            new_row_count=0,
            duplicate_count=0,
            needs_review_count=0,
            blocked_count=0,
            staged_files=[],
            remote_head_before="abc",
            remote_head_after="abc",
            message="check-only",
        )

    monkeypatch.setattr("opentraces.cli.dataset.publish_dataset", fake_publish_dataset)

    result = runner.invoke(dataset_group, ["publish", "private-ds", "--check-only", "--json"])

    assert result.exit_code == 0, result.output
    assert observed["name"] == "private-ds"
    assert observed["token"] == "hf_saved_token"
    assert observed["check_only"] is True
