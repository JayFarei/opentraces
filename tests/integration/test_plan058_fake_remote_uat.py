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

    from opentraces.core.datasets import withdraw_dataset_row

    withdraw_dataset_row("v25-leak", row_id, reason="user-request")

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
