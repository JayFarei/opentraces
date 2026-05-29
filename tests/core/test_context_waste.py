"""Unit tests for deterministic context-waste detection (plan 086)."""

from __future__ import annotations

from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord

from opentraces.core.context_waste import detect_context_waste


def _ts(minute: int) -> str:
    return f"2026-05-28T10:{minute:02d}:00Z"


def _record(steps, *, capture_methods=None) -> TraceRecord:
    record = TraceRecord(
        trace_id="trace-waste",
        session_id="session-waste",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "test"},
        steps=steps,
    )
    if capture_methods is not None:
        record.context_tree_summary = {"capture_methods": capture_methods}
    return record


def _read_step(idx: int, path: str, minute: int) -> Step:
    return Step(
        step_index=idx,
        role="agent",
        timestamp=_ts(minute),
        tool_calls=[ToolCall(tool_call_id=f"tc{idx}", tool_name="Read", input={"file_path": path})],
        observations=[Observation(source_call_id=f"tc{idx}", content="file body")],
    )


def _search_step(idx: int, minute: int) -> Step:
    return Step(
        step_index=idx,
        role="agent",
        timestamp=_ts(minute),
        tool_calls=[ToolCall(tool_call_id=f"tc{idx}", tool_name="Bash", input={"command": "grep -r foo ."})],
        observations=[Observation(source_call_id=f"tc{idx}", content="no matches")],
    )


def test_large_output_fires_at_threshold():
    big = "x" * 12000
    record = _record([
        Step(
            step_index=1,
            role="agent",
            timestamp=_ts(0),
            tool_calls=[ToolCall(tool_call_id="tc1", tool_name="Bash", input={"command": "cat huge.log"})],
            observations=[Observation(source_call_id="tc1", content=big)],
        )
    ])
    report = detect_context_waste(record)
    waste = [f for f in report.findings if f.pattern == "large_output"]
    assert len(waste) == 1
    assert waste[0].confidence == 0.8
    assert waste[0].severity == "medium"
    assert waste[0].node_id == "tu:trace-waste:observation:tc1"
    assert waste[0].evidence["output_chars"] == 12000
    assert waste[0].evidence["tool_name"] == "Bash"


def test_large_output_below_threshold_silent():
    record = _record([
        Step(
            step_index=1,
            role="agent",
            timestamp=_ts(0),
            tool_calls=[ToolCall(tool_call_id="tc1", tool_name="Bash", input={"command": "cat small.log"})],
            observations=[Observation(source_call_id="tc1", content="x" * 11999)],
        )
    ])
    report = detect_context_waste(record)
    assert report._summary()["large_output_count"] == 0


def test_repeated_file_read_fires_on_third():
    same = "src/parser.py"
    record = _record([
        _read_step(1, same, 0),
        _read_step(2, same, 5),
        _read_step(3, same, 10),
    ])
    report = detect_context_waste(record)
    reads = [f for f in report.findings if f.pattern == "repeated_file_read"]
    assert len(reads) == 1
    assert reads[0].step_index == 3
    assert reads[0].confidence == 0.78
    assert reads[0].evidence["read_count"] == 3
    assert reads[0].node_id == "tu:trace-waste:tool:tc3"


def test_repeated_file_read_redacts_secret_shaped_path():
    secret_path = "secrets/hf_ABCDEFGHIJKLMNOPQRSTUVWX.txt"
    record = _record([
        _read_step(1, secret_path, 0),
        _read_step(2, secret_path, 5),
        _read_step(3, secret_path, 10),
    ])
    report = detect_context_waste(record)
    read = [f for f in report.findings if f.pattern == "repeated_file_read"][0]
    assert "hf_ABCDEFGHIJKLMNOPQRSTUVWX" not in read.evidence["file_path"]
    assert "[REDACTED]" in read.evidence["file_path"]


def test_repeated_file_read_two_reads_silent():
    same = "src/parser.py"
    record = _record([_read_step(1, same, 0), _read_step(2, same, 5)])
    report = detect_context_waste(record)
    assert report._summary()["repeated_file_read_count"] == 0


def test_repeated_file_read_outside_window_silent():
    same = "src/parser.py"
    # third read is 25 minutes after the first → first falls out of the 20m window
    record = _record([_read_step(1, same, 0), _read_step(2, same, 5), _read_step(3, same, 25)])
    report = detect_context_waste(record)
    assert report._summary()["repeated_file_read_count"] == 0


def test_repeated_search_fires_on_fifth():
    record = _record([_search_step(i, i) for i in range(1, 6)])
    report = detect_context_waste(record)
    searches = [f for f in report.findings if f.pattern == "repeated_search"]
    assert len(searches) == 1
    assert searches[0].step_index == 5
    assert searches[0].confidence == 0.7
    assert searches[0].severity == "low"
    assert searches[0].evidence["command_family"] == "grep"
    assert searches[0].evidence["search_count"] == 5


def test_repeated_search_four_silent():
    record = _record([_search_step(i, i) for i in range(1, 5)])
    report = detect_context_waste(record)
    assert report._summary()["repeated_search_count"] == 0


def test_fidelity_record_by_default():
    record = _record([_read_step(1, "a.py", 0)])
    assert detect_context_waste(record).fidelity == "record"


def test_fidelity_otel_when_capture_method_present():
    record = _record([_read_step(1, "a.py", 0)], capture_methods=["otel"])
    assert detect_context_waste(record).fidelity == "otel"


def test_missing_timestamp_records_limitation():
    same = "src/parser.py"
    steps = [_read_step(1, same, 0), _read_step(2, same, 5), _read_step(3, same, 10)]
    for s in steps:
        s.timestamp = None
    report = detect_context_waste(_record(steps))
    assert "missing_timestamps_window_checks_approximate" in report.limitations


def test_determinism_byte_identical():
    same = "src/parser.py"
    record = _record([_read_step(1, same, 0), _read_step(2, same, 5), _read_step(3, same, 10)])
    assert detect_context_waste(record).to_dict() == detect_context_waste(record).to_dict()


def test_envelope_shape():
    record = _record([_read_step(1, "a.py", 0)])
    d = detect_context_waste(record).to_dict()
    assert d["schema_version"] == "opentraces.context_waste.v1"
    assert set(d) == {"schema_version", "trace_id", "fidelity", "thresholds", "findings", "summary", "limitations"}
    assert d["thresholds"]["large_output_chars"] == 12000
