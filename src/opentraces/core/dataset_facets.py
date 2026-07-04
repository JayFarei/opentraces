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
verbs. Only names resolvable against the manifest row are supported here;
anything else would silently require opening every trace to answer, which is
the wrong-scoping risk this module exists to avoid -- an unsupported name is a
loud ``ValueError``, never a silent no-op.
"""

from __future__ import annotations

from typing import Any

# Facet name -> manifest row field. Every field here is already resident on
# every ``bucket/manifest.json`` "traces[]" row (see
# ``core/bucket_envelope.py::_iter_traces_v2`` / the per-trace summary dict).
FACET_MANIFEST_FIELDS: dict[str, str] = {
    "model": "agent_model",
    "agent.name": "agent_name",
    "agent.version": "agent_version",
}


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
    manifest -- an honest failure instead of a silent no-match."""

    unknown = sorted(set(facets) - set(FACET_MANIFEST_FIELDS))
    if unknown:
        supported = ", ".join(sorted(FACET_MANIFEST_FIELDS))
        raise ValueError(
            "unsupported --facet name(s): "
            + ", ".join(unknown)
            + f" (supported against the persisted manifest: {supported})"
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

    O(manifest): reads the persisted ``bucket/manifest.json`` exactly once
    (falling back to a live in-memory recompute only when no usable persisted
    manifest exists yet -- the same degrade ``source_provenance_for_query``
    uses). Never opens a per-trace ``trace.json`` / ``current.json``.

    ``project_slug`` / ``trace_id`` narrow the row set first (mirroring
    ``--scope project`` / ``--scope trace``), matching the existing
    "facets compose with project/cwd" contract. Empty ``facets`` returns an
    empty list -- callers only invoke this when a facet predicate is present.
    """

    if not facets:
        return []
    validate_facet_names(facets)

    from .bucket_store import bucket_manifest, read_persisted_manifest_capped

    state, manifest = read_persisted_manifest_capped()
    if state != "ok" or not isinstance(manifest, dict):
        manifest = bucket_manifest(write=False, include_objects=False)

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
