"""Launch the bench.v0 terminal-browser-terminal authentication exemplar."""

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)


def cli_reports_authenticated(run, *, whoami, hf):
    """Use only the later public CLI observation as the verdict oracle."""

    assert whoami.returncode == 0, whoami.stderr
    payload = whoami.json
    assert payload["authenticated"] is True
    hf.ledger.rows()
    return {"evidence_refs": [whoami.result_ref, hf.ledger.evidence_ref]}


def test_browser_authorization_authenticates_the_cli(bench):
    """Browser authorization makes a separately invoked CLI report authenticated.

    The device flow begins in one terminal action, crosses the rendered provider
    page, then returns to a separate public ``auth whoami`` command. The sidecar
    ledger is retained only as corroborating exhaust; it does not decide the
    verdict.
    """

    with bench.run(app_state="install-only") as run:
        hf = run.emulate("huggingface")
        auth_env = {"HF_ENDPOINT": hf.env["HF_ENDPOINT"]}
        prepared = run.terminal.exec(
            "sh",
            "-c",
            'rm -rf "$HOME/.cache/huggingface" "$HOME/.huggingface"',
            env=auth_env,
        )
        assert prepared.returncode == 0, prepared.stderr

        login = run.terminal.start(
            "opentraces",
            "auth",
            "login",
            "--device-timeout",
            "30",
            env=auth_env,
            timeout=40,
        )
        assert login.running is True

        verification_url = f"{hf.browser_endpoint}/oauth/authorize"
        run.browser.navigate(verification_url)
        run.browser.wait("text=Authorize OpenTraces", timeout_ms=5_000)
        run.browser.click("button:has-text('Authorize')")
        run.browser.wait("text=Authorized", timeout_ms=5_000)
        screenshot = run.browser.screenshot("hf-device-authorized", full_page=True)

        authenticated = login.wait(timeout=40)
        assert authenticated.returncode == 0, authenticated.stderr
        whoami = run.terminal.exec(
            "opentraces",
            "--json",
            "auth",
            "whoami",
            env=auth_env,
        )
        run.verify(cli_reports_authenticated, whoami=whoami, hf=hf)
        assert screenshot.state["path"] == (
            "recordings/browser/screenshots/hf-device-authorized.png"
        )
