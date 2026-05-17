"""Focused tests for the new otbox journey assertion kinds + transcript
renderer (plan 077 Phase 1, Agent A scope).

Pytest plain functions, no classes — matching the rest of ``tests/``
(e.g. ``tests/otbox/test_otbox_slice.py``).
"""

from __future__ import annotations

import json

from tests.otbox.drivers.base import ExecResult
from tests.otbox.journey import (
    AssertionResult,
    JourneyResult,
    StepResult,
    _eval_assertion,
    render_transcript,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _make_step(
    payload: object,
    *,
    step_id: str = "cli-step",
    index: int = 0,
    argv: list[str] | None = None,
) -> StepResult:
    """Build a synthetic StepResult whose stdout is the JSON of ``payload``."""
    argv = argv or ["opentraces", "trace", "query", "--json"]
    result = ExecResult(
        argv=argv,
        returncode=0,
        stdout=json.dumps(payload),
        stderr="",
        duration_s=0.0,
        cwd="",
        timed_out=False,
    )
    return StepResult(
        index=index,
        step_id=step_id,
        type="cli",
        detail={"argv": argv},
        result=result,
        ok=True,
        message="",
    )


# --------------------------------------------------------------------------
# stdout_json_array_equals
# --------------------------------------------------------------------------
def test_stdout_json_array_equals_success() -> None:
    step = _make_step({"nodes": ["a", "b", "c"]})
    spec = {"kind": "stdout_json_array_equals", "path": "nodes", "equals": ["a", "b", "c"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert res.ok, res.message
    assert "array equals" in res.message


def test_stdout_json_array_equals_length_mismatch() -> None:
    step = _make_step({"nodes": ["a", "b"]})
    spec = {"kind": "stdout_json_array_equals", "path": "nodes", "equals": ["a", "b", "c"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "length" in res.message


def test_stdout_json_array_equals_element_mismatch() -> None:
    step = _make_step({"nodes": ["a", "b", "c"]})
    spec = {"kind": "stdout_json_array_equals", "path": "nodes", "equals": ["a", "X", "c"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "nodes[1]" in res.message


def test_stdout_json_array_equals_missing_path() -> None:
    step = _make_step({"other": []})
    spec = {"kind": "stdout_json_array_equals", "path": "nodes", "equals": []}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "path not found" in res.message


def test_stdout_json_array_equals_wrong_type() -> None:
    step = _make_step({"nodes": "not-a-list"})
    spec = {"kind": "stdout_json_array_equals", "path": "nodes", "equals": []}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "expected list" in res.message


# --------------------------------------------------------------------------
# stdout_json_set_contains
# --------------------------------------------------------------------------
def test_stdout_json_set_contains_success() -> None:
    step = _make_step({"uuids": ["a", "b", "c", "d"]})
    spec = {"kind": "stdout_json_set_contains", "path": "uuids", "equals": ["b", "d"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert res.ok, res.message


def test_stdout_json_set_contains_strict_subset_equal() -> None:
    """Equal sets are a valid subset."""
    step = _make_step({"uuids": ["a", "b"]})
    spec = {"kind": "stdout_json_set_contains", "path": "uuids", "equals": ["a", "b"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert res.ok, res.message


def test_stdout_json_set_contains_missing_element() -> None:
    step = _make_step({"uuids": ["a", "b"]})
    spec = {"kind": "stdout_json_set_contains", "path": "uuids", "equals": ["a", "z"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "missing elements" in res.message
    assert "z" in res.message


def test_stdout_json_set_contains_missing_path() -> None:
    step = _make_step({"other": []})
    spec = {"kind": "stdout_json_set_contains", "path": "uuids", "equals": ["a"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "path not found" in res.message


def test_stdout_json_set_contains_wrong_type() -> None:
    step = _make_step({"uuids": {"a": 1}})
    spec = {"kind": "stdout_json_set_contains", "path": "uuids", "equals": ["a"]}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "expected list" in res.message


# --------------------------------------------------------------------------
# stdout_json_length_equals
# --------------------------------------------------------------------------
def test_stdout_json_length_equals_list_success() -> None:
    step = _make_step({"nodes": ["a", "b", "c"]})
    spec = {"kind": "stdout_json_length_equals", "path": "nodes", "equals": 3}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert res.ok, res.message


def test_stdout_json_length_equals_dict_success() -> None:
    step = _make_step({"index": {"a": 1, "b": 2}})
    spec = {"kind": "stdout_json_length_equals", "path": "index", "equals": 2}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert res.ok, res.message


def test_stdout_json_length_equals_mismatch() -> None:
    step = _make_step({"nodes": ["a", "b"]})
    spec = {"kind": "stdout_json_length_equals", "path": "nodes", "equals": 3}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "len(" in res.message


def test_stdout_json_length_equals_missing_path() -> None:
    step = _make_step({"other": []})
    spec = {"kind": "stdout_json_length_equals", "path": "nodes", "equals": 0}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "path not found" in res.message


def test_stdout_json_length_equals_wrong_type() -> None:
    step = _make_step({"nodes": "scalar"})
    spec = {"kind": "stdout_json_length_equals", "path": "nodes", "equals": 6}
    res = _eval_assertion(0, spec, [step], ctx={})
    assert not res.ok
    assert "expected list or dict" in res.message


# --------------------------------------------------------------------------
# transcript renderer
# --------------------------------------------------------------------------
def _make_journey_with_3_steps_5_assertions() -> JourneyResult:
    """Build a JourneyResult with 3 cli steps and 5 assertions (2+2+1)."""
    steps = [
        _make_step(
            {"schema_version": "opentraces.context_tree.v1", "nodes": ["n1", "n2"]},
            step_id="tree",
            index=0,
            argv=["opentraces", "ctx", "tree", "abc123", "--json"],
        ),
        _make_step(
            {"node_id": "n1", "layers": {"system": "s1"}},
            step_id="show",
            index=1,
            argv=["opentraces", "ctx", "show", "n1", "--json"],
        ),
        _make_step(
            {"reads": [{"step_index": 5, "tool_name": "Read"}]},
            step_id="reads",
            index=2,
            argv=["opentraces", "ctx", "reads", "abc123", "--json"],
        ),
    ]
    assertions = [
        AssertionResult(
            index=0,
            kind="stdout_json",
            ok=True,
            message="schema_version='opentraces.context_tree.v1'",
            spec={"kind": "stdout_json", "step": "tree", "path": "schema_version",
                  "equals": "opentraces.context_tree.v1"},
        ),
        AssertionResult(
            index=1,
            kind="stdout_json_length_equals",
            ok=True,
            message="len('nodes')=2 expected 2",
            spec={"kind": "stdout_json_length_equals", "step": "tree",
                  "path": "nodes", "equals": 2},
        ),
        AssertionResult(
            index=2,
            kind="stdout_json",
            ok=True,
            message="node_id='n1'",
            spec={"kind": "stdout_json", "step": "show", "path": "node_id",
                  "equals": "n1"},
        ),
        AssertionResult(
            index=3,
            kind="stdout_json",
            ok=True,
            message="layers.system='s1'",
            spec={"kind": "stdout_json", "step": "show", "path": "layers.system",
                  "equals": "s1"},
        ),
        AssertionResult(
            index=4,
            kind="stdout_json_length_equals",
            ok=True,
            message="len('reads')=1 expected 1",
            spec={"kind": "stdout_json_length_equals", "step": "reads",
                  "path": "reads", "equals": 1},
        ),
    ]
    return JourneyResult(
        name="demo-acceptance",
        description="Context Tree demo, 3 steps, 5 assertions",
        lane="core",
        tier=0,
        seed=None,
        box_id="otbox-test",
        verdict="PASS",
        reason="",
        steps=steps,
        assertions=assertions,
    )


def test_render_transcript_shape() -> None:
    result = _make_journey_with_3_steps_5_assertions()
    md = render_transcript(
        result,
        fixture_name="c-context-tree-substrate",
        substrate_versions={"opentraces": "0.5.0", "schema": "0.4.0"},
        now_iso="2026-05-17T00:00:00+00:00",
    )

    # Header
    assert "# Context Tree demo acceptance, run 2026-05-17T00:00:00+00:00" in md
    assert "Fixture: c-context-tree-substrate" in md
    assert "Substrate: opentraces 0.5.0, schema 0.4.0" in md
    assert "All 3 commands ran successfully. All 5 assertions passed." in md

    # Per-step sections (numbered headings) — argv joined into the heading
    assert "## 1. opentraces ctx tree abc123 --json" in md
    assert "## 2. opentraces ctx show n1 --json" in md
    assert "## 3. opentraces ctx reads abc123 --json" in md

    # PASS markers on every step (3 steps; all 5 assertions passed)
    assert md.count("PASS:") == 3

    # Summary table
    assert "## Summary" in md
    assert "| step | id | command | assertions | result |" in md
    assert "|------|----|---------|------------|--------|" in md
    # Summary row per step with assertion counts (2 + 2 + 1)
    assert "| 1 | tree |" in md
    assert "| 2 | show |" in md
    assert "| 3 | reads |" in md
    # The result column on every row
    assert md.count("| PASS |") == 3


def test_render_transcript_truncates_large_stdout() -> None:
    big_payload = {"data": "x" * 10_000}
    step = _make_step(big_payload, step_id="big", index=0,
                      argv=["opentraces", "ctx", "tree", "--json"])
    result = JourneyResult(
        name="big",
        description="",
        lane="core",
        tier=0,
        seed=None,
        box_id="otbox-test",
        verdict="PASS",
        reason="",
        steps=[step],
        assertions=[],
    )
    md = render_transcript(result, fixture_name="big-fixture")
    assert "... (truncated)" in md
