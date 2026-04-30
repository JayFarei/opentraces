"""Cluster H T7 — per-burst ``blast_radius``.

A burst's ``blast_radius`` aggregates over its ``unique_files`` and
``patches`` to surface the impact of the burst at a glance:

* ``files_touched`` — total unique files
* ``test_files_touched`` — files matching test path conventions
* ``src_files_touched`` — files under ``src/``
* ``docs_files_touched`` — files with .md/.rst/.txt extensions
* ``lines_added`` — sum of ``new_string`` line counts across patches
  (looked up from the source TraceRecord's Edit/Write tool calls)
* ``lines_removed`` — sum of ``old_string`` line counts when payload
  was resolvable; ``None`` when no patches yielded a payload

This is pure aggregation — no new git operations, no second trace
walk beyond the existing ``_edits_payload_by_step`` index.
"""

from __future__ import annotations

from typing import Any

from opentraces_schema import TraceMap, TraceMapEdge, TraceMapNode, TraceRecord

from opentraces.core.bursts import detect_bursts
from opentraces.core.trace_map import build_trace_map


def _step(
    step_index: int,
    role: str,
    content: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "role": role,
        "content": content,
        "tool_calls": tool_calls or [],
    }


def _edit(call_id: str, file_path: str, old_string: str, new_string: str) -> dict[str, Any]:
    return {
        "tool_call_id": call_id,
        "tool_name": "Edit",
        "input": {
            "file_path": file_path,
            "old_string": old_string,
            "new_string": new_string,
        },
    }


def _make_trace(steps: list[dict[str, Any]]) -> TraceRecord:
    return TraceRecord.model_validate(
        {
            "schema_version": "0.3.0",
            "trace_id": "t-blast",
            "session_id": "sess-blast",
            "agent": {
                "name": "claude-code",
                "version": "test",
                "model": "anthropic/claude-opus-4-7",
            },
            "task": {
                "description": "blast radius test",
                "source": "user_prompt",
            },
            "steps": steps,
        }
    )


def test_blast_radius_classifies_test_vs_src() -> None:
    """5 files: 2 src/, 2 tests/, 1 docs file. blast_radius must report each."""
    steps = [
        _step(1, "user", "make changes"),
        _step(
            5,
            "agent",
            "edit src",
            tool_calls=[
                _edit("tc1", "src/module.py", "old1", "new1\nline2\n"),
                _edit("tc2", "src/util.py", "old2", "new2\nline2\nline3\n"),
            ],
        ),
        _step(
            6,
            "agent",
            "edit tests",
            tool_calls=[
                _edit("tc3", "tests/test_module.py", "old3", "new3\n"),
                _edit("tc4", "tests/cli/test_other.py", "old4", "new4\n"),
            ],
        ),
        _step(
            7,
            "agent",
            "edit docs",
            tool_calls=[
                _edit("tc5", "docs/README.md", "old5", "new5\n"),
            ],
        ),
    ]
    record = _make_trace(steps)
    tm = build_trace_map(record)
    bursts = detect_bursts(tm.nodes, trace_record=record, commit_lookup=False)
    assert len(bursts) == 1
    burst = bursts[0]
    br = burst.blast_radius
    assert br["files_touched"] == 5
    assert br["src_files_touched"] == 2
    assert br["test_files_touched"] == 2
    assert br["docs_files_touched"] == 1


def test_blast_radius_lines_added_aggregates_authored_text() -> None:
    """``lines_added`` sums newline counts across each patch's new_string."""
    steps = [
        _step(1, "user", "go"),
        _step(
            5,
            "agent",
            "first edit",
            tool_calls=[
                _edit(
                    "tc1",
                    "src/a.py",
                    "old",
                    "line1\nline2\nline3\n",
                ),
            ],
        ),
        _step(
            6,
            "agent",
            "second edit",
            tool_calls=[
                _edit(
                    "tc2",
                    "src/b.py",
                    "removed_a\nremoved_b\n",
                    "added_a\nadded_b\nadded_c\nadded_d\n",
                ),
            ],
        ),
    ]
    record = _make_trace(steps)
    tm = build_trace_map(record)
    bursts = detect_bursts(tm.nodes, trace_record=record, commit_lookup=False)
    assert len(bursts) == 1
    br = bursts[0].blast_radius
    # First edit: 3 lines added; second edit: 4 lines added → total 7.
    assert br["lines_added"] == 7
    # First edit removed 1 logical line ("old"), second edit removed 2 lines.
    assert br["lines_removed"] == 3


def test_blast_radius_handles_empty_unique_files() -> None:
    """A burst built from a candidate with no files (defensive) returns
    zero counts, not a crash. We can't easily get to this state through
    detect_bursts — patches always carry file paths — but the
    surface still defaults to a well-formed dict."""
    steps = [
        _step(1, "user", "noop"),
        _step(
            5,
            "agent",
            "no-op edit",
            tool_calls=[
                _edit("tc1", "noslash.txt", "old", "new\n"),
            ],
        ),
    ]
    record = _make_trace(steps)
    tm = build_trace_map(record)
    bursts = detect_bursts(tm.nodes, trace_record=record, commit_lookup=False)
    assert len(bursts) == 1
    br = bursts[0].blast_radius
    assert br["files_touched"] == 1
    # noslash.txt: docs (.txt). Not src, not test.
    assert br["docs_files_touched"] == 1
    assert br["src_files_touched"] == 0
    assert br["test_files_touched"] == 0
    assert br["lines_added"] == 1


def test_blast_radius_keys_present_in_metadata() -> None:
    """The serialized burst metadata exposes ``blast_radius`` under that key."""
    steps = [
        _step(1, "user", "go"),
        _step(
            5,
            "agent",
            "edit",
            tool_calls=[
                _edit("tc1", "src/x.py", "a", "b\n"),
            ],
        ),
    ]
    record = _make_trace(steps)
    tm = build_trace_map(record)
    bursts = detect_bursts(tm.nodes, trace_record=record, commit_lookup=False)
    md = bursts[0].to_metadata()
    assert "blast_radius" in md
    assert md["blast_radius"]["files_touched"] == 1
    # tool_call_density is also lifted in T6.
    assert "tool_call_density" in md
