"""0.3.3 -> 0.4 upgrade-UAT, Phase 1 (pytest layer).

Fixture-only / unit cases from runs/migration-0.3.3-to-0.4/UPGRADE-UAT-AUDIT.md.
These pin upgrade behavior across the context-tree, security, post-processor,
bucket-egress, and HF-publish code paths in isolation (no otbox checkpoints),
all default-CI-safe. Phase 0 (the live read-path P0 fix + U-trace-1/U-trace-2
guards) lives in tests/test_migration_0_3_3_to_0_4.py.

Each test names its catalogue ID. Where the audit's hypothesis turned out to be
wrong against the real code, the test characterizes the ACTUAL behavior and the
docstring says so, rather than asserting a false premise.
"""
from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from opentraces_schema import load_record_dict, load_record_json
from opentraces_schema.migrations import (
    RECONSTRUCTED_LIMITATION,
    migrate_record,
    reconstruct_patches_from_unified_diff,
)
from opentraces_schema.models import TraceRecord

LEGACY_WORLD = Path(__file__).parent / "migration" / "fixtures" / "legacy_world_v033"


def _frozen_legacy_raw() -> dict:
    files = sorted((LEGACY_WORLD / "opentraces_home" / "projects").glob("*/traces/*.jsonl"))
    if not files:
        pytest.skip("frozen legacy_world_v033 fixture not present")
    return json.loads(files[0].read_text().splitlines()[0])


# --- U-ctx-2 (P0) — Context Tree fields stay inert on a migrated legacy record -

def test_u_ctx_2_migrated_record_has_no_fabricated_context_tree():
    """U-ctx-2 (P0). After migrate_record + validate, the post-0.3.3 Context
    Tree fields stay empty: context_tree_summary is None and every
    Step.context_node_id is None. The additive schema must not block validation
    and migration must not fabricate context nodes the legacy session never had.
    """
    raw = _frozen_legacy_raw()
    assert raw["schema_version"] == "0.3.0"
    assert "context_tree_summary" not in raw
    record = TraceRecord.model_validate(migrate_record(raw))

    # Empty/falsy summary (the model defaults it to {}); no fabricated node graph.
    assert not record.context_tree_summary
    summary = record.context_tree_summary or {}
    assert "node_count" not in summary
    assert "active_path_leaf_id" not in summary
    assert record.steps, "fixture must carry steps"
    assert all(getattr(s, "context_node_id", None) is None for s in record.steps)


# --- U-trail-7 (P1, library half) — migration emits no TrailEvents; ----------
# reconstructed patches[] are file-granular and the degradation is labeled.

def test_u_trail_7_two_hunks_one_file_degrade_to_labeled_file_granular():
    """U-trail-7 (P1, library half). The documented per-hunk authorship unit is
    NOT recoverable from a legacy unified diff: a 2-hunk-1-file diff reconstructs
    to a single file-granular patch. The degradation is explicit and labeled via
    the `reconstructed_from_outcome_patch` limitation rather than silently
    presented as hunk-granular.
    """
    two_hunk = (
        "--- a/foo.py\n+++ b/foo.py\n"
        "@@ -1,2 +1,3 @@\n a\n+b\n c\n"
        "@@ -10,2 +11,3 @@\n x\n+y\n z\n"
    )
    patches = reconstruct_patches_from_unified_diff(two_hunk)
    assert len(patches) == 1, "v1 reconstruction is file-granular, not hunk-granular"
    assert patches[0]["file_path"] == "foo.py"
    assert RECONSTRUCTED_LIMITATION in patches[0]["limitations"]


# --- U-setup-10 (P1) — migration preserves legacy provenance verbatim --------

def test_u_setup_10_migration_preserves_attribution_and_git_links_verbatim():
    """U-setup-10 (P1). Migration must not silently rewrite legacy provenance:
    attribution and git_links survive byte-for-byte (NOT recomputed from the
    reconstructed patches[]), and new evidence fields the legacy session never
    had stay absent so consumers degrade honestly.
    """
    raw = _frozen_legacy_raw()
    before_attr = json.dumps(raw.get("attribution"), sort_keys=True)
    before_links = json.dumps(raw.get("git_links"), sort_keys=True)

    migrated = migrate_record(raw)

    assert json.dumps(migrated.get("attribution"), sort_keys=True) == before_attr
    assert json.dumps(migrated.get("git_links"), sort_keys=True) == before_links
    # New post-0.3.3 evidence field is not fabricated by the migration.
    assert "skill_invocations" not in (migrated.get("metadata") or {})


# --- U-sec-2 (P0) — sanitize over legacy/migrated records --------------------

def test_u_sec_2_sanitize_record_canonical_order_and_patch_survival():
    """U-sec-2 (P0). sanitize_record over a migrated legacy record: the
    reconstructed patches[] survive sanitization, metadata.security.tools_applied
    is emitted in CANONICAL order regardless of the requested order, and the
    all-off config default runs zero tools (no surprise scanning).
    """
    from opentraces.security.pipeline import sanitize_record

    raw_line = json.dumps(_frozen_legacy_raw())
    rec = load_record_json(raw_line)
    assert rec.patches and (rec.metadata.get("legacy") or {}).get("patch")

    # Deliberately request out of canonical order; pipeline must re-order.
    out, report = sanitize_record(rec, tools=["entropy", "regex"])
    assert report.tools_applied == ["regex", "entropy"]
    assert len(out.patches) == 1, "sanitize must not drop reconstructed patches[]"
    assert out.metadata["security"]["tools_applied"] == ["regex", "entropy"]

    # All-off config => zero tools applied, record untouched.
    from opentraces.core.config import Config

    fresh = load_record_json(raw_line)
    _, all_off = sanitize_record(fresh, cfg=Config())
    assert all_off.tools_applied == []


def test_u_sec_2_regex_tool_redacts_a_planted_secret():
    """U-sec-2 (P0, redaction half). A regex-only sanitize over a record with a
    planted high-signal secret redacts it (non-zero redactions) without dropping
    the migrated patches[].
    """
    from opentraces.security.pipeline import sanitize_record

    # Plant an AWS-style key in a properly-typed string field via the raw dict,
    # so the walker traverses it as a real model field (not an injected dict).
    raw = _frozen_legacy_raw()
    raw.setdefault("outcome", {})["description"] = "deploy with AKIAIOSFODNN7EXAMPLE secret key"
    rec = load_record_dict(raw)

    out, report = sanitize_record(rec, tools=["regex"])
    assert report.tools_applied == ["regex"]
    assert report.redactions_applied >= 1
    assert "AKIAIOSFODNN7EXAMPLE" not in out.outcome.description
    assert len(out.patches) == 1


def test_u_sec_2_cli_record_path_is_migration_aware():
    """U-sec-2 (P0, CLI loader guard). The `security sanitize` `{"record": ...}`
    stdin path must forward-migrate a legacy 0.3.0 record so its diff is
    preserved under metadata.legacy.patch (where the walker can scan it) instead
    of being silently dropped by a bare validate. Guards cli/security.py.
    """
    import opentraces.cli.security as sec_cli

    src = inspect.getsource(sec_cli)
    # The record-ingestion path uses the migration-aware loader, not bare validate.
    assert "load_record_dict" in src
    assert "TraceRecord.model_validate(rec_data)" not in src


# --- U-ds-8 (P0) — post-processor receives a migrated 0.6.0 record -----------

_ECHO_WITH_MARKERS = (
    "import sys, json\n"
    "rec = json.load(sys.stdin)\n"
    "md = rec.setdefault('metadata', {})\n"
    "md['processor_saw_patches'] = len(rec.get('patches') or [])\n"
    "md['processor_saw_outcome_patch'] = bool((rec.get('outcome') or {}).get('patch'))\n"
    "md['processor_saw_legacy_patch'] = bool((md.get('legacy') or {}).get('patch'))\n"
    "json.dump(rec, sys.stdout)\n"
)

_EMIT_GARBAGE = "import sys\nsys.stdout.write('not json at all')\n"


def _post_processor_spec(script: str, name: str = "p"):
    import sys as _sys

    from opentraces.core.config import PostProcessorConfig

    return PostProcessorConfig(name=name, command=_sys.executable, args=["-c", script])


def test_u_ds_8_processor_receives_migrated_shape():
    """U-ds-8 (P0). A post-processor in the chain receives the MIGRATED 0.6.0
    record on stdin: patches[] populated, metadata.legacy.patch present, and NO
    outcome.patch. Proves the processor contract operates on the post-migration
    shape, not the legacy one.
    """
    from opentraces.core.processors import run_chain

    rec = load_record_json(json.dumps(_frozen_legacy_raw()))
    result = run_chain(rec, [_post_processor_spec(_ECHO_WITH_MARKERS)])
    md = result.record.metadata
    assert md["processor_saw_patches"] >= 1
    assert md["processor_saw_outcome_patch"] is False
    assert md["processor_saw_legacy_patch"] is True


def test_u_ds_8_invalid_output_flagged_and_strict_raises():
    """U-ds-8 (P0). A processor emitting non-record output is flagged
    invalid_output (original record preserved) by default; under --strict it
    raises. (Note: a processor re-adding outcome.patch is NOT invalid_output —
    Pydantic silently drops the removed field — so malformed output is the real
    invalid_output trigger.)
    """
    from opentraces.core.processors import ProcessorError, run_processor

    rec = load_record_json(json.dumps(_frozen_legacy_raw()))
    spec = _post_processor_spec(_EMIT_GARBAGE)

    out_rec, res = run_processor(rec, spec)
    assert res.status == "invalid_output"
    assert out_rec is rec  # original preserved on bad output

    with pytest.raises(ProcessorError):
        run_processor(rec, spec, strict=True)


# --- U-bucket-2 (P0) — no surprise egress on capture -------------------------

def test_u_bucket_2_egress_is_opt_in_not_token_gated():
    """U-bucket-2 (P0). A pre-existing hf_token is NOT consent to egress. The
    bucket defaults local with the remote disabled, and the capture/ingest path
    contains no bucket-remote push: raw captures stay local until an explicit
    `setup bucket` flips the remote on.
    """
    from opentraces.core import ingest
    from opentraces.core.config import BucketConfig

    bc = BucketConfig()
    assert bc.storage == "local"
    assert bc.remote.enabled is False

    # The egress gate is storage=="remote" AND remote.enabled, never token presence.
    ingest_src = inspect.getsource(ingest)
    assert "reconcile_once" not in ingest_src
    assert "bucket_remote" not in ingest_src


# --- U-bucket-3 (P0) — two-store egress separation ---------------------------

def test_u_bucket_3_dataset_and_bucket_remotes_are_distinct_bindings():
    """U-bucket-3 (P0). The dataset remote (per-dataset manifest.remotes[].url)
    and the bucket remote (global cfg.bucket.remote.url) are independent config
    objects on independent storage, so dataset publish and bucket push cannot
    cross-contaminate repos.
    """
    from opentraces.core import datasets
    from opentraces.core.config import BucketConfig
    from opentraces_schema.dataset import DatasetManifest, DatasetRemote

    # Two distinct binding shapes, neither referencing the other.
    assert "url" in DatasetRemote.model_fields
    assert "remotes" in DatasetManifest.model_fields
    assert "remote" in BucketConfig.model_fields

    # core.datasets (the live publish path) never reads the bucket remote config.
    ds_src = inspect.getsource(datasets)
    assert "bucket.remote" not in ds_src
    assert "cfg.bucket" not in ds_src


# --- U-hf-1 (P0) — confirm/refute live dataset publish reaches HFUploader -----

def test_u_hf_1_dataset_publish_does_not_reach_hfuploader():
    """U-hf-1 (P0). Confirms the product gap (prior memory): the live
    `dataset publish` path (core.datasets.publish_dataset) does NOT route through
    HFUploader's schema-ahead / migrate_outdated_shards / dedup guards. It uploads
    via HfApi directly, and its own schema-ahead check is a no-op against a real
    remote (only the fake-remote test root is consulted). HFUploader is wired only
    to the legacy `opentraces push` command. This pins the gap as a fact so the
    forward HF shard-migration wiring is tracked, not assumed.
    """
    from opentraces.core import datasets

    ds_src = inspect.getsource(datasets)
    # The schema-safe uploader is absent from the live dataset publish module.
    assert "HFUploader" not in ds_src

    # Its self-rolled schema-ahead check consults only the fake-remote root, so it
    # no-ops without OPENTRACES_PLAN058_FAKE_REMOTE_ROOT (i.e. against real HF).
    assert os.environ.get("OPENTRACES_PLAN058_FAKE_REMOTE_ROOT") is None
    assert datasets._fake_remote_dir("owner/repo") is None

    # Document where the schema-safe guards ARE wired: the legacy push command.
    import opentraces.cli.publish as legacy_push

    assert "HFUploader" in inspect.getsource(legacy_push)


# --- U-config-6 / U-auth-1 (P0/P1, real-v033-venv) --------------------------
# The genuine two-version handoff: a HOME set up by the REAL 0.3.3 CLI must be
# read by the REAL 0.4 CLI without crashing and with the legacy config preserved.
# Mirrors the S7 Layer-B discipline: skip when the isolated v0.3.3 venv is absent
# so default CI stays green. The remaining two-venv parts that need a live agent
# or network (real claude/OTLP capture, live HF publish) are documented as
# runnable manual-UAT steps in runs/migration-0.3.3-to-0.4/MANUAL-UAT-TWO-VENV.md.

import subprocess
import sys

_V033_BIN = Path(
    os.environ.get("OT_V033_BIN", "/tmp/ot-v033-worktree/.venv-v033/bin/opentraces")
)


def _v04_bin() -> str:
    # The 0.4 CLI from the venv running these tests.
    cand = Path(sys.executable).parent / "opentraces"
    return str(cand) if cand.exists() else "opentraces"


def test_u_config_6_real_v033_home_is_read_by_real_v04(tmp_path):
    """U-config-6 / U-auth-1 (real-v033-venv). A project HOME initialized by the
    real opentraces 0.3.3 CLI is read by the real 0.4 CLI without crashing: the
    legacy config_version is preserved (forward-tolerant) and status/config-show
    return rc=0. Proves the actual binary-to-binary upgrade boundary, not just the
    frozen fixture. SKIPs when the isolated v0.3.3 venv is absent.
    """
    if not _V033_BIN.exists():
        pytest.skip(f"real v0.3.3 venv absent at {_V033_BIN}")

    home = tmp_path / "home"
    proj = tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    env = {**os.environ, "HOME": str(home)}

    def v033(*a):
        return subprocess.run(
            [str(_V033_BIN), *a], cwd=proj, env=env, capture_output=True, text=True
        )

    def v04(*a):
        return subprocess.run(
            [_v04_bin(), *a], cwd=proj, env=env, capture_output=True, text=True
        )

    assert "0.3.3" in v033("--version").stdout
    assert "0.4" in v04("--version").stdout

    # Real 0.3.3 enrolls the project.
    init = v033("init", "--agent", "claude-code")
    assert init.returncode == 0, init.stderr

    # Real 0.4 reads the 0.3.3-written home without crashing.
    show = v04("--json", "config", "show")
    assert show.returncode == 0, show.stderr
    payload = show.stdout.split("---OPENTRACES_JSON---")[-1]
    cfg = json.loads(payload)
    assert cfg["config_version"] == "0.2.0"  # legacy version preserved, no crash

    assert v04("status").returncode == 0


# --- U-auth-1 (P0) — 0.3.3-stored hf_token survives + env-over-stored ----------

def test_u_auth_1_legacy_credential_reused_and_env_wins(monkeypatch, tmp_path):
    """U-auth-1 (P0). A token stored by 0.3.3 at the version-stable opentraces
    credentials path (plain `hf_...` text at ~/.opentraces/credentials) is reused
    by the 0.4 resolver without re-login, and an HF_TOKEN env var beats the
    migrated stored token. Network-free: exercises the resolution precedence in
    core.config directly. The credential path + format are unchanged across the
    0.3.3 -> 0.4 jump, so an upgrader keeps HF auth.
    """
    from opentraces.core import config as cfg_mod

    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
    # Point the resolver at a throwaway credentials file (the 0.3.3-format store).
    cred = tmp_path / "credentials"
    cred.write_text("hf_legacy_stored_0337")
    monkeypatch.setattr(cfg_mod, "CREDENTIALS_PATH", cred)
    # Avoid leaking the real user's hf cache into the assertion.
    monkeypatch.setattr(cfg_mod.Path, "home", staticmethod(lambda: tmp_path))

    # The 0.3.3-stored token is reused by 0.4 with no re-login.
    assert cfg_mod._resolve_hf_token() == "hf_legacy_stored_0337"

    # HF_TOKEN env beats the migrated stored credential.
    monkeypatch.setenv("HF_TOKEN", "hf_env_override_999")
    assert cfg_mod._resolve_hf_token() == "hf_env_override_999"
