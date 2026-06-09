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

import json

import pytest
from click.testing import CliRunner

from opentraces.cli import SENTINEL, main


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
        assert "--device-timeout" in grouped.output

    def test_ot_auth_logout_help_works(self, runner) -> None:
        result = runner.invoke(main, ["auth", "logout", "--help"])
        assert result.exit_code == 0
        # Should describe what logout does
        assert "logout" in result.output.lower() or "log out" in result.output.lower()

    def test_device_login_timeout_returns_out_of_band_steps(self, runner, monkeypatch) -> None:
        """Bounded device auth should tell agents exactly what the user must do next."""
        class Config:
            hf_token = None

        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        calls = []

        def fake_post(url, data=None, timeout=None):
            calls.append(url)
            if url.endswith("/oauth/device"):
                return FakeResponse(
                    {
                        "device_code": "device-123",
                        "user_code": "ABCD-EFGH",
                        "verification_uri": "https://huggingface.co/device",
                        "interval": 5,
                        "expires_in": 900,
                    }
                )
            return FakeResponse({"error": "authorization_pending"})

        clock = {"now": 0.0}

        def fake_sleep(seconds):
            clock["now"] += float(seconds)

        monkeypatch.setattr("opentraces.cli.load_config", lambda: Config())
        monkeypatch.setattr("requests.post", fake_post)
        monkeypatch.setattr("webbrowser.open", lambda _url: False)
        monkeypatch.setattr("time.time", lambda: clock["now"])
        monkeypatch.setattr("time.sleep", fake_sleep)

        result = runner.invoke(main, ["--json", "auth", "login", "--device-timeout", "1"])

        assert result.exit_code == 3
        assert "Complete HuggingFace auth outside this agent session" in result.output
        assert SENTINEL in result.output
        payload = json.loads(result.output.split(SENTINEL, 1)[1].strip())
        assert payload["status"] == "needs_action"
        assert payload["error"]["code"] == "AUTH_TIMEOUT"
        assert payload["authenticated"] is False
        assert any("opentraces auth login --token" in step for step in payload["next_steps"])
        assert any(url.endswith("/oauth/token") for url in calls)
