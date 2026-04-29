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


# ---------------------------------------------------------------------------
# Commit-SHA and trace-handle completion (plan-043 follow-up)
# ---------------------------------------------------------------------------


@pytest.fixture
def complete_project(tmp_path, monkeypatch):
    """Set up a tmp project with one attributed commit + one staged trace.

    HOME / OPENTRACES_DIR isolation is handled by the autouse fixture
    in ``tests/conftest.py`` via ``monkeypatch.setattr`` on module
    globals — the old ``importlib.reload`` pattern broke class identity
    for cross-test ``pytest.raises(...)`` assertions.
    """
    from opentraces.core import cache as _cache
    from opentraces.core import config as _config
    from opentraces.core import state as _state_mod  # noqa: F401

    project = tmp_path / "proj"
    project.mkdir()
    # Opt-in marker.
    (project / ".opentraces.json").write_text(
        '{"project_id": "abc123", "policy": {}}'
    )
    monkeypatch.chdir(project)

    cache = _cache.AttributionCache(project)
    cache.write_attribution("2508ec10abcdef1234", {"traces": []})
    cache.write_attribution("25ffffffffffffffff", {"traces": []})
    cache.write_attribution("aabb00112233445566", {"traces": []})

    state = _state_mod.StateManager(_config.get_project_state_path(project))
    now = __import__("time").time()
    # Feed a trace entry matching the real cache trace_id.
    state._state.setdefault("traces", {})["b73af9c8-full-id"] = {
        "trace_id": "b73af9c8-full-id",
        "session_id": "b73af9c8-session",
        "status": "parsed",
        "created_at": now - 60,
    }
    state._state["traces"]["cafebabe-other"] = {
        "trace_id": "cafebabe-other",
        "session_id": "cafebabe-sess",
        "status": "parsed",
        "created_at": now - 120,
    }
    state.save()

    yield project


def _complete(runner, *tokens):
    result = runner.invoke(main, ["__complete", *tokens])
    assert result.exit_code == 0, result.output
    return [ln for ln in result.output.splitlines() if ln]


def test_complete_commit_prefix_bare(runner, complete_project):
    lines = _complete(runner, "blame", "25")
    names = [ln.split("\t")[0] for ln in lines]
    assert "2508ec10" in names
    assert "25ffffff" in names
    assert "aabb0011" not in names


def test_complete_commit_prefix_with_c_handle(runner, complete_project):
    lines = _complete(runner, "blame", "c:25")
    names = [ln.split("\t")[0] for ln in lines]
    assert "c:2508ec10" in names
    assert "c:25ffffff" in names


def test_complete_trace_prefix_bare(runner, complete_project):
    lines = _complete(runner, "graph", "--trace", "b7")
    names = [ln.split("\t")[0] for ln in lines]
    assert "b73af9c8" in names


def test_complete_trace_prefix_with_t_handle(runner, complete_project):
    lines = _complete(runner, "graph", "--trace", "t:b7")
    names = [ln.split("\t")[0] for ln in lines]
    assert "t:b73af9c8" in names


def test_complete_trace_ambiguous_returns_multiple(runner, complete_project):
    lines = _complete(runner, "graph", "--trace", "")
    names = [ln.split("\t")[0] for ln in lines]
    # No filter -> both traces show up (both actionable status).
    assert "b73af9c8" in names
    assert "cafebab" in " ".join(names)


# ---------------------------------------------------------------------------
# Dataset name completion
# ---------------------------------------------------------------------------


@pytest.fixture
def datasets_project(tmp_path, monkeypatch):
    """Seed two local datasets so the dataset-name completer has data."""
    from opentraces.core.datasets import (
        add_dataset_remote,
        create_dataset,
    )

    create_dataset("alpha-traces", workflow_skill="curator", workflow_digest="sha256:w")
    create_dataset("beta-traces", workflow_skill="curator", workflow_digest="sha256:w")
    add_dataset_remote("alpha-traces", "me/alpha-traces", visibility="private")
    yield tmp_path


def test_complete_dataset_name_full_list(runner, datasets_project):
    lines = _complete(runner, "dataset", "show", "")
    names = [ln.split("\t")[0] for ln in lines]
    assert "alpha-traces" in names
    assert "beta-traces" in names


def test_complete_dataset_name_prefix_filter(runner, datasets_project):
    lines = _complete(runner, "dataset", "publish", "alpha")
    names = [ln.split("\t")[0] for ln in lines]
    assert "alpha-traces" in names
    assert "beta-traces" not in names


def test_complete_dataset_name_skipped_for_new(runner, datasets_project):
    lines = _complete(runner, "dataset", "new", "")
    names = [ln.split("\t")[0] for ln in lines]
    # `new` takes a name that doesn't exist yet, so existing names must
    # not be proposed.
    assert "alpha-traces" not in names
    assert "beta-traces" not in names


def test_complete_dataset_name_skipped_outside_dataset_tree(runner, datasets_project):
    # The trace tree also has commands with a `name` argument elsewhere;
    # the dataset-name completer must not leak outside `ot dataset ...`.
    lines = _complete(runner, "trace", "")
    names = [ln.split("\t")[0] for ln in lines]
    assert "alpha-traces" not in names
    assert "beta-traces" not in names


def test_complete_dataset_name_works_for_remote_subcommand(runner, datasets_project):
    # `ot dataset remote add <NAME> <REPO>` — first positional is `name`,
    # which should complete from existing datasets.
    lines = _complete(runner, "dataset", "remote", "add", "")
    names = [ln.split("\t")[0] for ln in lines]
    assert "alpha-traces" in names
    assert "beta-traces" in names
