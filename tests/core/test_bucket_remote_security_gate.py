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


def test_missing_manifest_is_unknown_not_a_scan(monkeypatch) -> None:
    """No persisted manifest -> security_gate is ``unknown``, never a scan."""
    from opentraces.core import bucket_remote

    def _fail_scan(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("remote_status must not scan the bucket")

    # Both O(N) scan entry points are forbidden on the security-gate path.
    monkeypatch.setattr(
        "opentraces.core.bucket_store.bucket_manifest", _fail_scan
    )
    monkeypatch.setattr(
        "opentraces.core.bucket_security.bucket_security_overview", _fail_scan
    )

    gate = bucket_remote.remote_status()["security_gate"]
    assert gate["state"] == "unknown"
    assert gate["remediation"] is None
    # Unconfigured remote is still surfaced even without a manifest.
    assert "remote_unconfigured" in gate["blocking_reasons"]
    assert gate["advice"] == ["opentraces setup bucket"]


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
