from __future__ import annotations

import hashlib
import json
from pathlib import Path

from opentraces.core.arena.atlas import build_atlas, guarantees_source_digest
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
    payload = json.loads(GUARANTEES_PATH.read_text(encoding="utf-8"))
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


def test_future_hole_verifiers_adjudicate_concrete_stored_proofs(tmp_path: Path) -> None:
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

    assert verify_remote_rented_glibc_emulator(evidence) == {
        "evidence_refs": [remote_ref]
    }
    assert verify_linux_x86_64_hf_emulator(evidence) == {"evidence_refs": [x64_ref]}


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
    draft.write_text(ledger_ref, json.dumps({"nonce": nonce}) + "\n")
    capability_digest = "sha256:" + hashlib.sha256(
        json.dumps(capabilities, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
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
                    "evidence_ref": "world/huggingface.json",
                }
            },
        },
    )
    return draft.finalize(result), result


def test_manual_acceptance_cross_checks_fleet_atlas_and_capability_truth(
    tmp_path: Path,
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
                    {"run_id": browser["run_id"], "run_path": str(browser_path)},
                    {"run_id": publish["run_id"], "run_path": str(publish_path)},
                ],
                "observed_max_lease_concurrency": 2,
            }
        ),
        encoding="utf-8",
    )

    observed = verify_a7_acceptance(
        store_root=store.root,
        fleet_path=fleet_path,
        atlas_path=atlas_path,
        guarantees_path=GUARANTEES_PATH,
        capabilities_path=capabilities_path,
    )

    assert observed["works"] is True
    assert observed["run_ids"] == [browser["run_id"], publish["run_id"]]
    assert observed["lease_ids"] == ["lease-browser", "lease-publish"]
    assert observed["emulator_nonces"] == ["nonce-browser", "nonce-publish"]
    assert observed["observed_max_lease_concurrency"] == 2
    assert observed["atlas_states"] == {
        "browser-auth": "proven",
        "linux-x86_64-hf-emulator": "unbound",
        "publish-down": "failing",
        "remote-rented-glibc": "unbound",
    }
