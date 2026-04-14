"""Step 10: --project flag on setup subcommands that write config.

The plan's intent is uniform config-scope control. Hook installers
(claude-code, git, skill) write to system locations regardless of
scope; their --project would be misleading. Only the config-writing
subcommands get the flag: trufflehog, review-llm, review-policy.

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


@pytest.mark.parametrize("subcommand", ["trufflehog", "review-llm", "review-policy"])
def test_setup_subcommand_accepts_project_flag(runner, subcommand) -> None:
    """`--help` succeeds and lists --project."""
    result = runner.invoke(main, ["setup", subcommand, "--help"])
    assert result.exit_code == 0
    assert "--project" in result.output, (
        f"setup {subcommand} should accept --project; help was:\n{result.output}"
    )


def test_setup_review_policy_help_describes_scope(runner) -> None:
    """review-policy is the canonical per-project setup verb; help should
    mention scope flags."""
    result = runner.invoke(main, ["setup", "review-policy", "--help"])
    assert result.exit_code == 0
    assert "--project" in result.output
