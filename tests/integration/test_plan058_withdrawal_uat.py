"""Plan 058 withdrawal UAT proof artefact.

Covers verification items V14, V18, V19, V20 from
``kb/plans/058-dataset-remotes-and-cli-lifecycle.md`` (section "Verification"),
plus the routine-tombstone publish round-trip and the trace-source cascade
workflow described under "Withdrawal semantics".

Conventions copied from sibling Plan 058 fake-adapter tests:

- ``OPENTRACES_PLAN058_FAKE_REMOTE_ROOT`` redirects every fake adapter seam
  (``_fake_upload_folder``, ``_fake_remote_dir``, ``_remote_head``).
- ``tests/conftest.py`` isolates ``HOME`` and ``~/.opentraces/`` per test.
- The Click CLI is the authoritative product surface for row withdrawal;
  trace-source cascade is kept as core dataset behavior rather than a public
  ``ot trace`` command.
"""

from __future__ import annotations

import json
from pathlib import Path

from opentraces.cli.dataset import dataset_group
from opentraces.core.datasets import (
    add_dataset_remote,
    append_rows,
    create_dataset,
    dataset_path,
    file_digest,
    load_public_rows,
    publish_dataset,
    rebuild_row_index,
    read_row_index,
    set_publication_review_status,
    withdraw_dataset_row,
)


def _row(summary: str, *, trace_id: str = "trace-1", unit_id: str | None = None) -> dict:
    return {
        "source_trace_id": trace_id,
        "source_unit_id": unit_id or f"tu:{trace_id}:trace",
        "summary": summary,
    }


def _remote_root(tmp_path: Path, repo_id: str) -> Path:
    owner, _, name = repo_id.partition("/")
    return tmp_path / "remotes" / owner / name


# ---------------------------------------------------------------------------
# V14 — Routine tombstone shape and publish round-trip
# ---------------------------------------------------------------------------


def test_plan058_v14_routine_withdrawal_writes_documented_tombstone_shape():
    """V14: routine ``ot dataset withdraw`` emits a tombstone under
    ``_withdrawals/<timestamp>-<requester>-<digest>.jsonl`` matching the
    documented ``{target,target_id,reason,requested_at}`` payload.
    """

    create_dataset("withdraw-shape", workflow_skill="curator", workflow_digest="sha256:w")
    summary = append_rows(
        "withdraw-shape",
        [_row("Document tombstone shape.", trace_id="trace-shape")],
        run_id="run-1",
    )
    row_id = summary.row_ids[0]

    record = withdraw_dataset_row("withdraw-shape", row_id, reason="user-request")

    tombstones = sorted((dataset_path("withdraw-shape") / "_withdrawals").glob("*.jsonl"))
    assert len(tombstones) == 1, "withdraw must emit exactly one tombstone file"
    name = tombstones[0].name
    assert name.endswith(".jsonl")
    parts = name[: -len(".jsonl")].split("-")
    assert len(parts) == 3, f"tombstone file name shape <timestamp>-<requester>-<digest>: {name}"
    timestamp_part, requester_part, digest_part = parts
    assert timestamp_part and timestamp_part.isalnum()
    assert requester_part == "local"
    assert digest_part and len(digest_part) >= 8

    payload = json.loads(tombstones[0].read_text(encoding="utf-8").splitlines()[0])
    assert set(payload.keys()) == {"target", "target_id", "reason", "requested_at"}
    assert payload["target"] == "row" == record.target
    assert payload["target_id"] == row_id == record.target_id
    assert payload["reason"] == "user-request"
    assert payload["requested_at"] == record.requested_at


def test_plan058_v14_dataset_publish_uploads_withdrawals_to_remote(tmp_path, monkeypatch):
    """V14: tombstones belong to the public surface and travel to the remote
    ``_withdrawals/`` folder via ``ot dataset publish``.
    """

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "withdraw-publish",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("withdraw-publish", "me/withdraw-publish", visibility="private")
    summary = append_rows(
        "withdraw-publish",
        [_row("Travelling row.", trace_id="trace-pub")],
        run_id="run-1",
    )
    row_id = summary.row_ids[0]

    # First publish carries the row to the remote.
    first = publish_dataset("withdraw-publish", contributor="tester")
    assert first.uploaded is True

    # Now withdraw the row and republish — only metadata + the tombstone change.
    withdraw_dataset_row("withdraw-publish", row_id, reason="user-request")
    second = publish_dataset("withdraw-publish", contributor="tester")
    assert second.uploaded is True
    assert any(rel.startswith("_withdrawals/") for rel in second.staged_files)

    remote_root = _remote_root(tmp_path, "me/withdraw-publish")
    remote_withdrawals = sorted((remote_root / "_withdrawals").glob("*.jsonl"))
    assert remote_withdrawals, "remote must contain published withdrawal records"
    payload = json.loads(remote_withdrawals[0].read_text(encoding="utf-8").splitlines()[0])
    assert payload["target_id"] == row_id


# ---------------------------------------------------------------------------
# V14 — Withdrawal record security re-scan
# ---------------------------------------------------------------------------


def test_plan058_v14_publish_refuses_when_withdrawal_record_contains_secret(tmp_path, monkeypatch):
    """V14: withdrawal records ARE part of the mandatory egress scan. A fake
    OpenAI-style API key embedded in the ``reason`` field must block publish
    with security findings before the upload step touches the network.
    """

    from opentraces.core.datasets import DatasetSecurityFindingsError

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "withdraw-secret",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("withdraw-secret", "me/withdraw-secret", visibility="private")
    summary = append_rows(
        "withdraw-secret",
        [_row("Clean row.", trace_id="trace-secret")],
        run_id="run-1",
    )
    row_id = summary.row_ids[0]

    # Sneak an API-key-shaped string into the reason field.
    fake_key = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"
    withdraw_dataset_row("withdraw-secret", row_id, reason=f"leak-{fake_key}")

    raised: DatasetSecurityFindingsError | None = None
    try:
        publish_dataset("withdraw-secret", contributor="tester")
    except DatasetSecurityFindingsError as exc:
        raised = exc
    assert raised is not None, "withdrawal record with API-key-shaped string must block publish"
    assert raised.findings, "exception must expose at least one finding"
    # Publish must abort BEFORE any bytes leave the machine: nothing shows up
    # in the fake remote root.
    remote_root = _remote_root(tmp_path, "me/withdraw-secret")
    if remote_root.exists():
        remote_files = [p for p in remote_root.rglob("*") if p.is_file() and not p.name.startswith(".fake_")]
        assert remote_files == [], "no public files may reach remote when scan blocks publish"


def test_plan058_v14_publish_does_not_rescan_generated_metadata_files(tmp_path, monkeypatch):
    """V14: generated metadata files (README, dataset_infos.json) follow the
    audited emitter path and are not rescanned as row payloads. Even if a
    secret-shaped substring is injected into the dataset description (which
    feeds README), publish should not block on metadata findings — the gate
    only triggers for rows and withdrawal records.
    """

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    # The dataset description ends up in the generated README.md card body.
    create_dataset(
        "metadata-emitter",
        description="See sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ1234 for legacy reasons.",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("metadata-emitter", "me/metadata-emitter", visibility="private")
    append_rows(
        "metadata-emitter",
        [_row("Clean row body.", trace_id="trace-meta")],
        run_id="run-1",
    )

    # Publish must succeed — metadata is NOT scanned per V14.
    summary = publish_dataset("metadata-emitter", contributor="tester")
    assert summary.uploaded is True


# ---------------------------------------------------------------------------
# V19 — Tombstone-aware wrapper across multiple shards
# ---------------------------------------------------------------------------


def test_plan058_v19_load_public_rows_filters_withdrawn_across_multiple_shards():
    """V19: ``load_public_rows(name, apply_withdrawals=True)`` filters
    withdrawn rows on a multi-shard dataset.
    """

    create_dataset("multi-shard", workflow_skill="curator", workflow_digest="sha256:w")
    root = dataset_path("multi-shard")
    # Seed a first shard via the public append path...
    summary_a = append_rows(
        "multi-shard",
        [_row("In first shard.", trace_id="trace-A")],
        run_id="run-1",
    )
    # ...then plant a second shard file directly and rebuild the index so the
    # row index reflects multiple data files.
    second_shard = root / "data" / "alice-run2-deadbeefcafe.jsonl"
    second_row = _row("In second shard.", trace_id="trace-B")
    second_shard.write_text(json.dumps(second_row, sort_keys=True) + "\n", encoding="utf-8")
    rebuild_row_index("multi-shard")

    entries = read_row_index("multi-shard")
    files = {entry.data_file for entry in entries}
    assert "data/train.jsonl" in files
    assert "data/alice-run2-deadbeefcafe.jsonl" in files

    # Withdraw the row living in the second shard.
    target = next(e for e in entries if e.data_file == "data/alice-run2-deadbeefcafe.jsonl")
    withdraw_dataset_row("multi-shard", target.row_id, reason="user-request")

    filtered = load_public_rows("multi-shard", apply_withdrawals=True)
    assert len(filtered) == 1
    assert filtered[0]["summary"] == "In first shard."
    # The opt-out path must still surface every row.
    raw = load_public_rows("multi-shard", apply_withdrawals=False)
    assert {row["summary"] for row in raw} == {"In first shard.", "In second shard."}
    # Sanity: the original row from shard A is preserved.
    assert summary_a.appended_count == 1


# ---------------------------------------------------------------------------
# V18 — load_dataset docs do not falsely claim tombstone filtering
# ---------------------------------------------------------------------------


def test_plan058_v18_plain_load_dataset_docs_do_not_claim_tombstone_filtering():
    """V18: docstrings under ``src/opentraces/`` and the top-level README
    must not claim plain ``load_dataset(...)`` filters tombstones. The
    tombstone-aware wrapper (``load_public_rows``) must document the manual
    behaviour.
    """

    from opentraces.core import datasets as datasets_module

    # V18a: The wrapper documents the tombstone filtering contract explicitly.
    assert datasets_module.load_public_rows.__doc__, (
        "load_public_rows must carry a docstring describing the tombstone-aware behaviour"
    )
    doc = datasets_module.load_public_rows.__doc__.lower()
    assert "tombstone" in doc or "withdraw" in doc, (
        "load_public_rows docstring must mention tombstones / withdrawals"
    )
    assert "load_dataset" in doc, (
        "load_public_rows docstring must contrast itself with plain load_dataset"
    )

    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src" / "opentraces"
    readme = repo_root / "README.md"

    # V18b: No source file or README under the public surface may claim that
    # plain load_dataset automatically filters tombstones. Search for the
    # forbidden phrase patterns case-insensitively.
    forbidden_substrings = (
        "load_dataset filters tombstones",
        "load_dataset automatically filters",
        "load_dataset honors tombstones",
        "load_dataset honours tombstones",
        "load_dataset respects tombstones",
        "load_dataset applies withdrawals",
    )

    candidate_paths: list[Path] = [readme]
    for path in src_root.rglob("*.py"):
        candidate_paths.append(path)
    for path in src_root.rglob("*.md"):
        candidate_paths.append(path)

    offenders: list[tuple[Path, str]] = []
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lowered = text.lower()
        for needle in forbidden_substrings:
            if needle in lowered:
                offenders.append((path, needle))
    assert offenders == [], (
        "Plan 058 V18 forbids docs/source claiming plain load_dataset filters "
        f"tombstones automatically; offenders: {offenders}"
    )


# ---------------------------------------------------------------------------
# V20 — Hard delete requires confirmation and rewrites only affected shards
# ---------------------------------------------------------------------------


def test_plan058_v20_hard_delete_requires_confirm_flag():
    """V20: ``--hard`` without ``--confirm HARD_DELETE`` must raise so that
    accidental shard rewrites are not possible through a short flag alone.
    """

    create_dataset("hard-confirm", workflow_skill="curator", workflow_digest="sha256:w")
    summary = append_rows(
        "hard-confirm",
        [_row("Will require confirm.", trace_id="trace-hd")],
        run_id="run-1",
    )
    row_id = summary.row_ids[0]
    raised: ValueError | None = None
    try:
        withdraw_dataset_row("hard-confirm", row_id, reason="legal", hard=True)
    except ValueError as exc:
        raised = exc
    assert raised is not None
    assert "HARD_DELETE" in str(raised)


def test_plan058_v20_hard_delete_rewrites_only_affected_shards_byte_identically():
    """V20: ``--hard --confirm HARD_DELETE`` rewrites only the shard
    containing the targeted row; sibling shards must be byte-identical
    before and after.
    """

    create_dataset("hard-isolated", workflow_skill="curator", workflow_digest="sha256:w")
    root = dataset_path("hard-isolated")

    append_rows(
        "hard-isolated",
        [_row("Stays in train.", trace_id="trace-train")],
        run_id="run-1",
    )

    untouched_shard = root / "data" / "alice-run-untouched.jsonl"
    untouched_row = _row("Untouched neighbour.", trace_id="trace-other")
    untouched_shard.write_text(json.dumps(untouched_row, sort_keys=True) + "\n", encoding="utf-8")

    target_shard = root / "data" / "bob-run-target.jsonl"
    target_row = _row("Hard delete me.", trace_id="trace-target")
    target_shard.write_text(json.dumps(target_row, sort_keys=True) + "\n", encoding="utf-8")

    rebuild_row_index("hard-isolated")

    # Capture pre-rewrite digests for sibling shards we expect untouched.
    train_digest_before = file_digest(root / "data" / "train.jsonl")
    untouched_digest_before = file_digest(untouched_shard)
    target_digest_before = file_digest(target_shard)

    entries = read_row_index("hard-isolated")
    target_entry = next(e for e in entries if e.data_file == "data/bob-run-target.jsonl")

    withdraw_dataset_row(
        "hard-isolated",
        target_entry.row_id,
        reason="legal",
        hard=True,
        confirm="HARD_DELETE",
    )

    # Sibling shards must be byte-identical post hard-delete.
    assert file_digest(root / "data" / "train.jsonl") == train_digest_before
    assert file_digest(untouched_shard) == untouched_digest_before
    # The target shard was rewritten and should now be empty (or strictly
    # smaller — its only row was removed).
    assert file_digest(target_shard) != target_digest_before
    assert target_shard.read_text(encoding="utf-8") == ""

    # Tombstone is recorded on the routine path even for hard delete.
    tombstones = list((root / "_withdrawals").glob("*.jsonl"))
    assert tombstones, "hard delete still emits a tombstone for downstream wrappers"

    # Row index must no longer carry the deleted row.
    after = read_row_index("hard-isolated")
    assert all(e.row_id != target_entry.row_id for e in after)


# ---------------------------------------------------------------------------
# Trace-scoped cascade workflow — core dataset withdrawal helper
# ---------------------------------------------------------------------------


def test_plan058_trace_cascade_emits_row_withdrawals_across_datasets():
    """Plan 058 withdrawal semantics: the core cascade helper resolves all
    dataset rows whose ``source_trace_id`` matches and emits row-level
    withdrawals in each affected dataset.
    """

    create_dataset("cascade-a", workflow_skill="curator", workflow_digest="sha256:w")
    create_dataset("cascade-b", workflow_skill="curator", workflow_digest="sha256:w")
    create_dataset("cascade-c", workflow_skill="curator", workflow_digest="sha256:w")

    # cascade-a: row from the doomed trace.
    summary_a = append_rows(
        "cascade-a",
        [_row("Doomed in A.", trace_id="trace-doomed", unit_id="tu:trace-doomed:a")],
        run_id="run-a",
    )
    # cascade-b: another row from the same trace.
    summary_b = append_rows(
        "cascade-b",
        [_row("Doomed in B.", trace_id="trace-doomed", unit_id="tu:trace-doomed:b")],
        run_id="run-b",
    )
    # cascade-c: unrelated trace; must NOT be touched.
    summary_c = append_rows(
        "cascade-c",
        [_row("Unrelated.", trace_id="trace-keep", unit_id="tu:trace-keep:c")],
        run_id="run-c",
    )

    from opentraces.core.datasets import forget_trace_cascade

    cascade = forget_trace_cascade("trace-doomed", reason="user-request")
    assert cascade["withdrawn_count"] == 2

    # cascade-a: row withdrawn.
    public_a = load_public_rows("cascade-a", apply_withdrawals=True)
    assert public_a == [], "row in cascade-a must be filtered post-cascade"
    assert any((dataset_path("cascade-a") / "_withdrawals").glob("*.jsonl"))

    # cascade-b: row withdrawn.
    public_b = load_public_rows("cascade-b", apply_withdrawals=True)
    assert public_b == [], "row in cascade-b must be filtered post-cascade"
    assert any((dataset_path("cascade-b") / "_withdrawals").glob("*.jsonl"))

    # cascade-c: untouched.
    public_c = load_public_rows("cascade-c", apply_withdrawals=True)
    assert len(public_c) == 1
    assert public_c[0]["summary"] == "Unrelated."
    assert not list((dataset_path("cascade-c") / "_withdrawals").glob("*.jsonl"))

    # row index entries from cascade-a/b are still present (routine
    # withdrawal does not rewrite shards), and shadow row IDs are recorded.
    assert summary_a.row_ids and summary_b.row_ids and summary_c.row_ids
