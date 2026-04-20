"""End-to-end scenario tests for the push-flow version-safety matrix.

Each test places the fake remote in a specific "state" (fresh / equal /
older_additive / newer / mixed_versions / malformed_infos) and exercises the
real ``HFUploader`` methods against it, asserting both the *action taken*
(uploads, skips, raised errors) and the *final remote state* (loadable by
``datasets.load_dataset`` with the generator-derived Features schema).

The scenarios correspond one-to-one with the branches in
``HFUploader._sync_dataset_infos`` plus the migration path; together they
cover every version relation a client can encounter on a push.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from datasets import Features, load_dataset

from opentraces.publish.huggingface.upload import (
    HFUploader,
    RemoteSchemaAheadError,
)
from opentraces_schema.version import SCHEMA_VERSION

from ._remote_fake import FakeHFRepo
from ._remote_state_builder import (
    build_state_equal,
    build_state_fresh,
    build_state_mixed_versions,
    build_state_older_additive,
    previous_minor,
    static_fixture,
)


REPO_ID = "fake-user/fake-dataset"


def _make_uploader(fake: FakeHFRepo) -> HFUploader:
    """Instantiate HFUploader bound to a FakeHFRepo.

    We patch HfApi at the module level so __init__ gets our fake, then swap
    ``uploader.api`` directly for clarity in assertions.
    """
    with patch("opentraces.publish.huggingface.upload.HfApi") as MockApi:
        MockApi.return_value = fake
        uploader = HFUploader(token="fake-token", repo_id=REPO_ID)
    uploader.api = fake
    return uploader


def _load_final_state(fake: FakeHFRepo):
    """Load every shard in the fake's workdir through ``datasets`` using the
    *final* declared Features. Returns the list of rows.

    This is the real regression guardrail: if the uploaded ``dataset_infos``
    is narrower than the rows it declares, this call raises ``CastError``.
    """
    infos_path = fake.workdir / "dataset_infos.json"
    infos = json.loads(infos_path.read_text())
    features = Features.from_dict(infos["default"]["features"])
    shard_paths = sorted(str(p) for p in (fake.workdir / "data").glob("*.jsonl"))
    assert shard_paths, f"expected at least one shard under {fake.workdir}"
    dataset = load_dataset(
        "json",
        data_files=shard_paths,
        split="train",
        features=features,
    )
    return list(dataset)


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


# --- Scenario 1: fresh repo ----------------------------------------------


def test_fresh_repo_stamps_local_schema(tmp_path):
    fixture_dir = tmp_path / "fixture_fresh"
    build_state_fresh(fixture_dir)
    fake = FakeHFRepo.from_fixture(fixture_dir, tmp_path / "remote_fresh")

    uploader = _make_uploader(fake)
    uploader.ensure_repo_exists()

    infos_uploads = fake.uploads_to("dataset_infos.json")
    assert len(infos_uploads) == 1, "fresh repo should receive exactly one dataset_infos upload"
    payload = json.loads(infos_uploads[0].data.decode("utf-8"))
    assert payload["default"]["version"]["version_str"] == SCHEMA_VERSION
    assert "generation_index" in payload["default"]["features"]


# --- Scenario 2: remote equal to local -----------------------------------


def test_equal_remote_skips_dataset_infos_reupload(tmp_path):
    fixture_dir = tmp_path / "fixture_equal"
    build_state_equal(fixture_dir)
    fake = FakeHFRepo.from_fixture(fixture_dir, tmp_path / "remote_equal")

    infos_before = fake.read_file("dataset_infos.json")

    uploader = _make_uploader(fake)
    uploader.ensure_repo_exists()

    assert fake.uploads_to("dataset_infos.json") == [], (
        "Re-uploading the same-version dataset_infos.json creates a no-op commit"
        " on every push. The sync should detect the equal version and skip."
    )
    # Fixture file must not have been touched on disk either.
    assert fake.read_file("dataset_infos.json") == infos_before


# --- Scenario 3: remote older (same major, additive evolution) -----------


def test_older_additive_remote_gets_schema_refreshed(tmp_path):
    older = previous_minor(SCHEMA_VERSION)
    if older is None:
        pytest.skip(
            f"cannot compute a previous minor for SCHEMA_VERSION={SCHEMA_VERSION!r}"
        )

    fixture_dir = tmp_path / "fixture_older"
    build_state_older_additive(fixture_dir, older)
    fake = FakeHFRepo.from_fixture(fixture_dir, tmp_path / "remote_older")

    uploader = _make_uploader(fake)
    uploader.ensure_repo_exists()

    infos_uploads = fake.uploads_to("dataset_infos.json")
    assert len(infos_uploads) == 1
    payload = json.loads(infos_uploads[0].data.decode("utf-8"))
    assert payload["default"]["version"]["version_str"] == SCHEMA_VERSION

    # Migrating the shards is a separate step in the real push flow — run it
    # now and verify the end state casts cleanly.
    report = uploader.detect_outdated_shards(SCHEMA_VERSION)
    assert report["total_outdated"] == 2

    result = uploader.migrate_outdated_shards(SCHEMA_VERSION)
    assert result["migrated_records"] == 2

    rows = _load_final_state(fake)
    assert len(rows) == 2
    assert all(row["schema_version"] == SCHEMA_VERSION for row in rows)


# --- Scenario 4: remote newer --------------------------------------------


def test_newer_remote_blocks_push_with_upgrade_prompt(tmp_path):
    fake = FakeHFRepo.from_fixture(
        static_fixture("newer"), tmp_path / "remote_newer"
    )
    snapshot_before = {
        str(p.relative_to(fake.workdir)): _sha256(p.read_bytes())
        for p in fake.workdir.rglob("*") if p.is_file()
    }

    uploader = _make_uploader(fake)
    with pytest.raises(RemoteSchemaAheadError) as exc_info:
        uploader.ensure_repo_exists()

    assert exc_info.value.remote_version == "99.99.99"
    assert exc_info.value.local_version == SCHEMA_VERSION
    assert fake.uploads == [], "must not touch the repo when remote is ahead"

    snapshot_after = {
        str(p.relative_to(fake.workdir)): _sha256(p.read_bytes())
        for p in fake.workdir.rglob("*") if p.is_file()
    }
    assert snapshot_before == snapshot_after, "remote must be byte-identical after a blocked push"


# --- Scenario 5: mixed-version shard -------------------------------------


def test_mixed_version_shard_only_rewrites_older_rows(tmp_path):
    older = previous_minor(SCHEMA_VERSION)
    if older is None:
        pytest.skip(
            f"cannot compute a previous minor for SCHEMA_VERSION={SCHEMA_VERSION!r}"
        )

    fixture_dir = tmp_path / "fixture_mixed"
    build_state_mixed_versions(fixture_dir, older)
    fake = FakeHFRepo.from_fixture(fixture_dir, tmp_path / "remote_mixed")

    # Capture the per-line hashes of current-version rows — they must survive
    # migration unchanged.
    original_lines = [
        line for line in fake.read_file("data/traces_mixed_cccccccc.jsonl").decode().splitlines()
        if line.strip()
    ]
    current_line_hashes = {
        _sha256(line.encode()) for line in original_lines
        if json.loads(line)["schema_version"] == SCHEMA_VERSION
    }

    uploader = _make_uploader(fake)
    uploader.ensure_repo_exists()  # equal → no dataset_infos upload
    assert fake.uploads_to("dataset_infos.json") == []

    report = uploader.detect_outdated_shards(SCHEMA_VERSION)
    assert report["total_outdated"] == 2

    result = uploader.migrate_outdated_shards(SCHEMA_VERSION)
    assert result["migrated_records"] == 2

    migrated_lines = [
        line for line in fake.read_file("data/traces_mixed_cccccccc.jsonl").decode().splitlines()
        if line.strip()
    ]
    migrated_hashes = {_sha256(line.encode()) for line in migrated_lines}
    assert current_line_hashes.issubset(migrated_hashes), (
        "current-version rows must be byte-identical after migration"
    )

    rows = _load_final_state(fake)
    assert len(rows) == 4
    assert all(row["schema_version"] == SCHEMA_VERSION for row in rows)


# --- Scenario 6: malformed dataset_infos ---------------------------------


def test_malformed_dataset_infos_is_treated_as_fresh(tmp_path):
    fake = FakeHFRepo.from_fixture(
        static_fixture("malformed_infos"), tmp_path / "remote_malformed"
    )

    uploader = _make_uploader(fake)
    uploader.ensure_repo_exists()  # must not raise

    infos_uploads = fake.uploads_to("dataset_infos.json")
    assert len(infos_uploads) == 1, (
        "a missing/unparseable version must fall back to uploading the local schema"
    )
    payload = json.loads(infos_uploads[0].data.decode("utf-8"))
    assert payload["default"]["version"]["version_str"] == SCHEMA_VERSION


# --- CLI-level sanity check ----------------------------------------------


def test_ensure_repo_raises_type_carries_upgrade_hint(tmp_path):
    """The RemoteSchemaAheadError must carry both versions so the CLI's
    catch block can render a precise ``ot setup upgrade`` prompt.
    """
    fake = FakeHFRepo.from_fixture(
        static_fixture("newer"), tmp_path / "remote_type"
    )
    uploader = _make_uploader(fake)
    with pytest.raises(RemoteSchemaAheadError) as exc_info:
        uploader.ensure_repo_exists()

    msg = str(exc_info.value)
    assert "99.99.99" in msg
    assert SCHEMA_VERSION in msg
    assert exc_info.value.remote_version == "99.99.99"
    assert exc_info.value.local_version == SCHEMA_VERSION


# --- Regression: generator stays aligned with TraceRecord ----------------


def test_generated_schema_survives_round_trip_through_fake_repo(tmp_path):
    """Build an empty repo, push the generator's output, re-read it via the
    fake, and cast a fresh trace through ``datasets``. This exercises the
    full path from ``TraceRecord`` → HF_FEATURES → ``Features.from_dict``
    → ``load_dataset`` on a realistic row.
    """
    fake = FakeHFRepo.from_fixture(tmp_path / "nonexistent", tmp_path / "remote_roundtrip")

    uploader = _make_uploader(fake)
    uploader.ensure_repo_exists()

    # Manually publish one current-version shard so load_dataset has something
    # to cast. (The real push flow would handle this; we're exercising the
    # schema side only.)
    from tests.publish.test_upload import _make_trace  # reuse the fixture helper
    record = _make_trace(trace_id="roundtrip-1")
    fake.upload_file(
        path_or_fileobj=(record.to_jsonl_line() + "\n").encode("utf-8"),
        path_in_repo="data/traces_rt_eeeeeeee.jsonl",
        repo_id=REPO_ID,
        repo_type="dataset",
    )

    rows = _load_final_state(fake)
    assert len(rows) == 1
    assert rows[0]["trace_id"] == "roundtrip-1"
    # The regression: generator emitted Features that accept a real current-
    # schema row end-to-end.
    assert "generation_index" in rows[0]
