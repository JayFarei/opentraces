"""Bucket security service: posture, remediation, and apply (issue #89).

The security-policy boundary over the private trace bucket. ``bucket_store`` owns
storage; this module owns the *security posture* read model and the remediation
that makes records remote-sync eligible. It reads and rewrites records through
``bucket_store`` and re-runs the import security pipeline, but holds no storage
layout knowledge of its own.

Public surface (consumed by ``opentraces bucket security status|run`` and
``opentraces doctor``):

* :func:`bucket_security_overview` — read-only posture (filtered / unfiltered /
  stale counts + whether the filter is configured + the remediation).
* :func:`bucket_security_remediation` — map a posture to one actionable step.
* :func:`apply_bucket_security_filter` — run the configured filter over existing
  records (all or one trace) so they become remote-sync eligible.
"""

from __future__ import annotations

from typing import Any

from ..security.version import SECURITY_VERSION
from .bucket_store import (
    bucket_security_state,
    iter_trace_record_objects,
    read_bucket_record_for_trace,
    record_privacy_tier,
    write_trace_record,
)


def bucket_security_overview(cfg: Any = None) -> dict[str, Any]:
    """Read-only bucket security posture used by ``bucket security status``.

    Reports how many bucket records are remote-sync eligible (filtered),
    pending (unfiltered), or version-stale, plus whether the security filter is
    configured at all — so doctor / the CLI can give the exact remediation.
    """

    from .pipeline import _resolved_tool_names

    if cfg is None:
        from .config import load_config

        cfg = load_config()
    configured = list(_resolved_tool_names(cfg, skip_trufflehog=False))
    objects = iter_trace_record_objects()
    states = [bucket_security_state(obj.record) for obj in objects]
    filtered = sum(1 for s in states if s.get("syncable") is True)
    unfiltered = sum(1 for s in states if not s.get("syncable"))
    stale = sum(1 for s in states if s.get("stale") is True)
    return {
        "total": len(objects),
        "filtered": filtered,
        "unfiltered": unfiltered,
        "stale": stale,
        "configured_tools": configured,
        "filtering_configured": bool(configured),
        "security_version": SECURITY_VERSION,
        "remediation": bucket_security_remediation(
            unfiltered=unfiltered,
            stale=stale,
            filtering_configured=bool(configured),
        ),
    }


def bucket_sync_security_gate(*, unfiltered: int, stale: int) -> dict[str, Any]:
    """Shared bucket-sync security eligibility gate (issue #94).

    The single computation behind BOTH doctor's bucket security remediation
    (``doctor._bucket_security_remediation_for_doctor``) and ``bucket remote
    status``'s ``security_gate`` block. It reads ``filtering_configured`` from
    config and maps the ``(unfiltered, stale)`` counts to the one actionable
    remediation step.

    The counts are supplied by the caller — both callers read them off the
    PERSISTED ``bucket/manifest.json`` (``trace_records.unfiltered_count`` /
    ``security_stale_count``). This helper performs NO bucket scan; it never
    calls :func:`bucket_security_overview`. That keeps the sync-diagnostic
    surfaces cheap on large buckets (the O(N) live-scan fast path is #97/#88).

    Returns ``{configured, unfiltered_count, security_stale_count, eligible,
    blocking_reasons, remediation}``. ``eligible`` here is the SECURITY axis
    only (``remediation is None``); ``bucket remote status`` composes it with
    the remote-configured axis for overall sync eligibility.
    """

    from .pipeline import _resolved_tool_names

    try:
        from .config import load_config

        configured = bool(_resolved_tool_names(load_config(), skip_trufflehog=False))
    except Exception:
        configured = False
    remediation = bucket_security_remediation(
        unfiltered=unfiltered,
        stale=stale,
        filtering_configured=configured,
    )
    blocking_reasons: list[str] = []
    if unfiltered:
        blocking_reasons.append("unfiltered_records")
    if stale:
        blocking_reasons.append("security_version_stale")
    return {
        "configured": configured,
        "unfiltered_count": int(unfiltered),
        "security_stale_count": int(stale),
        "eligible": remediation is None,
        "blocking_reasons": blocking_reasons,
        "remediation": remediation,
    }


def bucket_security_remediation(
    *,
    unfiltered: int,
    stale: int,
    filtering_configured: bool,
) -> dict[str, Any] | None:
    """Map bucket security posture to a single actionable next step."""

    if unfiltered == 0 and stale == 0:
        return None
    if not filtering_configured:
        return {
            "state": "not_configured",
            "reason": (
                "bucket security filter is not configured, so records cannot be "
                "marked remote-sync eligible"
            ),
            "command": "opentraces bucket security policy --policy basic",
            "next_steps": [
                "Run 'opentraces bucket security policy --policy basic' (or "
                "'opentraces setup privacy-filter' / 'opentraces setup trufflehog' "
                "for stronger tools) to enable the filter.",
                "Then run 'opentraces bucket security run --all' to apply it.",
                "Inspect configured tools with 'opentraces security tools list'.",
            ],
        }
    if stale and not unfiltered:
        return {
            "state": "version_stale",
            "reason": (
                f"records were filtered by an older security version (current "
                f"{SECURITY_VERSION})"
            ),
            "command": "opentraces bucket security run --all",
            "next_steps": [
                "Run 'opentraces bucket security run --all' to re-apply the "
                "current security filter.",
            ],
        }
    return {
        "state": "pending",
        "reason": (
            "records have not had the configured bucket security filter applied "
            "or recorded yet"
        ),
        "command": "opentraces bucket security run --all",
        "next_steps": [
            "Run 'opentraces bucket security run --all' to apply the configured "
            "bucket security filter before remote sync.",
        ],
    }


def apply_bucket_security_filter(
    *,
    trace_id: str | None = None,
    cfg: Any = None,
) -> dict[str, Any]:
    """Apply the configured security filter to existing bucket records.

    The remediation behind ``bucket security run``. For each unfiltered /
    version-stale record it re-runs the import security pipeline and rewrites
    the bucket envelope so the record becomes remote-sync eligible. Idempotent:
    already-filtered records are skipped. Distinguishes four outcomes so callers
    can report precisely: filtered (now eligible), already_filtered,
    still_blocked (ran but not eligible — e.g. privacy tier ``off``), failed.
    """

    from .pipeline import _resolved_tool_names, process_imported_trace

    if cfg is None:
        from .config import load_config

        cfg = load_config()
    configured = list(_resolved_tool_names(cfg, skip_trufflehog=False))

    if trace_id is not None:
        obj = read_bucket_record_for_trace(trace_id)
        objects = [obj] if obj is not None else []
        if not objects:
            return {
                "status": "not_found",
                "trace_id": trace_id,
                "configured_tools": configured,
                "filtering_configured": bool(configured),
                "total": 0,
                "filtered": 0,
                "already_filtered": 0,
                "still_blocked": 0,
                "failed": 0,
                "trace_ids": [],
            }
    else:
        objects = iter_trace_record_objects()

    if not configured:
        unfiltered = sum(
            1 for obj in objects if not bucket_security_state(obj.record).get("syncable")
        )
        return {
            "status": "not_configured",
            "configured_tools": [],
            "filtering_configured": False,
            "total": len(objects),
            "filtered": 0,
            "already_filtered": len(objects) - unfiltered,
            "still_blocked": unfiltered,
            "failed": 0,
            "trace_ids": [],
            "remediation": bucket_security_remediation(
                unfiltered=unfiltered, stale=0, filtering_configured=False
            ),
        }

    filtered = 0
    already_filtered = 0
    still_blocked = 0
    failed = 0
    changed_ids: list[str] = []
    for obj in objects:
        state = bucket_security_state(obj.record)
        if state.get("syncable") is True:
            already_filtered += 1
            continue
        explicit_tier = record_privacy_tier(obj.record)
        try:
            new_record = process_imported_trace(obj.record, cfg).record
        except Exception:
            failed += 1
            continue
        new_state = bucket_security_state(new_record, privacy_tier=explicit_tier)
        try:
            write_trace_record(
                new_record,
                project_slug=obj.project_slug,
                source_layer=obj.source_layer,
                legacy_mirror=bool(obj.envelope.get("legacy_mirror", True)),
                privacy_tier=explicit_tier,
            )
        except OSError:
            failed += 1
            continue
        if new_state.get("syncable") is True:
            filtered += 1
            changed_ids.append(obj.trace_id)
        else:
            still_blocked += 1

    if filtered or still_blocked:
        try:
            from .trace_search_state import mark_search_snapshot_dirty

            mark_search_snapshot_dirty("bucket_security_run")
        except Exception:
            pass
        # Plan 087 — security re-scan rewrote object-envelope ``syncable``/``stale``
        # out of band of the manifest rows; flag the manifest read-model stale so
        # ``bucket status`` serves the cached counts marked stale until a heal.
        try:
            from .bucket_manifest_state import mark_bucket_manifest_dirty

            mark_bucket_manifest_dirty("bucket_security_run")
        except Exception:
            pass

    return {
        "status": "ok",
        "configured_tools": configured,
        "filtering_configured": True,
        "total": len(objects),
        "filtered": filtered,
        "already_filtered": already_filtered,
        "still_blocked": still_blocked,
        "failed": failed,
        "trace_ids": changed_ids,
    }
