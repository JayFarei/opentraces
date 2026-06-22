from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli import main


def test_capabilities_json_reports_agents_from_registry():
    result = CliRunner().invoke(main, ["capabilities", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "claude-code" in payload["agents"]
    assert "codex-cli" in payload["agents"]
    assert "pi" in payload["agents"]
    assert set(payload["features"]) >= {
        "private_bucket",
        "bucket_remote_sync",
        "trace_index",
        "trace_query",
        "trace_map",
        "trace_slice",
        "trace_teleport",
        "trace_trails",
        "context_tree",
        "otlp_receiver",
        "codex_cli_capture",
        "pi_capture",
        "workflow_templates",
        "security_tool_registry",
    }


def test_introspect_commands_follow_click_tree():
    result = CliRunner().invoke(main, ["introspect"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    commands = payload["commands"]

    # Core groups stay visible (capture-otlp is now hidden under the plan-087
    # core-surface simplification, but still registered + callable).
    for root in ("bucket", "security", "ctx", "workflow"):
        assert root in commands
        assert commands[root]["hidden"] is False
    assert commands["capture-otlp"]["hidden"] is True
    assert "status" in commands["capture-otlp"]["children"]  # still callable

    assert "index" in commands["trace"]["children"]
    assert "teleport" in commands["trace"]["children"]
    assert "templates" in commands["workflow"]["children"]
    assert "sanitize" in commands["security"]["children"]
    assert "tools" in commands["security"]["children"]

    trail_children = commands["trail"]["children"]
    assert trail_children["resume"]["hidden"] is True
    assert trail_children["sync"]["hidden"] is True

    assert (
        payload["exit_codes"]["7"]
        == "Lock/busy (concurrent local operation or remote sync lock)"
    )
