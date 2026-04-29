from __future__ import annotations

import json


def _row(summary: str, *, trace_id: str = "trace-1", unit_id: str | None = None) -> dict:
    return {
        "source_trace_id": trace_id,
        "source_unit_id": unit_id or f"tu:{trace_id}:trace",
        "summary": summary,
    }


def test_plan058_manifest_defaults_and_publication_state_required_review():
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        dataset_path,
        read_publication_state,
    )

    dataset = create_dataset(
        "remote-ready",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
    )

    assert dataset.manifest.remotes == {}
    assert dataset.manifest.active_remote is None
    assert dataset.manifest.remote_schema == "refuse_if_newer"
    assert dataset.manifest.publication_policy.review == "required"

    summary = append_rows("remote-ready", [_row("Needs a human review.")], run_id="run-1")
    state = read_publication_state("remote-ready")

    assert state.rows[summary.row_ids[0]].status == "needs_review"
    assert state.rows[summary.row_ids[0]].uploaded_to == {}

    raw_state = json.loads(
        (dataset_path("remote-ready") / ".opentraces" / "publication_state.json").read_text()
    )
    assert raw_state["rows"][summary.row_ids[0]]["status"] == "needs_review"


def test_plan058_publication_policy_auto_and_review_decisions_do_not_mutate_rows():
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        dataset_path,
        read_publication_state,
        set_publication_review_status,
    )

    create_dataset(
        "auto-publish",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    summary = append_rows("auto-publish", [_row("Ready without human review.")], run_id="run-1")
    row_id = summary.row_ids[0]

    assert read_publication_state("auto-publish").rows[row_id].status == "publishable"
    before_payload = (dataset_path("auto-publish") / "data" / "train.jsonl").read_text()

    rejected = set_publication_review_status("auto-publish", [row_id], "rejected")
    assert rejected.rows[row_id].status == "rejected"

    reset = set_publication_review_status("auto-publish", [row_id], "reset")
    assert reset.rows[row_id].status == "publishable"
    assert (dataset_path("auto-publish") / "data" / "train.jsonl").read_text() == before_payload


def test_plan058_publish_stages_only_publishable_rows_and_never_uploads_control_plane(
    tmp_path,
    monkeypatch,
):
    from opentraces.core.datasets import (
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
        read_publication_state,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "publishable",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("publishable", "me/publishable", visibility="private")
    good = append_rows(
        "publishable",
        [_row("Safe public row.", trace_id="trace-good")],
        run_id="run-1",
    )
    blocked = append_rows(
        "publishable",
        [
            _row(
                "Contains sk-live-abcdefghijklmnopqrstuvwxyz123456 and must not leave.",
                trace_id="trace-secret",
            )
        ],
        run_id="run-2",
    )

    checked = publish_dataset("publishable", check_only=True, contributor="tester")
    assert checked.uploaded is False
    assert checked.new_row_count == 1
    assert checked.blocked_count == 1
    assert any(path.startswith("data/tester-") for path in checked.staged_files)

    published = publish_dataset("publishable", contributor="tester")
    assert published.uploaded is True
    assert published.new_row_count == 1
    assert published.blocked_count == 1
    assert published.remote_head_before
    assert published.remote_head_after

    remote_root = tmp_path / "remotes" / "me" / "publishable"
    assert not (remote_root / ".opentraces").exists()
    remote_rows = "\n".join(path.read_text() for path in (remote_root / "data").glob("*.jsonl"))
    assert "Safe public row." in remote_rows
    assert "sk-live-" not in remote_rows

    state = read_publication_state("publishable")
    assert state.rows[good.row_ids[0]].status == "published"
    assert "me/publishable" in state.rows[good.row_ids[0]].uploaded_to
    assert state.rows[blocked.row_ids[0]].status == "blocked"


def test_plan058_withdrawal_tombstones_filter_wrapper_and_hard_delete_requires_confirmation():
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        dataset_path,
        load_public_rows,
        read_row_index,
        withdraw_dataset_row,
    )

    create_dataset("withdrawals", workflow_skill="curator", workflow_digest="sha256:w")
    summary = append_rows(
        "withdrawals",
        [_row("Withdraw this.", trace_id="trace-withdraw")],
        run_id="run-1",
    )
    row_id = summary.row_ids[0]

    tombstone = withdraw_dataset_row("withdrawals", row_id, reason="user-request")
    assert tombstone.target == "row"
    assert tombstone.target_id == row_id
    assert list((dataset_path("withdrawals") / "_withdrawals").glob("*.jsonl"))
    assert load_public_rows("withdrawals", apply_withdrawals=True) == []
    assert len(load_public_rows("withdrawals", apply_withdrawals=False)) == 1

    try:
        withdraw_dataset_row("withdrawals", row_id, reason="legal", hard=True)
    except ValueError as exc:
        assert "HARD_DELETE" in str(exc)
    else:
        raise AssertionError("hard delete must require explicit confirmation")

    withdraw_dataset_row(
        "withdrawals",
        row_id,
        reason="legal",
        hard=True,
        confirm="HARD_DELETE",
    )
    assert read_row_index("withdrawals") == []
    assert (dataset_path("withdrawals") / "data" / "train.jsonl").read_text() == ""


def test_plan058_publish_retries_parent_commit_conflict_and_preserves_remote_union(
    tmp_path,
    monkeypatch,
):
    from opentraces.core.datasets import (
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    monkeypatch.setenv(
        "OPENTRACES_PLAN058_FAKE_CONFLICT_ROW",
        json.dumps(_row("Concurrent remote row.", trace_id="trace-concurrent")),
    )
    create_dataset(
        "conflict",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("conflict", "me/conflict", visibility="private")
    append_rows("conflict", [_row("Local row.", trace_id="trace-local")], run_id="run-1")

    published = publish_dataset("conflict", contributor="tester", max_retries=2)
    assert published.uploaded is True
    assert published.attempts == 2

    remote_rows = "\n".join(
        path.read_text() for path in (tmp_path / "remotes" / "me" / "conflict" / "data").glob("*.jsonl")
    )
    assert "Concurrent remote row." in remote_rows
    assert "Local row." in remote_rows


def test_plan058_publish_classifies_no_write_access_without_fallback(tmp_path, monkeypatch):
    from opentraces.core.datasets import (
        DatasetRemotePermissionError,
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_DENY_WRITE", "1")
    create_dataset(
        "denied",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("denied", "me/denied", visibility="private")
    append_rows("denied", [_row("Cannot upload.", trace_id="trace-denied")], run_id="run-1")

    try:
        publish_dataset("denied", contributor="tester")
    except DatasetRemotePermissionError as exc:
        assert exc.classification == "permission_denied"
        assert "write access" in str(exc)
    else:
        raise AssertionError("write-denied publish must fail directly")


def test_plan058_publish_refuses_remote_schema_ahead(tmp_path, monkeypatch):
    from opentraces.core.datasets import (
        DatasetRemoteSchemaAheadError,
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "schema-ahead",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("schema-ahead", "me/schema-ahead", visibility="private")
    append_rows("schema-ahead", [_row("Local row.", trace_id="trace-local")], run_id="run-1")
    remote_root = tmp_path / "remotes" / "me" / "schema-ahead"
    remote_root.mkdir(parents=True)
    (remote_root / "README.md").write_text(
        "---\nopentraces:\n  schema:\n    version: 9.0.0\n---\n# newer\n",
        encoding="utf-8",
    )

    try:
        publish_dataset("schema-ahead", contributor="tester")
    except DatasetRemoteSchemaAheadError as exc:
        assert exc.remote_version == "9.0.0"
        assert exc.local_version == "1.0.0"
    else:
        raise AssertionError("remote schema ahead must refuse publish")
