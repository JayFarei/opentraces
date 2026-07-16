"""Real-pytest probe for verifier identities stored by the public bench engine."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from opentraces.core.arena.box import Box, BoxCommandResult
from opentraces.core.arena.engine import Bench, ScenarioSource
from opentraces.core.arena.run_store import RunStore
from opentraces.core.capabilities import build_capabilities_manifest


class _ProbeRuntime:
    crabbox_version = "probe"

    def lease(self) -> Box:
        return Box(
            id="identity-probe",
            slug="identity-probe",
            provider="local-container",
            sandbox_tier="container",
            ssh_host="127.0.0.1",
            ssh_user="probe",
            ssh_port="22",
            ssh_key="/tmp/identity-probe",
            image="ubuntu:24.04",
        )

    def materialize(self, box: Box, app_state: str, *, repository: Path) -> dict:
        return {"name": app_state, "digest": "sha256:identity-probe", "provides": ["cli"]}

    def exec_product(self, box, argv, *, cwd=None, env=None, timeout=60, timing_path):
        manifest = build_capabilities_manifest(
            verbs=(
                {"path": path, "hidden": False}
                for path in (
                    "auth.login",
                    "auth.whoami",
                    "capture-otlp.start",
                    "capture-otlp.status",
                    "capture-otlp.stop",
                    "dataset.publish",
                    "setup.capture-otlp",
                    "setup.claude-code",
                    "setup.codex-cli",
                    "setup.git",
                    "setup.pi",
                    "setup.watcher.install",
                )
            ),
            app_version="test",
            trace_schema_version="test",
            security_version="test",
        )
        return BoxCommandResult(
            argv=list(argv),
            returncode=0,
            stdout=json.dumps(manifest) + "\n",
            stderr="",
            timing={"schemaVersion": 1, "timing": {"exitCode": 0}},
        )

    def release(self, box: Box) -> None:
        return None


def _ledger_rows(item_name: str) -> list[dict[str, object]]:
    if item_name == "test_browser_authorization_authenticates_the_cli":
        return [
            {
                "method": "POST",
                "path": "/oauth/device",
                "operation_id": "issueDeviceCode",
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
        ]
    return [
        {
            "method": "GET",
            "path": "/api/datasets/bench/scenario-2",
            "operation_id": "datasetInfo",
            "response": {"status": 200},
        }
    ]


def pytest_collection_finish(session) -> None:
    store_value = os.environ.get("OT_VERIFIER_IDENTITY_PROBE_STORE")
    repository_value = os.environ.get("OT_VERIFIER_IDENTITY_PROBE_REPOSITORY")
    guarantees_value = os.environ.get("OT_VERIFIER_IDENTITY_PROBE_GUARANTEES")
    if not all((store_value, repository_value, guarantees_value)):
        return

    repository = Path(str(repository_value)).resolve()
    store = RunStore(Path(str(store_value)))
    guarantees = json.loads(Path(str(guarantees_value)).read_text(encoding="utf-8"))["guarantees"]
    guarantees_by_nodeid = {row["nodeid"]: row for row in guarantees}

    for item in session.items:
        guarantee = guarantees_by_nodeid.get(item.nodeid)
        if guarantee is None:
            continue
        verifier_name = (
            "cli_reports_authenticated"
            if item.name == "test_browser_authorization_authenticates_the_cli"
            else "publish_commit_is_witnessed"
        )
        verifier = getattr(item.module, verifier_name)
        source_path = Path(str(item.path)).resolve()
        source = ScenarioSource(
            nodeid=item.nodeid,
            claim=guarantee["claim"],
            source_path=source_path,
            scenario_path=source_path.relative_to(repository).as_posix(),
            repository="identity-probe",
            commit="a" * 40,
            dirty_diff_digest=None,
            product_worktree="clean",
            product_dirty_diff_digest=None,
        )
        bench = Bench(
            source=source,
            store=store,
            box_runtime=_ProbeRuntime(),
            repository_path=repository,
        )
        with bench.run(app_state="install-only") as run:
            run.require_capabilities()
            ledger_ref = "ledgers/huggingface.jsonl"
            rows = _ledger_rows(item.name)
            run.draft.write_text(
                ledger_ref,
                "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
            )
            hf = SimpleNamespace(
                ledger=SimpleNamespace(
                    rows=lambda: rows,
                    contains=lambda **_expected: False,
                    evidence_ref=ledger_ref,
                )
            )
            if item.name == "test_browser_authorization_authenticates_the_cli":
                login_ref = "actions/login/result.json"
                whoami_ref = "actions/whoami/result.json"
                run.draft.write_json(login_ref, {"returncode": 0})
                run.draft.write_json(whoami_ref, {"returncode": 0})
                run.verify(
                    verifier,
                    login=SimpleNamespace(returncode=0, stderr="", result_ref=login_ref),
                    whoami=SimpleNamespace(
                        returncode=0,
                        stderr="",
                        json={"authenticated": True},
                        result_ref=whoami_ref,
                    ),
                    hf=hf,
                )
            else:
                run.verify(verifier, hf=hf)
