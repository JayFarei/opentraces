"""Plan 058 fake remote UAT proof artefact.

This file holds the strict per-file digest, state-survival, and
metadata-only republish coverage that the higher-level harness in
`tests/integration/test_plan058_remote_acceptance_harness.py` does not
explicitly assert. Each test docstring cites the verification item from
`kb/plans/058-dataset-remotes-and-cli-lifecycle.md` (section "Verification")
that the test is proving.

Conventions copied from the existing Plan 058 fake-adapter tests:
- `OPENTRACES_PLAN058_FAKE_REMOTE_ROOT` redirects every fake adapter seam
  (`fake_remote_*`, `_fake_upload_folder`, `_fake_remote_dir`).
- `tests/conftest.py` isolates `HOME` and `~/.opentraces/` per test.
- The Click CLI (`dataset_group`) is the authoritative product surface;
  pure-core helpers are used to seed and inspect state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import (
    add_dataset_remote,
    append_rows,
    create_dataset,
    dataset_path,
    file_digest,
    publish_dataset,
    read_publication_state,
    read_row_index,
    rebuild_row_index,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(summary: str, *, trace_id: str = "trace-1", unit_id: str | None = None) -> dict:
    return {
        "source_trace_id": trace_id,
        "source_unit_id": unit_id or f"tu:{trace_id}:trace",
        "summary": summary,
    }


def _remote_root(tmp_path: Path, repo_id: str) -> Path:
    owner, _, name = repo_id.partition("/")
    return tmp_path / "remotes" / owner / name


def _list_remote_files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


def _digest_bytes(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _setup_fake_remote(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "remotes"
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(root))
    return root


def _seed_published_dataset(
    name: str,
    repo_id: str,
    *,
    rows: list[dict],
    contributor: str = "tester",
) -> None:
    create_dataset(
        name,
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote(name, repo_id, visibility="private")
    if rows:
        append_rows(name, rows, run_id=f"{name}-seed")
        publish_dataset(name, contributor=contributor)


# ---------------------------------------------------------------------------
# V1 / V2 — `dataset apply` creates remote-bound local dataset, no row data
# ---------------------------------------------------------------------------


def test_v1_apply_hf_scheme_creates_remote_bound_dataset_without_row_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1: `apply hf://owner/name` clones the contract without downloading rows."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    _seed_published_dataset(
        "v1-source",
        "me/v1-source",
        rows=[_row("Remote published row.", trace_id="trace-v1")],
    )

    result = runner.invoke(
        dataset_group,
        ["apply", "hf://me/v1-source", "--as", "v1-copy", "--read-only", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["dataset"]["name"] == "v1-copy"

    copy_root = dataset_path("v1-copy")
    manifest = payload["dataset"]["manifest"]
    # The applied local dataset must be remote-bound to the source repo.
    assert manifest["active_remote"] == "me/v1-source"
    assert "me/v1-source" in manifest["remotes"]

    # No row data should have been imported by `apply` alone.
    assert read_row_index("v1-copy") == []
    train = copy_root / "data" / "train.jsonl"
    assert train.exists()
    assert train.read_text(encoding="utf-8") == ""
    # No published shards either — apply patterns are metadata-only.
    other_shards = [
        path
        for path in (copy_root / "data").glob("*.jsonl")
        if path.name != "train.jsonl"
    ]
    assert other_shards == [], f"apply must not import row shards: {other_shards}"

    # Public contract files should have been copied.
    assert (copy_root / "README.md").exists()
    assert (copy_root / "dataset_infos.json").exists()
    assert (copy_root / "schemas" / "row.schema.json").exists()


def test_v2_apply_short_owner_name_resolves_as_hf_remote(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V2: `apply owner/name` (no scheme) resolves as HF and produces same contract."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    _seed_published_dataset(
        "v2-source",
        "me/v2-source",
        rows=[_row("Remote v2 row.", trace_id="trace-v2")],
    )

    result = runner.invoke(
        dataset_group,
        ["apply", "me/v2-source", "--as", "v2-copy", "--read-only", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)

    manifest = payload["dataset"]["manifest"]
    # Plan-text mandates: short `owner/name` resolves as HF; the recorded URL
    # must be the canonical hf:// form, identical to what `hf://owner/name`
    # would have produced (V1 parity).
    assert manifest["active_remote"] == "me/v2-source"
    remote = manifest["remotes"]["me/v2-source"]
    assert remote["url"] == "hf://me/v2-source"

    copy_root = dataset_path("v2-copy")
    assert read_row_index("v2-copy") == []
    assert (copy_root / "data" / "train.jsonl").read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# V4 — `pull` without `--data` only refreshes metadata
# ---------------------------------------------------------------------------


def test_v4_pull_without_data_refreshes_metadata_only_and_imports_zero_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V4: `pull` without `--data` only refreshes manifest/metadata."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    _seed_published_dataset(
        "v4-source",
        "me/v4-source",
        rows=[_row("Row stays remote.", trace_id="trace-v4")],
    )
    runner.invoke(
        dataset_group,
        ["apply", "hf://me/v4-source", "--as", "v4-copy", "--read-only", "--json"],
    )

    # Sanity: nothing imported yet.
    assert read_row_index("v4-copy") == []

    pulled = runner.invoke(
        dataset_group,
        ["pull", "v4-copy", "--json"],
    )
    assert pulled.exit_code == 0, pulled.output
    payload = json.loads(pulled.output)["pull"]
    assert payload["data"] is False
    assert payload["metadata_refreshed"] is True
    assert payload["imported_count"] == 0

    # Row index must remain empty — only metadata files were refreshed.
    assert read_row_index("v4-copy") == []
    train = dataset_path("v4-copy") / "data" / "train.jsonl"
    assert train.read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# V5 — `pull --data` refusal and `--force-pull` override
# ---------------------------------------------------------------------------


def test_v5_pull_data_refuses_with_publishable_unpublished_rows_and_force_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V5: pull --data refuses when local has publishable unpublished rows;
    --force-pull overrides; row-index entries are reconstructed for imports."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    _seed_published_dataset(
        "v5-source",
        "me/v5-source",
        rows=[_row("Remote-only row.", trace_id="trace-remote")],
    )

    # Apply remote, then create an unpublished local publishable row.
    runner.invoke(
        dataset_group,
        ["apply", "hf://me/v5-source", "--as", "v5-copy", "--read-only", "--json"],
    )
    # apply stamps the manifest with the public contract's policy. The remote
    # was published with `review: auto`, so the copy inherits that. We append
    # a local row to make it eagerly publishable.
    append_rows(
        "v5-copy",
        [_row("Local publishable row.", trace_id="trace-local")],
        run_id="v5-local",
    )
    # Confirm the local row is publishable (i.e. would be uploaded if we
    # re-published) before trying to pull.
    state_before = read_publication_state("v5-copy")
    assert any(entry.status == "publishable" for entry in state_before.rows.values()), (
        "test setup expected at least one publishable local row"
    )

    refused = runner.invoke(
        dataset_group,
        ["pull", "v5-copy", "--data", "--json"],
    )
    assert refused.exit_code != 0, refused.output
    assert "--force-pull" in refused.output

    forced = runner.invoke(
        dataset_group,
        ["pull", "v5-copy", "--data", "--force-pull", "--json"],
    )
    assert forced.exit_code == 0, forced.output
    forced_payload = json.loads(forced.output)["pull"]
    # The remote row should have been imported; row-index must contain it.
    row_ids_after = {entry.row_id for entry in read_row_index("v5-copy")}
    # The remote row is identified by row_identity_hash(_row("Remote-only row."))
    # so we don't hardcode the id; we just require that imported_count >= 1
    # and the row index grew.
    assert forced_payload["imported_count"] >= 1
    assert len(row_ids_after) >= 2, (
        f"expected local + imported remote rows in index, got {row_ids_after}"
    )


# ---------------------------------------------------------------------------
# V7 — `publish --check-only` performs all gates without uploading
# ---------------------------------------------------------------------------


def test_v7_publish_check_only_passes_gates_and_does_not_upload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V7: `publish --check-only` performs gates and stages without uploading."""
    remote_root = _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    create_dataset(
        "v7-check",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("v7-check", "me/v7-check", visibility="private")
    append_rows(
        "v7-check",
        [_row("Check only row.", trace_id="trace-v7")],
        run_id="v7-1",
    )

    # Pre-condition: remote has nothing yet.
    repo_root = _remote_root(tmp_path, "me/v7-check")
    assert not repo_root.exists() or _list_remote_files(repo_root) == []

    result = runner.invoke(
        dataset_group,
        ["publish", "v7-check", "--check-only", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)["publish"]
    assert payload["check_only"] is True
    assert payload["uploaded"] is False
    assert payload["new_row_count"] == 1
    # Staged files reported (gate ran), but no remote files written.
    assert any(path.startswith("data/") for path in payload["staged_files"])

    remote_files_after = _list_remote_files(repo_root)
    # No public surface bytes should have been uploaded.
    public_files = [
        path
        for path in remote_files_after
        if not path.startswith(".") and not path.endswith(".fake_meta.json")
    ]
    assert public_files == [], (
        f"check-only must not write public surface files, got {public_files}"
    )


# ---------------------------------------------------------------------------
# V8 — Metadata-only republish path and full no-op
# ---------------------------------------------------------------------------


def test_v8_metadata_only_change_uploads_metadata_and_full_no_op_exits_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V8: zero new rows but changed README/dataset_infos still uploads metadata;
    fully unchanged second publish is a clean no-op (exit 0, message)."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    create_dataset(
        "v8-meta",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("v8-meta", "me/v8-meta", visibility="private")
    append_rows(
        "v8-meta",
        [_row("Initial row.", trace_id="trace-v8")],
        run_id="v8-1",
    )

    first = runner.invoke(dataset_group, ["publish", "v8-meta", "--json"])
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)["publish"]
    assert first_payload["uploaded"] is True
    assert first_payload["new_row_count"] == 1

    repo_root = _remote_root(tmp_path, "me/v8-meta")
    remote_readme_first = (repo_root / "README.md").read_bytes()

    # Mutate README.md locally to simulate a metadata-only change. The
    # publish flow re-emits README from the manifest each time, so we
    # change a manifest field that propagates into the rendered card.
    local_root = dataset_path("v8-meta")
    manifest_path = local_root / ".opentraces" / "manifest.yaml"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    assert "description:" not in manifest_text or "v8-fresh" not in manifest_text
    if "description:" in manifest_text:
        new_manifest = manifest_text.replace(
            "description:",
            "description: v8-fresh-description # autotest\n# old:",
            1,
        )
    else:
        # Inject a description line so the rendered card body changes.
        new_manifest = manifest_text + "description: v8-fresh-description\n"
    manifest_path.write_text(new_manifest, encoding="utf-8")

    second = runner.invoke(dataset_group, ["publish", "v8-meta", "--json"])
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)["publish"]
    # No new rows — but README must still be uploaded because it changed.
    assert second_payload["new_row_count"] == 0, second_payload
    assert second_payload["uploaded"] is True, (
        "metadata-only change must still result in an upload"
    )
    assert "README.md" in second_payload["staged_files"]
    remote_readme_second = (repo_root / "README.md").read_bytes()
    assert (
        remote_readme_second != remote_readme_first
    ), "README on remote must reflect the local metadata change"

    # Third publish: nothing changed — clean no-op.
    third = runner.invoke(dataset_group, ["publish", "v8-meta", "--json"])
    assert third.exit_code == 0, third.output
    third_payload = json.loads(third.output)["publish"]
    assert third_payload["uploaded"] is False
    assert third_payload["new_row_count"] == 0
    assert third_payload["message"], "no-op publish must carry a concise message"


# ---------------------------------------------------------------------------
# V12 — Duplicate rows no-op by row_id
# ---------------------------------------------------------------------------


def test_v12_duplicate_rows_no_op_by_row_id_on_second_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V12: re-staging an already-published row must not re-upload it."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    create_dataset(
        "v12-dup",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("v12-dup", "me/v12-dup", visibility="private")
    summary = append_rows(
        "v12-dup",
        [_row("Dedup me.", trace_id="trace-v12")],
        run_id="v12-1",
    )
    row_id = summary.row_ids[0]

    first = runner.invoke(dataset_group, ["publish", "v12-dup", "--json"])
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)["publish"]
    assert first_payload["uploaded"] is True
    assert first_payload["new_row_count"] == 1

    repo_root = _remote_root(tmp_path, "me/v12-dup")
    shards_first = sorted((repo_root / "data").glob("*.jsonl"))
    assert len(shards_first) == 1
    shard_first_digest = file_digest(shards_first[0])

    # Re-append the same row payload — identity_hash dedup makes append a
    # duplicate-noop locally. Even if a caller forces an additional row index
    # entry that points at the same payload, publish must still skip it
    # because the remote already contains a row with the same row_id.
    append_rows(
        "v12-dup",
        [_row("Dedup me.", trace_id="trace-v12")],
        run_id="v12-2",
    )
    # Confirm the row is still considered "published" locally so the publish
    # logic exercises its remote-row-id dedup gate.
    state = read_publication_state("v12-dup")
    assert state.rows[row_id].status == "published"

    second = runner.invoke(dataset_group, ["publish", "v12-dup", "--json"])
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)["publish"]
    assert second_payload["new_row_count"] == 0, second_payload
    assert second_payload["uploaded"] is False, (
        "republish of dedup-only state must be a clean no-op"
    )

    # Remote shards must be byte-identical — no new shard appended.
    shards_after = sorted((repo_root / "data").glob("*.jsonl"))
    assert [path.name for path in shards_after] == [shards_first[0].name]
    assert file_digest(shards_after[0]) == shard_first_digest


# ---------------------------------------------------------------------------
# V15 — publication_state.json survives row_index rebuild
# ---------------------------------------------------------------------------


def test_v15_publication_state_preserved_across_row_index_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V15: review decisions and `uploaded_to` survive `row_index.jsonl` rebuild."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    create_dataset(
        "v15-survive",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "required"},
    )
    add_dataset_remote("v15-survive", "me/v15-survive", visibility="private")
    summary = append_rows(
        "v15-survive",
        [
            _row("Approved row.", trace_id="trace-approved"),
            _row("Rejected row.", trace_id="trace-rejected"),
        ],
        run_id="v15-1",
    )
    approved_id = summary.row_ids[0]
    rejected_id = summary.row_ids[1]

    runner.invoke(
        dataset_group,
        ["approve", "v15-survive", approved_id, "--json"],
    )
    runner.invoke(
        dataset_group,
        ["reject", "v15-survive", rejected_id, "--json"],
    )
    # Publish the approved row so `uploaded_to` is non-empty for it.
    publish_result = runner.invoke(
        dataset_group, ["publish", "v15-survive", "--json"]
    )
    assert publish_result.exit_code == 0, publish_result.output

    state_before = read_publication_state("v15-survive")
    approved_before = state_before.rows[approved_id]
    rejected_before = state_before.rows[rejected_id]
    assert approved_before.status == "published"
    assert "me/v15-survive" in approved_before.uploaded_to
    assert rejected_before.status == "rejected"

    # Wipe and rebuild the row index from data/*.jsonl. Publication state
    # must be unaffected.
    rebuild_summary = rebuild_row_index("v15-survive")
    assert rebuild_summary.rebuilt_count == len(read_row_index("v15-survive"))

    state_after = read_publication_state("v15-survive")
    approved_after = state_after.rows[approved_id]
    rejected_after = state_after.rows[rejected_id]
    assert approved_after.status == approved_before.status == "published"
    assert approved_after.uploaded_to == approved_before.uploaded_to
    assert rejected_after.status == rejected_before.status == "rejected"


# ---------------------------------------------------------------------------
# V17 — `dataset status --remote --json` reports head, withdrawals, byte_identity
# ---------------------------------------------------------------------------


def test_v17_dataset_status_remote_json_reports_head_withdrawals_byte_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V17: `status --remote --json` reports remote head, withdrawals, and byte-identity."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    create_dataset(
        "v17-status",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("v17-status", "me/v17-status", visibility="private")
    summary = append_rows(
        "v17-status",
        [_row("Status row.", trace_id="trace-v17")],
        run_id="v17-1",
    )
    row_id = summary.row_ids[0]
    publish_result = runner.invoke(dataset_group, ["publish", "v17-status", "--json"])
    assert publish_result.exit_code == 0, publish_result.output

    # Add a withdrawal so the withdrawals counter is non-zero.
    withdraw = runner.invoke(
        dataset_group,
        [
            "withdraw",
            "v17-status",
            row_id,
            "--reason",
            "user-request",
            "--json",
        ],
    )
    assert withdraw.exit_code == 0, withdraw.output
    # Republish so the withdrawal tombstone reaches the remote — V17 mandates
    # the status payload reports remote-side withdrawals.
    runner.invoke(dataset_group, ["publish", "v17-status", "--json"])

    status = runner.invoke(
        dataset_group,
        ["status", "v17-status", "--remote", "--json"],
    )
    assert status.exit_code == 0, status.output
    payload = json.loads(status.output)
    remote_payload = payload["remote"]
    assert remote_payload["active_remote"] == "me/v17-status"

    # V17 prereqs: head, withdrawals, byte_identity.
    assert "head" in remote_payload, remote_payload
    head_value = remote_payload["head"]
    assert isinstance(head_value, str) and head_value, head_value

    assert "withdrawals" in remote_payload, remote_payload
    withdrawals_value = remote_payload["withdrawals"]
    # Either a count, a list, or a dict — any of these is acceptable as long
    # as it reports >= 1 withdrawal after the cycle above.
    if isinstance(withdrawals_value, int):
        assert withdrawals_value >= 1
    elif isinstance(withdrawals_value, list):
        assert len(withdrawals_value) >= 1
    elif isinstance(withdrawals_value, dict):
        assert withdrawals_value.get("count", 0) >= 1
    else:
        raise AssertionError(
            f"withdrawals must be an int|list|dict summary, got {type(withdrawals_value)}"
        )

    assert "byte_identity" in remote_payload, remote_payload
    byte_identity = remote_payload["byte_identity"]
    assert isinstance(byte_identity, dict)
    assert byte_identity.get("status") == "ok", byte_identity


# ---------------------------------------------------------------------------
# V23 — `dataset doctor --byte-identity` flags drift
# ---------------------------------------------------------------------------


def test_v23_doctor_byte_identity_detects_remote_file_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V23: `doctor --byte-identity` flags when a published file differs on the remote."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    create_dataset(
        "v23-drift",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("v23-drift", "me/v23-drift", visibility="private")
    append_rows(
        "v23-drift",
        [_row("Doctor row.", trace_id="trace-v23")],
        run_id="v23-1",
    )
    publish_result = runner.invoke(dataset_group, ["publish", "v23-drift", "--json"])
    assert publish_result.exit_code == 0, publish_result.output

    # Sanity: doctor should be ok right after publish.
    healthy = runner.invoke(
        dataset_group,
        ["doctor", "v23-drift", "--byte-identity", "--json"],
    )
    assert healthy.exit_code == 0, healthy.output
    healthy_payload = json.loads(healthy.output)
    assert healthy_payload["byte_identity"]["status"] == "ok", healthy_payload

    # Mutate the remote README.md on disk to simulate drift away from the
    # remembered staged digest.
    remote_root = _remote_root(tmp_path, "me/v23-drift")
    drift_target = remote_root / "README.md"
    assert drift_target.exists()
    drift_target.write_bytes(drift_target.read_bytes() + b"\n# tampered\n")

    drifted = runner.invoke(
        dataset_group,
        ["doctor", "v23-drift", "--byte-identity", "--json"],
    )
    # The CLI emits exit 0 with status=error embedded — assert the embedded
    # status faithfully reports drift, regardless of process exit code.
    drifted_payload = json.loads(drifted.output)
    bi = drifted_payload["byte_identity"]
    assert bi["status"] == "error", bi
    mismatches = bi.get("mismatches") or []
    assert any(item.get("path") == "README.md" for item in mismatches), bi


# ---------------------------------------------------------------------------
# V25 — Every staged upload omits `.opentraces/**`
# ---------------------------------------------------------------------------


def test_v25_remote_repo_never_contains_dot_opentraces_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V25: every `upload_folder` call must omit `.opentraces/**` recursively."""
    _setup_fake_remote(monkeypatch, tmp_path)
    runner = CliRunner()
    create_dataset(
        "v25-leak",
        workflow_skill="curator",
        workflow_digest="sha256:workflow",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("v25-leak", "me/v25-leak", visibility="private")
    summary = append_rows(
        "v25-leak",
        [_row("Public row.", trace_id="trace-v25")],
        run_id="v25-1",
    )
    row_id = summary.row_ids[0]

    # Drive several lifecycle steps that all stage and upload to the remote.
    publish_one = runner.invoke(dataset_group, ["publish", "v25-leak", "--json"])
    assert publish_one.exit_code == 0, publish_one.output

    withdraw = runner.invoke(
        dataset_group,
        [
            "withdraw",
            "v25-leak",
            row_id,
            "--reason",
            "user-request",
            "--json",
        ],
    )
    assert withdraw.exit_code == 0, withdraw.output

    publish_two = runner.invoke(dataset_group, ["publish", "v25-leak", "--json"])
    assert publish_two.exit_code == 0, publish_two.output

    # Recursively assert the remote folder never contains a `.opentraces/`
    # path component anywhere — including nested directories.
    repo_root = _remote_root(tmp_path, "me/v25-leak")
    leaked: list[str] = []
    for path in repo_root.rglob("*"):
        rel = path.relative_to(repo_root).as_posix()
        # Allow the fake adapter's own bookkeeping files; only the public
        # surface contract forbids `.opentraces/**`.
        if path.name in {".fake_head", ".fake_meta.json", ".fake_conflict_once"}:
            continue
        if ".opentraces" in rel.split("/"):
            leaked.append(rel)
    assert leaked == [], f"control-plane leak detected: {leaked}"

    # Belt and suspenders: assert the public surface includes the expected
    # files but never any `.opentraces` artefact.
    public_files = [
        rel
        for rel in _list_remote_files(repo_root)
        if rel not in {".fake_head", ".fake_meta.json", ".fake_conflict_once"}
    ]
    assert any(rel == "README.md" for rel in public_files)
    assert any(rel.startswith("data/") for rel in public_files)
    assert not any(".opentraces" in rel.split("/") for rel in public_files)
