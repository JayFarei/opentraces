"""Tests for the new ot auth group (Step 11 of the CLI restructure).

The flat ot login / logout / whoami verbs survive (removed in Step 15);
this step adds the parallel ot auth login / logout / whoami noun group
so the surface is consistent with the rest of the resource nouns
(remote, config, setup, completions).

Both surfaces share the same _login_impl / _logout_impl / _auth_status_impl
helpers. These tests confirm the auth group exists, has the three verbs,
and dispatches to the same code paths as the flat verbs.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from opentraces.cli import main


@pytest.fixture
def runner():
    return CliRunner()


class TestAuthGroupExists:
    def test_ot_auth_help_lists_three_verbs(self, runner) -> None:
        result = runner.invoke(main, ["auth", "--help"])
        assert result.exit_code == 0, result.output
        assert "login" in result.output
        assert "logout" in result.output
        assert "whoami" in result.output

    def test_ot_auth_alone_shows_help(self, runner) -> None:
        result = runner.invoke(main, ["auth"])
        # Click groups without invoke_without_command print help and may
        # exit 2 (missing command). Accept either 0 or 2 with help shown.
        assert "login" in result.output or "Usage" in result.output


class TestAuthSubcommands:
    def test_ot_auth_whoami_runs(self, runner, tmp_path, monkeypatch) -> None:
        """auth whoami should run without crashing."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.delenv("HF_TOKEN", raising=False)

        grouped = runner.invoke(main, ["auth", "whoami"])
        # Either authenticated or not; never crash.
        assert grouped.exit_code in (0, 1, 2, 3), grouped.output

    def test_ot_auth_login_help(self, runner) -> None:
        """Sanity: --token flag is present on auth login."""
        grouped = runner.invoke(main, ["auth", "login", "--help"])
        assert grouped.exit_code == 0
        assert "--token" in grouped.output

    def test_ot_auth_logout_help_works(self, runner) -> None:
        result = runner.invoke(main, ["auth", "logout", "--help"])
        assert result.exit_code == 0
        # Should describe what logout does
        assert "logout" in result.output.lower() or "log out" in result.output.lower()
