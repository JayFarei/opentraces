"""Step 10: --project flag on setup subcommands that write config.

The plan's intent is uniform config-scope control. Hook installers
(claude-code, git, skill) write to system locations regardless of
scope; their --project would be misleading. Only the global security setup
commands that write config get the flag: trufflehog and llm-review.

These tests confirm the flag is accepted at the Click parsing layer.
End-to-end behavior (project marker vs global config) is exercised
by the existing test_config_set.py and test_project_config_migration.py.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from opentraces.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.mark.parametrize("subcommand", ["trufflehog", "llm-review"])
def test_setup_subcommand_accepts_project_flag(runner, subcommand) -> None:
    """`--help` succeeds and lists --project."""
    result = runner.invoke(main, ["setup", subcommand, "--help"])
    assert result.exit_code == 0
    assert "--project" in result.output, (
        f"setup {subcommand} should accept --project; help was:\n{result.output}"
    )


def test_setup_review_policy_is_not_registered(runner) -> None:
    result = runner.invoke(main, ["setup", "review-policy", "--help"])
    assert result.exit_code == 2
    assert "No such command" in result.output
