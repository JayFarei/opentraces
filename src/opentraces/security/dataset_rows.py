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

# Non-overridable reader's floor (issue #84). These tools run over EVERY
# projected dataset row regardless of the workflow author's declared security
# contract: the redaction rules that run are the reader's, not the author's.
# A workflow contract may only ADD tools; it can never narrow a row below this
# floor, and the default ``tier="off"`` no longer ships rows verbatim.
#
# Mirrors capsule's ``REDACTION_FLOOR = (regex, entropy, business_logic)``
# (core/capsule/redaction.py) and extends it with ``path_anonymizer`` to scrub
# username / home-path PII that a prefix-regex + entropy floor structurally
# misses. ``business_logic`` covers internal hostnames / collab-tool URLs / DB
# connection strings / AWS account ids the publish gate does NOT scan for. All
# four are members of ``DATASET_ROW_TOOLS`` (a test asserts the invariant), and
# the floor is a superset of the strongest tier mapping (no-drift guard).
DATASET_ROW_FLOOR: tuple[str, ...] = (
    "regex",
    "entropy",
    "business_logic",
    "path_anonymizer",
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
    """Security sidecar for one composed dataset row.

    ``requested_tools`` is the author-declared / tier-derived input (what the
    workflow contract asked for); ``effective_tools`` is the floor-resolved set
    that actually governs sanitization (``union(DATASET_ROW_FLOOR, requested)``).
    The author can only ever ADD to the floor — the distinction makes that
    auditable (mirrors capsule's ``floor`` / ``floor_satisfied`` manifest).
    """

    privacy_tier: PrivacyTier
    security_version: str | None
    redactions_applied: int = 0
    findings_count: int = 0
    filtered: bool = True
    tools_applied: tuple[str, ...] = ()
    requested_tools: tuple[str, ...] = ()
    effective_tools: tuple[str, ...] = ()
    floor: tuple[str, ...] = DATASET_ROW_FLOOR
    floor_satisfied: bool = False


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

    ``tools`` (when given) is the dataset's resolved security policy — the
    author's workflow contract. It is *additive*: the non-overridable reader's
    floor (:data:`DATASET_ROW_FLOOR`) is ALWAYS unioned in as the LAST step, so
    a narrowing contract (``tools=["regex"]``) or the default ``tier="off"``
    can never ship a row below the floor (issue #84). ``requested`` captures the
    author/tier input distinctly from the floor-resolved ``effective_tools`` so
    the "author may only add" guarantee is auditable.

    The floor is unioned AFTER any override/disable resolution upstream (the
    author-controlled ``allow_disable_required`` lives in the policy that seeds
    ``tools``); because the union is the final step here, no policy flag can
    remove a floor tool — non-disableable by construction (codex finding #2).
    """
    tier = normalize_privacy_tier(privacy_tier)
    if tools is not None:
        requested = [t for t in dict.fromkeys(tools) if t]
    elif tier == "off":
        requested = []
    else:
        requested = list(_TIER_TOOLS.get(tier, ("regex", "entropy")))

    # The floor is unioned in LAST and canonically — author tools can only add.
    effective_tools = [
        t for t in dict.fromkeys((*DATASET_ROW_FLOOR, *requested)) if t
    ]
    floor_satisfied = set(DATASET_ROW_FLOOR).issubset(effective_tools)

    # sanitize_dict only runs Detector-protocol tools; path_anonymizer is a
    # transformer applied by the explicit _anon step below. Both are driven off
    # ``effective_tools`` (which always contains the floor).
    sanitised, report = sanitize_dict(row, tools=effective_tools)

    username = os.environ.get("USER") or os.environ.get("USERNAME") or None

    applied = list(report.tools_applied)
    if "path_anonymizer" in effective_tools:
        def _anon(text: str, _path: str, _ft) -> str:
            return anonymize_paths(text, username=username)

        sanitised, _ = walk_dict_strings(sanitised, _anon)
        if "path_anonymizer" not in applied:
            applied.append("path_anonymizer")
    if not isinstance(sanitised, dict):  # defensive
        sanitised = dict(row)

    return SanitizedDatasetRow(
        row=sanitised,
        security=DatasetRowSecurity(
            privacy_tier=tier,
            security_version=SECURITY_VERSION,
            redactions_applied=report.redactions_applied,
            findings_count=len(report.findings),
            filtered=True,
            tools_applied=tuple(applied),
            requested_tools=tuple(requested),
            effective_tools=tuple(effective_tools),
            floor=DATASET_ROW_FLOOR,
            floor_satisfied=floor_satisfied,
        ),
    )
