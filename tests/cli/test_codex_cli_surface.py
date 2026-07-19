from __future__ import annotations

import json

from click.testing import CliRunner

from opentraces.cli import main


def test_capabilities_json_reports_agent_interface_from_registry():
    result = CliRunner().invoke(main, ["capabilities", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["schema_version"] == "opentraces.capabilities.v0"
    agent = next(row for row in payload["interfaces"] if row["id"] == "agent")
    assert agent["harnesses"] == ["claude-code", "codex-cli", "pi"]


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
