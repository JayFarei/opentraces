"""Freeze gate for #137: the per-trace / per-commit read seam must never call
the whole-log reader.

The size-independence verifier (``test_size_independent_reads_137``) proves the
read floor was removed *today*. This lint keeps it removed: it fails CI if a
future change reintroduces a ``read_events(...)`` whole-log walk into any of the
single-key read functions (the exact regression that turned each release's "fast
again" command into the next hang). The routing rule it enforces (PRD #137):

    any read keyed by a single trace_id or commit_sha must resolve through the
    index / per-trace companion; only whole-log AGGREGATE consumers
    (``build_trail_query_projection``, ``to_summary``, the Trace Index rebuild)
    may call the full reader.

It is mechanical, not tribal — and red-proof: ``test_lint_is_red_capable`` shows
the detector fires on a deliberately-added full read, so a green lint means the
seam is genuinely clean, not that the check is asleep.
"""
from __future__ import annotations

import inspect
import re

from opentraces.core.context_tree import query as ctx_query
from opentraces.core.trails import event_log, query

# A bare call to the whole-log reader: ``read_events(`` NOT followed by another
# identifier char (so ``read_events_for_trace(`` / ``read_events_scoped(`` /
# ``read_events_since(`` — the bounded readers — are allowed).
_FULL_READER = re.compile(r"\bread_events\(")

# The single-key read seam #137 owns. Each takes one trace_id or commit set and
# must stay O(result) — never O(log).
_SINGLE_KEY_FUNCS = [
    event_log.read_events_for_trace,
    event_log.read_events_scoped,
    event_log._read_events_for_trace_fullscan,
    query.build_trail_query_projection_for_trace,
    query.build_trail_query_projection_for_commits,
    ctx_query.build_context_tree_projection_for_trace,
]


def test_single_key_reads_never_call_the_full_reader() -> None:
    offenders = []
    for func in _SINGLE_KEY_FUNCS:
        source = inspect.getsource(func)
        if _FULL_READER.search(source):
            offenders.append(f"{func.__module__}.{func.__qualname__}")
    assert not offenders, (
        "single-key read functions must not call the whole-log read_events() "
        f"(the #137 read-floor regression): {offenders}. Route the read through "
        "read_events_for_trace / read_events_scoped (index-backed) instead."
    )


def test_lint_is_red_capable() -> None:
    """The detector must actually fire on a full-log read — a check only ever
    seen green is unproven."""

    def _offending_single_key_read(cwd, trace_id):  # pragma: no cover - sample
        from opentraces.core.trails.event_log import read_events

        return [e for e in read_events(cwd) if e.trace_id == trace_id]

    assert _FULL_READER.search(inspect.getsource(_offending_single_key_read))
    # ...and does NOT false-positive on the bounded readers.
    assert not _FULL_READER.search("events = read_events_for_trace(repo, trace_id)")
    assert not _FULL_READER.search("read_events_scoped(repo, event_types=types)")
