"""Record-level privacy/security metadata helpers.

The tool registry in :mod:`.tools` is the single source of truth for *what*
runs over each record. The ``PrivacyTier`` vocabulary (``off`` / ``low`` /
``medium`` / ``high``) survives only as a labelled enum for the
dataset-publishing surface (``DatasetRowSecurity.privacy_tier``, bucket
envelope ``privacy_tier``), where it serves as a coarse shareable /
filtered / unfiltered shorthand. ``off`` is the default; named tools are
authoritative for what actually ran.
"""

from __future__ import annotations

from typing import Any, Literal

from opentraces_schema import TraceRecord

from .version import SECURITY_VERSION
from .walker import ensure_security_metadata

PrivacyTier = Literal["off", "low", "medium", "high"]

DEFAULT_PRIVACY_TIER: PrivacyTier = "off"
PRIVACY_TIERS: tuple[PrivacyTier, ...] = ("off", "low", "medium", "high")


def normalize_privacy_tier(
    value: str | None,
    *,
    default: PrivacyTier = DEFAULT_PRIVACY_TIER,
) -> PrivacyTier:
    """Return a supported privacy tier label, using ``default`` for empty values."""
    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower().replace("_", "-")
    if normalized not in PRIVACY_TIERS:
        raise ValueError(
            f"unsupported privacy tier: {value!r} "
            f"(expected one of {', '.join(PRIVACY_TIERS)})"
        )
    return normalized  # type: ignore[return-value]


def record_privacy_tier(record: TraceRecord) -> PrivacyTier | None:
    """Return the privacy-tier label stamped on a record, if present."""
    meta = record.metadata.get("security")
    if not isinstance(meta, dict):
        return None
    privacy = meta.get("privacy")
    if not isinstance(privacy, dict):
        return None
    tier = privacy.get("privacy_tier")
    if not isinstance(tier, str):
        return None
    try:
        return normalize_privacy_tier(tier)
    except ValueError:
        return None


def record_tools_applied(record: TraceRecord) -> list[str]:
    """Return ``metadata.security.tools_applied`` if present, else ``[]``."""
    meta = record.metadata.get("security")
    if not isinstance(meta, dict):
        return []
    val = meta.get("tools_applied")
    if not isinstance(val, list):
        return []
    return [str(x) for x in val if isinstance(x, str)]


def mark_record_tools_applied(
    record: TraceRecord,
    tool_names: list[str] | None,
) -> None:
    """Stamp ``metadata.security.tools_applied`` on ``record``."""
    meta = ensure_security_metadata(record)
    names = list(tool_names or [])
    meta["tools_applied"] = names
    if not names:
        meta["tools"] = {}


def bucket_security_state(
    record: TraceRecord,
    *,
    privacy_tier: str | None = None,
) -> dict[str, Any]:
    """Build the bucket envelope security state for one record.

    ``privacy_tier`` is accepted for back-compat and reflected into the
    envelope's legacy ``privacy_tier`` key (so existing HF dataset readers
    still see it). The authoritative state lives in ``tools_applied``.
    """
    explicit_tier: PrivacyTier
    legacy_scanned = (
        bool(record.security.scanned)
        and bool(record.security.classifier_version)
        and record.security.classifier_version == SECURITY_VERSION
    )
    try:
        explicit_tier = normalize_privacy_tier(
            privacy_tier,
            default=record_privacy_tier(record)
            or ("medium" if legacy_scanned else DEFAULT_PRIVACY_TIER),
        )
    except ValueError:
        explicit_tier = DEFAULT_PRIVACY_TIER

    applied = record_tools_applied(record)
    security_version = record.security.classifier_version
    filtered = (bool(applied) or explicit_tier != "off") and bool(record.security.scanned)
    stale = bool(security_version and security_version != SECURITY_VERSION)
    if filtered and not security_version:
        stale = True
    syncable = bool(filtered and not stale)
    return {
        "privacy_tier": explicit_tier,
        "tools_applied": applied,
        "security_version": security_version,
        "current_security_version": SECURITY_VERSION,
        "scanned": bool(record.security.scanned),
        "redactions_applied": int(record.security.redactions_applied or 0),
        "flags_reviewed": int(record.security.flags_reviewed or 0),
        "filtered": filtered,
        "stale": stale,
        "syncable": syncable,
    }
