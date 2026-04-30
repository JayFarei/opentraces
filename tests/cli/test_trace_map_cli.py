"""CLI proof for the Cluster-B trace map projections (bursts + actions filter).

Covers:
- `ot trace map <id> --bursts --json` returns only `change_burst` nodes.
- `ot trace map <id> --actions ...` reduces JSON size meaningfully.
- `ot trace get <id> --bursts --json` returns the same bursts as the map flag.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import SENTINEL, main
from opentraces_schema import Agent, Observation, Step, ToolCall, TraceRecord


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _trace_with_two_bursts() -> TraceRecord:
    """Synthesize a trace with two file-edit bursts separated by a long gap.

    First burst at steps 10/12/15, second at steps 80/82 — with default gap=35
    they form two distinct change bursts.
    """
    steps = [
        Step(step_index=1, role="user", content="please fix the parser and the tokenizer"),
        # First burst.
        Step(
            step_index=10,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-e1",
                    tool_name="Edit",
                    input={"file_path": "src/parser.py", "old_string": "a", "new_string": "b"},
                )
            ],
        ),
        Step(
            step_index=12,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-e2",
                    tool_name="Edit",
                    input={"file_path": "src/parser.py", "old_string": "c", "new_string": "d"},
                )
            ],
        ),
        Step(
            step_index=15,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-e3",
                    tool_name="Edit",
                    input={"file_path": "src/tokens.py", "old_string": "e", "new_string": "f"},
                )
            ],
        ),
        # Long gap — second burst.
        Step(step_index=70, role="user", content="now refactor the lexer"),
        Step(
            step_index=80,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-e4",
                    tool_name="Edit",
                    input={"file_path": "src/lexer.py", "old_string": "g", "new_string": "h"},
                )
            ],
        ),
        Step(
            step_index=82,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-e5",
                    tool_name="Edit",
                    input={"file_path": "src/lexer.py", "old_string": "i", "new_string": "j"},
                )
            ],
        ),
        Step(step_index=99, role="agent", content="Done — both areas refactored."),
    ]
    return TraceRecord(
        trace_id="trace-cluster-b-bursts",
        session_id="session-cluster-b-bursts",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Two bursts"},
        steps=steps,
    )


def _extract_json(output: str) -> dict:
    if SENTINEL in output:
        return json.loads(output.split(SENTINEL, 1)[1].strip())
    return json.loads(output)


def test_trace_map_bursts_flag_returns_burst_nodes(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace_with_two_bursts())

    runner = CliRunner()
    # Force the index to rebuild for the freshly-enrolled project.
    rebuild = runner.invoke(
        main,
        ["trace", "query", "--project", project.name, "--force-rebuild", "--json"],
    )
    assert rebuild.exit_code == 0, rebuild.output

    res = runner.invoke(
        main,
        ["trace", "map", "trace-cluster-b-bursts", "--bursts", "--json"],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    nodes = payload["map"]["nodes"]
    assert nodes, payload
    assert all(node["action_type"] == "change_burst" for node in nodes)
    # Two bursts expected for this fixture under default gap=35.
    assert len(nodes) == 2
    # Each carries the expected aggregate keys (in metadata for forward-compat).
    for node in nodes:
        meta = node.get("metadata", {})
        assert "step_range" in meta
        assert "unique_files" in meta
        assert "patches" in meta
        assert "unique_git_anchors" in meta
        assert "has_git_anchor" in meta


def test_trace_get_bursts_matches_trace_map_bursts(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace_with_two_bursts())

    runner = CliRunner()
    rebuild = runner.invoke(
        main,
        ["trace", "query", "--project", project.name, "--force-rebuild", "--json"],
    )
    assert rebuild.exit_code == 0, rebuild.output

    via_map = runner.invoke(
        main,
        ["trace", "map", "trace-cluster-b-bursts", "--bursts", "--json"],
    )
    assert via_map.exit_code == 0, via_map.output
    via_get = runner.invoke(
        main,
        ["trace", "get", "trace-cluster-b-bursts", "--bursts", "--json"],
    )
    assert via_get.exit_code == 0, via_get.output

    map_payload = json.loads(via_map.output)
    get_payload = json.loads(via_get.output)
    map_bursts = [n["metadata"] for n in map_payload["map"]["nodes"]]
    get_bursts = get_payload["bursts"]
    # Same shape, same content (ignoring stable IDs) — compare by step_range + patches.
    assert [b["step_range"] for b in map_bursts] == [b["step_range"] for b in get_bursts]
    assert [b["unique_files"] for b in map_bursts] == [b["unique_files"] for b in get_bursts]


def test_trace_map_actions_filter_compactness_via_cli(tmp_path):
    """`--actions` should reduce CLI JSON output noticeably for trace-heavy traces."""
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace_with_two_bursts())

    runner = CliRunner()
    rebuild = runner.invoke(
        main,
        ["trace", "query", "--project", project.name, "--force-rebuild", "--json"],
    )
    assert rebuild.exit_code == 0, rebuild.output

    full = runner.invoke(main, ["trace", "map", "trace-cluster-b-bursts", "--json"])
    assert full.exit_code == 0, full.output
    filtered = runner.invoke(
        main,
        [
            "trace",
            "map",
            "trace-cluster-b-bursts",
            "--actions",
            "user_instruction,file_edit",
            "--json",
        ],
    )
    assert filtered.exit_code == 0, filtered.output

    full_size = len(full.output)
    filtered_size = len(filtered.output)
    # Should be strictly smaller — the filter drops the final_response node.
    assert filtered_size < full_size
    # Every kept node lives in the filter set.
    payload = json.loads(filtered.output)
    types = {n["action_type"] for n in payload["map"]["nodes"]}
    assert types <= {"user_instruction", "file_edit"}
