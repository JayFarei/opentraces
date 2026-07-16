"""Launch the bench.v0 terminal-browser-terminal authentication exemplar."""

from __future__ import annotations

import json
import os

import pytest

from opentraces.core.arena.retrieval import StoredEvidence


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)


def _auth_mutations_from_ledger(rows):
    mutations = []
    for row in rows:
        assert isinstance(row, dict)
        operation_id = row.get("operation_id")
        method = row.get("method")
        path = row.get("path")
        response = row.get("response")
        assert isinstance(operation_id, str) and operation_id
        assert isinstance(method, str) and method
        assert isinstance(path, str) and path.startswith("/")
        assert isinstance(response, dict)
        status = response.get("status")
        assert type(status) is int and 200 <= status < 300
        if method != "GET":
            mutations.append((operation_id, method, path, status))

    assert mutations == [
        ("issueDeviceCode", "POST", "/oauth/device", 200),
        ("authorizeDeviceCode", "POST", "/oauth/authorize", 200),
        ("completeDeviceCode", "POST", "/oauth/token", 200),
    ]
    return [row[0] for row in mutations]


def cli_reports_authenticated(evidence, *, login=None, whoami=None, hf=None):
    """Use only the later public CLI observation as the verdict oracle."""

    if isinstance(evidence, StoredEvidence):
        result = evidence.read_json("result.json")
        verifier_name = (
            f"{cli_reports_authenticated.__module__}.{cli_reports_authenticated.__qualname__}"
        )
        verifier = next(row for row in result["verifiers"] if row["name"] == verifier_name)
        login_ref, whoami_ref, ledger_ref = verifier["evidence_refs"]
        login_result = evidence.read_json(login_ref)
        whoami_result = evidence.read_json(whoami_ref)
        assert login_result["returncode"] == 0
        assert whoami_result["returncode"] == 0
        payload = json.loads(evidence.read_text(whoami_result["stdout_ref"]))
        assert payload["authenticated"] is True
        for action_ref in (login_ref, whoami_ref):
            invocation = evidence.read_json(action_ref.replace("result.json", "invocation.json"))
            assert set(invocation["env_pins"]) == {
                "HF_ENDPOINT",
                "OPENTRACES_DISABLE_VERSION_CHECK",
            }
        _auth_mutations_from_ledger(
            list(map(json.loads, evidence.read_text(ledger_ref).splitlines()))
        )
        return {"evidence_refs": [login_ref, whoami_ref, ledger_ref]}

    assert login is not None and whoami is not None and hf is not None
    assert login.returncode == 0, login.stderr
    assert whoami.returncode == 0, whoami.stderr
    payload = whoami.json
    assert payload["authenticated"] is True
    _auth_mutations_from_ledger(hf.ledger.rows())
    return {"evidence_refs": [login.result_ref, whoami.result_ref, hf.ledger.evidence_ref]}


def test_browser_authorization_authenticates_the_cli(bench):
    """Browser authorization makes a separately invoked CLI report authenticated.

    The device flow begins in one terminal action, crosses the rendered provider
    page, then returns to a separate public ``auth whoami`` command. The sidecar
    ledger is retained only as corroborating exhaust; it does not decide the
    verdict.
    """

    with bench.run(app_state="install-only") as run:
        hf = run.emulate("huggingface")
        run.require_capabilities("cli:auth.login", "cli:auth.whoami")
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
        run.verify(
            cli_reports_authenticated,
            login=authenticated,
            whoami=whoami,
            hf=hf,
        )
        assert screenshot.state["path"] == (
            "recordings/browser/screenshots/hf-device-authorized.png"
        )
