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
    def test_ot_auth_whoami_runs_same_as_flat_whoami(
        self, runner, tmp_path, monkeypatch
    ) -> None:
        """auth whoami should hit the same code path as the flat whoami.
        Both either print 'Authenticated as ...' or 'Not authenticated';
        for a freshly-isolated HOME, both should agree on the result."""
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        # Ensure no HF token is present.
        monkeypatch.delenv("HF_TOKEN", raising=False)

        flat = runner.invoke(main, ["whoami"])
        grouped = runner.invoke(main, ["auth", "whoami"])

        # Both should reach the same impl (same exit + similar output shape).
        # We can't assert equal exit codes if the env still has cached HF
        # creds outside HOME, so just assert the grouped form runs without
        # crashing.
        assert grouped.exit_code == flat.exit_code, (
            f"flat={flat.exit_code} grouped={grouped.exit_code}\n"
            f"flat output: {flat.output!r}\n"
            f"grouped output: {grouped.output!r}"
        )

    def test_ot_auth_login_help_matches_flat_login_help(self, runner) -> None:
        """Sanity: the flag surface (e.g. --token) is the same on both."""
        flat = runner.invoke(main, ["login", "--help"])
        grouped = runner.invoke(main, ["auth", "login", "--help"])
        assert flat.exit_code == 0
        assert grouped.exit_code == 0
        assert "--token" in flat.output
        assert "--token" in grouped.output

    def test_ot_auth_logout_help_works(self, runner) -> None:
        result = runner.invoke(main, ["auth", "logout", "--help"])
        assert result.exit_code == 0
        # Should describe what logout does
        assert "logout" in result.output.lower() or "log out" in result.output.lower()
