from __future__ import annotations

import inspect
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from opentraces.core.arena.box import BoxCommandResult
from opentraces.core.arena.engine import Bench
from opentraces.core.arena.retrieval import reverify_stored_run
from opentraces.core.arena.run_store import RunStore
from tests.arena.scenarios import test_browser_auth_reaches_hf as scenario
from tests.core.arena.test_browser_drive import PublicBrowserSession
from tests.core.arena.test_hf_emulator_run import (
    WorldRuntime,
    _source,
    _trust_test_binary,
)


def _result(argv: list[str], *, stdout: str = "", returncode: int = 0) -> BoxCommandResult:
    return BoxCommandResult(
        argv=argv,
        returncode=returncode,
        stdout=stdout,
        stderr="" if returncode == 0 else "command failed\n",
        timing={"schemaVersion": 1, "timing": {"exitCode": returncode}},
    )


def _cold_ledger_rows() -> list[dict[str, object]]:
    return [
        {
            "method": "GET",
            "path": "/_emulate/manifest",
            "operation_id": "manifest",
            "response": {"status": 200},
        },
        {
            "method": "POST",
            "path": "/oauth/device",
            "operation_id": "issueDeviceCode",
            "response": {"status": 200},
        },
        {
            "method": "GET",
            "path": "/oauth/authorize",
            "operation_id": "viewDeviceAuthorization",
            "response": {"status": 200},
        },
        {
            "method": "POST",
            "path": "/oauth/authorize",
            "operation_id": "authorizeDeviceCode",
            "response": {"status": 200},
        },
        {
            "method": "POST",
            "path": "/oauth/token",
            "operation_id": "completeDeviceCode",
            "response": {"status": 200},
        },
        {
            "method": "GET",
            "path": "/api/whoami-v2",
            "operation_id": "whoami",
            "response": {"status": 200},
        },
        {
            "method": "GET",
            "path": "/api/datasets/bench/unrelated",
            "operation_id": "datasetInfo",
            "response": {"status": 200},
        },
        {
            "method": "GET",
            "path": "/api/whoami-v2",
            "operation_id": "whoami",
            "response": {"status": 200},
        },
    ]


class AuthJourneyRuntime(WorldRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.authorization = threading.Event()
        self.login_started = threading.Event()
        self.product_envs: list[dict[str, str]] = []

    def exec_product(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        rendered = " ".join(map(str, argv))
        self.product_envs.append(dict(env or {}))
        if list(argv) == ["opentraces", "capabilities", "--json"]:
            return super().exec_product(
                box,
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
                timing_path=timing_path,
            )
        if "CUSTODY_PROBE" in rendered:
            return super().exec_product(
                box,
                argv,
                cwd=cwd,
                env=env,
                timeout=timeout,
                timing_path=timing_path,
            )
        if "auth login" in rendered:
            self.login_started.set()
            if not self.authorization.wait(timeout=5):
                return _result(list(argv), returncode=3)
            return _result(list(argv), stdout="Authenticated as bench.\n")
        if "auth whoami" in rendered:
            return _result(
                list(argv),
                stdout=json.dumps({"status": "ok", "authenticated": True, "username": "bench"})
                + "\n",
            )
        return _result(list(argv))

    def collect(self, box, globs, *, destination, repository):
        files = destination / "files"
        files.mkdir(parents=True)
        timing = files / Path(globs[0]).name
        typescript = files / Path(globs[1]).name
        timing.write_text("0.010 4\n", encoding="utf-8")
        typescript.write_bytes(b"ok\r\n")
        return {timing.name: timing, typescript.name: typescript}


class AuthorizingBrowser(PublicBrowserSession):
    def __init__(self, runtime: AuthJourneyRuntime) -> None:
        super().__init__(suppressed_channel="browser_video")
        self.runtime = runtime

    def click(self, selector: str) -> dict[str, object]:
        observed = super().click(selector)
        if selector == "button:has-text('Authorize')":
            self.runtime.raw_ledger = (
                "\n".join(json.dumps(row, separators=(",", ":")) for row in _cold_ledger_rows())
                + "\n"
            ).encode()
            self.runtime.authorization.set()
        return observed


def test_recorder_only_failure_cannot_rewrite_successful_browser_auth_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "opentraces-hf-emulator"
    binary.write_bytes(b"exact-linux-arm64-emulator")
    binary.chmod(0o755)
    _trust_test_binary(binary, monkeypatch)
    monkeypatch.setenv("OPENTRACES_HF_EMULATOR_BINARY", str(binary))
    runtime = AuthJourneyRuntime()
    bench = Bench(
        source=_source(),
        store=RunStore(tmp_path / "bucket" / "runs" / "v1"),
        box_runtime=runtime,
        repository_path=Path(__file__).resolve().parents[3],
        browser_factory=lambda: AuthorizingBrowser(runtime),
    )

    scenario.test_browser_authorization_authenticates_the_cli(bench)

    assert runtime.login_started.is_set()
    assert all(
        set(env) <= {"HF_ENDPOINT", "OPENTRACES_DISABLE_VERSION_CHECK"}
        for env in runtime.product_envs
    )
    assert all("HF_TOKEN" not in env for env in runtime.product_envs)
    assert bench.store.verify(next(bench.store.root.glob("run_*"))) is True
    result = json.loads(next(bench.store.root.glob("run_*/result.json")).read_text())
    channels = {channel["kind"]: channel for channel in result["recordings"]["channels"]}
    assert result["verdict"] == "pass"
    assert result["recordings"]["rewatchable"] is False
    assert channels["browser_video"]["complete"] is False
    assert channels["browser_video"]["path"] is None
    assert channels["terminal"]["complete"] is True
    assert channels["playwright_trace"]["complete"] is True
    assert channels["browser_screenshots"]["complete"] is True

    verifier = result["verifiers"][0]
    runtime.lease = lambda: pytest.fail("stored reverify attempted a box lease")
    runtime.exec_product = lambda *_args, **_kwargs: pytest.fail(
        "stored reverify attempted a product action"
    )
    reverification = reverify_stored_run(
        bench.store,
        result["run_id"],
        verifier_name=verifier["name"],
        verifier_digest=verifier["source_ref"]["digest"],
        verifier=scenario.cli_reports_authenticated,
    )
    assert reverification["status"] == "pass"
    assert reverification["evidence_refs"] == verifier["evidence_refs"]


@pytest.mark.parametrize(
    "fault",
    [
        "extra-auth-mutation",
        "missing-auth-mutation",
        "reordered-auth-mutation",
        "failed-auth-mutation",
        "unknown-write",
        "failed-whoami",
    ],
)
def test_browser_auth_verifier_rejects_non_claiming_ledger_exhaust(fault: str) -> None:
    rows = _cold_ledger_rows()
    if fault == "extra-auth-mutation":
        rows.insert(2, dict(rows[1]))
    elif fault == "missing-auth-mutation":
        rows.pop(3)
    elif fault == "reordered-auth-mutation":
        rows[3], rows[4] = rows[4], rows[3]
    elif fault == "failed-auth-mutation":
        rows[3]["response"] = {"status": 500}
    elif fault == "unknown-write":
        rows.insert(
            1,
            {
                "method": "POST",
                "path": "/oauth/unknown",
                "operation_id": "unknownAuthWrite",
                "response": {"status": 200},
            },
        )
    elif fault == "failed-whoami":
        rows[-2]["response"] = {"status": 401}

    login = SimpleNamespace(returncode=0, stderr="", result_ref="actions/login/result.json")
    whoami = SimpleNamespace(
        returncode=0,
        stderr="",
        json={"authenticated": True},
        result_ref="actions/whoami/result.json",
    )
    hf = SimpleNamespace(
        ledger=SimpleNamespace(rows=lambda: rows, evidence_ref="world/huggingface/ledger.jsonl")
    )

    with pytest.raises(AssertionError):
        scenario.cli_reports_authenticated(None, login=login, whoami=whoami, hf=hf)


@pytest.mark.parametrize("read_variation", ["missing-whoami", "reordered-reads"])
def test_browser_auth_verifier_ignores_benign_read_exhaust_variation(
    read_variation: str,
) -> None:
    rows = _cold_ledger_rows()
    if read_variation == "missing-whoami":
        rows.pop(5)
    elif read_variation == "reordered-reads":
        rows[0], rows[2] = rows[2], rows[0]

    login = SimpleNamespace(returncode=0, stderr="", result_ref="actions/login/result.json")
    whoami = SimpleNamespace(
        returncode=0,
        stderr="",
        json={"authenticated": True},
        result_ref="actions/whoami/result.json",
    )
    hf = SimpleNamespace(
        ledger=SimpleNamespace(rows=lambda: rows, evidence_ref="world/huggingface/ledger.jsonl")
    )

    assert scenario.cli_reports_authenticated(None, login=login, whoami=whoami, hf=hf) == {
        "evidence_refs": [
            "actions/login/result.json",
            "actions/whoami/result.json",
            "world/huggingface/ledger.jsonl",
        ]
    }


def test_browser_auth_source_requires_only_public_auth_cli_capabilities() -> None:
    source = inspect.getsource(scenario.test_browser_authorization_authenticates_the_cli)

    assert 'run.emulate("huggingface")' in source
    assert 'run.require_capabilities("cli:auth.login", "cli:auth.whoami")' in source
    assert "emulator:huggingface" not in source
