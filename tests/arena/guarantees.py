"""Future A7 guarantee verifiers over complete stored proof artifacts."""

from __future__ import annotations

from typing import Any

from opentraces.core.arena.retrieval import StoredEvidence


def _proof(evidence: StoredEvidence, reference: str) -> dict[str, Any]:
    payload = evidence.read_json(reference)
    assert isinstance(payload, dict), f"{reference} must contain an object"
    return payload


def verify_remote_rented_glibc_emulator(evidence: StoredEvidence) -> dict[str, list[str]]:
    """Verify the future remote/rented glibc lease proof from stored bytes only."""

    reference = "artifacts/remote-rented-glibc.json"
    proof = _proof(evidence, reference)
    assert proof.get("schema_version") == "opentraces.bench.remote-rented-glibc.v0"
    assert proof.get("placement") == "remote-rented"
    assert proof.get("os") == "linux"
    assert proof.get("libc") == "glibc"
    assert isinstance(proof.get("lease_id"), str) and proof["lease_id"]
    assert proof.get("contract_suite") == "pass"
    return {"evidence_refs": [reference]}


def verify_linux_x86_64_hf_emulator(evidence: StoredEvidence) -> dict[str, list[str]]:
    """Verify the future Linux x86_64 emulator proof from stored bytes only."""

    reference = "artifacts/linux-x86_64-hf-emulator.json"
    proof = _proof(evidence, reference)
    assert proof.get("schema_version") == ("opentraces.bench.linux-x86_64-hf-emulator.v0")
    assert proof.get("os") == "linux"
    assert proof.get("architecture") == "x86_64"
    assert proof.get("binary_target") == "bun-linux-x64"
    assert proof.get("contract_suite") == "pass"
    return {"evidence_refs": [reference]}
