from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.arena.atlas import (
    build_atlas,
    cross_check_atlas,
    guarantees_source_digest,
)
from opentraces.core.arena.contract import build_result
from opentraces.core.arena.retrieval import StoredEvidence
from opentraces.core.arena.run_store import RunStore
from tests.arena.guarantees import (
    verify_linux_x86_64_hf_emulator,
    verify_remote_rented_glibc_emulator,
)
from tests.core.arena.test_hf_emulator_run import _capabilities_payload
from tests.manual.verify_a7_fleet import verify_a7_acceptance


REPOSITORY = Path(__file__).resolve().parents[3]
GUARANTEES_PATH = REPOSITORY / "tests/arena/guarantees.json"


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_canonical_guarantees_bind_four_exact_importable_verifiers() -> None:
    from tests.manual.generate_a7_guarantees import canonical_guarantees

    payload = json.loads(GUARANTEES_PATH.read_text(encoding="utf-8"))
    assert payload == canonical_guarantees(repository=REPOSITORY)
    rows = payload["guarantees"]

    assert [row["id"] for row in rows] == [
        "browser-auth",
        "publish-down",
        "remote-rented-glibc",
        "linux-x86_64-hf-emulator",
    ]
    expected = {
        "browser-auth": (
            "tests.arena.scenarios.test_browser_auth_reaches_hf.cli_reports_authenticated",
            REPOSITORY / "tests/arena/scenarios/test_browser_auth_reaches_hf.py",
        ),
        "publish-down": (
            "tests.arena.scenarios.test_publish_reaches_hf_remote.publish_commit_is_witnessed",
            REPOSITORY / "tests/arena/scenarios/test_publish_reaches_hf_remote.py",
        ),
        "remote-rented-glibc": (
            "tests.arena.guarantees.verify_remote_rented_glibc_emulator",
            REPOSITORY / "tests/arena/guarantees.py",
        ),
        "linux-x86_64-hf-emulator": (
            "tests.arena.guarantees.verify_linux_x86_64_hf_emulator",
            REPOSITORY / "tests/arena/guarantees.py",
        ),
    }
    for row in rows:
        name, source = expected[row["id"]]
        assert row["verifier"] == {"name": name, "digest": _digest(source)}
        assert row["black_box_review"] == "unreviewed"


def test_real_pytest_loaded_verifiers_bind_public_reverify_and_atlas(
    tmp_path: Path,
) -> None:
    guarantees_source = GUARANTEES_PATH.read_bytes()
    guarantees = json.loads(guarantees_source)["guarantees"]
    selected = [row for row in guarantees if row["id"] in {"browser-auth", "publish-down"}]
    store_root = tmp_path / "runs" / "v1"
    environment = dict(os.environ)
    environment.update(
        {
            "OT_VERIFIER_IDENTITY_PROBE_STORE": str(store_root),
            "OT_VERIFIER_IDENTITY_PROBE_REPOSITORY": str(REPOSITORY),
            "OT_VERIFIER_IDENTITY_PROBE_GUARANTEES": str(GUARANTEES_PATH),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--collect-only",
            "-p",
            "tests.core.arena.fixtures.pytest_verifier_identity_probe",
            *(row["nodeid"] for row in selected),
        ],
        cwd=REPOSITORY,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout

    store = RunStore(store_root)
    run_paths = sorted(store_root.glob("run_*"))
    assert len(run_paths) == 2
    results = [json.loads((path / "result.json").read_text(encoding="utf-8")) for path in run_paths]
    results_by_nodeid = {result["scenario"]["nodeid"]: result for result in results}
    guarantees_by_nodeid = {row["nodeid"]: row for row in selected}
    for nodeid, result in results_by_nodeid.items():
        guarantee = guarantees_by_nodeid[nodeid]
        assert result["verifiers"][0]["name"] == guarantee["verifier"]["name"]
        assert result["verifiers"][0]["source_ref"]["digest"] == guarantee["verifier"]["digest"]

    runner = CliRunner()
    expected_status = {"browser-auth": (0, "pass"), "publish-down": (1, "fail")}
    run_ids: dict[str, str] = {}
    for guarantee in selected:
        result = results_by_nodeid[guarantee["nodeid"]]
        run_ids[guarantee["id"]] = result["run_id"]
        expected_exit, status = expected_status[guarantee["id"]]
        invoked = runner.invoke(
            main,
            [
                "bench",
                "reverify",
                result["run_id"],
                "--store-root",
                str(store_root),
                "--verifier-name",
                guarantee["verifier"]["name"],
                "--verifier-digest",
                guarantee["verifier"]["digest"],
                "--json",
            ],
        )
        assert invoked.exit_code == expected_exit, invoked.output
        assert json.loads(invoked.output)["status"] == status

    storage = {path.name: store.verified_integrity(path) for path in run_paths}
    product_commit = "a" * 40
    capabilities_digest = results[0]["pins"]["capabilities"]["digest"]
    atlas = build_atlas(
        guarantees=guarantees,
        guarantees_digest=guarantees_source_digest(guarantees_source),
        results=results,
        storage_integrity_by_run_id=storage,
        product_commit=product_commit,
        capabilities_digest=capabilities_digest,
    )
    rows_by_id = {row["id"]: row for row in atlas["rows"]}
    assert (rows_by_id["browser-auth"]["state"], rows_by_id["browser-auth"]["latest_run_id"]) == (
        "proven",
        run_ids["browser-auth"],
    )
    assert (rows_by_id["publish-down"]["state"], rows_by_id["publish-down"]["latest_run_id"]) == (
        "failing",
        run_ids["publish-down"],
    )
    assert cross_check_atlas(
        atlas,
        guarantees=guarantees,
        guarantees_digest=guarantees_source_digest(guarantees_source),
        results=results,
        storage_integrity_by_run_id=storage,
    )


def test_future_hole_verifiers_reject_unbound_self_report_json(tmp_path: Path) -> None:
    run_path = tmp_path / "run_future"
    (run_path / "artifacts").mkdir(parents=True)
    (run_path / "result.json").write_text("{}\n", encoding="utf-8")
    remote_ref = "artifacts/remote-rented-glibc.json"
    x64_ref = "artifacts/linux-x86_64-hf-emulator.json"
    (run_path / remote_ref).write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bench.remote-rented-glibc.v0",
                "placement": "remote-rented",
                "os": "linux",
                "libc": "glibc",
                "lease_id": "cbx_remote",
                "contract_suite": "pass",
            }
        ),
        encoding="utf-8",
    )
    (run_path / x64_ref).write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bench.linux-x86_64-hf-emulator.v0",
                "os": "linux",
                "architecture": "x86_64",
                "binary_target": "bun-linux-x64",
                "contract_suite": "pass",
            }
        ),
        encoding="utf-8",
    )
    evidence = StoredEvidence(run_path)

    with pytest.raises(AssertionError, match="unbound"):
        verify_remote_rented_glibc_emulator(evidence)
    with pytest.raises(AssertionError, match="unbound"):
        verify_linux_x86_64_hf_emulator(evidence)


def test_public_reverification_transcript_disables_box_processes_and_network(
    tmp_path: Path,
) -> None:
    from tests.manual.reverify_a7_without_box import run_public_reverification

    observed: list[tuple[list[str], dict[str, str]]] = []

    def fake_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        observed.append((command, environment))
        run_id = command[command.index("reverify") + 1]
        status = "pass" if run_id == "run_browser" else "fail"
        return subprocess.CompletedProcess(
            command,
            0 if status == "pass" else 1,
            stdout=json.dumps(
                {
                    "schema_version": "opentraces.bench.reverification.v0",
                    "run_id": run_id,
                    "status": status,
                    "verifier": {
                        "name": command[command.index("--verifier-name") + 1],
                        "digest": command[command.index("--verifier-digest") + 1],
                    },
                    "evidence_refs": ["ledgers/huggingface.jsonl"],
                    "reason": None,
                }
            ),
            stderr="",
        )

    transcript = run_public_reverification(
        store_root=tmp_path / "runs/v1",
        guarantees_path=GUARANTEES_PATH,
        run_ids={"browser-auth": "run_browser", "publish-down": "run_publish"},
        repository=REPOSITORY,
        runner=fake_runner,
        integrity_by_run_id={
            "run_browser": {
                "verified": True,
                "result_digest": "sha256:" + "1" * 64,
                "integrity_digest": "sha256:" + "2" * 64,
            },
            "run_publish": {
                "verified": True,
                "result_digest": "sha256:" + "3" * 64,
                "integrity_digest": "sha256:" + "4" * 64,
            },
        },
    )

    assert [attempt["status"] for attempt in transcript["attempts"]] == ["pass", "fail"]
    assert [attempt["exit_code"] for attempt in transcript["attempts"]] == [0, 1]
    for attempt in transcript["attempts"]:
        guarantee = next(
            row
            for row in json.loads(GUARANTEES_PATH.read_text())["guarantees"]
            if row["id"] == attempt["guarantee_id"]
        )
        assert attempt["run_ref"] == f"runs/v1/{attempt['run_id']}/result.json"
        assert attempt["verifier"] == guarantee["verifier"]
        assert attempt["evidence_refs"] == ["ledgers/huggingface.jsonl"]
        assert attempt["storage_integrity"]["verified"] is True
    assert transcript["runtime_constraints"] == {
        "box_runtime": "unavailable",
        "external_process_path": "empty",
        "network": "closed-loop-proxy",
    }
    assert len(observed) == 2
    for command, environment in observed:
        assert command[1:4] == ["-m", "opentraces", "bench"]
        assert "reverify" in command and "--json" in command
        assert environment["PATH"] == ""
        assert environment["HOME"].startswith(str(tmp_path))
        assert environment["HTTPS_PROXY"] == "http://127.0.0.1:9"
        assert set(environment) <= {
            "ALL_PROXY",
            "HOME",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "LANG",
            "LC_ALL",
            "NO_PROXY",
            "PATH",
            "PYTHONPATH",
            "TMPDIR",
        }


def _finalize_run(
    store: RunStore,
    *,
    guarantee: dict,
    verdict: str,
    nonce: str,
    lease_id: str,
    acquired: str,
    released: str,
    capabilities: dict,
    ledger_operations: tuple[str, ...],
) -> tuple[Path, dict]:
    draft = store.begin()
    capability_ref = "artifacts/capabilities.json"
    lease_ref = "artifacts/lease-lifecycle.json"
    ledger_ref = "ledgers/huggingface.jsonl"
    draft.write_json(capability_ref, capabilities)
    draft.write_json(
        lease_ref,
        {
            "schema_version": "opentraces.bench.lease-lifecycle.v0",
            "id": lease_id,
            "provider": "local-container",
            "acquired": acquired,
            "release_started": released,
            "released": released,
            "status": "released",
        },
    )
    ledger_source = "".join(
        json.dumps(
            {
                "request_id": f"req_{index}",
                "observed_at": f"2026-07-16T10:00:0{index}Z",
                "method": "GET",
                "path": f"/fixture/{operation}",
                "operation_id": operation,
                "request": None,
                "response": {"status": 200},
            }
        )
        + "\n"
        for index, operation in enumerate(ledger_operations, start=1)
    )
    draft.write_text(ledger_ref, ledger_source)
    world_ref = "world/huggingface.json"
    draft.write_json(
        world_ref,
        {
            "schema_version": "opentraces.bench.world.huggingface.v1",
            "readiness": {"launch": {"nonce": nonce}},
            "manifest": {"launch": {"nonce": nonce}},
        },
    )
    capability_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    result = build_result(
        run_id=draft.run_id,
        claim=guarantee["claim"],
        nodeid=guarantee["nodeid"],
        source_ref="source/scenario.py",
        execution_mode="direct",
        started_at=acquired,
        duration_ms=1,
        execution_status="complete",
        verdict=verdict,
        reason=(
            None
            if verdict == "pass"
            else {"code": "assertion_failed", "message": "world stayed down"}
        ),
        verifiers=[
            {
                "name": guarantee["verifier"]["name"],
                "source_ref": {
                    "path": "tests/arena/verifier.py",
                    "digest": guarantee["verifier"]["digest"],
                },
                "status": verdict,
                "duration_ms": 1,
                "evidence_refs": [ledger_ref],
                "reason": None,
            }
        ],
        evidence={
            "complete": True,
            "requirements": [
                {
                    "name": guarantee["verifier"]["name"],
                    "complete": True,
                    "evidence_refs": [ledger_ref],
                }
            ],
        },
        recordings={"rewatchable": True, "channels": []},
        artifacts=[
            {
                "path": capability_ref,
                "media_type": "application/json",
                "kind": "capabilities",
            },
            {
                "path": lease_ref,
                "media_type": "application/json",
                "kind": "lease_lifecycle",
            },
        ],
        capture=None,
        pins={
            "product": {"commit": "a" * 40, "worktree": "clean", "dirty_diff_digest": None},
            "environment": {
                "provider": "local-container",
                "image": "ubuntu:24.04",
                "sandbox_tier": "container",
            },
            "capabilities": {"digest": capability_digest, "evidence_ref": capability_ref},
            "emulators": {
                "huggingface": {
                    "setup": {"readiness": {"launch": {"nonce": nonce}}},
                    "evidence_ref": world_ref,
                    "ledger_binding": {
                        "launch_nonce": nonce,
                        "ledger_sha256": (
                            "sha256:" + hashlib.sha256(ledger_source.encode()).hexdigest()
                        ),
                        "evidence_ref": ledger_ref,
                    },
                }
            },
        },
    )
    return draft.finalize(result), result


@pytest.mark.parametrize(
    ("ledger_operations", "transcript_fault", "error_match"),
    [
        (
            (
                ("issueDeviceCode", "authorizeDeviceCode", "completeDeviceCode"),
                ("createRepo", "datasetInfo", "listRepoTree"),
            ),
            None,
            None,
        ),
        (
            (
                ("issueDeviceCode", "authorizeDeviceCode", "completeDeviceCode"),
                ("issueDeviceCode", "authorizeDeviceCode", "completeDeviceCode"),
            ),
            None,
            "ledger",
        ),
        (
            (
                ("issueDeviceCode", "authorizeDeviceCode", "completeDeviceCode"),
                ("createRepo", "datasetInfo", "listRepoTree"),
            ),
            "wrong-verifier-digest",
            "reverify",
        ),
        (
            (
                ("issueDeviceCode", "authorizeDeviceCode", "completeDeviceCode"),
                ("createRepo", "datasetInfo", "listRepoTree"),
            ),
            "missing-evidence-ref",
            "reverify",
        ),
    ],
    ids=(
        "independent-ledgers",
        "shared-ledger-state",
        "wrong-reverify-digest",
        "missing-reverify-evidence",
    ),
)
def test_manual_acceptance_cross_checks_fleet_atlas_and_capability_truth(
    tmp_path: Path,
    ledger_operations: tuple[tuple[str, ...], tuple[str, ...]],
    transcript_fault: str | None,
    error_match: str | None,
) -> None:
    guarantees_source = GUARANTEES_PATH.read_bytes()
    guarantees = json.loads(guarantees_source)["guarantees"]
    by_id = {row["id"]: row for row in guarantees}
    capabilities = _capabilities_payload()
    capabilities_path = tmp_path / "capabilities.json"
    capabilities_path.write_text(json.dumps(capabilities), encoding="utf-8")
    store = RunStore(tmp_path / "bucket/runs/v1")
    browser_path, browser = _finalize_run(
        store,
        guarantee=by_id["browser-auth"],
        verdict="pass",
        nonce="nonce-browser",
        lease_id="lease-browser",
        acquired="2026-07-16T10:00:00Z",
        released="2026-07-16T10:00:03Z",
        capabilities=capabilities,
        ledger_operations=ledger_operations[0],
    )
    publish_path, publish = _finalize_run(
        store,
        guarantee=by_id["publish-down"],
        verdict="fail",
        nonce="nonce-publish",
        lease_id="lease-publish",
        acquired="2026-07-16T10:00:01Z",
        released="2026-07-16T10:00:02Z",
        capabilities=capabilities,
        ledger_operations=ledger_operations[1],
    )
    results = [browser, publish]
    storage = {
        result["run_id"]: store.verified_integrity(path)
        for result, path in ((browser, browser_path), (publish, publish_path))
    }
    capability_digest = browser["pins"]["capabilities"]["digest"]
    atlas = build_atlas(
        guarantees=guarantees,
        guarantees_digest=guarantees_source_digest(guarantees_source),
        results=results,
        storage_integrity_by_run_id=storage,
        product_commit="a" * 40,
        capabilities_digest=capability_digest,
    )
    atlas_path = tmp_path / "atlas.json"
    atlas_path.write_text(json.dumps(atlas), encoding="utf-8")
    fleet_path = tmp_path / "fleet.json"
    fleet_path.write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "run_id": browser["run_id"],
                        "run_ref": f"runs/v1/{browser['run_id']}",
                    },
                    {
                        "run_id": publish["run_id"],
                        "run_ref": f"runs/v1/{publish['run_id']}",
                    },
                ],
                "observed_max_lease_concurrency": 2,
            }
        ),
        encoding="utf-8",
    )
    reverify_attempts = [
        {
            "guarantee_id": "browser-auth",
            "run_id": browser["run_id"],
            "run_ref": f"runs/v1/{browser['run_id']}/result.json",
            "storage_integrity": storage[browser["run_id"]],
            "exit_code": 0,
            "status": "pass",
            "verifier": by_id["browser-auth"]["verifier"],
            "evidence_refs": browser["verifiers"][0]["evidence_refs"],
        },
        {
            "guarantee_id": "publish-down",
            "run_id": publish["run_id"],
            "run_ref": f"runs/v1/{publish['run_id']}/result.json",
            "storage_integrity": storage[publish["run_id"]],
            "exit_code": 1,
            "status": "fail",
            "verifier": by_id["publish-down"]["verifier"],
            "evidence_refs": publish["verifiers"][0]["evidence_refs"],
        },
    ]
    if transcript_fault == "wrong-verifier-digest":
        reverify_attempts[0]["verifier"] = {
            **reverify_attempts[0]["verifier"],
            "digest": "sha256:" + "0" * 64,
        }
    elif transcript_fault == "missing-evidence-ref":
        reverify_attempts[0]["evidence_refs"] = []
    reverify_path = tmp_path / "reverify-without-box.json"
    reverify_path.write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bench.reverify-without-box.v0",
                "runtime_constraints": {
                    "box_runtime": "unavailable",
                    "external_process_path": "empty",
                    "network": "closed-loop-proxy",
                },
                "attempts": reverify_attempts,
            }
        ),
        encoding="utf-8",
    )

    arguments = {
        "store_root": store.root,
        "fleet_path": fleet_path,
        "atlas_path": atlas_path,
        "guarantees_path": GUARANTEES_PATH,
        "capabilities_path": capabilities_path,
        "reverify_path": reverify_path,
    }
    if error_match is not None:
        with pytest.raises(AssertionError, match=error_match):
            verify_a7_acceptance(**arguments)
        return

    observed = verify_a7_acceptance(**arguments)

    assert observed["works"] is True
    assert observed["run_ids"] == [browser["run_id"], publish["run_id"]]
    assert observed["lease_ids"] == ["lease-browser", "lease-publish"]
    assert observed["emulator_nonces"] == ["nonce-browser", "nonce-publish"]
    assert observed["observed_max_lease_concurrency"] == 2
    assert observed["reverify_without_box"] == {"browser-auth": "pass", "publish-down": "fail"}
    assert observed["atlas_states"] == {
        "browser-auth": "proven",
        "linux-x86_64-hf-emulator": "unbound",
        "publish-down": "failing",
        "remote-rented-glibc": "unbound",
    }
