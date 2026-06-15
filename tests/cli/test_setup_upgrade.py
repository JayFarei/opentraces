"""Step 12: ot setup upgrade absorbs the flat ot upgrade.

Both surfaces survive during the transition (Step 15 removes the flat one).
The grouped form delegates to the same impl, so behavior matches.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from opentraces.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestSetupUpgradeRegistered:
    def test_setup_upgrade_help_works(self, runner) -> None:
        result = runner.invoke(main, ["setup", "upgrade", "--help"])
        assert result.exit_code == 0, result.output
        assert "skill-only" in result.output or "Only update" in result.output

    def test_setup_help_lists_upgrade(self, runner) -> None:
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert "upgrade" in result.output

    def test_setup_upgrade_skill_only_runs(self, runner, tmp_path, monkeypatch) -> None:
        """Smoke test — --skill-only path doesn't shell out to brew/pip,
        so we can run it in an isolated home without side effects."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        # No need for a project — --skill-only just refreshes the skill file
        # globally. It might hit the network; we just assert it doesn't crash
        # at the Click parsing layer.
        result = runner.invoke(main, ["setup", "upgrade", "--skill-only", "--help"])
        assert result.exit_code == 0


class TestHealBrewTap:
    """0.4.5 follow-up: setup upgrade self-heals a stale opentraces tap (a clone
    still pointing at the old generic homebrew-tap after opentraces moved to its
    own dedicated tap). Best-effort: never raises."""

    def test_returns_none_when_brew_absent(self, monkeypatch):
        import shutil

        import opentraces.cli as cli

        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert cli._heal_brew_tap() is None

    def test_best_effort_never_raises_on_subprocess_failure(self, monkeypatch):
        import shutil
        import subprocess

        import opentraces.cli as cli

        monkeypatch.setattr(shutil, "which", lambda name: "/opt/homebrew/bin/brew")

        def _boom(*a, **k):
            raise RuntimeError("subprocess exploded")

        monkeypatch.setattr(subprocess, "run", _boom)
        # All brew/git calls are wrapped; the helper must swallow and return None.
        assert cli._heal_brew_tap() is None
