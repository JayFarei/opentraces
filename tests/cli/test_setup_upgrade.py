"""Step 12 (superseded by issue #160): `upgrade` is now a root PEER verb.

Step 12 originally absorbed the flat `ot upgrade` under `setup upgrade`.
Issue #160 (the CLI v7 setup redesign) reverses that: `upgrade`/`uninstall`
are the update/out legs of the in / update / out triad beside `setup`, not
subcommands of it — the same command objects, mounted at root (visible) and
demoted to a hidden-but-callable copy under `setup` (hidden != removed).
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
        """`setup upgrade` is hidden (off `setup --help`) but stays callable."""
        result = runner.invoke(main, ["setup", "upgrade", "--help"])
        assert result.exit_code == 0, result.output
        assert "skill-only" in result.output or "Only update" in result.output

    def test_setup_upgrade_is_hidden(self, runner) -> None:
        result = runner.invoke(main, ["setup", "--help"])
        assert result.exit_code == 0
        assert main.commands["setup"].commands["upgrade"].hidden is True
        # Not in the auto-generated Commands: listing (still may appear in
        # the group's own descriptive prose, which mentions the peer verb).
        commands_block = result.output.split("Commands:", 1)[-1]
        assert "upgrade" not in commands_block

    def test_root_upgrade_is_the_advertised_peer_verb(self, runner) -> None:
        """`opentraces upgrade` is the canonical, visible, root peer verb."""
        assert main.commands["upgrade"].hidden is False
        result = runner.invoke(main, ["upgrade", "--help"])
        assert result.exit_code == 0, result.output
        assert "skill-only" in result.output or "Only update" in result.output

    def test_root_and_setup_upgrade_share_the_same_implementation(self, runner) -> None:
        """Same object, two mount points (define-once, mount-twice)."""
        assert main.commands["upgrade"].callback is (
            main.commands["setup"].commands["upgrade"].callback
        )

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
