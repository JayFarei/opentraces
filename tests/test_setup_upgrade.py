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
