#!/usr/bin/env python3
"""Cross-check the cold A7 fleet package from immutable stored evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opentraces.core.arena.atlas import cross_check_atlas, guarantees_source_digest
from opentraces.core.arena.capability_probe import parse_capabilities_probe
from opentraces.core.arena.fleet import FleetAttempt, observed_max_lease_concurrency
from opentraces.core.arena.run_store import RunStore


EXPECTED_STATES = {
    "browser-auth": "proven",
    "linux-x86_64-hf-emulator": "unbound",
    "publish-down": "failing",
    "remote-rented-glibc": "unbound",
}


def _json_object(path: Path, *, name: str) -> Mapping[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise AssertionError(f"{name} must contain an object")
    return payload


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _nested(mapping: Mapping[str, Any], *path: str) -> Any:
    value: Any = mapping
    for name in path:
        assert isinstance(value, Mapping), f"missing nested result field: {'.'.join(path)}"
        value = value.get(name)
    return value


def verify_a7_acceptance(
    *,
    store_root: Path,
    fleet_path: Path,
    atlas_path: Path,
    guarantees_path: Path,
    capabilities_path: Path,
    reverify_path: Path,
) -> dict[str, Any]:
    """Return the independently cross-checked A7 acceptance facts or fail closed."""

    store = RunStore(store_root)
    fleet = _json_object(fleet_path, name="fleet summary")
    raw_attempts = fleet.get("attempts")
    assert isinstance(raw_attempts, list) and len(raw_attempts) == 2
    attempts: list[FleetAttempt] = []
    results: list[Mapping[str, Any]] = []
    run_paths: list[Path] = []
    for row in raw_attempts:
        assert isinstance(row, Mapping)
        run_id = row.get("run_id")
        assert isinstance(run_id, str)
        assert row.get("run_ref") == f"runs/v1/{run_id}"
        assert "run_path" not in row
        run_path = (store.root / run_id).resolve(strict=True)
        attempt = FleetAttempt.from_run(run_path, store=store)
        assert attempt.run_id == run_id
        attempts.append(attempt)
        run_paths.append(run_path)
        result = _json_object(run_path / "result.json", name=f"result {run_id}")
        results.append(result)
    assert len(set(run_paths)) == len(run_paths), "fleet reused a finalized run directory"

    observed_concurrency = observed_max_lease_concurrency(attempts)
    assert observed_concurrency >= 2, "leases did not overlap strictly"
    assert fleet.get("observed_max_lease_concurrency") == observed_concurrency
    lease_ids = [str(attempt.lease_lifecycle["id"]) for attempt in attempts]
    assert len(set(lease_ids)) == len(lease_ids), "fleet reused a lease id"

    capabilities = _json_object(capabilities_path, name="capabilities")
    parse_capabilities_probe(
        returncode=0,
        stdout=json.dumps(capabilities),
        stderr="",
    )
    capabilities_digest = _canonical_digest(capabilities)
    emulator_nonces: list[str] = []
    ledger_paths: list[Path] = []
    ledger_digests: list[str] = []
    ledger_operations: dict[str, list[str]] = {}
    for run_path, result in zip(run_paths, results, strict=True):
        pin = _nested(result, "pins", "capabilities")
        assert isinstance(pin, Mapping) and pin.get("digest") == capabilities_digest
        evidence_ref = pin.get("evidence_ref")
        assert isinstance(evidence_ref, str)
        observed_capabilities = _json_object(
            run_path / evidence_ref,
            name=f"capabilities {result['run_id']}",
        )
        assert _canonical_digest(observed_capabilities) == capabilities_digest
        emulator_pin = _nested(result, "pins", "emulators", "huggingface")
        assert isinstance(emulator_pin, Mapping)
        nonce = _nested(emulator_pin, "setup", "readiness", "launch", "nonce")
        assert isinstance(nonce, str) and nonce
        emulator_nonces.append(nonce)
        world_ref = emulator_pin.get("evidence_ref")
        assert world_ref == "world/huggingface.json"
        world = _json_object(run_path / world_ref, name=f"world {result['run_id']}")
        assert _nested(world, "readiness", "launch", "nonce") == nonce
        assert _nested(world, "manifest", "launch", "nonce") == nonce
        binding = emulator_pin.get("ledger_binding")
        assert isinstance(binding, Mapping), "ledger binding is missing"
        assert set(binding) == {"evidence_ref", "launch_nonce", "ledger_sha256"}
        assert binding.get("launch_nonce") == nonce, "ledger binding has the wrong launch nonce"
        ledger_ref = binding.get("evidence_ref")
        assert ledger_ref == "ledgers/huggingface.jsonl"
        ledger = (run_path / ledger_ref).resolve(strict=True)
        ledger.relative_to(run_path)
        ledger_source = ledger.read_bytes()
        assert ledger_source, "stored emulator ledger is empty"
        ledger_digest = f"sha256:{hashlib.sha256(ledger_source).hexdigest()}"
        assert binding.get("ledger_sha256") == ledger_digest, "ledger digest disagrees with pin"
        rows = [json.loads(line) for line in ledger_source.splitlines()]
        assert rows and all(isinstance(row, Mapping) for row in rows)
        operations = [row.get("operation_id") for row in rows]
        assert all(isinstance(operation, str) and operation for operation in operations)
        ledger_operations[str(result["run_id"])] = [str(operation) for operation in operations]
        ledger_paths.append(ledger)
        ledger_digests.append(ledger_digest)
    assert len(set(emulator_nonces)) == len(emulator_nonces), "emulator nonce crossed runs"
    assert len(set(ledger_paths)) == len(ledger_paths), "emulator ledger crossed runs"
    assert len(set(ledger_digests)) == len(ledger_digests), "ledger digest crossed runs"

    guarantees_source = Path(guarantees_path).read_bytes()
    guarantees_payload = json.loads(guarantees_source)
    assert isinstance(guarantees_payload, Mapping)
    guarantees = guarantees_payload.get("guarantees")
    assert isinstance(guarantees, list)
    atlas = _json_object(atlas_path, name="atlas")
    storage = {
        result["run_id"]: store.verified_integrity(run_path)
        for run_path, result in zip(run_paths, results, strict=True)
    }
    assert cross_check_atlas(
        atlas,
        guarantees=guarantees,
        guarantees_digest=guarantees_source_digest(guarantees_source),
        results=results,
        storage_integrity_by_run_id=storage,
    )
    atlas_rows = atlas.get("rows")
    assert isinstance(atlas_rows, list)
    atlas_states = {
        str(row["id"]): str(row["state"]) for row in atlas_rows if isinstance(row, Mapping)
    }
    assert atlas_states == EXPECTED_STATES
    bound = {str(row["id"]): row for row in atlas_rows if row.get("latest_run_id")}
    result_by_nodeid = {str(_nested(result, "scenario", "nodeid")): result for result in results}
    for guarantee in guarantees:
        if guarantee["id"] in {"browser-auth", "publish-down"}:
            assert (
                bound[guarantee["id"]]["latest_run_id"]
                == result_by_nodeid[guarantee["nodeid"]]["run_id"]
            )

    reverify = _json_object(reverify_path, name="reverify-without-box transcript")
    assert reverify.get("schema_version") == "opentraces.bench.reverify-without-box.v0"
    assert reverify.get("runtime_constraints") == {
        "box_runtime": "unavailable",
        "external_process_path": "empty",
        "network": "closed-loop-proxy",
    }
    raw_reverify_attempts = reverify.get("attempts")
    assert isinstance(raw_reverify_attempts, list) and len(raw_reverify_attempts) == 2
    reverify_statuses: dict[str, str] = {}
    guarantee_by_id = {str(guarantee["id"]): guarantee for guarantee in guarantees}
    result_by_run_id = {str(result["run_id"]): result for result in results}
    run_path_by_id = {
        str(result["run_id"]): run_path for run_path, result in zip(run_paths, results, strict=True)
    }
    for row in raw_reverify_attempts:
        assert isinstance(row, Mapping)
        assert set(row) == {
            "evidence_refs",
            "exit_code",
            "guarantee_id",
            "run_id",
            "run_ref",
            "status",
            "storage_integrity",
            "verifier",
        }, "reverify transcript has the wrong fields"
        guarantee_id = str(row["guarantee_id"])
        expected_status = "pass" if guarantee_id == "browser-auth" else "fail"
        expected_exit = 0 if guarantee_id == "browser-auth" else 1
        assert guarantee_id in {"browser-auth", "publish-down"}
        run_id = str(row["run_id"])
        assert run_id == bound[guarantee_id]["latest_run_id"], "reverify run is not atlas-bound"
        assert row["run_ref"] == f"runs/v1/{run_id}/result.json", "reverify run ref is unstable"
        assert row["storage_integrity"] == storage[run_id], "reverify integrity binding differs"
        assert row["status"] == expected_status
        assert row["exit_code"] == expected_exit
        guarantee = guarantee_by_id[guarantee_id]
        assert row["verifier"] == guarantee["verifier"], "reverify verifier binding differs"
        result = result_by_run_id[run_id]
        matching_verifiers = [
            verifier
            for verifier in result.get("verifiers") or []
            if isinstance(verifier, Mapping)
            and verifier.get("name") == guarantee["verifier"]["name"]
            and _nested(verifier, "source_ref", "digest") == guarantee["verifier"]["digest"]
        ]
        assert len(matching_verifiers) == 1, "reverify stored verifier is not unique"
        stored_verifier = matching_verifiers[0]
        assert stored_verifier.get("status") == expected_status
        evidence_refs = row["evidence_refs"]
        assert (
            isinstance(evidence_refs, list)
            and evidence_refs
            and evidence_refs == stored_verifier.get("evidence_refs")
        ), "reverify evidence refs differ from stored verifier"
        for evidence_ref in evidence_refs:
            assert isinstance(evidence_ref, str)
            target = (run_path_by_id[run_id] / evidence_ref).resolve(strict=True)
            target.relative_to(run_path_by_id[run_id])
            assert target.is_file()
        reverify_statuses[guarantee_id] = expected_status
    assert set(reverify_statuses) == {"browser-auth", "publish-down"}

    return {
        "works": True,
        "run_ids": [attempt.run_id for attempt in attempts],
        "lease_ids": lease_ids,
        "emulator_nonces": emulator_nonces,
        "ledger_refs": [
            {"run_id": attempt.run_id, "ref": "ledgers/huggingface.jsonl"} for attempt in attempts
        ],
        "ledger_digests": ledger_digests,
        "ledger_operations": ledger_operations,
        "capabilities_digest": capabilities_digest,
        "observed_max_lease_concurrency": observed_concurrency,
        "atlas_states": atlas_states,
        "reverify_without_box": reverify_statuses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store-root", required=True, type=Path)
    parser.add_argument("--fleet", required=True, type=Path)
    parser.add_argument("--atlas", required=True, type=Path)
    parser.add_argument("--guarantees", required=True, type=Path)
    parser.add_argument("--capabilities", required=True, type=Path)
    parser.add_argument("--reverify-without-box", required=True, type=Path)
    args = parser.parse_args()
    observed = verify_a7_acceptance(
        store_root=args.store_root,
        fleet_path=args.fleet,
        atlas_path=args.atlas,
        guarantees_path=args.guarantees,
        capabilities_path=args.capabilities,
        reverify_path=args.reverify_without_box,
    )
    print(json.dumps(observed, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
