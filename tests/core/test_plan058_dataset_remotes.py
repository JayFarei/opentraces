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

    summary = append_rows(
        "remote-ready",
        [_row("Needs a human review.")],
        run_id="run-1",
        privacy_tier="medium",
    )
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
    summary = append_rows(
        "auto-publish",
        [_row("Ready without human review.")],
        run_id="run-1",
        privacy_tier="medium",
    )
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
        privacy_tier="medium",
    )
    # Issue #84: an off-tier row carrying a secret is no longer shipped raw and
    # blocked — the reader floor redacts the secret at append time, so the row
    # publishes CLEAN (a stronger outcome: the data is shared, the secret is not).
    secret_row = append_rows(
        "publishable",
        [
            _row(
                "Contains sk-live-abcdefghijklmnopqrstuvwxyz123456 and must not leave.",
                trace_id="trace-secret",
            )
        ],
        run_id="run-2",
        privacy_tier="off",
    )
    state = read_publication_state("publishable")
    # The floor ran (row_tools non-empty) -> not a raw privacy_tier_off block.
    assert "privacy_tier_off" not in state.rows[secret_row.row_ids[0]].block_reasons
    assert state.rows[secret_row.row_ids[0]].block_reasons == []

    checked = publish_dataset("publishable", check_only=True, contributor="tester")
    assert checked.uploaded is False
    assert checked.new_row_count == 2
    assert checked.blocked_count == 0
    assert any(path.startswith("data/tester-") for path in checked.staged_files)

    published = publish_dataset("publishable", contributor="tester")
    assert published.uploaded is True
    assert published.new_row_count == 2
    assert published.blocked_count == 0
    assert published.remote_head_before
    assert published.remote_head_after

    remote_root = tmp_path / "remotes" / "me" / "publishable"
    assert not (remote_root / ".opentraces").exists()
    remote_rows = "\n".join(path.read_text() for path in (remote_root / "data").glob("*.jsonl"))
    assert "Safe public row." in remote_rows
    # The secret was scrubbed by the floor, so the row leaves with it redacted.
    assert "sk-live-" not in remote_rows

    state = read_publication_state("publishable")
    assert state.rows[good.row_ids[0]].status == "published"
    assert "me/publishable" in state.rows[good.row_ids[0]].uploaded_to
    assert state.rows[secret_row.row_ids[0]].status == "published"
    assert "me/publishable" in state.rows[secret_row.row_ids[0]].uploaded_to


def test_plan058_append_rows_default_off_tier_runs_the_reader_floor():
    """Issue #84: the default ``tier="off"`` no longer ships rows verbatim — the
    non-overridable reader floor (regex/entropy/business_logic/path_anonymizer)
    runs over every row, so a regex-detectable secret is redacted at append time
    rather than shipped raw and blocked. The tier LABEL stays ``off`` (a
    shareable shorthand), but the row is filtered, not raw.
    """
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        dataset_path,
        read_publication_state,
    )
    from opentraces.security import SECURITY_VERSION

    create_dataset(
        "raw-by-default",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    summary = append_rows(
        "raw-by-default",
        [
            _row(
                "Uses sk-proj-abcdefghijklmnopqrstuvwxyz123456 and remains raw.",
                trace_id="trace-secret",
            )
        ],
        run_id="run-1",
    )

    data = (dataset_path("raw-by-default") / "data" / "train.jsonl").read_text()
    # The reader floor redacted the secret even at tier "off" (the security fix).
    assert "sk-proj-" not in data
    assert "[REDACTED]" in data
    entry = read_publication_state("raw-by-default").rows[summary.row_ids[0]]
    # No longer raw -> not blocked as privacy_tier_off; the floor satisfied the gate.
    assert entry.privacy_tier == "off"
    assert entry.security_version == SECURITY_VERSION
    assert entry.redactions_applied >= 1
    assert "privacy_tier_off" not in entry.block_reasons
    assert entry.block_reasons == []
    assert entry.status == "publishable"


def test_plan058_append_rows_writes_row_provenance_sidecar():
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        read_row_index,
        read_row_provenance,
    )

    create_dataset(
        "row-provenance",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
        row_schema={
            "type": "object",
            "required": ["source_trace_id", "source_unit_id", "summary"],
            "properties": {
                "source_trace_id": {"type": "string"},
                "source_unit_id": {"type": "string"},
                "source_slice_id": {"type": "string"},
                "step_range": {"type": "object"},
                "summary": {"type": "string"},
            },
            "additionalProperties": False,
        },
    )
    summary = append_rows(
        "row-provenance",
        [
            {
                **_row("Has provenance.", trace_id="trace-prov", unit_id="tu:trace-prov:trace"),
                "source_slice_id": "slice-1",
                "step_range": {"start": 2, "end": 5},
            }
        ],
        run_id="run-1",
        run_provenance={"executor": "test"},
        trail_freshness=[
            {
                "kind": "trail_projection_freshness",
                "severity": "info",
                "state": "current",
                "project_slug": "demo",
            }
        ],
    )
    row_id = summary.row_ids[0]
    entry = read_row_index("row-provenance")[0]
    provenance = read_row_provenance("row-provenance")[row_id]

    assert entry.source_trace_id == "trace-prov"
    assert entry.source_unit_id == "tu:trace-prov:trace"
    assert entry.source_slice_id == "slice-1"
    assert entry.provenance["source_refs"]["step_range"] == {"start": 2, "end": 5}
    assert provenance["workflow"]["skill"] == "curator"
    assert provenance["trail"]["freshness"][0]["state"] == "current"
    assert provenance["run"]["executor"] == "test"


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
    append_rows(
        "conflict",
        [_row("Local row.", trace_id="trace-local")],
        run_id="run-1",
        privacy_tier="medium",
    )

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


def test_publish_refuses_remote_schema_ahead_on_live_hf_path(tmp_path, monkeypatch):
    """Phase B4: the schema-ahead negotiation must be reachable from a LIVE
    `dataset publish` (no fake remote configured). Previously
    ``_check_remote_schema_not_ahead`` returned early whenever the fake
    remote dir was absent, so real-HF publishes never negotiated at all
    (issue #25 finding #6). The live path fetches the remote dataset card
    via ``hf_hub_download``; a remote-newer contract must refuse publish."""
    from opentraces.core.datasets import (
        DatasetRemoteSchemaAheadError,
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    # NO fake remote root: this drives the live-HF branch.
    monkeypatch.delenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", raising=False)

    create_dataset(
        "schema-ahead-live",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("schema-ahead-live", "me/schema-ahead-live", visibility="private")
    append_rows(
        "schema-ahead-live",
        [_row("Local row.", trace_id="trace-local")],
        run_id="run-1",
    )

    card = tmp_path / "README.md"
    card.write_text(
        "---\nopentraces:\n  schema:\n    version: 9.0.0\n---\n# newer\n",
        encoding="utf-8",
    )

    calls: dict = {}

    def _fake_hf_hub_download(*, repo_id, repo_type, filename, token=None):
        calls["repo_id"] = repo_id
        calls["filename"] = filename
        assert repo_type == "dataset"
        return str(card)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _fake_hf_hub_download)
    # publish_dataset reads the remote head before the schema check; keep the
    # unit test offline (the schema check itself raises before anything else
    # touches the remote).
    monkeypatch.setattr(
        "opentraces.core.datasets._remote_head", lambda repo_id, token: None
    )

    try:
        publish_dataset("schema-ahead-live", contributor="tester")
    except DatasetRemoteSchemaAheadError as exc:
        assert exc.remote_version == "9.0.0"
        assert exc.local_version == "1.0.0"
    else:
        raise AssertionError("live remote schema ahead must refuse publish")
    assert calls["repo_id"] == "me/schema-ahead-live"
    assert calls["filename"] == "README.md"


def test_publish_proceeds_when_live_remote_has_no_card(tmp_path, monkeypatch):
    """A live remote with no README (first publish) is treated as fresh.

    ``hf_hub_download`` raising EntryNotFoundError must map to "nothing to
    compare", letting the publish proceed (issue #33: the guard must block
    remote-newer without breaking first publishes)."""
    from opentraces.core.datasets import (
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    monkeypatch.delenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", raising=False)

    create_dataset(
        "fresh-live",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("fresh-live", "me/fresh-live", visibility="private")
    append_rows(
        "fresh-live",
        [_row("Local row.", trace_id="trace-local")],
        run_id="run-1",
    )

    try:
        from huggingface_hub.errors import EntryNotFoundError
    except ImportError:  # older huggingface_hub
        from huggingface_hub.utils import EntryNotFoundError  # type: ignore

    def _raise_not_found(*, repo_id, repo_type, filename, token=None):
        raise EntryNotFoundError("no README")

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _raise_not_found)
    monkeypatch.setattr(
        "opentraces.core.datasets._remote_head", lambda repo_id, token: None
    )

    summary = publish_dataset("fresh-live", check_only=True, contributor="tester")
    assert summary.message == "check passed"
