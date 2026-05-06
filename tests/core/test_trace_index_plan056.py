"""Plan 56 local Trace Index behavior tests."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from opentraces_schema import Agent, GitLink, Observation, Step, ToolCall, TraceRecord


def _write_project_trace(project_dir: Path, record: TraceRecord) -> None:
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project_dir)
    traces_dir.mkdir(parents=True, exist_ok=True)
    (traces_dir / f"{record.trace_id}.jsonl").write_text(record.model_dump_json() + "\n")


def _enroll_project(project_dir: Path, project_id: str) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )


def _register_project(project_dir: Path, project_id: str) -> None:
    from opentraces.core.config import (
        Config,
        ProjectRegistration,
        _make_slug,
        get_project_traces_dir,
        save_config,
    )

    _enroll_project(project_dir, project_id)
    get_project_traces_dir(project_dir).mkdir(parents=True, exist_ok=True)
    save_config(
        Config(
            projects={
                str(project_dir.resolve()): ProjectRegistration(
                    project_id=project_id,
                    slug=_make_slug(project_dir.name, project_id),
                )
            }
        )
    )


def _bug_fix_trace(
    trace_id: str,
    *,
    generation_index: int = 0,
    session_id: str = "session-plan056-index",
    file_path: str = "src/parser.py",
    committed: bool = True,
) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=session_id,
        generation_index=generation_index,
        agent=Agent(name="claude-code", model="claude-opus-4-6"),
        task={"description": "Bug fix failing parser test"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Use grill-me to fix the parser bug and prove the failing test passes.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Plan:\n- reproduce failure\n- edit parser\n- rerun tests",
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
                    Observation(source_call_id="tc-skill", content="skill loaded"),
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
                            "file_path": file_path,
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
        ],
        outcome={"success": True, "committed": committed},
    )


def _enriched_filter_trace(trace_id: str) -> TraceRecord:
    record = _bug_fix_trace(
        trace_id,
        session_id=f"session-{trace_id}",
        file_path="src/parser.py",
    )
    record.agent.model = "anthropic/claude-opus-4-6"
    record.timestamp_end = "2026-04-20T12:00:00Z"
    record.dependencies = ["pytest", "flask"]
    record.git_links = [
        GitLink(vcs_type="git", revision="abc123", tier="tool_emitted")
    ]
    record.metadata["survival_state"] = "alive_on_path"
    record.steps.append(
        Step(
            step_index=5,
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
    return record


def _distractor_trace() -> TraceRecord:
    return TraceRecord(
        trace_id="trace-plan056-distractor",
        session_id="session-plan056-distractor",
        agent=Agent(name="claude-code"),
        task={"description": "Read docs that mention grill me as prose"},
        steps=[
            Step(step_index=1, role="user", content="Read the skill docs that say grill me."),
            Step(step_index=2, role="agent", content="No skill invocation happened."),
        ],
    )


def test_rebuild_index_creates_sqlite_fts_and_query_returns_bounded_packets(tmp_path):
    from opentraces.core import paths
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _bug_fix_trace("trace-plan056-old", generation_index=0))
    _write_project_trace(project, _bug_fix_trace("trace-plan056-new", generation_index=1))
    _write_project_trace(project, _distractor_trace())

    summary = rebuild_index()
    assert summary.trace_count == 3
    assert summary.unit_count > 3
    assert summary.index_path == paths.OPENTRACES_DIR / "index" / "index.db"

    with sqlite3.connect(summary.index_path) as conn:
        fts_tables = {
            row[0]
            for row in conn.execute(
                "select name from sqlite_master where type = 'table' and name like 'units_fts%'"
            )
        }
    assert "units_fts" in fts_tables

    # Plan-59 D1=(b): session_id/generation_index alone is no longer treated
    # as supersession. Both generations are returned because neither carries
    # an explicit `metadata.superseded_by` marker.
    skill_packets = query_index(skill="grill-me")
    assert sorted(packet.trace_id for packet in skill_packets) == [
        "trace-plan056-new",
        "trace-plan056-old",
    ]
    assert skill_packets[0].facets[0].name == "skill.name"

    packets = query_index(
        lex="bug fix failing test",
        signal="tested_successful_fix_candidate",
        latest_generation=True,
    )
    assert sorted(packet.trace_id for packet in packets) == [
        "trace-plan056-new",
        "trace-plan056-old",
    ]
    packet = next(p for p in packets if p.trace_id == "trace-plan056-new")
    assert packet.unit_type == "trace"
    assert packet.map_ref == "ot://trace/trace-plan056-new/map"
    assert packet.map_node_refs
    assert packet.score_parts["signal"] > 0
    assert packet.refs["trace"] == "trace-plan056-new"
    assert "return parse_good" not in packet.model_dump_json()
    assert "FAILED tests/test_parser.py::test_parser_regression" not in packet.model_dump_json()


def test_rebuild_index_uses_metadata_skill_invocations(tmp_path):
    from opentraces.core.trace_index import get_unit, query_index, rebuild_index

    project = tmp_path / "demo"
    _register_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project,
        TraceRecord(
            trace_id="trace-plan056-metadata-skill",
            session_id="session-plan056-metadata-skill",
            agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
            task={"description": "research browser-harness for VFS retrieval"},
            steps=[
                Step(step_index=1, role="agent", content="I will research the target."),
                Step(
                    step_index=2,
                    role="agent",
                    tool_calls=[
                        ToolCall(
                            tool_call_id="tc-read",
                            tool_name="Read",
                            input={"file_path": "kb/br/00-convention.md"},
                        )
                    ],
                ),
            ],
            metadata={
                "skill_invocations": [
                    {
                        "name": "scout-research",
                        "source": "claude_slash_command",
                        "command_name": "/scout-research",
                        "args": "research browser-harness for VFS retrieval",
                        "command_line_no": 0,
                        "body_line_no": 1,
                    }
                ]
            },
        ),
    )

    rebuild_index()
    packets = query_index(skill="scout-research", candidate_kind="skill_invocation")

    assert [packet.trace_id for packet in packets] == ["trace-plan056-metadata-skill"]
    assert packets[0].unit_type == "skill_invocation"
    assert packets[0].skills == ["scout-research"]
    unit = get_unit("tu:trace-plan056-metadata-skill:skill:metadata:0")
    assert unit is not None
    assert unit.metadata["source"] == "claude_slash_command"


def test_metadata_filters_and_pagination_narrow_candidate_packets(tmp_path):
    from opentraces.core.trace_index import query_index, query_index_page, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project,
        _bug_fix_trace(
            "trace-plan056-parser",
            session_id="session-plan056-parser",
            file_path="src/parser.py",
        ),
    )
    _write_project_trace(
        project,
        _bug_fix_trace(
            "trace-plan056-ui",
            session_id="session-plan056-ui",
            file_path="web/ui.tsx",
            committed=False,
        ),
    )
    _write_project_trace(project, _distractor_trace())
    rebuild_index()

    filtered = query_index(
        tool="Edit",
        files="src/*.py",
        file_kind="py",
        success=True,
        committed=True,
        candidate_kind="bug_fix",
    )
    assert [packet.trace_id for packet in filtered] == ["trace-plan056-parser"]

    first_page = query_index_page(
        signal="tested_successful_fix_candidate",
        limit=1,
        latest_generation=True,
    )
    assert len(first_page.candidates) == 1
    assert first_page.next_page_token == "offset:1"

    second_page = query_index_page(
        signal="tested_successful_fix_candidate",
        limit=1,
        page_token=first_page.next_page_token,
        latest_generation=True,
    )
    assert len(second_page.candidates) == 1
    assert second_page.next_page_token is None
    assert {first_page.candidates[0].trace_id, second_page.candidates[0].trace_id} == {
        "trace-plan056-parser",
        "trace-plan056-ui",
    }


def test_generic_facet_and_metadata_filters_are_anded(tmp_path):
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project,
        _bug_fix_trace(
            "trace-plan056-parser",
            session_id="session-plan056-parser",
            file_path="src/parser.py",
        ),
    )
    _write_project_trace(
        project,
        _bug_fix_trace(
            "trace-plan056-ui",
            session_id="session-plan056-ui",
            file_path="web/ui.tsx",
        ),
    )
    rebuild_index()

    packets = query_index(
        signal="tested_successful_fix_candidate",
        facet_filters=("file.path=src/parser.py", "agent.name=claude-code"),
        metadata_filters=("session_id=session-plan056-parser",),
    )

    assert [packet.trace_id for packet in packets] == ["trace-plan056-parser"]
    assert packets[0].score_parts["metadata"] > 0
    assert "src/parser.py" in packets[0].files


def test_named_plan056_filters_match_exact_and_derived_facets(tmp_path):
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _enriched_filter_trace("trace-plan056-enriched"))
    _write_project_trace(
        project,
        _bug_fix_trace(
            "trace-plan056-other",
            session_id="session-plan056-other",
            file_path="web/ui.tsx",
        ),
    )
    rebuild_index()

    packets = query_index(
        signal="tested_successful_fix_candidate",
        provider="anthropic",
        cmd_family="pytest",
        bash_action="test",
        test_framework="pytest",
        service="localhost",
        service_channel="http",
        dependency="pytest",
        git_tier="tool_emitted",
        survival="alive_on_path",
        since="2026-04-01",
        file_op="edit",
    )

    assert [packet.trace_id for packet in packets] == ["trace-plan056-enriched"]
    facet_pairs = {
        (facet.name, str(facet.value))
        for facet in packets[0].facets
    }
    assert ("provider.kind", "anthropic") in facet_pairs
    assert ("bash.command_family", "pytest") in facet_pairs
    assert ("bash.action", "test") in facet_pairs
    assert ("test.framework", "pytest") in facet_pairs
    assert ("service.name", "localhost") in facet_pairs
    assert ("dependency.name", "pytest") in facet_pairs
    assert packets[0].score_parts["metadata"] >= 10


def test_lexical_query_uses_token_search_not_substring_matching(tmp_path):
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _bug_fix_trace("trace-plan056-bug"))
    _write_project_trace(
        project,
        TraceRecord(
            trace_id="trace-plan056-debug",
            session_id="session-plan056-debug",
            agent=Agent(name="claude-code"),
            task={"description": "Debug release note formatting"},
            steps=[Step(step_index=1, role="user", content="Debug the release notes.")],
        ),
    )
    rebuild_index()

    packets = query_index(lex="bug")

    assert [packet.trace_id for packet in packets] == ["trace-plan056-bug"]


def test_redacted_or_malformed_service_urls_do_not_break_indexing(tmp_path):
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    record = _bug_fix_trace("trace-plan056-redacted-url")
    record.steps.append(
        Step(
            step_index=5,
            role="agent",
            tool_calls=[
                ToolCall(
                    tool_call_id="tc-redacted-url",
                    tool_name="Bash",
                    input={"command": "curl http://[REDACTED]/api/health"},
                )
            ],
        )
    )
    _write_project_trace(project, record)

    rebuild_index()
    packets = query_index(skill="grill-me")

    assert [packet.trace_id for packet in packets] == ["trace-plan056-redacted-url"]


def test_rebuild_index_skips_malformed_jsonl_lines_without_dropping_valid_records(tmp_path):
    from opentraces.core.config import get_project_traces_dir
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    traces_dir = get_project_traces_dir(project)
    traces_dir.mkdir(parents=True, exist_ok=True)
    valid = _bug_fix_trace("trace-plan056-valid-jsonl")
    (traces_dir / "mixed.jsonl").write_text(
        "\n".join(
            [
                "{not valid json",
                json.dumps({"trace_id": "missing-required-fields"}),
                valid.model_dump_json(),
            ]
        )
        + "\n"
    )

    summary = rebuild_index()
    packets = query_index(skill="grill-me")

    assert summary.trace_count == 1
    assert [packet.trace_id for packet in packets] == ["trace-plan056-valid-jsonl"]


def test_trace_index_redacts_secret_shapes_from_packets_units_and_map_previews(tmp_path):
    from opentraces.core.trace_index import get_trace_map, get_unit, query_index, rebuild_index

    openai_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
    aws_key = "AKIAIOSFODNN7EXAMPLE"
    password = "PASSWORD=hunter2"
    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    record = TraceRecord(
        trace_id="trace-plan056-secret-proof",
        session_id="session-plan056-secret-proof",
        agent=Agent(name="claude-code"),
        task={"description": f"Rotate leaked credential OPENAI_API_KEY={openai_key}"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content=f"Use grill-me to inspect the auth failure with {password}.",
            ),
            Step(
                step_index=2,
                role="agent",
                content="Plan:\n- inspect failing auth\n- avoid leaking secrets",
                tool_calls=[
                    ToolCall(
                        tool_call_id="tc-skill",
                        tool_name="Skill",
                        input={"name": "grill-me"},
                    ),
                    ToolCall(
                        tool_call_id="tc-auth",
                        tool_name="Bash",
                        input={"command": f"curl https://api.example.test?token={openai_key}"},
                    ),
                ],
                observations=[
                    Observation(
                        source_call_id="tc-auth",
                        content=f"403 denied for access key {aws_key}",
                        output_summary=f"auth denied for {aws_key}",
                    )
                ],
            ),
            Step(step_index=3, role="agent", content="Done without exposing credentials."),
        ],
    )
    _write_project_trace(project, record)

    rebuild_index()
    packets = query_index(skill="grill-me")
    unit = get_unit("tu:trace-plan056-secret-proof:trace")
    trace_map = get_trace_map("trace-plan056-secret-proof")

    assert len(packets) == 1
    assert unit is not None
    assert trace_map is not None
    indexed_payload = json.dumps(
        {
            "packet": packets[0].model_dump(mode="json"),
            "unit": unit.model_dump(mode="json"),
            "map": trace_map.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    for raw_secret in (openai_key, aws_key, password, "hunter2"):
        assert raw_secret not in indexed_payload
    assert "[REDACTED]" in indexed_payload


def test_get_accessors_do_not_silently_rebuild_missing_index(tmp_path):
    from opentraces.core.trace_index import (
        default_index_path,
        get_map_node,
        get_trace_map,
        get_trace_path,
        get_unit,
    )

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _bug_fix_trace("trace-plan056-missing-index"))

    index_path = default_index_path()
    assert not index_path.exists()

    assert get_unit("tu:trace-plan056-missing-index:trace") is None
    assert get_trace_map("trace-plan056-missing-index") is None
    assert get_map_node("tmn:trace-plan056-missing-index:1") is None
    assert get_trace_path("trace-plan056-missing-index") is None
    assert not index_path.exists()


def test_rebuild_index_adds_remaining_m1_trace_unit_types(tmp_path):
    from opentraces.core.trace_index import get_unit, query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    record = _bug_fix_trace("trace-plan056-units")
    _write_project_trace(project, record)
    rebuild_index()

    intent_unit = get_unit("tu:trace-plan056-units:intent")
    slice_unit = get_unit("tu:trace-plan056-units:slice:intent")
    skill_unit = get_unit("tu:trace-plan056-units:skill:tc-skill")
    sequence_unit = get_unit("tu:trace-plan056-units:tool-sequence")

    assert intent_unit is not None
    assert intent_unit.unit_type == "trace_intent_candidate"
    assert "Use grill-me" in intent_unit.intent_text

    assert slice_unit is not None
    assert slice_unit.unit_type == "trace_slice"
    assert slice_unit.metadata["slice_kind"] == "intent"
    assert slice_unit.metadata["map_node_refs"]

    assert skill_unit is not None
    assert skill_unit.unit_type == "skill_invocation"
    assert skill_unit.skills == ["grill-me"]

    assert sequence_unit is not None
    assert sequence_unit.unit_type == "tool_sequence"
    assert sequence_unit.metadata["tools"] == ["Skill", "Bash", "Edit", "Bash"]

    packets = query_index(
        skill="grill-me",
        facet_filters=("unit.type=skill_invocation",),
    )
    assert [packet.unit_type for packet in packets] == ["skill_invocation"]
    assert packets[0].unit_id == "tu:trace-plan056-units:skill:tc-skill"


def test_query_refreshes_incrementally_when_trace_store_changes(tmp_path):
    import os

    from opentraces.core.trace_index import default_index_path, query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project,
        _bug_fix_trace("trace-plan056-first", session_id="session-plan056-first"),
    )
    rebuild_index()
    index_path = default_index_path()
    inode_before = os.stat(index_path).st_ino

    assert [packet.trace_id for packet in query_index(skill="grill-me")] == [
        "trace-plan056-first"
    ]

    _write_project_trace(
        project,
        _bug_fix_trace("trace-plan056-second", session_id="session-plan056-second"),
    )

    packets = query_index(skill="grill-me")

    assert {packet.trace_id for packet in packets} == {
        "trace-plan056-first",
        "trace-plan056-second",
    }
    assert os.stat(index_path).st_ino == inode_before


def test_query_refresh_updates_modified_and_deleted_trace_sources(tmp_path):
    import os

    from opentraces.core.config import get_project_traces_dir
    from opentraces.core.trace_index import default_index_path, query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(project, _bug_fix_trace("trace-plan056-stale"))
    rebuild_index()
    index_path = default_index_path()
    inode_before = os.stat(index_path).st_ino

    packets = query_index(skill="grill-me")
    assert packets[0].files == ["src/parser.py"]

    _write_project_trace(
        project,
        _bug_fix_trace("trace-plan056-stale", file_path="src/changed.py"),
    )
    trace_path = get_project_traces_dir(project) / "trace-plan056-stale.jsonl"
    stat = trace_path.stat()
    os.utime(trace_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    packets = query_index(skill="grill-me")
    assert [packet.trace_id for packet in packets] == ["trace-plan056-stale"]
    assert packets[0].files == ["src/changed.py"]

    trace_path.unlink()

    assert query_index(skill="grill-me") == []
    assert os.stat(index_path).st_ino == inode_before


def test_rebuild_index_replaces_duplicate_trace_ids_across_shards(tmp_path):
    from opentraces.core.trace_index import query_index, rebuild_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(project)
    traces_dir.mkdir(parents=True, exist_ok=True)
    old = _bug_fix_trace("trace-plan056-duplicate", file_path="old.py")
    new = _bug_fix_trace("trace-plan056-duplicate", file_path="new.py")
    (traces_dir / "a.jsonl").write_text(old.model_dump_json() + "\n")
    (traces_dir / "b.jsonl").write_text(new.model_dump_json() + "\n")

    summary = rebuild_index()
    packets = query_index(skill="grill-me")

    assert summary.trace_count == 1
    assert [packet.trace_id for packet in packets] == ["trace-plan056-duplicate"]
    assert packets[0].files == ["new.py"]


def test_rebuild_index_preserves_existing_cache_when_rebuild_fails(tmp_path, monkeypatch):
    from opentraces.core import trace_index

    project = tmp_path / "demo"
    _enroll_project(project, "1234567890abcdef1234567890abcdef")
    _write_project_trace(
        project,
        _bug_fix_trace("trace-plan056-first", session_id="session-plan056-first"),
    )
    summary = trace_index.rebuild_index()

    assert trace_index.get_unit("tu:trace-plan056-first:trace") is not None
    assert summary.index_path.exists()

    _write_project_trace(
        project,
        _bug_fix_trace("trace-plan056-second", session_id="session-plan056-second"),
    )
    original_insert_unit = trace_index._insert_unit

    def fail_on_second_trace(conn, unit):
        if unit.trace_id == "trace-plan056-second":
            raise RuntimeError("simulated rebuild failure")
        original_insert_unit(conn, unit)

    monkeypatch.setattr(trace_index, "_insert_unit", fail_on_second_trace)

    with pytest.raises(RuntimeError, match="simulated rebuild failure"):
        trace_index.rebuild_index()

    assert summary.index_path.exists()
    assert trace_index.get_unit("tu:trace-plan056-first:trace") is not None
    assert trace_index.get_unit("tu:trace-plan056-second:trace") is None


def test_rebuild_index_adds_trail_patch_and_git_anchor_units_without_authored_text(tmp_path):
    from opentraces.core.trace_index import get_trace_map, get_unit, query_index, rebuild_index
    from opentraces.core.trace_map import walk_trace_map
    from opentraces.core.trails import append_exact_patch_trail, build_trail_query_projection

    project = tmp_path / "demo"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=project, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=project, check=True)
    _register_project(project, "1234567890abcdef1234567890abcdef")

    (project / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "app.py"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=project, check=True)
    authored_text = "    return 'sensitive authored trail text'\n"
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
        trace_id="trace-plan056-trail",
        step_index=3,
        file_path="app.py",
        authored_text=authored_text,
        commit_ref=commit_sha,
        writer="test",
        capture_method=["hook_posttooluse"],
    )

    rebuild_index()
    projection = build_trail_query_projection(project)
    patch_row = next(iter(projection.patches_by_id.values()))
    anchor_row = next(iter(projection.anchors_by_id.values()))

    patch_unit = get_unit(f"tu:trace-plan056-trail:patch:{patch_row['trace_patch_id']}")
    anchor_unit = get_unit(f"tu:trace-plan056-trail:git-anchor:{anchor_row['git_anchor_id']}")

    assert patch_unit is not None
    assert patch_unit.unit_type == "patch"
    assert patch_unit.files == ["app.py"]
    assert patch_unit.metadata["trace_patch_id"] == patch_row["trace_patch_id"]
    assert patch_unit.metadata["commit_sha"] == commit_sha
    assert patch_unit.signals[0].name == "trail_patch_created"
    assert patch_unit.trail_refs
    assert all(ref.startswith("ot://") for ref in patch_unit.trail_refs)
    assert all(
        ref.startswith("ot://")
        for signal in patch_unit.signals
        for ref in signal.evidence_refs
    )

    assert anchor_unit is not None
    assert anchor_unit.unit_type == "git_anchor"
    assert anchor_unit.metadata["git_anchor_id"] == anchor_row["git_anchor_id"]
    assert anchor_unit.metadata["trace_patch_id"] == patch_row["trace_patch_id"]
    assert any(facet.name == "git.commit_sha" for facet in anchor_unit.facets)

    indexed_text = " ".join(
        [
            patch_unit.title_text,
            patch_unit.intent_text,
            patch_unit.action_text,
            patch_unit.evidence_text,
            patch_unit.artifact_text,
        ]
    )
    assert "sensitive authored trail text" not in indexed_text

    packets = query_index(facet_filters=("unit.type=patch",))
    assert [packet.unit_id for packet in packets] == [patch_unit.unit_id]
    assert packets[0].unit_type == "patch"
    assert all(ref.startswith("ot://") for ref in packets[0].trail_refs)
    assert packets[0].map_node_refs

    trail_map = get_trace_map("trace-plan056-trail")
    assert trail_map is not None
    assert [node.action_type for node in trail_map.nodes] == ["patch_created", "git_anchor"]
    assert trail_map.nodes[0].unit_id == patch_unit.unit_id
    assert trail_map.nodes[1].unit_id == anchor_unit.unit_id
    forward = walk_trace_map(
        trail_map,
        trail_map.nodes[0].node_id,
        direction="forward",
        until_action_types={"git_anchor"},
    )
    assert [node.action_type for node in forward.nodes] == ["patch_created", "git_anchor"]


def test_query_reuses_unchanged_trail_projection_cache(tmp_path, monkeypatch):
    from opentraces.core import trails
    from opentraces.core.trace_index import query_index, rebuild_index
    from opentraces.core.trails import append_exact_patch_trail

    project = tmp_path / "demo"
    project.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=project, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=project, check=True)
    _register_project(project, "1234567890abcdef1234567890abcdef")

    (project / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "app.py"], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=project, check=True)
    authored_text = "    return 'cached trail projection'\n"
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
        trace_id="trace-plan056-trail-cache",
        step_index=3,
        file_path="app.py",
        authored_text=authored_text,
        commit_ref=commit_sha,
        writer="test",
        capture_method=["hook_posttooluse"],
    )

    rebuild_index()

    def fail_if_rebuilt(_repo):
        raise AssertionError("unchanged TrailEvents should use the cached projection")

    monkeypatch.setattr(trails, "build_trail_query_projection", fail_if_rebuilt)

    packets = query_index(facet_filters=("unit.type=patch",))

    assert [packet.trace_id for packet in packets] == ["trace-plan056-trail-cache"]
