"""The old root llm-review command is not part of the public CLI tree."""

from __future__ import annotations

from click.testing import CliRunner

from opentraces.cli import main


def test_root_llm_review_command_is_not_registered() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["llm-review", "--help"])
    assert result.exit_code == 2
    assert "No such command" in result.output


def test_setup_llm_review_remains_the_global_configuration_entrypoint() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["setup", "llm-review", "--help"])
    assert result.exit_code == 0
    assert "Configure the optional LLM dataset-row reviewer" in result.output
    assert "Tier 2" not in result.output
