"""Sanctioned bounded entrypoints for reading the canonical event log.

The canonical Trace Trails / Context Tree event log
(``refs/opentraces/local/events/v1``) grows without bound — ~983K git
objects on a mature repo. Every read verb MUST therefore resolve its
selector (a commit-sha set, a ``trace_id``, a ``node_id``, or an
event-type slice) to a SCOPED read *before* building any projection.
This is the "predicates-before-projection" contract; violating it
re-admits the whole-log walk that made ``ctx resume`` / ``trail
resolve`` / ``bucket manifest`` hang (issues #87 / #110 / #120 / #121).

This module is a thin re-export seam over the primitives that already
implement the contract. It exists so a migrated caller can import ONE
place and so the whole-log primitives stay clearly labelled as
internal-only.

Sanctioned bounded entrypoints (import these from callers):

* :func:`read_events_scoped` — read only events of the given
  ``event_types`` (OID-indexed; a raw-bytes prefilter fallback). The
  result is byte-identical to a filtered full scan, just bounded.
* :func:`read_events_for_trace` — read only the footprint of one
  ``trace_id``.
* :func:`build_trail_query_projection_for_commits` — Trail Query
  projection scoped to a commit-sha set (``trail blame commit`` /
  ``trail graph``).
* :func:`build_trail_query_projection_for_trace` — Trail Query
  projection scoped to one trace.
* :func:`resolve_node_traces` — resolve ``node_id`` -> owning
  ``trace_id`` via a ``context_node_observed``-only scoped read.
* :func:`build_context_tree_projection_for_trace` — Context Tree
  projection for one trace only.

INTERNAL-ONLY whole-log primitives (do NOT call from a read verb —
they materialise the entire canonical log):

* ``event_log.read_events`` — full parsed event graph.
* ``query.build_trail_query_projection`` — whole-log Trail projection.
* ``context_tree.query.build_context_tree_projection`` — whole-log
  Context Tree projection.

Those three legitimately serve whole-log aggregates (the Trace Index,
capsule export, inverse blame, ``bucket repair``); they are named here
only so their whole-corpus cost is unambiguous at the call site.
"""

from __future__ import annotations

from .event_log import read_events_for_trace, read_events_scoped
from .query import (
    build_trail_query_projection_for_commits,
    build_trail_query_projection_for_trace,
)

# Context Tree lives in a sibling package; the bounded builders there sit
# on the SAME event_log primitives, so they belong to the same contract.
from ..context_tree.query import (
    build_context_tree_projection_for_trace,
    resolve_node_traces,
)

__all__ = [
    "read_events_scoped",
    "read_events_for_trace",
    "build_trail_query_projection_for_commits",
    "build_trail_query_projection_for_trace",
    "resolve_node_traces",
    "build_context_tree_projection_for_trace",
]
