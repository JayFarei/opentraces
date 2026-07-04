"""Oracle widening (#156, U3, Part 2) — the ``oracle_trust`` factor.

Lands the success-path capture (``extract_passing_test_payload``), the
``oracle_trust`` ordinal (``declared`` ▸ ``captured_error`` ▸ ``captured_pass`` ▸
``intent_reposed`` ▸ ``none``), and proves it feeds U1's clamp at the exact read
path (``capsule["oracle_trust"]``) — all WITHOUT widening the frozen verdict
triple ``oracle.VERDICTS``.

All SYNTHETIC + RED-first (mirrors ``tests/test_capsule_oracle.py::_v`` and the
``tests/core/test_size_independent_reads_137.py`` symbol-existence discipline):
every net-new symbol (``extract_passing_test_payload``, ``oracle_trust_of``, the
stamped ``capsule["oracle_trust"]``) is named here BEFORE it exists in ``src/``.

STOP-CONDITION (intrinsic ceiling): a no-test success/design session is capped at
``oracle_trust="intent_reposed"`` — never invent a synthetic test to force a
higher rung. The clamp then honestly caps ``verdict_trust`` (env_tier=L0 floors
it anyway on today's corpus).
"""

from __future__ import annotations

import json

import pytest

from opentraces.core.capsule.oracle import VERDICTS, classify_result


# --------------------------------------------------------------------------- #
# RED-first: the net-new symbols must exist.
# --------------------------------------------------------------------------- #


def test_net_new_oracle_symbols_exist():
    from opentraces.core.capsule import test_extract

    assert hasattr(test_extract, "extract_passing_test_payload"), (
        "net-new symbol `extract_passing_test_payload` must exist (RED-first spec)"
    )
    assert hasattr(test_extract, "oracle_trust_of"), (
        "net-new symbol `oracle_trust_of` must exist (RED-first spec)"
    )
    assert callable(test_extract.extract_passing_test_payload)
    assert callable(test_extract.oracle_trust_of)


def _record(steps: list[dict], **extra):
    from opentraces_schema import TraceRecord

    base = {
        "trace_id": "t-oracle",
        "session_id": "t-oracle",
        "agent": {"name": "codex", "version": "1.0", "model": "gpt"},
        "task": {"description": "add a helper", "base_commit": "abc123def456"},
        "environment": {"language_ecosystem": ["python"]},
        "steps": steps,
    }
    base.update(extra)
    return TraceRecord.model_validate(base)


def _v(command, output, code, expected=None):
    return classify_result(command=command, output=output, exit_code=code, expected=expected)["verdict"]


# --------------------------------------------------------------------------- #
# 3a — a captured FAILING test extracts and grades to the frozen vocab, incl the
# 126/127 env-differs guard. This is the regression fence.
# --------------------------------------------------------------------------- #


def test_3a_captured_failing_test_grades_to_frozen_vocab_with_env_guard():
    from opentraces.core.capsule.test_extract import extract_test_payload

    rec = _record(
        [
            {"step_index": 0, "role": "agent", "content": "start"},
            {
                "step_index": 1,
                "role": "agent",
                "content": "run the tests",
                "tool_calls": [
                    {"tool_call_id": "tc1", "tool_name": "bash", "input": {"command": "pytest -q test_x.py"}}
                ],
                "observations": [
                    {"source_call_id": "tc1", "error": "AssertionError: boom", "content": "AssertionError: boom"}
                ],
            },
            {"step_index": 2, "role": "agent", "content": "done"},
        ]
    )

    payload = extract_test_payload(rec, 1)
    assert payload is not None
    assert payload["source"] == "captured"
    assert payload["captured_at_step"] == 1
    assert payload["command"] == "pytest -q test_x.py"

    cmd, exp = payload["command"], payload["expected"]
    # Error present (test failed, exit 1) -> reproduces.
    assert _v(cmd, "AssertionError: boom\n1 failed", 1, exp) == "reproduces"
    # Error gone + exit 0 -> fixed.
    assert _v(cmd, "1 passed in 0.1s", 0, exp) == "fixed"
    # command not found (exit 127) -> inconclusive (the env-differs guard).
    assert _v(cmd, "pytest: command not found", 127, exp) == "inconclusive"

    # Reuse tests/test_capsule_oracle.py:54 VERBATIM for the 126/127 guard.
    assert _v("pytest test_x.py", "pytest: command not found", 127) == "inconclusive"
    assert _v("make test", "make: command not found", 127) == "inconclusive"

    # C3 feeds INTO the frozen vocab; it never widens it.
    assert VERDICTS == ("fixed", "reproduces", "inconclusive")


# --------------------------------------------------------------------------- #
# 3c — the success-path captured test sits at captured_pass: above intent, below
# declared. It does NOT capture a bare non-test command that happened to pass.
# --------------------------------------------------------------------------- #


def test_3c_captured_pass_placement_above_intent_below_declared():
    from opentraces.core.capsule.replay import FACTOR_POSITIONS
    from opentraces.core.capsule.test_extract import (
        extract_passing_test_payload,
        oracle_trust_of,
    )

    rec = _record(
        [
            {"step_index": 0, "role": "user", "content": "add a feature"},
            {
                "step_index": 1,
                "role": "agent",
                "content": "implemented; running tests",
                "tool_calls": [
                    {"tool_call_id": "tc1", "tool_name": "Bash", "input": {"command": "pytest -q"}}
                ],
                "observations": [{"source_call_id": "tc1", "content": "5 passed in 0.2s"}],
            },
            {"step_index": 2, "role": "agent", "content": "shipped"},
        ]
    )

    payload = extract_passing_test_payload(rec)
    assert payload is not None
    assert payload["source"] == "captured_pass"
    assert payload["captured_at_step"] == 1
    assert payload["command"] == "pytest -q"
    assert oracle_trust_of(payload) == "captured_pass"

    # The ordinal mapping across all sources.
    assert oracle_trust_of({"source": "declared"}) == "declared"
    assert oracle_trust_of({"source": "captured"}) == "captured_error"
    assert oracle_trust_of({"source": "captured_pass"}) == "captured_pass"
    assert oracle_trust_of(None) == "intent_reposed"  # sealed capsule always has intent

    # Placement on U1's frozen lattice: captured_pass is ABOVE intent_reposed,
    # BELOW declared, and level with captured_error.
    pos = FACTOR_POSITIONS["oracle_trust"]
    assert pos["captured_pass"] > pos["intent_reposed"]
    assert pos["captured_pass"] < pos["declared"]
    assert pos["captured_pass"] == pos["captured_error"]


def test_3c_bare_non_test_command_that_passed_is_not_captured():
    # A random command that exited 0 (`ls`, `git status`) is NOT "the test".
    from opentraces.core.capsule.test_extract import extract_passing_test_payload

    rec = _record(
        [
            {
                "step_index": 0,
                "role": "agent",
                "content": "look around",
                "tool_calls": [
                    {"tool_call_id": "tc1", "tool_name": "bash", "input": {"command": "ls -la"}}
                ],
                "observations": [{"source_call_id": "tc1", "content": "total 0\nfoo.py"}],
            },
        ]
    )
    assert extract_passing_test_payload(rec) is None


# --------------------------------------------------------------------------- #
# 3b — a SUCCESS-ONLY session honestly yields test=null + no_executable_test +
# oracle_trust=intent_reposed (never declared/captured), and that ordinal flows
# to U1's clamp at capsule["oracle_trust"].
# --------------------------------------------------------------------------- #


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    from opentraces.core import paths

    monkeypatch.setattr(paths, "OPENTRACES_DIR", tmp_path / "ot")
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    (project_dir / paths.MARKER_FILENAME).write_text(
        json.dumps({"project_id": "test-project-id"}), encoding="utf-8"
    )
    return project_dir


def _seed(project_dir, trace_id, steps, **extra):
    from opentraces.core.bucket_trace_records import write_trace_record
    from opentraces.core.config import get_project_dir

    slug = get_project_dir(project_dir).name
    rec = _record_for_bucket(trace_id, steps, **extra)
    write_trace_record(rec, project_slug=slug, source_layer="test")
    return slug


def _record_for_bucket(trace_id, steps, **extra):
    from opentraces_schema import TraceRecord

    base = {
        "trace_id": trace_id,
        "session_id": trace_id,
        "agent": {"name": "codex", "version": "1.0", "model": "gpt"},
        "task": {"description": "add a helper function", "base_commit": "abc123def456"},
        "environment": {"language_ecosystem": ["python"]},
        "context_tree_summary": {"capture_method": "transcript_reconstruction"},
        "steps": steps,
    }
    base.update(extra)
    return TraceRecord.model_validate(base)


def test_3b_success_only_session_is_intent_reposed_not_declared(export_env):
    from opentraces.core.capsule.export import export_capsule
    from opentraces.core.capsule.replay import build_replay_packet

    tid = "dddd1111-0000-0000-0000-000000000000"
    # No errored observation, no test tool call at all: nothing runnable to grade.
    steps = [
        {"step_index": i, "role": "agent", "content": f"step {i}: added a helper function"}
        for i in range(6)
    ]
    _seed(export_env, tid, steps)

    cap = export_capsule(project_dir=export_env, trace_id=tid)

    assert cap["test"] is None
    assert "no_executable_test" in cap["limitations"]
    # The net-new ordinal — intent_reposed, its intrinsic ceiling, NOT declared
    # and NOT any captured tier.
    assert cap["oracle_trust"] == "intent_reposed"
    assert cap["oracle_trust"] != "declared"
    assert not cap["oracle_trust"].startswith("captured")

    # It flows to U1's clamp at the exact read path.
    packet = build_replay_packet(cap, target_ref="HEAD")
    assert packet["trust_factors"]["oracle_trust"] == "intent_reposed"


def test_3b_declared_test_command_is_declared_oracle_trust(export_env):
    # A maintainer declaring a passing test on a success session -> declared.
    from opentraces.core.capsule.export import export_capsule

    tid = "dddd2222-0000-0000-0000-000000000000"
    steps = [
        {"step_index": i, "role": "agent", "content": f"step {i}: added a helper function"}
        for i in range(6)
    ]
    _seed(export_env, tid, steps)

    cap = export_capsule(project_dir=export_env, trace_id=tid, test_command="pytest -q")
    assert cap["test"] is not None
    assert cap["test"]["source"] == "declared"
    assert cap["oracle_trust"] == "declared"
