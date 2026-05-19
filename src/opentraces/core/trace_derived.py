"""Derive legacy TraceRecord fields from the authoritative ``patches[]`` spine.

Plan 080 Resolution C — ``Outcome.committed``, ``Outcome.commit_sha``, and
``TraceRecord.git_links`` are derived projections of ``TraceRecord.patches[]``
at construction time. ``patches[]`` is the new authoritative cross-substrate
reference; this module is the single source of truth for the projection.

Callers:

* Track 2 (post-commit hook) calls :func:`derive_outcome_and_git_links_from_patches`
  after updating each ``Patch.anchor`` so the on-disk JSONL stays consistent.
* Track 3 (ingest pipeline) calls it at TraceRecord construction time so traces
  have valid derived fields even before the post-commit hook fires.

Import discipline: this module must NOT import from ``core/pipeline.py``,
``core/ingest.py``, or ``capture/*`` — those callers depend on us, not the
other way around. ``opentraces_schema`` is the only allowed dependency.
"""

from __future__ import annotations

from typing import Mapping

from opentraces_schema import GitLink, TraceRecord

# Plan 080 Resolution C: deterministic projection from the 10-value Trails
# ``evidence_tier`` vocabulary onto the 4-value ``GitLink.tier`` vocabulary
# used by reader sites (trace_index, inverse_blame, viewer). Frozen at v1;
# any new evidence_tier value must update this table explicitly.
_EVIDENCE_TIER_TO_GITLINK_TIER: Mapping[str, str] = {
    "exact_blob_hash": "tool_emitted",
    "exact_range_hash": "tool_emitted",
    "patch_id": "tool_emitted",
    "git_clean_range_hash": "tool_emitted_with_divergence",
    "structural_symbol": "tool_emitted_with_divergence",
    "formatter_divergent": "tool_emitted_with_divergence",
    "overlapping_hunk": "overlapping",
    "time_file_overlap": "overlapping",
    "orphan": "orphan",
    "unknown": "orphan",  # conservative fallback
}


def evidence_tier_to_gitlink_tier(evidence_tier: str | None) -> str:
    """Project a Trails evidence_tier onto the 4-value GitLink.tier vocab.

    Conservative fallback: unknown/missing values project to ``orphan`` so
    callers never see an unmapped value leak into ``git_links``.
    """
    if evidence_tier is None:
        return "orphan"
    return _EVIDENCE_TIER_TO_GITLINK_TIER.get(evidence_tier, "orphan")


# Inverse mapping for callers that have a GitLink.tier (4-value correlator
# vocabulary) and need a canonical evidence_tier (10-value Trails vocabulary)
# to stamp onto ``Patch.anchor.evidence_tier``. Picks the most representative
# value of each equivalence class so the round trip
# ``evidence_tier_to_gitlink_tier(gitlink_tier_to_evidence_tier(t)) == t``
# holds for all 4 tiers in the projection table.
_GITLINK_TIER_TO_EVIDENCE_TIER: Mapping[str, str] = {
    "tool_emitted": "exact_range_hash",
    "tool_emitted_with_divergence": "formatter_divergent",
    "overlapping": "overlapping_hunk",
    "orphan": "orphan",
}


def gitlink_tier_to_evidence_tier(tier: str | None) -> str:
    """Inverse of :func:`evidence_tier_to_gitlink_tier` (canonical representative).

    Lossy in the forward direction (10 → 4) but stable in the inverse: callers
    using this to populate ``Patch.anchor.evidence_tier`` from a correlator
    result get a value that projects back to the original GitLink.tier.
    """
    if tier is None:
        return "unknown"
    return _GITLINK_TIER_TO_EVIDENCE_TIER.get(tier, "unknown")


def derive_outcome_and_git_links_from_patches(record: TraceRecord) -> None:
    """Mutate ``record`` so derived fields agree with ``record.patches[]``.

    Sets, in-place:

    * ``record.outcome.committed`` — True iff any patch has ``anchor.found``.
    * ``record.outcome.commit_sha`` — commit_sha of the most-recently-anchored
      patch (latest by ``anchor.last_searched_at``); None when no patch is
      anchored.
    * ``record.git_links`` — one GitLink per (patch, commit_sha) pair across
      ``anchor.commit_sha`` + ``superseded_by``, deduped by (revision, tier).
      Empty when no patch is anchored.

    Idempotent. Safe to call repeatedly on the same record. No-op when
    ``record.patches`` is empty (legacy traces predating plan 080).
    """
    patches = record.patches or []

    # outcome.committed / commit_sha — pick the latest-anchored patch.
    anchored = [p for p in patches if p.anchor is not None and p.anchor.found]
    if anchored:
        record.outcome.committed = True
        # Sort by last_searched_at descending (string ISO8601 comparison is
        # correct for tz-aware UTC timestamps that share the same format).
        latest = max(anchored, key=lambda p: p.anchor.last_searched_at)
        record.outcome.commit_sha = latest.anchor.commit_sha
    else:
        record.outcome.committed = False
        record.outcome.commit_sha = None

    # git_links — project every patch's anchor + superseded_by chain.
    links: list[GitLink] = []
    seen: set[tuple[str, str]] = set()
    for patch in patches:
        anchor = patch.anchor
        if anchor is None or not anchor.found:
            continue
        tier = evidence_tier_to_gitlink_tier(anchor.evidence_tier)
        # Chain: current commit_sha (newest) + historical supersede chain.
        chain: list[str] = []
        if anchor.commit_sha:
            chain.append(anchor.commit_sha)
        for sha in patch.superseded_by or []:
            if sha and sha not in chain:
                chain.append(sha)
        for revision in chain:
            key = (revision, tier)
            if key in seen:
                continue
            seen.add(key)
            links.append(GitLink(
                vcs_type="git",
                revision=revision,
                tier=tier,
            ))

    record.git_links = links
