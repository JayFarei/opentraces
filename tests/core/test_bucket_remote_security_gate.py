"""Issue #94 — `bucket remote status` reports the shared security-gate.

The four-state matrix the issue requires, plus the doctor <-> remote-status
convergence proof. Both surfaces read the PERSISTED ``bucket/manifest.json``
counts through one shared helper (``bucket_sync_security_gate``) — no bucket
scan, no second eligibility computation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _write_manifest(
    *,
    unfiltered: int,
    stale: int,
    extra_trace_records: dict[str, Any] | None = None,
) -> Path:
    """Persist a minimal v2 ``bucket/manifest.json`` with chosen counts.

    Driving the gate off a persisted manifest (not a live scan) is exactly
    the cheap path doctor uses; the test controls the counts directly so the
    four security states are deterministic.
    """
    from opentraces.core import paths

    trace_records: dict[str, Any] = {
        "object_count": unfiltered + stale,
        "syncable_count": 0,
        "unfiltered_count": unfiltered,
        "security_stale_count": stale,
    }
    if extra_trace_records:
        trace_records.update(extra_trace_records)
    manifest_path = paths.bucket_dir() / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bucket.manifest.v2",
                "root": str(paths.bucket_dir()),
                "digest": "sha256:persisted",
                "bucket_digest": "sha256:persisted",
                "trace_records": trace_records,
                "trail": {"stale_count": 0},
                "sync": {
                    "eligible": unfiltered == 0 and stale == 0,
                    "blocked_reasons": [],
                },
            }
        )
    )
    return manifest_path


def _enable_regex_tool() -> None:
    from opentraces.core.config import load_config, save_config

    cfg = load_config()
    cfg.security.regex.enabled = True
    save_config(cfg)


def _configure_fake_remote() -> None:
    from opentraces.core.config import (
        BucketConfig,
        BucketRemoteConfig,
        load_config,
        save_config,
    )

    cfg = load_config()
    cfg.bucket = BucketConfig(
        storage="remote",
        local_cache=True,
        remote=BucketRemoteConfig(
            enabled=True,
            provider="fake",
        ),
    )
    save_config(cfg)


def test_no_remote_unfiltered() -> None:
    """Unconfigured remote + unfiltered records: the gate names the full chain.

    Remote is unconfigured (storage local), the filter is off, and there are
    unfiltered records — so the remediation is the ``policy --policy basic``
    step and the advice chain is setup-bucket -> policy -> run, in order.
    """
    from opentraces.core.bucket_remote import remote_status

    _write_manifest(unfiltered=3, stale=0)

    payload = remote_status()
    assert payload["state"] == "unconfigured"

    gate = payload["security_gate"]
    assert gate["state"] == "ok"
    assert gate["configured"] is False
    assert gate["unfiltered_count"] == 3
    assert gate["security_stale_count"] == 0
    assert gate["eligible"] is False
    assert gate["blocking_reasons"] == ["remote_unconfigured", "unfiltered_records"]
    assert (
        gate["remediation"]["command"]
        == "opentraces bucket security policy --policy basic"
    )
    assert gate["remediation"]["state"] == "not_configured"
    assert gate["advice"] == [
        "opentraces setup bucket",
        "opentraces bucket security policy --policy basic",
        "opentraces bucket security run --all",
    ]


def test_configured_filter_stale() -> None:
    """Filter configured + version-stale records: remediation is ``run --all``.

    Remote is configured (fake) so the gate isolates the SECURITY axis: the
    only blocker is the stale records, and the advice is just ``run --all``.
    """
    from opentraces.core.bucket_remote import remote_status

    _enable_regex_tool()
    _configure_fake_remote()
    _write_manifest(unfiltered=0, stale=2)

    gate = remote_status()["security_gate"]
    assert gate["configured"] is True
    assert gate["unfiltered_count"] == 0
    assert gate["security_stale_count"] == 2
    assert gate["eligible"] is False
    assert gate["blocking_reasons"] == ["security_version_stale"]
    assert gate["remediation"]["state"] == "version_stale"
    assert (
        gate["remediation"]["command"] == "opentraces bucket security run --all"
    )
    assert gate["advice"] == ["opentraces bucket security run --all"]


def test_eligible_bucket() -> None:
    """Remote configured + zero unfiltered/stale: eligible, no remediation."""
    from opentraces.core.bucket_remote import remote_status

    _enable_regex_tool()
    _configure_fake_remote()
    _write_manifest(unfiltered=0, stale=0)

    gate = remote_status()["security_gate"]
    assert gate["eligible"] is True
    assert gate["remediation"] is None
    assert gate["blocking_reasons"] == []
    assert gate["advice"] == []


def _manifest_path() -> Path:
    from opentraces.core import paths

    return paths.bucket_dir() / "manifest.json"


def _forbid_all_scanners(monkeypatch) -> None:
    """Make every O(N) scan / write entry point hard-fail.

    Covers BOTH module bindings of ``bucket_manifest`` (bucket_store's, used by
    ``fake_remote_status``; bucket_remote's, used by ``_hf_status``) plus the
    live ``bucket_security_overview`` scan. If the gate path touches any of
    them, the test fails loudly.
    """

    def _fail(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("the security gate must not scan or write")

    monkeypatch.setattr("opentraces.core.bucket_store.bucket_manifest", _fail)
    monkeypatch.setattr("opentraces.core.bucket_remote.bucket_manifest", _fail)
    monkeypatch.setattr(
        "opentraces.core.bucket_security.bucket_security_overview", _fail
    )


def test_gate_reads_persisted_manifest_read_only_no_scan(monkeypatch) -> None:
    """`_security_gate` never scans or writes — direct proof.

    With every scan/write entry point rigged to raise, the gate must still
    resolve from the read-only byte-capped persisted read: ``unknown`` when no
    manifest exists (and creating none), ``ok`` with counts when one does.
    """
    from opentraces.core import bucket_remote

    _forbid_all_scanners(monkeypatch)

    # No manifest -> unknown, and NOTHING was created.
    assert not _manifest_path().exists()
    gate = bucket_remote._security_gate(remote_configured=True)
    assert gate["state"] == "unknown"
    assert gate["remediation"] is None
    assert not _manifest_path().exists(), "the gate must not create manifest.json"

    # A hand-written persisted manifest -> ok with counts, still no scan/write.
    _write_manifest(unfiltered=2, stale=0)
    gate = bucket_remote._security_gate(remote_configured=True)
    assert gate["state"] == "ok"
    assert gate["unfiltered_count"] == 2


def test_remote_status_unconfigured_gate_unknown_without_creating_manifest() -> None:
    """End-to-end: unconfigured remote + no manifest -> unknown, none created.

    The unconfigured branch runs no status helper at all, so this is a clean
    end-to-end proof that the gate neither scanned nor wrote.
    """
    from opentraces.core import bucket_remote

    assert not _manifest_path().exists()
    gate = bucket_remote.remote_status()["security_gate"]
    assert gate["state"] == "unknown"
    assert gate["remediation"] is None
    assert "remote_unconfigured" in gate["blocking_reasons"]
    assert gate["advice"] == ["opentraces setup bucket"]
    assert not _manifest_path().exists()


def test_remote_status_configured_branches_gate_no_scan_no_write(
    monkeypatch, tmp_path
) -> None:
    """Codex finding #1/#3: the gate adds no scan/write on EVERY branch.

    For the explicit-fake-root, ambient-fake, configured-fake, and HF branches:
    with no persisted manifest and the status-helper digest scan neutralized to
    a read-only stub, ``remote_status`` reports ``security_gate.state ==
    "unknown"`` AND creates NO manifest.json — proving the gate neither scanned
    nor wrote on the configured branches (the configured-remote digest scan
    itself is the pre-existing #97 surface, neutralized here).
    """
    from opentraces.core import bucket_remote
    from opentraces.core.config import (
        BucketConfig,
        BucketRemoteConfig,
        load_config,
        save_config,
    )

    # Read-only stub for the status helpers' local-digest scan: returns a digest
    # WITHOUT writing manifest.json (the gate doesn't call this at all).
    def _readonly_manifest(*args, **kwargs):
        return {"digest": "sha256:stub"}

    monkeypatch.setattr(
        "opentraces.core.bucket_store.bucket_manifest", _readonly_manifest
    )
    monkeypatch.setattr(
        "opentraces.core.bucket_remote.bucket_manifest", _readonly_manifest
    )

    def _assert_unknown_no_write(payload) -> None:
        assert payload["security_gate"]["state"] == "unknown"
        assert not _manifest_path().exists(), "the gate must not create manifest.json"

    # 1. Explicit fake_root branch.
    _assert_unknown_no_write(bucket_remote.remote_status(fake_root=tmp_path / "fr"))

    # 2. Ambient fake branch (env-wired fake remote root, storage still local).
    monkeypatch.setenv(
        "OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", str(tmp_path / "ambient")
    )
    _assert_unknown_no_write(bucket_remote.remote_status())
    monkeypatch.delenv("OPENTRACES_FAKE_BUCKET_REMOTE_ROOT", raising=False)

    # 3. Configured fake remote branch.
    cfg = load_config()
    cfg.bucket = BucketConfig(
        storage="remote",
        local_cache=True,
        remote=BucketRemoteConfig(
            enabled=True,
            provider="fake",
            url=f"file://{tmp_path / 'configured'}",
        ),
    )
    save_config(cfg)
    _assert_unknown_no_write(bucket_remote.remote_status())

    # 4. HF remote branch (no token -> helper returns error; gate is unknown).
    cfg = load_config()
    cfg.hf_token = None
    cfg.bucket = BucketConfig(
        storage="remote",
        local_cache=True,
        remote=BucketRemoteConfig(
            enabled=True,
            provider="huggingface",
            url="hf://me/private-bucket",
        ),
    )
    save_config(cfg)
    _assert_unknown_no_write(bucket_remote.remote_status())


def test_too_large_manifest_degrades_like_doctor(monkeypatch) -> None:
    """An oversized persisted manifest -> ``unknown``, no parse (matches doctor).

    The byte cap is the same knob doctor uses; the gate must NOT ``read_text``
    a huge manifest.
    """
    from opentraces.core import bucket_remote

    # Cap at the floor (1024 bytes); pad the manifest past it.
    monkeypatch.setenv("OPENTRACES_DOCTOR_BUCKET_MANIFEST_MAX_BYTES", "1024")
    _write_manifest(unfiltered=3, stale=0, extra_trace_records={"_pad": "x" * 4000})
    assert _manifest_path().stat().st_size > 1024

    gate = bucket_remote.remote_status()["security_gate"]
    assert gate["state"] == "unknown"
    assert gate["reason"] == "manifest_too_large"


def test_doctor_and_remote_converge() -> None:
    """doctor's bucket gate and remote_status().security_gate agree.

    Same persisted manifest state -> identical counts + remediation. The
    shared helper guarantees this by construction; this pins it.
    """
    from opentraces.core import doctor
    from opentraces.core.bucket_remote import remote_status

    _enable_regex_tool()
    _write_manifest(unfiltered=4, stale=1)

    gate = remote_status()["security_gate"]
    info = doctor._bucket_status()
    doctor_remediation = info["security_remediation"]

    assert gate["unfiltered_count"] == 4
    assert gate["security_stale_count"] == 1
    # Counts converge with what doctor read from the same persisted manifest.
    assert info["trace_records"]["unfiltered_count"] == gate["unfiltered_count"]
    assert info["trace_records"]["security_stale_count"] == gate["security_stale_count"]
    # Remediation converges: same object from the same shared helper.
    assert doctor_remediation == gate["remediation"]
    assert doctor_remediation is not None
