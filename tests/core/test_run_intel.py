"""Unit tests for deterministic run-intelligence annotations (plan 086)."""

from __future__ import annotations

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord

from opentraces.core.run_intel import detect_run_signals


def _record(steps) -> TraceRecord:
    return TraceRecord(
        trace_id="trace-ri",
        session_id="session-ri",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "test"},
        steps=steps,
    )


def _bash(idx, command, *, obs=None, role="agent", ts=None):
    return Step(
        step_index=idx,
        role=role,
        timestamp=ts,
        tool_calls=[ToolCall(tool_call_id=f"tc{idx}", tool_name="Bash", input={"command": command})],
        observations=[Observation(source_call_id=f"tc{idx}", content=obs)] if obs is not None else [],
    )


def test_recovery_fires_only_after_failure():
    record = _record([
        Step(step_index=1, role="user", content="run the tests"),
        _bash(2, "pytest", obs="exit code 1\nFAILED"),
        _bash(3, "pytest", obs="exit code 0\ntests passed"),
    ])
    report = detect_run_signals(record)
    counts = report.counts()
    assert counts["failure"] == 1
    assert counts["recovery"] == 1
    rec = [s for s in report.signals if s.kind == "recovery"][0]
    assert rec.step_index == 3
    assert rec.confidence == 0.85
    assert rec.node_id == "tu:trace-ri:observation:tc3"


def test_recovery_without_prior_failure_silent():
    record = _record([_bash(1, "pytest", obs="exit code 0\ntests passed")])
    report = detect_run_signals(record)
    assert report.counts()["recovery"] == 0
    assert report.counts()["failure"] == 0


def test_resteer_fires_on_correction():
    record = _record([Step(step_index=1, role="user", content="actually that is wrong, try again")])
    report = detect_run_signals(record)
    rest = [s for s in report.signals if s.kind == "resteer"]
    assert len(rest) == 1
    assert rest[0].severity == "medium"
    assert rest[0].confidence == 0.85
    assert rest[0].node_id == "tu:trace-ri:step:1"


def test_resteer_suppressed_for_authorization():
    record = _record([Step(step_index=1, role="user", content="yes")])
    report = detect_run_signals(record)
    assert report.counts()["resteer"] == 0


def test_loop_fires_on_third_repeat():
    record = _record([_bash(i, "pytest -x") for i in (1, 2, 3)])
    report = detect_run_signals(record)
    loops = [s for s in report.signals if s.kind == "loop"]
    assert len(loops) == 1
    assert loops[0].step_index == 3
    assert loops[0].confidence == 0.75
    assert loops[0].node_id == "tu:trace-ri:tool:tc3"


def test_loop_two_repeats_silent():
    record = _record([_bash(i, "pytest -x") for i in (1, 2)])
    report = detect_run_signals(record)
    assert report.counts()["loop"] == 0


def test_loop_emits_one_signal_with_repeat_count():
    # 5 identical commands -> exactly ONE loop signal (not 3), carrying count.
    record = _record([_bash(i, "pytest -x") for i in (1, 2, 3, 4, 5)])
    report = detect_run_signals(record)
    loops = [s for s in report.signals if s.kind == "loop"]
    assert len(loops) == 1
    assert loops[0].step_index == 3  # anchored where it crossed the threshold
    assert loops[0].evidence["repeat_count"] == 5
    assert loops[0].evidence["first_step"] == 1
    assert loops[0].evidence["last_step"] == 5


def test_structured_observation_error_is_failure():
    record = _record([
        Step(step_index=1, role="agent",
             tool_calls=[ToolCall(tool_call_id="tc1", tool_name="Bash", input={"command": "x"})],
             observations=[Observation(source_call_id="tc1", error="ENOENT: missing")])
    ])
    report = detect_run_signals(record)
    fails = [s for s in report.signals if s.kind == "failure"]
    assert len(fails) == 1
    assert fails[0].severity == "high"
    assert fails[0].confidence == 0.9
    assert fails[0].evidence["source"] == "observation_error"


def test_benign_failed_in_prose_is_not_a_failure():
    # precision fix: loose substrings like "failed"/"exception" must not fire
    record = _record([
        _bash(1, "echo plan", obs="the legacy approach failed to scale, so we redesigned"),
        _bash(2, "echo note", obs="we handle this exception gracefully and set a 30s timeout"),
    ])
    report = detect_run_signals(record)
    assert report.counts()["failure"] == 0


def test_loop_excluded_outside_time_window():
    # three identical commands all >10min apart → never 2 priors in window
    record = _record([
        _bash(1, "pytest -x", ts="2026-05-28T10:00:00Z"),
        _bash(2, "pytest -x", ts="2026-05-28T10:11:00Z"),
        _bash(3, "pytest -x", ts="2026-05-28T10:22:00Z"),
    ])
    report = detect_run_signals(record)
    assert report.counts()["loop"] == 0


def test_failure_severity_and_confidence():
    record = _record([_bash(1, "x", obs="Traceback (most recent call last)")])
    report = detect_run_signals(record)
    fail = [s for s in report.signals if s.kind == "failure"][0]
    assert fail.severity == "high"  # traceback
    assert fail.confidence == 0.65  # not is_error/exit-code


def test_envelope_shape_and_counts():
    record = _record([
        Step(step_index=1, role="user", content="actually, try again"),
        _bash(2, "pytest", obs="exit code 1"),
        _bash(3, "pytest", obs="exit code 0"),
    ])
    env = detect_run_signals(record).to_envelope()
    assert set(env) == {"status", "trace_id", "signals", "counts"}
    assert env["status"] == "ok"
    assert set(env["counts"]) == {"resteer", "recovery", "loop", "failure"}
    assert sum(env["counts"].values()) == len(env["signals"])


def test_determinism_byte_identical():
    record = _record([
        Step(step_index=1, role="user", content="actually, try again"),
        _bash(2, "pytest", obs="exit code 1"),
        _bash(3, "pytest", obs="exit code 0"),
    ])
    assert detect_run_signals(record).to_envelope() == detect_run_signals(record).to_envelope()
