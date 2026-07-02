"""CLI proof for Plan 56 trace query/map/get vertical slice."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import SENTINEL, main
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    append_exact_patch_trail,
)
from opentraces.core.trails.models import sha256_text
from opentraces_schema import Agent, GitLink, Observation, Step, ToolCall, TraceRecord


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir
    from opentraces.core.trace_search_snapshot import build_trace_search_snapshot

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")
    # Queries are read-only against an explicit snapshot now; tests rebuild it
    # after every seed write (cheap on these tiny corpora).
    build_trace_search_snapshot()


def _rebuild_legacy_index() -> None:
    """Build the legacy Trace Index that `trace map` / `trace get` unit and
    map-node lookups still read.

    The old trigger (`trace query --force-rebuild`) is rejected by design in
    the read-only snapshot architecture; in real flows capture-time keep-warm
    (`keep_index_warm`) maintains this index.
    """
    from opentraces.core.trace_index import rebuild_index
    from opentraces.core.trace_search_snapshot import build_trace_search_snapshot

    rebuild_index()
    # The legacy rebuild mirrors records into the bucket store, which marks
    # the search snapshot dirty — rebuild the snapshot so queries serve again.
    build_trace_search_snapshot()


def _register_project_source(project_dir: Path) -> None:
    from opentraces.core.config import load_config, register_project, save_config

    cfg = load_config()
    register_project(cfg, project_dir)
    save_config(cfg)


def _init_git_project(project_dir: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=project_dir, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=project_dir, check=True)


def _trace() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-plan056-cli",
        session_id="session-plan056-cli",
        generation_index=2,
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Bug fix failing parser test from CLI"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Use grill-me to fix the parser bug and prove the failing test passes.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Plan:\n- reproduce failure\n- patch parser\n- rerun pytest",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-skill",
                        tool_name="Skill",
                        input={"name": "grill-me"},
                    ),
                    ToolCall(
                        tool_call_id="tc-fail",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    ),
                ],
                observations=[
                    Observation(source_call_id="tc-skill", content="loaded"),
                    Observation(
                        source_call_id="tc-fail",
                        content="FAILED tests/test_parser.py::test_parser_regression",
                        output_summary="1 failed",
                    ),
                ],
            ),
            Step(
                step_index=3,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-edit",
                        tool_name="Edit",
                        input={
                            "file_path": "src/parser.py",
                            "old_string": "return parse_bad(token)",
                            "new_string": "return parse_good(token)",
                        },
                    )
                ],
            ),
            Step(
                step_index=4,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-pass",
                        tool_name="Bash",
                        input={"command": "pytest tests/test_parser.py"},
                    )
                ],
                observations=[
                    Observation(
                        source_call_id="tc-pass",
                        content="tests/test_parser.py . 1 passed",
                        output_summary="1 passed",
                    )
                ],
            ),
            Step(step_index=5, role="agent", content="Done; pytest passes."),
        ],
        outcome={"success": True, "committed": True},
    )


def _ui_trace() -> TraceRecord:
    trace = _trace().model_copy(deep=True)
    trace.trace_id = "trace-plan056-cli-ui"
    trace.session_id = "session-plan056-cli-ui"
    trace.outcome.committed = False
    # Distinct intent text: the search snapshot collapses duplicate titles in
    # SQL, and this was always a different piece of work than _trace().
    trace.task.description = "Bug fix failing UI render test from CLI"
    trace.steps[0].content = (
        "Use grill-me to fix the UI render bug and prove the failing test passes."
    )
    for step in trace.steps:
        for call in step.tool_calls:
            if call.tool_name == "Edit":
                call.input["file_path"] = "web/ui.tsx"
    return trace


def _filter_trace() -> TraceRecord:
    trace = _trace()
    trace.agent.model = "anthropic/claude-opus-4-6"
    trace.timestamp_end = "2026-04-20T12:00:00Z"
    trace.dependencies = ["pytest", "flask"]
    trace.git_links = [
        GitLink(vcs_type="git", revision="abc123", tier="tool_emitted")
    ]
    trace.metadata["survival_state"] = "alive_on_path"
    trace.steps.append(
        Step(
            step_index=6,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-service",
                    tool_name="Bash",
                    input={"command": "curl http://localhost:5050/api/health"},
                )
            ],
            observations=[
                Observation(
                    source_call_id="tc-service",
                    content='{"status":"ok"}',
                    output_summary="service ok",
                )
            ],
        )
    )
    return trace


def _extract_json(output: str) -> dict:
    if SENTINEL in output:
        return json.loads(output.split(SENTINEL, 1)[1].strip())
    return json.loads(output)


def test_trace_query_map_get_json_cli_round_trip(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())
    _rebuild_legacy_index()

    runner = CliRunner()
    query = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--lex",
            "bug fix failing test",
            "--skill",
            "grill-me",
            "--signal",
            "tested_successful_fix_candidate",
            "--json",
        ],
    )
    assert query.exit_code == 0, query.output
    query_payload = json.loads(query.output)
    packet = query_payload["candidates"][0]
    assert packet["trace_id"] == "trace-plan056-cli"
    assert packet["unit_type"] == "trace"
    assert "return parse_good" not in query.output

    trace_map = runner.invoke(
        main,
        [
            "trace",
            "map",
            packet["trace_id"],
            "--candidate",
            packet["unit_id"],
            "--max-steps",
            "6",
            "--json",
        ],
    )
    assert trace_map.exit_code == 0, trace_map.output
    map_payload = json.loads(trace_map.output)
    assert [node["action_type"] for node in map_payload["map"]["nodes"]] == [
        "user_instruction",
        "agent_plan",
        "test_run",
        "file_edit",
        "test_run",
        "final_response",
    ]

    got_unit = runner.invoke(main, ["trace", "get", packet["unit_id"], "--json"])
    assert got_unit.exit_code == 0, got_unit.output
    unit_payload = json.loads(got_unit.output)
    assert unit_payload["unit"]["unit_id"] == packet["unit_id"]
    assert unit_payload["unit"]["unit_type"] == "trace"

    node_id = map_payload["map"]["nodes"][0]["node_id"]
    got_node = runner.invoke(main, ["trace", "get", node_id, "--json"])
    assert got_node.exit_code == 0, got_node.output
    node_payload = json.loads(got_node.output)
    assert node_payload["map_node"]["node_id"] == node_id
    assert node_payload["map_node"]["action_type"] == "user_instruction"

    got = runner.invoke(main, ["trace", "get", packet["trace_id"], "--json"])
    assert got.exit_code == 0, got.output
    get_payload = json.loads(got.output)
    # v7: bare `trace get <id>` returns a bounded overview, NOT a full step dump.
    overview = get_payload["trace"]
    assert overview["trace_id"] == "trace-plan056-cli"
    assert overview["step_count"] >= 1
    assert "steps" not in overview  # bounded: no O(steps) array
    assert overview["summary"] and overview["title"]  # readable, not id-only


def test_trace_skills_json_lists_usage_from_snapshot(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    primary = _trace()
    primary.steps[1].tool_calls.append(
        ToolCall(
            tool_call_id="tc-skill-second",
            tool_name="Skill",
            input={"name": "grill-me"},
        )
    )
    _write_project_trace(project, primary)

    other = _trace().model_copy(deep=True)
    other.trace_id = "trace-plan056-cli-opentraces"
    other.session_id = "session-plan056-cli-opentraces"
    other.task.description = "Use opentraces to inspect skill traces"
    other.steps[1].tool_calls[0].input["name"] = "opentraces"
    _write_project_trace(project, other)

    result = CliRunner().invoke(main, ["trace", "skills", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert payload["source"] == "snapshot"
    assert payload["total_skills"] == 2
    assert payload["total_invocations"] == 3
    assert payload["skills"][0]["skill_name"] == "grill-me"
    assert payload["skills"][0]["invocation_count"] == 2
    assert payload["skills"][0]["trace_count"] == 1
    assert payload["search_diagnostics"]["raw_trace_scan"] is False
    assert payload["telemetry"]["duration_ms"] >= 0


def test_trace_map_rebuild_upgrades_verified_bash_write_from_trail_projection(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _register_project_source(project)
    _init_git_project(project)
    record = TraceRecord(
        trace_id="trace-plan056-cli-bash-write",
        session_id="session-plan056-cli-bash-write",
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Generate a file through bash"},
        steps=[
            Step(step_index=1, role="user", content="Generate the file through bash."),
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-bash-write",
                        tool_name="Bash",
                        input={"command": "cat > generated.txt <<'EOF'\nhello\nEOF"},
                    )
                ],
            ),
        ],
    )
    _write_project_trace(project, record)
    append_event_batch(
        project,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=record.trace_id,
                generation_index=record.generation_index,
                step_index=2,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": sha256_text("bash-write-patch").split(":", 1)[1],
                    "file_path": "generated.txt",
                    "affected_range": {"start_line": 1, "end_line": 1},
                    "authored_text": "hello\n",
                    "raw_authored_hash": sha256_text("hello\n"),
                    "git_clean_hash": sha256_text("hello"),
                    "limitations": [],
                },
            )
        ],
        writer="test-fixture",
    )
    # The legacy index rebuild folds the trail projection (Git-survival
    # verification) into the stored map.
    _rebuild_legacy_index()

    runner = CliRunner()
    query = runner.invoke(
        main,
        ["trace", "query", "--lex", "generate", "--json"],
    )
    assert query.exit_code == 0, query.output

    # Issue #89: the default `trace map` accessor derives from the durable
    # bucket/project record and is intentionally NOT trail-enriched — the
    # Bash write stays an unverified tool_call. Git-survival / patch-verification
    # evidence now lives in the Trail substrate (`trail blame/graph/track`), and
    # the trail-enriched map remains reachable only through the explicit legacy
    # index path.
    from opentraces.core.trace_index import default_index_path, get_trace_map

    # v7: the default map is the bounded faceted browse; use a selector
    # (--actions) to obtain the node dump for a specific node lookup.
    cli_map = json.loads(
        runner.invoke(
            main, ["trace", "map", record.trace_id, "--actions", "tool_call", "--json"]
        ).output
    )
    cli_bash = next(n for n in cli_map["map"]["nodes"] if n.get("tool_name") == "Bash")
    assert cli_bash["metadata"].get("write_verified") is not True

    legacy_map = get_trace_map(record.trace_id, index_path=default_index_path())
    bash_node = next(
        node for node in legacy_map.model_dump(mode="json")["nodes"]
        if node.get("tool_name") == "Bash"
    )
    assert bash_node["action_type"] == "file_edit"
    assert bash_node["files_modified"] == ["generated.txt"]
    assert bash_node["metadata"]["classification_source"] == "trail_projection"
    assert bash_node["metadata"]["pre_verification_action_type"] == "tool_call"
    assert bash_node["metadata"]["write_verified"] is True
    assert bash_node["metadata"]["trace_patches"][0]["trace_patch_id"]


def test_trace_query_rejects_patch_candidate_kind_even_with_patch_trails(tmp_path):
    """Patch units (and the per-patch ``trail_freshness`` payload only they
    carried) are no longer ``trace query`` results: the read-only search
    snapshot is trace-level by design. Even when patch trails exist, the new
    explicit contract is a hard rejection pointing at the hydration path
    (``trace get`` / ``trace map``)."""
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _register_project_source(project)
    _init_git_project(project)

    (project / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "app.py"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=project, check=True)
    authored_text = "    return 'trail query freshness'\n"
    (project / "app.py").write_text("def value():\n" + authored_text)
    subprocess.run(["git", "add", "app.py"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "patch"], cwd=project, check=True)
    commit_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        text=True,
    ).strip()
    append_exact_patch_trail(
        project,
        trace_id="trace-plan056-cli-trail-freshness",
        step_index=2,
        file_path="app.py",
        authored_text=authored_text,
        commit_ref=commit_sha,
        writer="test",
        capture_method=["hook_posttooluse"],
    )

    result = CliRunner().invoke(
        main,
        ["trace", "query", "--candidate-kind", "patch", "--json"],
    )

    assert result.exit_code == 2, result.output
    assert "--candidate-kind is trace-level in the compact search snapshot" in result.output
    assert "trace get/map" in result.output


def test_trace_map_accepts_ot_map_uri_and_map_node_target(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())
    _rebuild_legacy_index()

    runner = CliRunner()
    query = runner.invoke(
        main,
        ["trace", "query", "--skill", "grill-me", "--json"],
    )
    assert query.exit_code == 0, query.output
    trace_id = json.loads(query.output)["candidates"][0]["trace_id"]

    # v7: the ot:// map URI resolves the same trace; use a selector to get the
    # node dump (the default view is the bounded faceted browse).
    by_uri = runner.invoke(
        main,
        ["trace", "map", f"ot://trace/{trace_id}/map", "--actions", "user_instruction", "--json"],
    )
    assert by_uri.exit_code == 0, by_uri.output
    uri_payload = json.loads(by_uri.output)
    assert uri_payload["trace_id"] == trace_id
    node_id = uri_payload["map"]["nodes"][0]["node_id"]

    by_node = runner.invoke(main, ["trace", "map", node_id, "--around", node_id, "--json"])
    assert by_node.exit_code == 0, by_node.output
    assert json.loads(by_node.output)["trace_id"] == trace_id


def test_trace_query_can_embed_bounded_slice_preview(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--lex",
            "bug fix failing test",
            "--signal",
            "tested_successful_fix_candidate",
            "--include-slice",
            "evidence",
            "--max-slice-nodes",
            "4",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    packet = json.loads(result.output)["candidates"][0]
    assert packet["slice_preview"]["trace_id"] == "trace-plan056-cli"
    assert len(packet["slice_preview"]["nodes"]) == 4
    assert [node["action_type"] for node in packet["slice_preview"]["nodes"]] == [
        "user_instruction",
        "agent_plan",
        "test_run",
        "file_edit",
    ]
    assert "return parse_good" not in json.dumps(packet["slice_preview"])


def test_trace_map_walks_backward_and_forward_from_node(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())
    _rebuild_legacy_index()

    runner = CliRunner()
    query = runner.invoke(
        main,
        ["trace", "query", "--signal", "tested_successful_fix_candidate", "--json"],
    )
    assert query.exit_code == 0, query.output
    trace_id = json.loads(query.output)["candidates"][0]["trace_id"]

    # v7: the default map is bounded; use the --actions selector to get the
    # file_edit node dump to seed the directional walk.
    full_map = runner.invoke(
        main, ["trace", "map", trace_id, "--actions", "file_edit", "--json"]
    )
    assert full_map.exit_code == 0, full_map.output
    edit_node = next(
        node
        for node in json.loads(full_map.output)["map"]["nodes"]
        if node["action_type"] == "file_edit"
    )

    backward = runner.invoke(
        main,
        [
            "trace",
            "map",
            trace_id,
            "--from-node",
            edit_node["node_id"],
            "--walk",
            "back",
            "--until",
            "user_instruction",
            "--json",
        ],
    )
    assert backward.exit_code == 0, backward.output
    assert [node["action_type"] for node in json.loads(backward.output)["map"]["nodes"]] == [
        "file_edit",
        "test_run",
        "skill_invocation",
        "agent_plan",
        "user_instruction",
    ]

    forward = runner.invoke(
        main,
        [
            "trace",
            "map",
            trace_id,
            "--from-node",
            edit_node["node_id"],
            "--walk",
            "forward",
            "--until",
            "final_response",
            "--json",
        ],
    )
    assert forward.exit_code == 0, forward.output
    assert [node["action_type"] for node in json.loads(forward.output)["map"]["nodes"]] == [
        "file_edit",
        "test_run",
        "final_response",
    ]


def test_trace_query_reserved_vector_mode_exits_10():
    result = CliRunner().invoke(main, ["trace", "query", "--vec", "parser bug"])

    assert result.exit_code == 10
    assert "reserved in M1" in result.output


def test_trace_query_reserved_hyde_mode_exits_10():
    result = CliRunner().invoke(main, ["trace", "query", "--hyde", "parser bug"])

    assert result.exit_code == 10
    assert "reserved in M1" in result.output


def test_trace_query_include_superseded_returns_older_generations(tmp_path):
    """Plan-59 D1=(b): supersession is now an explicit writer-declared marker.

    A trace whose ``metadata.superseded_by`` points at another trace is the
    only trace dropped under ``--latest-generation``; siblings sharing a
    ``session_id`` but missing the marker are preserved as distinct results.
    """

    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    old = _trace()
    old.trace_id = "trace-plan056-cli-old"
    old.generation_index = 1
    # The snapshot also collapses duplicate titles in SQL, so the older
    # generation keeps its own (earlier) intent text — supersession must be
    # proven by the explicit metadata marker, not by title dedup.
    old.task.description = "Bug fix failing parser test from CLI (first attempt)"
    old.steps[0].content = (
        "Use grill-me to take a first pass at the parser bug before the retry."
    )
    old.metadata = {**old.metadata, "superseded_by": "trace-plan056-cli-new"}
    new = _trace()
    new.trace_id = "trace-plan056-cli-new"
    new.generation_index = 2
    _write_project_trace(project, old)
    _write_project_trace(project, new)

    runner = CliRunner()
    latest_only = runner.invoke(
        main,
        ["trace", "query", "--skill", "grill-me", "--json"],
    )
    assert latest_only.exit_code == 0, latest_only.output
    assert [packet["trace_id"] for packet in json.loads(latest_only.output)["candidates"]] == [
        "trace-plan056-cli-new"
    ]

    with_superseded = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--skill",
            "grill-me",
            "--include-superseded",
            "--json",
        ],
    )
    assert with_superseded.exit_code == 0, with_superseded.output
    assert {
        packet["trace_id"]
        for packet in json.loads(with_superseded.output)["candidates"]
    } == {"trace-plan056-cli-old", "trace-plan056-cli-new"}


def test_trace_query_and_get_resolve_units(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())
    _rebuild_legacy_index()

    runner = CliRunner()
    search = runner.invoke(
        main,
        ["trace", "query", "--skill", "grill-me", "--json"],
    )
    assert search.exit_code == 0, search.output
    packet = json.loads(search.output)["candidates"][0]

    get = runner.invoke(main, ["trace", "get", packet["unit_id"], "--json"])
    assert get.exit_code == 0, get.output
    assert json.loads(get.output)["unit"]["unit_id"] == packet["unit_id"]

    positional_search = runner.invoke(
        main,
        ["trace", "query", "bug", "fix", "failing", "test", "--json"],
    )
    assert positional_search.exit_code == 0, positional_search.output
    assert json.loads(positional_search.output)["candidates"][0]["trace_id"] == packet["trace_id"]


def test_trace_get_ot_resource_errors_without_traceback():
    result = CliRunner().invoke(main, ["trace", "get", "ot://trace/nonexistent", "--json"])

    assert result.exit_code == 6
    assert "Trace resource not found" in result.output
    assert "Traceback" not in result.output


def test_trace_query_cli_metadata_filters_and_page_tokens(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())
    _write_project_trace(project, _ui_trace())

    runner = CliRunner()
    filtered = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--tool",
            "Edit",
            "--files",
            "src/*.py",
            "--file-kind",
            "py",
            "--success",
            "--committed",
            "--candidate-kind",
            "bug_fix",
            "--json",
        ],
    )
    assert filtered.exit_code == 0, filtered.output
    filtered_payload = json.loads(filtered.output)
    assert [packet["trace_id"] for packet in filtered_payload["candidates"]] == [
        "trace-plan056-cli"
    ]

    first = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--signal",
            "tested_successful_fix_candidate",
            "--limit",
            "1",
            "--json",
        ],
    )
    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.output)
    assert first_payload["next_page_token"] == "offset:1"

    second = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--signal",
            "tested_successful_fix_candidate",
            "--limit",
            "1",
            "--page-token",
            first_payload["next_page_token"],
            "--json",
        ],
    )
    assert second.exit_code == 0, second.output
    second_payload = json.loads(second.output)
    assert second_payload["next_page_token"] is None
    assert {
        first_payload["candidates"][0]["trace_id"],
        second_payload["candidates"][0]["trace_id"],
    } == {"trace-plan056-cli", "trace-plan056-cli-ui"}


def test_trace_query_cli_generic_facet_and_metadata_filters(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())
    _write_project_trace(project, _ui_trace())

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--signal",
            "tested_successful_fix_candidate",
            "--facet",
            "file.path=src/parser.py",
            "--facet",
            "agent.name=claude-code",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [packet["trace_id"] for packet in payload["candidates"]] == [
        "trace-plan056-cli"
    ]
    # Filter-only queries carry no FTS score in the read-only snapshot, so the
    # legacy {"metadata": >0} score part is gone; selection is the contract.
    assert payload["candidates"][0]["score_parts"] == {}

    # --metadata unit filters were removed with typed units; the snapshot
    # rejects them explicitly instead of silently ignoring them.
    rejected = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--signal",
            "tested_successful_fix_candidate",
            "--metadata",
            "session_id=session-plan056-cli",
            "--json",
        ],
    )
    assert rejected.exit_code == 2
    assert "--metadata filters are not part of the compact trace search snapshot" in rejected.output


def test_trace_query_cli_named_exact_and_derived_filters(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _filter_trace())
    _write_project_trace(project, _ui_trace())

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--signal",
            "tested_successful_fix_candidate",
            "--provider",
            "anthropic",
            "--cmd-family",
            "pytest",
            "--bash-action",
            "test",
            "--test",
            "pytest",
            "--service",
            "localhost",
            "--service-channel",
            "http",
            "--dependency",
            "pytest",
            "--git-tier",
            "tool_emitted",
            "--survival",
            "alive_on_path",
            "--since",
            "2026-04-01",
            "--file-op",
            "edit",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [packet["trace_id"] for packet in payload["candidates"]] == [
        "trace-plan056-cli"
    ]
    facets = {
        (facet["name"], str(facet["value"]))
        for facet in payload["candidates"][0]["facets"]
    }
    assert ("provider.kind", "anthropic") in facets
    assert ("bash.command_family", "pytest") in facets
    assert ("bash.action", "test") in facets
    assert ("test.framework", "pytest") in facets
    assert ("service.name", "localhost") in facets
    assert ("dependency.name", "pytest") in facets


def test_trace_query_cli_typed_units_rejected_but_skill_still_discoverable(tmp_path):
    """Typed units (``skill_invocation`` etc.) were removed from query results
    by design: the snapshot is trace-level. The CLI now rejects unit-level
    candidate kinds explicitly, and the skill information survives on the
    trace-level packet so skill discovery still works."""
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())

    runner = CliRunner()
    rejected = runner.invoke(
        main,
        [
            "trace",
            "query",
            "--skill",
            "grill-me",
            "--candidate-kind",
            "skill_invocation",
            "--json",
        ],
    )

    assert rejected.exit_code == 2, rejected.output
    assert "--candidate-kind is trace-level in the compact search snapshot" in rejected.output

    result = runner.invoke(
        main,
        ["trace", "query", "--skill", "grill-me", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [packet["unit_type"] for packet in payload["candidates"]] == ["trace"]
    packet = payload["candidates"][0]
    assert packet["unit_id"] == "tu:trace-plan056-cli:trace"
    assert "grill-me" in packet["skills"]


def test_trace_query_rejects_invalid_generic_filter_syntax(tmp_path):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())

    result = CliRunner().invoke(
        main,
        [
            "trace",
            "query",
            "--signal",
            "tested_successful_fix_candidate",
            "--facet",
            "missing-equals",
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert "filters must use name=value" in result.output


def test_trace_query_cwd_scope_uses_current_project_slug(tmp_path, monkeypatch):
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    _enroll_project(project_a, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    _enroll_project(project_b, "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

    # Distinct intent texts: the snapshot's SQL title-dedup is global across
    # projects, and these are two different pieces of work.
    trace_a = _trace()
    trace_a.trace_id = "trace-plan056-alpha"
    trace_a.session_id = "session-plan056-alpha"
    trace_a.task.description = "Bug fix failing parser test in alpha"
    trace_a.steps[0].content = (
        "Use grill-me to fix the alpha parser bug and prove the failing test passes."
    )
    trace_b = _trace()
    trace_b.trace_id = "trace-plan056-beta"
    trace_b.session_id = "session-plan056-beta"
    trace_b.task.description = "Bug fix failing parser test in beta"
    trace_b.steps[0].content = (
        "Use grill-me to fix the beta parser bug and prove the failing test passes."
    )
    _write_project_trace(project_a, trace_a)
    _write_project_trace(project_b, trace_b)

    runner = CliRunner()
    global_result = runner.invoke(
        main,
        ["trace", "query", "--signal", "tested_successful_fix_candidate", "--json"],
    )
    assert global_result.exit_code == 0, global_result.output
    assert {p["trace_id"] for p in json.loads(global_result.output)["candidates"]} == {
        "trace-plan056-alpha",
        "trace-plan056-beta",
    }

    monkeypatch.chdir(project_a)
    cwd_result = runner.invoke(
        main,
        ["trace", "query", "--signal", "tested_successful_fix_candidate", "--cwd", "--json"],
    )
    assert cwd_result.exit_code == 0, cwd_result.output
    assert [p["trace_id"] for p in json.loads(cwd_result.output)["candidates"]] == [
        "trace-plan056-alpha"
    ]

    conflict = runner.invoke(
        main,
        ["trace", "query", "--signal", "tested_successful_fix_candidate", "--cwd", "--project", "alpha-aaaaaaaa"],
    )
    assert conflict.exit_code == 2
    assert "either --cwd or --project" in conflict.output


def test_doctor_reports_trace_index_status(tmp_path, monkeypatch):
    project = tmp_path / "demo"
    _enroll_project(project, "abcdef1234567890abcdef1234567890")
    _write_project_trace(project, _trace())
    monkeypatch.setattr("opentraces.core.doctor.find_trufflehog", lambda: None)
    monkeypatch.setattr(
        "opentraces.core.doctor._review_llm_status",
        lambda _cfg: {"enabled": False, "status": "disabled"},
    )
    monkeypatch.setattr(
        "opentraces.core.doctor._entity_parser_status",
        lambda: {"installed": False, "advice": "run 'opentraces setup entity-parser'"},
    )
    monkeypatch.setattr(
        "opentraces.core.doctor._attribution_status",
        lambda _cwd: {"health": "no-project"},
    )
    monkeypatch.setattr(
        "opentraces.core.doctor._watcher_status",
        lambda: {"health": "not-installed", "platform": "test", "installed": False},
    )
    monkeypatch.setattr("opentraces.core.doctor._hook_installers", lambda: [])
    monkeypatch.setattr(
        "opentraces.core.doctor._trail_event_log_status",
        lambda _cwd: {"state": "missing"},
    )
    monkeypatch.setattr(
        "opentraces.core.doctor._post_commit_hook_status",
        lambda _cwd: {"state": "missing", "installed": False},
    )

    runner = CliRunner()
    # `trace query --force-rebuild` used to build the legacy Trace Index as a
    # side effect; search is read-only now, so build it the way maintenance/
    # capture-time keep-warm does before asking doctor about it.
    _rebuild_legacy_index()
    from opentraces.core import paths

    (paths.OPENTRACES_DIR / "trace_index.json").write_text("{}")
    legacy_db = paths.OPENTRACES_DIR / "db" / "ot.sqlite"
    legacy_db.parent.mkdir(parents=True)
    legacy_db.write_text("legacy")

    result = runner.invoke(main, ["doctor"])
    assert result.exit_code == 0, result.output
    payload = _extract_json(result.output)
    trace_index = payload["doctor"]["trace_index"]
    assert trace_index["state"] == "ok"
    assert trace_index["trace_count"] == 1
    assert trace_index["unit_count"] > 1
    assert trace_index["map_node_count"] > 1
    assert trace_index["rebuild_advice"] == "opentraces trace index rebuild"
    assert (
        trace_index["legacy_rebuild_advice"]
        == "opentraces trace index rebuild --legacy"
    )
    assert trace_index["legacy_warning"] is True
    assert {
        item["path"].rsplit("/", 1)[-1]
        for item in trace_index["legacy_artifacts"]
    } == {"trace_index.json", "ot.sqlite"}
