"""Future A7 guarantee verifiers over complete stored proof artifacts."""

from __future__ import annotations

from opentraces.core.arena.retrieval import StoredEvidence


def verify_remote_rented_glibc_emulator(evidence: StoredEvidence) -> dict[str, list[str]]:
    """Fail closed until a real remote/rented evidence chain exists."""

    del evidence
    raise AssertionError("unbound: remote/rented glibc proof chain is not implemented")


def verify_linux_x86_64_hf_emulator(evidence: StoredEvidence) -> dict[str, list[str]]:
    """Fail closed until a real Linux x86_64 evidence chain exists."""

    del evidence
    raise AssertionError("unbound: Linux x86_64 proof chain is not implemented")
