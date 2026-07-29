"""Issue #358 compaction stage: verifier-proven #137 index gap.

``_posting_from_doc`` built ``by_patch`` postings from a summary event's
``results[]`` entries but never from ``unanchored_trace_patch_ids`` -- so the
v3-compact shape (compaction's rewrite output, and maturation's live slim
flush, #359) under-indexed: a by-patch lookup would silently miss every
unanchored outcome those shapes carry outside ``results[]``, even though
``iter_search_records`` (search_records.py) yields a record for it.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import event_index
from opentraces.core.trails.event_index import _posting_from_doc
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails.models import TrailEventDraft


def _v3_compact_doc(**overrides: object) -> dict:
    doc = {
        "event_sequence": 7,
        "event_type": "git_anchor_search_completed",
        "trace_id": None,
        "payload": {
            "schema_version": "opentraces.trail.anchor_search.v3",
            "summary": True,
            "search_head": {"algo": "sha1", "hex": "c" * 40},
            "algorithms_attempted": ["exact_range_hash"],
            "searched": 2,
            "anchored": 1,
            "unknown": 1,
            "results": [
                {
                    "trace_patch_id": "tracepatch-sha256:" + "a" * 64,
                    "trace_id": "t-1",
                    "step_index": 0,
                    "generation_index": 0,
                    "result": "anchored",
                    "created_anchor_ids": [],
                }
            ],
            "unanchored_trace_patch_ids": ["tracepatch-sha256:" + "b" * 64],
        },
    }
    doc.update(overrides)
    return doc


def test_posting_indexes_unanchored_trace_patch_ids() -> None:
    posting = _posting_from_doc(7, "deadbeef", _v3_compact_doc())
    assert "tracepatch-sha256:" + "a" * 64 in posting["p"], (
        "sanity: the anchored results[] entry must still be indexed"
    )
    assert "tracepatch-sha256:" + "b" * 64 in posting["p"], (
        "by_patch must also cover unanchored_trace_patch_ids -- a compact-"
        "shaped event's only record of an unknown outcome lives outside "
        "results[], and iter_search_records still yields a record for it"
    )


def test_posting_indexes_empty_unanchored_list_without_error() -> None:
    doc = _v3_compact_doc(payload={
        "schema_version": "opentraces.trail.anchor_search.v3",
        "summary": True,
        "search_head": {"algo": "sha1", "hex": "c" * 40},
        "algorithms_attempted": ["exact_range_hash"],
        "searched": 1,
        "anchored": 1,
        "unknown": 0,
        "results": [
            {
                "trace_patch_id": "tracepatch-sha256:" + "a" * 64,
                "trace_id": "t-1",
                "step_index": 0,
                "generation_index": 0,
                "result": "anchored",
                "created_anchor_ids": [],
            }
        ],
        "unanchored_trace_patch_ids": [],
    })
    posting = _posting_from_doc(7, "deadbeef", doc)
    assert posting["p"] == [
        "a" * 64,
        "tracepatch-sha256:" + "a" * 64,
    ]


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def test_rebuild_event_index_by_patch_covers_unanchored_ids_end_to_end(
    tmp_path: Path,
) -> None:
    """Same gap, exercised through the real append + rebuild path rather than
    the pure posting function directly -- proves the fix lands where reads
    actually happen, not just in the unit-level extraction."""
    repo = tmp_path / "r"
    repo.mkdir()
    _init_repo(repo)
    anchored_id = "tracepatch-sha256:" + "a" * 64
    unanchored_id = "tracepatch-sha256:" + "b" * 64
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None,
                step_index=None,
                capture_method=["watcher_reconcile"],
                payload={
                    "schema_version": "opentraces.trail.anchor_search.v3",
                    "summary": True,
                    "search_head": {"algo": "sha1", "hex": "d" * 40},
                    "algorithms_attempted": ["exact_range_hash"],
                    "searched": 2,
                    "anchored": 1,
                    "unknown": 1,
                    "results": [
                        {
                            "trace_patch_id": anchored_id,
                            "trace_id": "t-1",
                            "step_index": 0,
                            "generation_index": 0,
                            "result": "anchored",
                            "created_anchor_ids": [],
                        }
                    ],
                    "unanchored_trace_patch_ids": [unanchored_id],
                },
            )
        ],
        writer="test-fixture",
    )
    idx = event_index.rebuild_event_index(repo)
    assert idx is not None
    assert anchored_id in idx.by_patch
    assert unanchored_id in idx.by_patch, (
        "rebuild_event_index must index unanchored_trace_patch_ids into "
        "by_patch, not just results[] entries"
    )
