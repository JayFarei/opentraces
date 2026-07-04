"""Canonical facet-name vocabulary shared between the dataset-run facet
scoping seam (``core/dataset_facets.py``) and the Trace Index facet emitters
(``core/trace_index.py``) -- issue #212 external review fix (finding 4).

``trace query --facet`` accepts the FULL open-ended vocabulary of whatever
facet name any Trace Index emitter has ever produced -- there is no allow-list
there; see ``core/trace_index.py::_matches_facet_filters``, which matches
literally any ``TraceFacet.name`` a unit carries. ``dataset run --facet`` is
deliberately narrower: it only supports the subset of that vocabulary that is
ALSO resolvable at O(manifest) cost against the persisted
``bucket/manifest.json`` row (``core/bucket_envelope.py``'s per-trace
summary), because resolving anything else would require opening every
trace's ``trace.json`` / ``current.json`` -- exactly the #87 anti-pattern
this family exists to avoid.

This module is the single source of truth for those three facet names, so
``dataset_facets.py``'s allow-list and ``trace_index.py``'s skill-invocation
facet emitter share the same string constants instead of two hand-synced
copies that can silently drift apart.
"""

from __future__ import annotations

FACET_MODEL = "model"
FACET_AGENT_NAME = "agent.name"
FACET_AGENT_VERSION = "agent.version"

# Facet name -> persisted bucket-manifest row field. Every field here is
# already resident on every ``bucket/manifest.json`` "traces[]" row, so
# resolving against it never opens a per-trace file regardless of corpus
# size (see ``core/dataset_facets.py::resolve_facet_candidates``).
MANIFEST_RESOLVABLE_FACETS: dict[str, str] = {
    FACET_MODEL: "agent_model",
    FACET_AGENT_NAME: "agent_name",
    FACET_AGENT_VERSION: "agent_version",
}
