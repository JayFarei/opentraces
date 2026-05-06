"""Root-level inbox compatibility verbs are intentionally absent."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from opentraces.cli import main


@pytest.mark.parametrize(
    "verb",
    [
        "add",
        "show",
        "list",
        "reject",
        "reset",
        "redact",
        "discard",
        "llm-review",
        "push",
        "pull",
        "web",
        "tui",
        "remote",
        "blame",
        "graph",
        "resume",
    ],
)
def test_old_root_verbs_are_not_registered(verb: str) -> None:
    result = CliRunner().invoke(main, [verb, "--help"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_canonical_roots_are_registered() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    for verb in ["setup", "init", "trace", "trail", "dataset"]:
        assert f"ot {verb}" in result.output
    assert "ot workflow" not in result.output


def test_workflow_registry_remains_callable_but_not_advertised() -> None:
    result = CliRunner().invoke(main, ["workflow", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output
    assert "list" in result.output
    assert "remove" in result.output


def test_trace_forget_is_not_registered() -> None:
    result = CliRunner().invoke(main, ["trace", "forget", "--help"])
    assert result.exit_code != 0
    assert "No such command" in result.output
