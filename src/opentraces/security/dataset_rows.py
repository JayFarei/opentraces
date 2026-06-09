"""Dataset row privacy filtering.

Backwards-compatible wrapper over :func:`opentraces.security.sanitize_dict`.
The tier label (``off``/``low``/``medium``/``high``) survives here as a
coarse shareable/unfiltered shorthand for the dataset-publishing surface;
the actual sanitization is the new tool-registry pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .anonymizer import anonymize_paths
from .pipeline import sanitize_dict
from .privacy import DEFAULT_PRIVACY_TIER, PrivacyTier, normalize_privacy_tier
from .version import SECURITY_VERSION
from .walker import walk_dict_strings

# Security tools that can actually sanitize a projected dataset-row dict: the
# detector-protocol tools `sanitize_dict` executes over string leaves, plus
# `path_anonymizer` which the unconditional anonymize_paths step below applies.
# The remaining registry tools operate on TraceRecord structure and cannot run
# over a row: trufflehog / llm_pii are apply-only detectors (no `find`),
# capsule_scope is a record field-exclusion transformer, classifier is a
# whole-record judge. A dataset security contract may only reference this set.
DATASET_ROW_TOOLS: frozenset[str] = frozenset(
    {"regex", "entropy", "privacy_filter", "business_logic", "path_anonymizer"}
)


def unsupported_dataset_row_tools(names: "list[str] | tuple[str, ...]") -> list[str]:
    """Return any tool names that cannot run over a dataset row (order-stable)."""
    seen: list[str] = []
    for name in names:
        if name and name not in DATASET_ROW_TOOLS and name not in seen:
            seen.append(name)
    return seen


@dataclass(frozen=True)
class DatasetRowSecurity:
    """Security sidecar for one composed dataset row."""

    privacy_tier: PrivacyTier
    security_version: str | None
    redactions_applied: int = 0
    findings_count: int = 0
    filtered: bool = True
    tools_applied: tuple[str, ...] = ()


@dataclass(frozen=True)
class SanitizedDatasetRow:
    """A row plus the security state produced while filtering it."""

    row: dict[str, Any]
    security: DatasetRowSecurity


_TIER_TOOLS: dict[PrivacyTier, tuple[str, ...]] = {
    "low": ("regex",),
    "medium": ("regex", "entropy"),
    "high": ("regex", "entropy"),
}


def sanitize_dataset_row(
    row: dict[str, Any],
    *,
    privacy_tier: str | None = DEFAULT_PRIVACY_TIER,
    tools: list[str] | tuple[str, ...] | None = None,
) -> SanitizedDatasetRow:
    """Return a sanitised copy of a dataset row.

    When ``tools`` is provided (the dataset's resolved security policy), it is
    authoritative: exactly those registry tools run, regardless of the privacy
    tier, so a workflow's required tools cannot be silently dropped. When
    ``tools`` is ``None`` the coarse tier mapping is used: ``tier="off"`` ships
    the row unchanged, other tiers map onto a small fixed tool set.
    """
    tier = normalize_privacy_tier(privacy_tier)
    if tools is not None:
        effective_tools = [t for t in dict.fromkeys(tools) if t]
    elif tier == "off":
        effective_tools = []
    else:
        effective_tools = list(_TIER_TOOLS.get(tier, ("regex", "entropy")))

    if not effective_tools:
        # No tools run: explicit deferral (tier off) or an empty policy.
        return SanitizedDatasetRow(
            row=dict(row),
            security=DatasetRowSecurity(
                privacy_tier=tier,
                security_version=None,
                filtered=False,
                tools_applied=(),
            ),
        )

    # sanitize_dict + walk_dict_strings rebuild containers only where a leaf
    # changed and never mutate the input, so no upfront copy is needed.
    sanitised, report = sanitize_dict(row, tools=effective_tools)

    username = os.environ.get("USER") or os.environ.get("USERNAME") or None

    def _anon(text: str, _path: str, _ft) -> str:
        return anonymize_paths(text, username=username)

    sanitised, _ = walk_dict_strings(sanitised, _anon)
    if not isinstance(sanitised, dict):  # defensive
        sanitised = dict(row)

    # Record the tools that ACTUALLY executed: the detector-protocol tools
    # sanitize_dict ran, plus path_anonymizer (the unconditional _anon step
    # above). A workflow may list tools that cannot run over a row dict, so this
    # is the truthful execution evidence the publish gate checks against.
    applied = list(report.tools_applied)
    if "path_anonymizer" not in applied:
        applied.append("path_anonymizer")

    return SanitizedDatasetRow(
        row=sanitised,
        security=DatasetRowSecurity(
            privacy_tier=tier,
            security_version=SECURITY_VERSION,
            redactions_applied=report.redactions_applied,
            findings_count=len(report.findings),
            filtered=True,
            tools_applied=tuple(applied),
        ),
    )
