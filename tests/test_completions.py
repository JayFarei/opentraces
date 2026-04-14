"""Tests for `ot completions` noun and hidden `__complete` resolver."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def test_completions_bare_detects_shell_from_env(runner, monkeypatch):
    monkeypatch.setenv("SHELL", "/bin/zsh")
    result = runner.invoke(main, ["completions"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() != ""
    assert "#compdef ot" in result.output


def test_completions_zsh_prints_zsh_script(runner):
    result = runner.invoke(main, ["completions", "zsh"])
    assert result.exit_code == 0
    assert "#compdef ot" in result.output
    assert "_ot" in result.output


def test_completions_bash_prints_bash_script(runner):
    result = runner.invoke(main, ["completions", "bash"])
    assert result.exit_code == 0
    assert "complete -F" in result.output
    assert "ot" in result.output


def test_completions_fish_prints_fish_script(runner):
    result = runner.invoke(main, ["completions", "fish"])
    assert result.exit_code == 0
    assert "complete -c ot" in result.output


def test_install_zsh_writes_script_and_sources_from_rc(runner, isolated_home):
    result = runner.invoke(main, ["completions", "install", "zsh"])
    assert result.exit_code == 0, result.output
    script = isolated_home / ".config" / "opentraces" / "completions" / "_ot.zsh"
    assert script.exists()
    assert "#compdef ot" in script.read_text()
    rc = isolated_home / ".zshrc"
    assert rc.exists()
    rc_text = rc.read_text()
    assert str(script) in rc_text
    assert "opentraces completions" in rc_text


def test_install_is_idempotent(runner, isolated_home):
    runner.invoke(main, ["completions", "install", "zsh"])
    runner.invoke(main, ["completions", "install", "zsh"])
    rc = isolated_home / ".zshrc"
    text = rc.read_text()
    # Source line should appear exactly once.
    count = text.count("opentraces completions")
    assert count == 1, f"duplicate source line, found {count}:\n{text}"


def test_uninstall_removes_file_and_source_line(runner, isolated_home):
    runner.invoke(main, ["completions", "install", "zsh"])
    result = runner.invoke(main, ["completions", "uninstall", "zsh"])
    assert result.exit_code == 0, result.output
    script = isolated_home / ".config" / "opentraces" / "completions" / "_ot.zsh"
    assert not script.exists()
    rc = isolated_home / ".zshrc"
    text = rc.read_text() if rc.exists() else ""
    assert "opentraces completions" not in text


def test_quiet_suppresses_install_output(runner, isolated_home):
    result = runner.invoke(main, ["completions", "install", "zsh", "--quiet"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test_quiet_suppresses_uninstall_output(runner, isolated_home):
    runner.invoke(main, ["completions", "install", "zsh"])
    result = runner.invoke(main, ["completions", "uninstall", "-q", "zsh"])
    assert result.exit_code == 0
    assert result.output.strip() == ""


def test___complete_returns_candidates(runner):
    # With no tokens, should list some top-level subcommand names.
    result = runner.invoke(main, ["__complete"])
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln]
    # "completions" should be among the candidates (non-hidden subcommand).
    assert any("completions" in ln for ln in lines)


def test___complete_with_partial(runner):
    # Partial "comp" should at least include "completions".
    result = runner.invoke(main, ["__complete", "comp"])
    assert result.exit_code == 0
    assert "completions" in result.output


def test___complete_hidden_from_help(runner):
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "__complete" not in result.output


def test_invalid_shell_errors(runner):
    result = runner.invoke(main, ["completions", "install", "fishbash"])
    assert result.exit_code == 2, result.output
