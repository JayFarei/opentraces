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
import subprocess
from pathlib import Path

import pytest
from opentraces.publish.huggingface import upload as _upload
from opentraces.publish.huggingface.upload import HFUploader, RemoteSchemaAheadError
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


def _frozen_legacy_shard() -> Path:
    files = sorted((LEGACY_WORLD / "opentraces_home" / "projects").glob("*/traces/*.jsonl"))
    if not files:
        pytest.skip("frozen legacy_world_v033 fixture not present")
    return files[0]


def test_u_trace_2_live_read_path_keeps_legacy_patch():
    """U-trace-2 (Phase 0 regression guard). The live CLI read path
    (``cli.trace._read_trace_record_from_path``) must forward-migrate a
    schema-0.3.0 shard so ``patches[]`` is reconstructed and the raw diff is
    preserved under ``metadata.legacy.patch``. A bare ``model_validate_json``
    silently drops ``outcome.patch`` — the P0 this guards against — so we pin
    both the broken old behaviour and the fixed new behaviour here.
    """
    from opentraces.cli.trace import _read_trace_record_from_path

    shard = _frozen_legacy_shard()
    line = next(candidate for candidate in shard.read_text().splitlines() if candidate.strip())
    raw = json.loads(line)
    assert raw["schema_version"] == "0.3.0"
    assert raw["outcome"]["patch"], "fixture must carry the at-risk field"

    # The old, broken behaviour: a bare validate drops the diff entirely.
    bare = TraceRecord.model_validate_json(line)
    assert not bare.patches
    assert "legacy" not in (bare.metadata or {})

    # The fixed live read path recovers patches[] + preserves the raw diff.
    record = _read_trace_record_from_path(shard)
    assert record.patches, "live read path must reconstruct patches[]"
    assert record.metadata.get("legacy", {}).get("patch") == raw["outcome"]["patch"]


def test_diff_parser_handles_dev_null_creates_and_deletes():
    create = "--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+x = 1\n"
    delete = "--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    assert [p["file_path"] for p in reconstruct_patches_from_unified_diff(create)] == ["new.py"]
    assert [p["file_path"] for p in reconstruct_patches_from_unified_diff(delete)] == ["old.py"]


# --- S7 — HF downgrade refusal (reciprocal) -----------------------------
#
# A 0.3.3 client (LOCAL schema 0.3.0) pushing to a remote already declaring
# the 0.6.0 schema must be REFUSED: ``ensure_repo_exists`` raises
# ``RemoteSchemaAheadError`` before any upload, so the 0.3.3 push command
# (``cli/publish.py``) exits 3 with the ``ot setup upgrade`` hint and never
# overwrites the newer ``dataset_infos.json``. The guard is byte-identical
# code in 0.3.3 and 0.4 (``_sync_dataset_infos``); the only variable is
# ``LOCAL_SCHEMA_VERSION``.
#
# Layer A (below) proves the guard at the 0.3.3 -> 0.6.0 version boundary in
# default CI by pinning the local version to 0.3.0; Layer B drives the REAL
# v0.3.3 client and SKIPs when its isolated venv is absent (CI-safe).

_V033_VENV_PYTHON = Path("/tmp/ot-v033-worktree/.venv-v033/bin/python")


def _skip_if_v033_client_env_unavailable() -> None:
    if not _V033_VENV_PYTHON.exists():
        pytest.skip(f"real v0.3.3 venv absent at {_V033_VENV_PYTHON}")

    probe = subprocess.run(
        [
            str(_V033_VENV_PYTHON),
            "-c",
            (
                "from opentraces_schema.version import SCHEMA_VERSION\n"
                "from opentraces.publish.huggingface import upload\n"
                "assert SCHEMA_VERSION == '0.3.0', SCHEMA_VERSION\n"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if probe.returncode != 0:
        pytest.skip(
            "real v0.3.3 venv is not importable at "
            f"{_V033_VENV_PYTHON}:\n{probe.stdout}{probe.stderr}"
        )


def _uploader_with_remote_version(monkeypatch, *, local: str, remote: str | None):
    """Build an HFUploader whose remote declares ``remote`` and whose local
    client reports ``local``, with ``_upload_dataset_infos`` spied so we can
    assert no overwrite happens. Returns ``(uploader, calls)``."""
    monkeypatch.setattr(_upload, "LOCAL_SCHEMA_VERSION", local)
    uploader = HFUploader(token="fake-token", repo_id="ns/dataset")
    monkeypatch.setattr(uploader, "_fetch_remote_schema_version", lambda: remote)
    calls: list[bool] = []
    monkeypatch.setattr(uploader, "_upload_dataset_infos", lambda: calls.append(True))
    return uploader, calls


def test_s7_v033_client_refuses_push_to_0_6_0_remote(monkeypatch):
    """0.3.3 client (0.3.0) vs a 0.6.0 remote -> RemoteSchemaAheadError, no
    overwrite. This is the exact relation the 0.3.3 push CLI maps to exit 3."""
    uploader, calls = _uploader_with_remote_version(
        monkeypatch, local="0.3.0", remote="0.6.0"
    )
    with pytest.raises(RemoteSchemaAheadError) as exc:
        uploader._sync_dataset_infos()
    assert exc.value.remote_version == "0.6.0"
    assert exc.value.local_version == "0.3.0"
    # The newer remote schema was NOT overwritten by the older client.
    assert calls == [], "0.3.3 client must not upload over a 0.6.0 remote schema"


def test_s7_same_version_is_a_noop_not_a_refusal(monkeypatch):
    """Reciprocal control: a same-version remote is a skip, not a refusal —
    proves the guard fires on 'newer', not merely 'different'."""
    uploader, calls = _uploader_with_remote_version(
        monkeypatch, local="0.3.0", remote="0.3.0"
    )
    uploader._sync_dataset_infos()  # no raise
    assert calls == [], "equal-version remote should skip re-upload"


def test_s7_real_v033_client_refuses_0_6_0_remote():
    """Layer B — drive the REAL v0.3.3 client. Imports the 0.3.3
    ``HFUploader`` in its isolated venv, points it at a 0.6.0 remote, and
    asserts the 0.3.3 code raises ``RemoteSchemaAheadError``. SKIPs when the
    ephemeral /tmp venv is absent (so default CI on other machines is safe)."""
    _skip_if_v033_client_env_unavailable()
    snippet = (
        "import sys\n"
        "from opentraces_schema.version import SCHEMA_VERSION\n"
        "assert SCHEMA_VERSION == '0.3.0', SCHEMA_VERSION\n"
        "from opentraces.publish.huggingface import upload as u\n"
        "up = u.HFUploader(token='fake', repo_id='ns/dataset')\n"
        "up._fetch_remote_schema_version = lambda: '0.6.0'\n"
        "overwrote = []\n"
        "up._upload_dataset_infos = lambda: overwrote.append(True)\n"
        "try:\n"
        "    up._sync_dataset_infos()\n"
        "except u.RemoteSchemaAheadError as e:\n"
        "    assert e.remote_version == '0.6.0' and e.local_version == '0.3.0', (e.remote_version, e.local_version)\n"
        "    assert overwrote == []\n"
        "    print('REFUSED_OK')\n"
        "else:\n"
        "    print('NO_REFUSAL'); sys.exit(1)\n"
    )
    proc = subprocess.run(
        [str(_V033_VENV_PYTHON), "-c", snippet],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, f"v0.3.3 client did not refuse:\n{proc.stdout}\n{proc.stderr}"
    assert "REFUSED_OK" in proc.stdout, proc.stdout
