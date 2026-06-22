"""Plan 058 publish concurrency UAT proof artefact.

Covers the verification items called out by Plan 058 for concurrent publish,
duplicate handling, and bound-remote enforcement (V6, V11, V12). Each test
documents the verification item it asserts in its docstring.

The tests run entirely against the fake HF adapter behind
``OPENTRACES_PLAN058_FAKE_REMOTE_ROOT`` so they are deterministic and
network-free, while exercising the same parent_commit retry and dedup paths
the live HF uploader uses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _row(summary: str, *, trace_id: str = "trace-1", unit_id: str | None = None) -> dict:
    return {
        "source_trace_id": trace_id,
        "source_unit_id": unit_id or f"tu:{trace_id}:trace",
        "summary": summary,
    }


def _list_remote_shards(remote_root: Path) -> list[Path]:
    data_dir = remote_root / "data"
    if not data_dir.exists():
        return []
    return sorted(data_dir.glob("*.jsonl"))


def _read_remote_row_summaries(remote_root: Path) -> list[str]:
    summaries: list[str] = []
    for shard in _list_remote_shards(remote_root):
        for line in shard.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            summaries.append(row.get("summary", ""))
    return summaries


def _remote_row_id_set(remote_root: Path, identity) -> set[str]:
    from opentraces.core.datasets import row_identity_hash

    ids: set[str] = set()
    for shard in _list_remote_shards(remote_root):
        for line in shard.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            identity_hash = row_identity_hash(row, identity)
            ids.add(f"row_{identity_hash.removeprefix('sha256:')[:16]}")
    return ids


def test_plan058_publish_v11_multi_shard_concurrent_publish_produces_remote_union(
    tmp_path,
    monkeypatch,
):
    """V11: two datasets sharing one HF remote each emit a fresh shard,
    final remote contains the union of their row_ids with no duplicates.
    """
    from opentraces.core.datasets import (
        add_dataset_remote,
        append_rows,
        create_dataset,
        load_dataset,
        publish_dataset,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    repo = "team/shared"

    create_dataset(
        "local_a",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("local_a", repo, visibility="private")
    appended_a = append_rows(
        "local_a",
        [
            _row("Row from A one.", trace_id="trace-a-1"),
            _row("Row from A two.", trace_id="trace-a-2"),
        ],
        run_id="run-a",
    )

    create_dataset(
        "local_b",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("local_b", repo, visibility="private")
    appended_b = append_rows(
        "local_b",
        [
            _row("Row from B one.", trace_id="trace-b-1"),
            _row("Row from B two.", trace_id="trace-b-2"),
        ],
        run_id="run-b",
    )

    pub_a = publish_dataset("local_a", contributor="alice", resume="run-a")
    pub_b = publish_dataset("local_b", contributor="bob", resume="run-b")

    assert pub_a.uploaded is True
    assert pub_b.uploaded is True
    assert pub_a.new_row_count == 2
    assert pub_b.new_row_count == 2

    remote_root = tmp_path / "remotes" / repo
    shards = _list_remote_shards(remote_root)
    assert len(shards) == 2, f"expected one shard per publisher, got {[s.name for s in shards]}"

    summaries = _read_remote_row_summaries(remote_root)
    assert sorted(summaries) == [
        "Row from A one.",
        "Row from A two.",
        "Row from B one.",
        "Row from B two.",
    ]

    identity = load_dataset("local_a").manifest.identity
    expected_ids = set(appended_a.row_ids) | set(appended_b.row_ids)
    assert _remote_row_id_set(remote_root, identity) == expected_ids
    assert len(summaries) == len(set(summaries)), "remote shards must not contain duplicate rows"


def test_plan058_publish_v11_persistent_conflict_exhausts_retries_and_preserves_remote(
    tmp_path,
    monkeypatch,
):
    """V11: when the remote head conflicts on every retry attempt, publish
    raises a clear bounded-retry error and the remote is left at the original
    head with no partial writes from the failing publisher.
    """
    from opentraces.core.datasets import (
        MAX_PARENT_COMMIT_RETRIES,
        RemoteHeadConflict,
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    assert MAX_PARENT_COMMIT_RETRIES == 5

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_CONFLICT_COUNT", "999")

    create_dataset(
        "always-conflict",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("always-conflict", "me/always-conflict", visibility="private")
    append_rows(
        "always-conflict",
        [_row("Doomed row.", trace_id="trace-doomed")],
        run_id="run-doomed",
    )

    remote_root = tmp_path / "remotes" / "me" / "always-conflict"
    remote_root.mkdir(parents=True, exist_ok=True)
    head_path = remote_root / ".fake_head"
    head_path.write_text("fake-head-original\n", encoding="utf-8")

    with pytest.raises(RemoteHeadConflict):
        publish_dataset(
            "always-conflict",
            contributor="tester",
            resume="run-doomed",
            max_retries=MAX_PARENT_COMMIT_RETRIES,
        )

    # No data shard from the failing publisher should be on the remote.
    remote_shards = _list_remote_shards(remote_root)
    failing_shards = [shard for shard in remote_shards if shard.name.startswith("tester-run-doomed-")]
    assert failing_shards == [], f"failed publish must not leave shards: {[s.name for s in remote_shards]}"


def test_plan058_publish_v11_v12_deterministic_shard_ordering_in_remote_union(
    tmp_path,
    monkeypatch,
):
    """V11/V12: shard ordering in the remote union is stable and follows
    ``(contributor, run_id, digest)`` so consumers see deterministic output.
    """
    from opentraces.core.datasets import (
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    repo = "team/ordered"

    payloads = [
        ("alpha", "run-001", "Alpha first."),
        ("alpha", "run-002", "Alpha second."),
        ("bravo", "run-001", "Bravo first."),
        ("bravo", "run-002", "Bravo second."),
    ]

    for index, (contributor, run_id, summary) in enumerate(payloads):
        dataset_name = f"ds-{index}"
        create_dataset(
            dataset_name,
            workflow_skill="curator",
            workflow_digest="sha256:w",
            publication_policy={"review": "auto"},
        )
        add_dataset_remote(dataset_name, repo, visibility="private")
        append_rows(
            dataset_name,
            [_row(summary, trace_id=f"trace-{index}")],
            run_id=run_id,
        )
        publish_dataset(dataset_name, contributor=contributor, resume=run_id)

    remote_root = tmp_path / "remotes" / repo
    shards = _list_remote_shards(remote_root)
    assert len(shards) == 4
    shard_prefixes = [shard.name.rsplit("-", 1)[0] for shard in shards]
    assert shard_prefixes == sorted(shard_prefixes), (
        f"shards must sort by (contributor, run_id, digest); got {[s.name for s in shards]}"
    )
    # Each shard name encodes contributor and run_id in stable order.
    assert shards[0].name.startswith("alpha-run-001-")
    assert shards[1].name.startswith("alpha-run-002-")
    assert shards[2].name.startswith("bravo-run-001-")
    assert shards[3].name.startswith("bravo-run-002-")


def test_plan058_publish_v12_dedup_no_op_on_repeat_publish_of_same_row(
    tmp_path,
    monkeypatch,
):
    """V12: re-publishing the same row from a fresh run yields ``new_row_count
    == 0``, ``duplicate_count == 1``, and the remote still contains exactly
    one copy of that row.
    """
    from opentraces.core.datasets import (
        add_dataset_remote,
        append_rows,
        create_dataset,
        load_dataset,
        publish_dataset,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    create_dataset(
        "dedup",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("dedup", "me/dedup", visibility="private")
    append_rows(
        "dedup",
        [_row("Idempotent row.", trace_id="trace-idem")],
        run_id="run-1",
    )

    first = publish_dataset("dedup", contributor="tester", resume="run-1")
    assert first.uploaded is True
    assert first.new_row_count == 1
    assert first.duplicate_count == 0

    second = publish_dataset("dedup", contributor="tester", resume="run-2")
    assert second.uploaded is False, "second publish of the same row must be a clean no-op"
    assert second.new_row_count == 0
    assert second.duplicate_count == 1

    remote_root = tmp_path / "remotes" / "me" / "dedup"
    summaries = _read_remote_row_summaries(remote_root)
    assert summaries == ["Idempotent row."], f"row must appear exactly once on remote, saw {summaries}"

    identity = load_dataset("dedup").manifest.identity
    assert len(_remote_row_id_set(remote_root, identity)) == 1


def test_plan058_publish_v12_multi_shard_union_has_no_duplicate_row_ids(
    tmp_path,
    monkeypatch,
):
    """V12: when two datasets publish overlapping rows to the same remote,
    the second publisher dedups by ``row_id`` and only contributes the rows
    the first did not already publish.
    """
    from opentraces.core.datasets import (
        add_dataset_remote,
        append_rows,
        create_dataset,
        load_dataset,
        publish_dataset,
    )

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    repo = "team/overlap"

    shared_row = _row("Shared by both.", trace_id="trace-shared")
    only_a = _row("Only on A.", trace_id="trace-only-a")
    only_b = _row("Only on B.", trace_id="trace-only-b")

    create_dataset(
        "ds_a",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("ds_a", repo, visibility="private")
    append_rows("ds_a", [shared_row, only_a], run_id="run-a")

    create_dataset(
        "ds_b",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("ds_b", repo, visibility="private")
    append_rows("ds_b", [shared_row, only_b], run_id="run-b")

    pub_a = publish_dataset("ds_a", contributor="alice", resume="run-a")
    pub_b = publish_dataset("ds_b", contributor="bob", resume="run-b")

    assert pub_a.new_row_count == 2
    # ``shared_row`` is already on the remote when B publishes, so it dedups.
    assert pub_b.new_row_count == 1
    assert pub_b.duplicate_count == 1

    remote_root = tmp_path / "remotes" / repo
    summaries = sorted(_read_remote_row_summaries(remote_root))
    assert summaries == ["Only on A.", "Only on B.", "Shared by both."]

    identity = load_dataset("ds_a").manifest.identity
    remote_ids = _remote_row_id_set(remote_root, identity)
    assert len(remote_ids) == 3, f"remote must contain unique row_ids only, got {remote_ids}"


def test_plan058_publish_v6_no_bound_remote_fails_with_hint_and_no_side_effects(
    tmp_path,
    monkeypatch,
):
    """V6: ``publish_dataset`` with no bound remote raises a ``ValueError``
    that points the user at ``dataset remote create/add`` and produces no
    network or filesystem side effect under ``OPENTRACES_PLAN058_FAKE_REMOTE_ROOT``.
    """
    from opentraces.core.datasets import (
        append_rows,
        create_dataset,
        publish_dataset,
    )

    fake_remote_root = tmp_path / "remotes"
    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(fake_remote_root))

    create_dataset(
        "no-remote",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    append_rows(
        "no-remote",
        [_row("Should never leave.", trace_id="trace-stuck")],
        run_id="run-1",
    )

    fake_remote_snapshot_before: list[Path] = []
    if fake_remote_root.exists():
        fake_remote_snapshot_before = sorted(
            p.relative_to(fake_remote_root) for p in fake_remote_root.rglob("*")
        )

    with pytest.raises(ValueError) as excinfo:
        publish_dataset("no-remote", contributor="tester")

    message = str(excinfo.value)
    assert "no active remote" in message
    # Plan 087: `create` is the canonical idempotent create-or-bind verb.
    assert "dataset remote create" in message

    fake_remote_snapshot_after: list[Path] = []
    if fake_remote_root.exists():
        fake_remote_snapshot_after = sorted(
            p.relative_to(fake_remote_root) for p in fake_remote_root.rglob("*")
        )

    assert fake_remote_snapshot_before == fake_remote_snapshot_after, (
        "no-remote publish must not touch the fake remote tree"
    )
    # Specifically: no repo dir was created.
    assert not (fake_remote_root / "no-remote").exists()
    assert not any(fake_remote_root.glob("**/data")) if fake_remote_root.exists() else True


def test_plan058_publish_v11_single_retry_after_one_conflict_succeeds_with_union(
    tmp_path,
    monkeypatch,
):
    """V11: a single transient 412 conflict is retried under
    ``MAX_PARENT_COMMIT_RETRIES`` and the final remote contains the union of
    the simulated concurrent row plus the publisher's own rows.
    """
    from opentraces.core.datasets import (
        MAX_PARENT_COMMIT_RETRIES,
        add_dataset_remote,
        append_rows,
        create_dataset,
        publish_dataset,
    )

    assert MAX_PARENT_COMMIT_RETRIES >= 1

    monkeypatch.setenv("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT", str(tmp_path / "remotes"))
    monkeypatch.setenv(
        "OPENTRACES_PLAN058_FAKE_CONFLICT_ROW",
        json.dumps(_row("Concurrent remote row.", trace_id="trace-concurrent")),
    )

    create_dataset(
        "single-retry",
        workflow_skill="curator",
        workflow_digest="sha256:w",
        publication_policy={"review": "auto"},
    )
    add_dataset_remote("single-retry", "me/single-retry", visibility="private")
    append_rows(
        "single-retry",
        [_row("Local row.", trace_id="trace-local")],
        run_id="run-1",
    )

    # Default max_retries should be sufficient for a single transient conflict.
    published = publish_dataset("single-retry", contributor="tester", resume="run-1")
    assert published.uploaded is True
    assert published.attempts == 2

    remote_root = tmp_path / "remotes" / "me" / "single-retry"
    summaries = sorted(_read_remote_row_summaries(remote_root))
    assert summaries == ["Concurrent remote row.", "Local row."]
