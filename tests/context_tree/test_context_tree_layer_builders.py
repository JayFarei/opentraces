"""Unit tests for the Context Tree per-layer builders.

These cover Agent D's slice of the Phase 2 capture scaffold:

- ``build_system_layer`` (assembled system prompt approximation)
- ``build_messages_layer`` (active-path message history)
- ``build_tool_registry_layer`` (built-in + MCP + skill tools)
- ``build_runtime_state_layer`` (cwd, model, env, MCP server state)

Each function is exercised against empty inputs, the canonical
``context-tree-linear`` fixture, and the completeness/capture_method
honesty rules from the plan's "Honest gaps" table.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from opentraces.capture.claude_code.context_tree_capture import (
    DEFAULT_ENV_ALLOWLIST,
    JsonlRecord,
    build_messages_layer,
    build_parent_graph,
    build_runtime_state_layer,
    build_system_layer,
    build_tool_registry_layer,
    read_jsonl_records,
)


FIXTURES_DIR = Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"


# --------------------------------------------------------------------------- #
# Shared fixture helpers (mirrored from test_context_tree_capture_walker.py)
# --------------------------------------------------------------------------- #


def _run_fixture(name: str, tmp_path: Path) -> Path:
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(f"_fx_{name}", base / "session.py")
    assert spec is not None and spec.loader is not None, f"missing session.py in {base}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    transcript = tmp_path / "transcript.jsonl"
    mod.run(tmp_path, transcript)
    return transcript


def _init_record(records: list[JsonlRecord]) -> JsonlRecord | None:
    for rec in records:
        if rec.record_type == "system" and rec.subtype == "init":
            return rec
    return None


# --------------------------------------------------------------------------- #
# build_system_layer
# --------------------------------------------------------------------------- #


class TestBuildSystemLayer:

    def test_empty_inputs_produce_valid_layer(self):
        layer = build_system_layer(
            init_record=None,
            on_disk_claude_md=[],
            on_disk_memory_md=None,
            append_system_prompt_override=None,
            read_from_disk=False,
        )
        assert layer.layer_type == "system"
        assert layer.completeness == "approximated"
        assert layer.capture_method == "transcript_reconstruction"
        content = layer.content
        assert content["static_core_ref"] == "claude_code:unknown"
        assert content["claude_md_set"] == []
        assert content["memory_md_head_hash"] is None
        assert content["append_system_prompt_hash"] is None
        # Environment block is JSON-stringified, sorted keys.
        assert content["environment_block"].startswith('{"claude_code_version":')

    def test_uses_init_record_environment(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        init = _init_record(records)
        assert init is not None
        layer = build_system_layer(
            init_record=init,
            read_from_disk=False,
        )
        env = layer.content["environment_block"]
        assert str(tmp_path) in env
        assert "claude-sonnet-4-5" in env
        assert "otbox-fake-1.0" in env
        assert layer.content["static_core_ref"] == "claude_code:otbox-fake-1.0"

    def test_reads_project_claude_md_from_disk(self, tmp_path):
        # Seed a project CLAUDE.md, a local override, and one managed rule.
        (tmp_path / "CLAUDE.md").write_text("# project memory\n", encoding="utf-8")
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude" / "CLAUDE.md").write_text(
            "# local override\n", encoding="utf-8"
        )
        (tmp_path / ".claude" / "rules").mkdir()
        (tmp_path / ".claude" / "rules" / "style.md").write_text(
            "no long dashes\n", encoding="utf-8"
        )
        # An empty home so the user-scope file is absent.
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        layer = build_system_layer(
            init_record=None,
            project_dir=tmp_path,
            home_dir=fake_home,
            read_from_disk=True,
        )
        scopes = sorted(entry["scope"] for entry in layer.content["claude_md_set"])
        assert scopes == ["local", "managed", "project"]
        # Each entry has a content_hash with the canonical prefix.
        for entry in layer.content["claude_md_set"]:
            assert entry["content_hash"].startswith("sha256:")

    def test_caller_supplied_claude_md_wins_on_duplicate_path(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("disk\n", encoding="utf-8")
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        caller_entry = {
            "path": str(tmp_path / "CLAUDE.md"),
            "content_hash": "sha256:" + "a" * 64,
            "scope": "project",
            "load_reason": "caller",
        }
        layer = build_system_layer(
            init_record=None,
            on_disk_claude_md=[caller_entry],
            project_dir=tmp_path,
            home_dir=fake_home,
            read_from_disk=True,
        )
        entries = [
            e for e in layer.content["claude_md_set"]
            if e["path"] == str(tmp_path / "CLAUDE.md")
        ]
        assert len(entries) == 1
        assert entries[0]["load_reason"] == "caller"

    def test_missing_files_do_not_raise(self, tmp_path):
        fake_home = tmp_path / "fake_home"  # never created
        layer = build_system_layer(
            init_record=None,
            project_dir=tmp_path / "missing_project",
            home_dir=fake_home,
            read_from_disk=True,
        )
        assert layer.content["claude_md_set"] == []

    def test_append_system_prompt_override_hashed(self):
        layer = build_system_layer(
            init_record=None,
            append_system_prompt_override="you are helpful",
            read_from_disk=False,
        )
        assert layer.content["append_system_prompt_hash"].startswith("sha256:")

    def test_honesty_rule_capture_method_is_transcript_reconstruction(self):
        # Per the plan, the static core is approximated; the layer's
        # capture_method is the weakest contributor. We tag the whole
        # layer as transcript_reconstruction to keep the honesty signal
        # consistent across consumers.
        layer = build_system_layer(init_record=None, read_from_disk=False)
        assert layer.completeness == "approximated"
        assert layer.capture_method == "transcript_reconstruction"


# --------------------------------------------------------------------------- #
# build_messages_layer
# --------------------------------------------------------------------------- #


class TestBuildMessagesLayer:

    def test_empty_active_path_produces_empty_layer(self):
        layer = build_messages_layer(active_path=[])
        assert layer.layer_type == "messages"
        assert layer.completeness == "full"
        assert layer.capture_method == "transcript_reconstruction"
        assert layer.content["messages"] == []
        assert layer.content["total_token_estimate"] == 0
        assert layer.content["span_first_uuid"] == ""
        assert layer.content["span_last_uuid"] == ""
        assert layer.content["is_summary"] is False
        assert layer.content["summary_of_span"] is None

    def test_linear_fixture_yields_message_entries(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        graph = build_parent_graph(records)
        active = graph.active_path()
        layer = build_messages_layer(active_path=active)
        # Active path includes alternating user/assistant records.
        messages = layer.content["messages"]
        assert len(messages) >= 4
        roles = [m["role"] for m in messages]
        assert "user" in roles and "assistant" in roles
        # Every message carries a content_hash with the sha256 prefix.
        for m in messages:
            assert m["content_hash"].startswith("sha256:")
        # Span anchors are the first/last message uuids.
        assert layer.content["span_first_uuid"] == messages[0]["uuid"]
        assert layer.content["span_last_uuid"] == messages[-1]["uuid"]

    def test_token_estimate_is_char_length_quarter(self):
        # One assistant record with 40 chars of text -> estimate == 10.
        rec = JsonlRecord(
            offset=0,
            uuid="u-1",
            parent_uuid=None,
            record_type="assistant",
            subtype=None,
            raw={
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "x" * 40}],
                }
            },
        )
        layer = build_messages_layer(active_path=[rec])
        assert layer.content["total_token_estimate"] == 10

    def test_summary_flag_propagates(self):
        rec = JsonlRecord(
            offset=0,
            uuid="u-1",
            parent_uuid=None,
            record_type="user",
            subtype=None,
            raw={"message": {"role": "user", "content": "hi"}},
        )
        layer = build_messages_layer(
            active_path=[rec],
            is_summary=True,
            summary_of_span=("u-first", "u-last"),
        )
        assert layer.content["is_summary"] is True
        assert layer.content["summary_of_span"] == ["u-first", "u-last"]

    def test_non_message_records_filtered(self):
        result_rec = JsonlRecord(
            offset=0,
            uuid=None,
            parent_uuid=None,
            record_type="result",
            subtype="success",
            raw={},
        )
        layer = build_messages_layer(active_path=[result_rec])
        assert layer.content["messages"] == []


# --------------------------------------------------------------------------- #
# build_tool_registry_layer
# --------------------------------------------------------------------------- #


class TestBuildToolRegistryLayer:

    def test_empty_inputs_produce_valid_layer(self):
        layer = build_tool_registry_layer(init_record=None)
        assert layer.layer_type == "tool_registry"
        assert layer.completeness == "approximated"
        assert layer.capture_method == "transcript_reconstruction"
        assert layer.content["tools"] == []
        assert layer.content["deferred_tools"] == []

    def test_pulls_builtins_from_init_record(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        init = _init_record(records)
        layer = build_tool_registry_layer(init_record=init)
        names = sorted(t["name"] for t in layer.content["tools"])
        assert names == ["Bash", "Edit", "Read"]
        for tool in layer.content["tools"]:
            assert tool["source"] == "builtin"
            assert tool["source_path"] is None
            assert tool["input_schema"] is None

    def test_mcp_tools_namespaced_with_server(self):
        layer = build_tool_registry_layer(
            init_record=None,
            on_disk_mcp_servers={
                "gmail": {
                    "tools": [
                        {"name": "send", "description": "send a mail"},
                        {"name": "search", "description": "search inbox"},
                    ]
                }
            },
        )
        names = sorted(t["name"] for t in layer.content["tools"])
        assert names == ["mcp__gmail__search", "mcp__gmail__send"]
        for tool in layer.content["tools"]:
            assert tool["source"] == "mcp"
            assert tool["source_path"] == "mcp_server:gmail"

    def test_mcp_tool_names_list_shape_supported(self):
        layer = build_tool_registry_layer(
            init_record=None,
            on_disk_mcp_servers={"slack": {"tool_names": ["post", "react"]}},
        )
        names = sorted(t["name"] for t in layer.content["tools"])
        assert names == ["mcp__slack__post", "mcp__slack__react"]

    def test_skill_tools_included(self):
        layer = build_tool_registry_layer(
            init_record=None,
            on_disk_skills=[
                {
                    "name": "ship",
                    "description": "deploy",
                    "source_path": "~/.claude/skills/ship/SKILL.md",
                }
            ],
        )
        assert layer.content["tools"][0]["source"] == "skill"
        assert (
            layer.content["tools"][0]["source_path"]
            == "~/.claude/skills/ship/SKILL.md"
        )

    def test_deferred_tools_passed_through_and_deduped(self):
        layer = build_tool_registry_layer(
            init_record=None,
            deferred_tools=["A", "B", "A"],
        )
        assert layer.content["deferred_tools"] == ["A", "B"]

    def test_honesty_rule_completeness_approximated_for_v1(self, tmp_path):
        # Built-in schemas are missing in v1; the layer must declare that.
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        layer = build_tool_registry_layer(init_record=_init_record(records))
        assert layer.completeness == "approximated"
        # No tool has an input_schema yet — that's the documented gap.
        assert all(t["input_schema"] is None for t in layer.content["tools"])


# --------------------------------------------------------------------------- #
# build_runtime_state_layer
# --------------------------------------------------------------------------- #


class TestBuildRuntimeStateLayer:

    def test_empty_inputs_produce_valid_layer(self):
        layer = build_runtime_state_layer(init_record=None, env={})
        assert layer.layer_type == "runtime_state"
        assert layer.completeness == "full"
        assert layer.capture_method == "transcript_reconstruction"
        content = layer.content
        assert content["cwd"] == ""
        assert content["model"] == ""
        assert content["permission_mode"] == "default"
        assert content["claude_code_version"] == "unknown"
        assert content["effort_level"] is None
        assert content["allowlisted_env"] == {}
        assert content["mcp_servers"] == {}

    def test_pulls_init_record_fields(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        init = _init_record(records)
        layer = build_runtime_state_layer(init_record=init, env={})
        assert layer.content["cwd"] == str(tmp_path)
        assert layer.content["model"] == "claude-sonnet-4-5"
        assert layer.content["permission_mode"] == "default"
        assert layer.content["claude_code_version"] == "otbox-fake-1.0"

    def test_env_values_are_hashed_only_for_allowlisted_keys(self):
        env = {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-secret",  # NOT allowlisted -> dropped
            "CLAUDE_AGENT_TYPE": "claude-code",
            "RANDOM_THING": "noise",  # NOT allowlisted -> dropped
        }
        layer = build_runtime_state_layer(init_record=None, env=env)
        allowed = layer.content["allowlisted_env"]
        assert "PATH" in allowed
        assert "CLAUDE_AGENT_TYPE" in allowed  # CLAUDE_ prefix
        assert "OPENAI_API_KEY" not in allowed
        assert "RANDOM_THING" not in allowed
        for value in allowed.values():
            assert value.startswith("sha256:")
            # Raw value must never appear in storage.
            assert "sk-secret" not in value
            assert "/usr/bin" not in value
            assert "claude-code" not in value

    def test_allowlist_can_be_overridden(self):
        layer = build_runtime_state_layer(
            init_record=None,
            env={"MY_KEY": "value"},
            env_allowlist=("MY_KEY",),
        )
        assert "MY_KEY" in layer.content["allowlisted_env"]

    def test_effort_level_extracted_from_init(self):
        init = JsonlRecord(
            offset=0,
            uuid="sys-1",
            parent_uuid=None,
            record_type="system",
            subtype="init",
            raw={
                "cwd": "/tmp",
                "model": "claude-opus",
                "claude_code_version": "1.0",
                "effort": {"level": "high"},
            },
        )
        layer = build_runtime_state_layer(init_record=init, env={})
        assert layer.content["effort_level"] == "high"

    def test_mcp_servers_passed_through(self):
        servers = {"gmail": {"tool_names": ["send"], "instructions_hash": "sha256:abc"}}
        layer = build_runtime_state_layer(
            init_record=None,
            env={},
            on_disk_mcp_servers=servers,
        )
        assert layer.content["mcp_servers"] == servers

    def test_default_env_allowlist_includes_expected_keys(self):
        assert "PATH" in DEFAULT_ENV_ALLOWLIST
        assert "SHELL" in DEFAULT_ENV_ALLOWLIST
        assert "OPENAI_API_KEY" not in DEFAULT_ENV_ALLOWLIST

    def test_honesty_rule_completeness_full_capture_method_transcript(self):
        # All four fields derive from the init record; nothing missing,
        # so completeness=full. Capture method is the weakest contributor,
        # which the goal prompt pins at transcript_reconstruction.
        layer = build_runtime_state_layer(init_record=None, env={})
        assert layer.completeness == "full"
        assert layer.capture_method == "transcript_reconstruction"


# --------------------------------------------------------------------------- #
# Determinism cross-cuts
# --------------------------------------------------------------------------- #


class TestLayerIdsAreDeterministic:

    def test_messages_layer_id_stable_for_same_input(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        graph = build_parent_graph(records)
        a = build_messages_layer(active_path=graph.active_path())
        b = build_messages_layer(active_path=graph.active_path())
        assert a.layer_id == b.layer_id

    def test_tool_registry_layer_id_stable(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        init = _init_record(records)
        a = build_tool_registry_layer(init_record=init)
        b = build_tool_registry_layer(init_record=init)
        assert a.layer_id == b.layer_id

    def test_runtime_state_layer_id_stable_for_same_env(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        init = _init_record(records)
        env = {"PATH": "/usr/bin", "CLAUDE_AGENT_TYPE": "claude-code"}
        a = build_runtime_state_layer(init_record=init, env=env)
        b = build_runtime_state_layer(init_record=init, env=env)
        assert a.layer_id == b.layer_id

    def test_system_layer_id_stable_without_disk_reads(self, tmp_path):
        transcript = _run_fixture("context-tree-linear", tmp_path)
        records = read_jsonl_records(transcript)
        init = _init_record(records)
        a = build_system_layer(init_record=init, read_from_disk=False)
        b = build_system_layer(init_record=init, read_from_disk=False)
        assert a.layer_id == b.layer_id
