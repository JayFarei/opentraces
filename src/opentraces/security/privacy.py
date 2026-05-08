"""Privacy tier policy shared by ingest, bucket, and dataset composition."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from opentraces_schema import TraceRecord

from .version import SECURITY_VERSION

PrivacyTier = Literal["off", "low", "medium", "high"]

DEFAULT_PRIVACY_TIER: PrivacyTier = "medium"
PRIVACY_TIERS: tuple[PrivacyTier, ...] = ("off", "low", "medium", "high")
FILTERED_PRIVACY_TIERS: tuple[PrivacyTier, ...] = ("low", "medium", "high")


@dataclass(frozen=True)
class PrivacyPolicy:
    """Resolved behavior for a privacy tier."""

    tier: PrivacyTier
    filters_enabled: bool
    include_entropy: bool
    run_trufflehog: bool
    classifier_sensitivity: Literal["low", "medium", "high"]
    anonymize_sources: bool
    llm_review_required: bool = False


def normalize_privacy_tier(
    value: str | None,
    *,
    default: PrivacyTier = DEFAULT_PRIVACY_TIER,
) -> PrivacyTier:
    """Return a supported privacy tier, using ``default`` for empty values."""

    if value is None or str(value).strip() == "":
        return default
    normalized = str(value).strip().lower().replace("_", "-")
    if normalized not in PRIVACY_TIERS:
        raise ValueError(
            f"unsupported privacy tier: {value!r} "
            f"(expected one of {', '.join(PRIVACY_TIERS)})"
        )
    return normalized  # type: ignore[return-value]


def privacy_policy_for_tier(
    tier: str | None,
    *,
    classifier_sensitivity: str = "medium",
) -> PrivacyPolicy:
    """Resolve a tier into concrete scanner/anonymizer knobs.

    The current implementation maps low/medium/high onto existing local
    controls. Future OPF/Viterbi integration can swap the internals while
    keeping this contract stable.
    """

    resolved = normalize_privacy_tier(tier)
    if resolved == "off":
        return PrivacyPolicy(
            tier="off",
            filters_enabled=False,
            include_entropy=False,
            run_trufflehog=False,
            classifier_sensitivity="low",
            anonymize_sources=False,
        )
    if resolved == "low":
        return PrivacyPolicy(
            tier="low",
            filters_enabled=True,
            include_entropy=False,
            run_trufflehog=False,
            classifier_sensitivity="low",
            anonymize_sources=True,
        )
    if resolved == "high":
        return PrivacyPolicy(
            tier="high",
            filters_enabled=True,
            include_entropy=True,
            run_trufflehog=True,
            classifier_sensitivity="high",
            anonymize_sources=True,
            llm_review_required=True,
        )

    sensitivity = classifier_sensitivity if classifier_sensitivity in {"low", "medium", "high"} else "medium"
    return PrivacyPolicy(
        tier="medium",
        filters_enabled=True,
        include_entropy=True,
        run_trufflehog=True,
        classifier_sensitivity=sensitivity,  # type: ignore[arg-type]
        anonymize_sources=True,
    )


def record_privacy_tier(record: TraceRecord) -> PrivacyTier | None:
    """Return the explicit privacy tier stamped on a record, if present."""

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


def mark_record_privacy(
    record: TraceRecord,
    tier: str | None,
    *,
    redactions_applied: int | None = None,
) -> None:
    """Stamp record-local privacy policy metadata without changing schema fields."""

    resolved = normalize_privacy_tier(tier)
    filtered = resolved in FILTERED_PRIVACY_TIERS and bool(record.security.scanned)
    security_version = record.security.classifier_version
    stale = bool(security_version and security_version != SECURITY_VERSION)
    syncable = bool(filtered and security_version == SECURITY_VERSION)

    meta = record.metadata.setdefault("security", {})
    if not isinstance(meta, dict):
        meta = {}
        record.metadata["security"] = meta
    meta["privacy"] = {
        "privacy_tier": resolved,
        "security_version": security_version,
        "filtered": filtered,
        "syncable": syncable,
        "stale": stale,
        "redactions_applied": (
            record.security.redactions_applied
            if redactions_applied is None
            else redactions_applied
        ),
    }


def bucket_security_state(
    record: TraceRecord,
    *,
    privacy_tier: str | None = None,
) -> dict[str, Any]:
    """Build the bucket envelope security state for one record."""

    explicit_tier = normalize_privacy_tier(
        privacy_tier,
        default=record_privacy_tier(record)
        or (DEFAULT_PRIVACY_TIER if record.security.scanned else "off"),
    )
    security_version = record.security.classifier_version
    filtered = explicit_tier in FILTERED_PRIVACY_TIERS and bool(record.security.scanned)
    stale = bool(security_version and security_version != SECURITY_VERSION)
    if filtered and not security_version:
        stale = True
    syncable = bool(filtered and not stale)
    return {
        "privacy_tier": explicit_tier,
        "security_version": security_version,
        "current_security_version": SECURITY_VERSION,
        "scanned": bool(record.security.scanned),
        "redactions_applied": int(record.security.redactions_applied or 0),
        "flags_reviewed": int(record.security.flags_reviewed or 0),
        "filtered": filtered,
        "stale": stale,
        "syncable": syncable,
    }
