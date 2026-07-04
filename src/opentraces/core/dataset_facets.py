"""Facet-based dataset candidate scoping (issue #212).

Scoping a dataset run by model / harness / harness-version composes with the
existing ``project`` / ``trace`` scope narrowing via a ``facets: {name:
value}`` predicate resolved ENTIRELY against the persisted
``bucket/manifest.json``. Every field this filters on
(``agent_name`` / ``agent_version`` / ``agent_model``) is already on every
manifest row (``core/bucket_envelope.py``'s per-trace summary), so resolution
never opens a per-trace ``trace.json`` / ``current.json`` -- doing that would
be exactly the #87 anti-pattern this family already cured for reads.

The facet name vocabulary intentionally reuses the ``TraceFacet`` names
``trace query --facet`` already exposes (``model``, ``agent.name``,
``agent.version``) so the same mental model and CLI syntax works across both
verbs -- but ``trace query --facet`` accepts that FULL open-ended emitted
vocabulary while this module only supports the narrow subset resolvable at
O(manifest) cost (external review of issue #212, finding 4). Both this
module's allow-list and the ``trace_index.py`` facet emitters that produce
these three names import the same constants from ``core/facet_registry.py``
so the two surfaces cannot silently drift apart. Only names resolvable
against the manifest row are supported here; anything else would silently
require opening every trace to answer, which is the wrong-scoping risk this
module exists to avoid -- an unsupported name is a loud ``ValueError`` that
says WHY (not just that it's unsupported) and points at ``trace query
--facet`` for the full vocabulary, never a silent no-op.
"""

from __future__ import annotations

from typing import Any

from .facet_registry import MANIFEST_RESOLVABLE_FACETS as FACET_MANIFEST_FIELDS


class FacetResolutionUnavailableError(ValueError):
    """Raised when facet resolution has no usable persisted manifest to read.

    Facet resolution is contractually O(manifest): it must NEVER recompute
    the manifest by scanning every trace's ``current.json``/``trace.json``
    (external review of issue #212, finding 2 -- the exact #87 anti-pattern
    this family exists to cure). When the persisted ``bucket/manifest.json``
    is absent, too large to read safely, or unreadable/malformed, resolution
    fails LOUDLY with a maintenance hint instead of silently degrading into a
    full corpus scan. A subclass of ``ValueError`` so existing callers that
    already catch ``ValueError`` around facet resolution (the CLI's
    ``dataset run`` error handling) surface it without new plumbing.
    """

    def __init__(self, state: str) -> None:
        reason = {
            "absent": "no persisted bucket/manifest.json exists yet",
            "too_large": (
                "the persisted bucket/manifest.json exceeds the safe read cap"
            ),
            "error": "the persisted bucket/manifest.json is unreadable or malformed",
        }.get(state, f"persisted manifest state={state!r}")
        message = (
            "facet-scoped resolution requires a persisted bucket manifest, but "
            f"{reason}. Run `ot bucket repair` (or `ot bucket manifest --heal` "
            "for a lighter sweep) to persist bucket/manifest.json, then retry."
        )
        super().__init__(message)
        self.state = state


def parse_facet_filters(items: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated ``--facet name=value`` CLI args into an ordered dict.

    Mirrors ``trace query --facet``'s ``name=value`` syntax so the same
    filter reads identically across both verbs. A later duplicate name
    overwrites an earlier one (last wins), matching how repeatable Click
    options are conventionally folded.
    """

    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"--facet must use name=value syntax, got: {item!r}")
        name, value = item.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name or not value:
            raise ValueError(f"--facet must use name=value syntax, got: {item!r}")
        parsed[name] = value
    return parsed


def validate_facet_names(facets: dict[str, str]) -> None:
    """Raise ``ValueError`` for any facet name not resolvable against the
    manifest -- an honest failure instead of a silent no-match.

    The error explains WHY the name is rejected (not resolvable at O(manifest)
    cost -- resolving it would require opening every trace) and points at
    ``trace query --facet``, which accepts the full emitted facet vocabulary
    with no such restriction (external review of issue #212, finding 4).
    """

    unknown = sorted(set(facets) - set(FACET_MANIFEST_FIELDS))
    if unknown:
        supported = ", ".join(sorted(FACET_MANIFEST_FIELDS))
        raise ValueError(
            "unsupported --facet name(s): "
            + ", ".join(unknown)
            + " -- not resolvable at O(manifest) cost (dataset run --facet "
            f"only supports: {supported}). Use `ot trace query --facet` for "
            "the full facet vocabulary."
        )


def _row_matches_facets(row: dict[str, Any], facets: dict[str, str]) -> bool:
    for name, expected in facets.items():
        field = FACET_MANIFEST_FIELDS[name]
        actual = row.get(field)
        if actual is None:
            return False
        if str(actual).lower() != str(expected).lower():
            return False
    return True


def resolve_facet_candidates(
    facets: dict[str, str],
    *,
    project_slug: str | None = None,
    trace_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the manifest rows matching ``facets`` (case-insensitive).

    O(manifest): reads the persisted ``bucket/manifest.json`` exactly once.
    Never opens a per-trace ``trace.json`` / ``current.json``, and NEVER
    recomputes the manifest as a fallback (external review of issue #212,
    finding 2) -- an absent, oversized, or unreadable persisted manifest
    raises :class:`FacetResolutionUnavailableError` instead of silently
    degrading into the exact #87 anti-pattern (a full per-trace scan) this
    family exists to cure. Callers see a clear maintenance error pointing at
    the manifest-sweep command, not a multi-minute hang on a large bucket.

    ``project_slug`` / ``trace_id`` narrow the row set first (mirroring
    ``--scope project`` / ``--scope trace``), matching the existing
    "facets compose with project/cwd" contract. Empty ``facets`` returns an
    empty list -- callers only invoke this when a facet predicate is present.
    """

    if not facets:
        return []
    validate_facet_names(facets)

    from .bucket_store import read_persisted_manifest_capped

    state, manifest = read_persisted_manifest_capped()
    if state != "ok" or not isinstance(manifest, dict):
        raise FacetResolutionUnavailableError(state)

    rows = [row for row in (manifest.get("traces") or []) if isinstance(row, dict)]
    matches: list[dict[str, Any]] = []
    for row in rows:
        if project_slug and row.get("project_slug") != project_slug:
            continue
        if trace_id and row.get("trace_id") != trace_id:
            continue
        if _row_matches_facets(row, facets):
            matches.append(
                {
                    "project_slug": row.get("project_slug"),
                    "trace_id": row.get("trace_id"),
                    "agent_name": row.get("agent_name"),
                    "agent_version": row.get("agent_version"),
                    "agent_model": row.get("agent_model"),
                }
            )
    matches.sort(key=lambda item: (item["project_slug"] or "", item["trace_id"] or ""))
    return matches


def merge_facet_scopes(
    persisted_facets: dict[str, str] | None,
    runtime_facets: dict[str, str] | None,
) -> dict[str, str]:
    """Merge a dataset's persisted ``candidate_query.facets`` with a run's
    runtime ``--facet`` flags into the one effective scope (external review of
    issue #212, finding 1a).

    This is the canonical merge point: ``run_dataset_workflow`` must call this
    instead of reading only ``scope["facets"]``, so a facet scope persisted at
    ``dataset new`` / schedule time (with no CLI flags at run time, e.g. a
    scheduled run) is honored exactly like an ad-hoc ``--facet`` flag. Runtime
    flags win on a per-key conflict (last-wins, matching
    :func:`parse_facet_filters`'s repeated-flag semantics) since they are the
    caller's explicit, most-specific request for this one run.
    """

    merged = dict(persisted_facets or {})
    if runtime_facets:
        merged.update(runtime_facets)
    return merged


def candidate_trace_id_set(packet: dict[str, Any]) -> set[str] | None:
    """Return the resolved candidate trace-id set from a run packet, or
    ``None`` when the run carried no facet scope (every candidate is in
    scope).

    Shared helper for bundled/consumer workflow ``build_rows.py`` scripts
    (external review of issue #212, finding 1b): a script that scans the
    bucket directly (instead of trusting an externally-supplied row stream)
    must still narrow to the facet-resolved set when the run packet carries
    one, rather than silently ignoring it and emitting every trace it can
    see.
    """

    candidate_trace_ids = packet.get("candidate_trace_ids")
    if candidate_trace_ids is None:
        return None
    return {
        str(entry["trace_id"])
        for entry in candidate_trace_ids
        if isinstance(entry, dict) and entry.get("trace_id")
    }
