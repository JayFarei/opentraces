"""Dataset row privacy filtering."""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any

from .anonymizer import anonymize_paths
from .privacy import DEFAULT_PRIVACY_TIER, PrivacyTier, normalize_privacy_tier, privacy_policy_for_tier
from .secrets import redact_text, scan_text
from .version import SECURITY_VERSION


@dataclass(frozen=True)
class DatasetRowSecurity:
    """Security sidecar for one composed dataset row."""

    privacy_tier: PrivacyTier
    security_version: str | None
    redactions_applied: int = 0
    findings_count: int = 0
    filtered: bool = True


@dataclass(frozen=True)
class SanitizedDatasetRow:
    """A row plus the security state produced while filtering it."""

    row: dict[str, Any]
    security: DatasetRowSecurity


def sanitize_dataset_row(
    row: dict[str, Any],
    *,
    privacy_tier: str | None = DEFAULT_PRIVACY_TIER,
) -> SanitizedDatasetRow:
    """Return a privacy-filtered copy of a dataset row.

    ``off`` is an explicit deferral mode: the row is copied unchanged and
    sidecar metadata marks it unfiltered/non-publishable. Other tiers map to
    the local scanner/anonymizer controls.
    """

    tier = normalize_privacy_tier(privacy_tier)
    policy = privacy_policy_for_tier(tier)
    copied = copy.deepcopy(row)
    if not policy.filters_enabled:
        return SanitizedDatasetRow(
            row=copied,
            security=DatasetRowSecurity(
                privacy_tier=tier,
                security_version=None,
                filtered=False,
            ),
        )

    redactions = 0
    findings = 0
    username = os.environ.get("USER") or os.environ.get("USERNAME") or None

    def _filter(value: Any) -> Any:
        nonlocal redactions, findings
        if isinstance(value, str):
            matches = scan_text(value, include_entropy=policy.include_entropy)
            if matches:
                findings += len(matches)
                value = redact_text(value, matches)
                redactions += len(matches)
            if policy.anonymize_sources:
                value = anonymize_paths(value, username=username)
            return value
        if isinstance(value, list):
            return [_filter(item) for item in value]
        if isinstance(value, dict):
            return {key: _filter(item) for key, item in value.items()}
        return value

    filtered = _filter(copied)
    if not isinstance(filtered, dict):  # Defensive; callers validate rows as objects.
        filtered = copied
    return SanitizedDatasetRow(
        row=filtered,
        security=DatasetRowSecurity(
            privacy_tier=tier,
            security_version=SECURITY_VERSION,
            redactions_applied=redactions,
            findings_count=findings,
            filtered=True,
        ),
    )
