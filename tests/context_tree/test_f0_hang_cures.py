"""F0 Phase-0 hang cure — the Context Tree / ctx-resolve read verbs must take
the BOUNDED (predicates-before-projection) route, never the whole-log
``read_events`` walk that made them hang on a mature ~983K-event log.

Strategy: after seeding a small world (which legitimately uses the full read
to build its fixture), monkeypatch the whole-log ``read_events`` in the
Context Tree query namespace to blow up. Every cured read verb resolves the
owning trace via a scoped (``context_node_observed``-only) read and then
projects just that trace via ``read_events_for_trace`` — so none of them may
touch ``read_events``. The verb still returns the correct result, proving the
scoped route is taken.
"""
from __future__ import annotations

import pytest

from .test_context_tree_ot_resolver import _ingest_and_project


def _boom(*_args, **_kwargs):  # pragma: no cover - only invoked on regression
    raise AssertionError(
        "whole-log read_events must NOT be called by a bounded ctx read verb"
    )


def test_ctx_resume_takes_the_bounded_route(tmp_path, monkeypatch):
    from opentraces.core.context_tree import query as ct_query
    from opentraces.core.context_tree.resume import context_resume_packet

    projection = _ingest_and_project("context-tree-linear", tmp_path)
    node_id = projection.nodes_by_trace["trace-context-tree-linear"][0]

    monkeypatch.setattr(ct_query, "read_events", _boom)
    packet = context_resume_packet(tmp_path, node_id)

    assert packet.get("node_id") == node_id
    assert "error" not in packet, packet
    assert packet.get("trace_id") == "trace-context-tree-linear"


def test_ctx_prune_takes_the_bounded_route(tmp_path, monkeypatch):
    from opentraces.core.context_tree import query as ct_query
    from opentraces.core.context_tree.prune import prune_session_to_node

    projection = _ingest_and_project("context-tree-linear", tmp_path)
    leaf = projection.nodes_by_trace["trace-context-tree-linear"][-1]
    transcript = tmp_path / "transcript.jsonl"
    assert transcript.exists()

    monkeypatch.setattr(ct_query, "read_events", _boom)
    # Dry-run: resolves the node + walks its active path, no file written.
    result = prune_session_to_node(tmp_path, leaf, transcript, write=False)

    assert result.get("error") is None, result
    assert result.get("node_id") == leaf
    assert result.get("record_count", 0) >= 1


def test_ctx_resolve_context_node_takes_the_bounded_route(tmp_path, monkeypatch):
    from opentraces.core.context_tree import query as ct_query
    from opentraces.core.trails.resources import resolve_resource

    projection = _ingest_and_project("context-tree-linear", tmp_path)
    node_id = projection.nodes_by_trace["trace-context-tree-linear"][0]

    monkeypatch.setattr(ct_query, "read_events", _boom)
    result = resolve_resource(
        tmp_path, f"ot://context-node/sha256/{node_id.split(':', 1)[1]}"
    )

    assert result["resource_type"] == "context_node"
    assert result["node_id"] == node_id


def test_ctx_resolve_context_tree_takes_the_bounded_route(tmp_path, monkeypatch):
    from opentraces.core.context_tree import query as ct_query
    from opentraces.core.trails.resources import resolve_resource

    _ingest_and_project("context-tree-linear", tmp_path)

    monkeypatch.setattr(ct_query, "read_events", _boom)
    result = resolve_resource(
        tmp_path, "ot://context-tree/trace-context-tree-linear"
    )

    assert result["resource_type"] == "context_tree"
    assert result["trace_id"] == "trace-context-tree-linear"
    assert result.get("nodes")
