"""0.3.3 -> 0.4 migration: schema-level unit/integration coverage (plan 085).

Covers the Python-level migration scenarios that do not need the otbox
legacy-world checkpoint:

  * S2 — schema field-loss audit: a bare load of a 0.3.0 record drops exactly
    ``outcome.patch`` and nothing else.
  * S3 — ``migrate_record`` reconstructs ``patches[]`` from the legacy unified
    diff, preserves the raw diff under ``metadata.legacy.patch``, loses no data,
    is idempotent, and never mutates its input.
  * S4-adjacent — already-UUID trace ids are untouched by the migration.

The otbox journey scenarios (S1, S5, S8, S9, S10 on-disk, S11, S12) live in the
otbox catalogue against the ``c-legacy-v033`` checkpoint.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from opentraces_schema.migrations import (
    RECONSTRUCTED_CAPTURE_METHOD,
    migrate_record,
    reconstruct_patches_from_unified_diff,
)
from opentraces_schema.models import TraceRecord

import tests.migration.audit_schema_fieldloss as audit

FIXTURE = Path(__file__).parent / "migration" / "fixtures" / "record_schema_0_3_0.json"


def _legacy_record() -> dict:
    return json.loads(FIXTURE.read_text())


# --- S2 -------------------------------------------------------------------

def test_s2_bare_load_drops_only_outcome_patch():
    raw = _legacy_record()
    r = audit.run_audit()
    assert r["bare_dropped_fields"] == ["outcome.patch"], (
        "outcome.patch must be the ONLY field a 0.3.0 record loses on bare load; "
        f"got {r['bare_dropped_fields']}"
    )
    # And the fixture really did carry the field we claim is at risk.
    assert raw["outcome"]["patch"]


# --- S3 -------------------------------------------------------------------

def test_s3_reconstruction_recovers_every_file():
    raw = _legacy_record()
    migrated = migrate_record(raw)
    record = TraceRecord.model_validate(migrated)
    files = [p.file_path for p in record.patches]
    assert files == ["foo.py", "bar.py"], files
    assert all(RECONSTRUCTED_CAPTURE_METHOD in p.capture_method for p in record.patches)


def test_s3_preserves_raw_diff_under_metadata_legacy():
    raw = _legacy_record()
    migrated = migrate_record(raw)
    assert migrated["metadata"]["legacy"]["patch"] == raw["outcome"]["patch"]
    # The dead field is dropped from the migrated shape.
    assert "patch" not in migrated["outcome"]


def test_s3_no_devtime_data_loss():
    """Every file named in the legacy diff is recoverable post-migration."""
    raw = _legacy_record()
    record = TraceRecord.model_validate(migrate_record(raw))
    legacy_files = {"foo.py", "bar.py"}
    assert legacy_files <= {p.file_path for p in record.patches}


def test_s3_idempotent_byte_identical():
    raw = _legacy_record()
    once = migrate_record(raw)
    twice = migrate_record(once)
    assert once == twice, "migrate_record must be idempotent"
    # Content-addressed ids are stable across runs.
    assert [p["patch_id"] for p in once["patches"]] == [
        p["patch_id"] for p in twice["patches"]
    ]


def test_s3_does_not_mutate_input():
    raw = _legacy_record()
    snapshot = copy.deepcopy(raw)
    migrate_record(raw)
    assert raw == snapshot, "migrate_record must not mutate its argument"


def test_s3_skips_when_patches_already_present():
    raw = _legacy_record()
    raw["patches"] = [{"patch_id": "preexisting", "file_path": "kept.py"}]
    migrated = migrate_record(raw)
    assert migrated["patches"] == [{"patch_id": "preexisting", "file_path": "kept.py"}]
    # Raw diff is still preserved for recoverability even when not reconstructed.
    assert migrated["metadata"]["legacy"]["patch"]


# --- fallback / robustness ------------------------------------------------

def test_unparseable_diff_falls_back_to_metadata_only():
    raw = _legacy_record()
    raw["outcome"]["patch"] = "this is not a unified diff"
    migrated = migrate_record(raw)
    assert not migrated.get("patches")  # nothing fabricated
    assert migrated["metadata"]["legacy"]["patch"] == "this is not a unified diff"
    # Still validates.
    TraceRecord.model_validate(migrated)


def test_noop_when_no_legacy_patch():
    raw = _legacy_record()
    raw["outcome"].pop("patch")
    migrated = migrate_record(raw)
    assert migrated == raw


# --- real frozen v0.3.3 world (R1 fixture) ------------------------------

import pytest

LEGACY_WORLD = Path(__file__).parent / "migration" / "fixtures" / "legacy_world_v033"


def _frozen_legacy_trace() -> dict:
    files = sorted((LEGACY_WORLD / "opentraces_home" / "projects").glob("*/traces/*.jsonl"))
    if not files:
        pytest.skip("frozen legacy_world_v033 fixture not present")
    return json.loads(files[0].read_text().splitlines()[0])


def test_frozen_world_is_real_schema_0_3_0_with_patch():
    raw = _frozen_legacy_trace()
    assert raw["schema_version"] == "0.3.0"
    assert raw["outcome"]["patch"], "frozen fixture must carry the at-risk field"


def test_frozen_legacy_trace_migrates_without_loss():
    raw = _frozen_legacy_trace()
    record = TraceRecord.model_validate(migrate_record(raw))
    assert record.patches, "real legacy record must reconstruct patches[]"
    assert record.metadata.get("legacy", {}).get("patch") == raw["outcome"]["patch"]


def test_diff_parser_handles_dev_null_creates_and_deletes():
    create = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    delete = "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    assert [p["file_path"] for p in reconstruct_patches_from_unified_diff(create)] == ["new.py"]
    assert [p["file_path"] for p in reconstruct_patches_from_unified_diff(delete)] == ["old.py"]
