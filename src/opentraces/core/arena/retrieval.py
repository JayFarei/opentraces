"""Pure read projections over finalized arena runs.

This module deliberately has no dependency on the runner, box, emulator, or
network layers.  Retrieval verifies the write-once record, then gives a named
verifier a narrow view of bytes that are already inside that record.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diagnostics import sanitize_reason
from .page import render_evidence_page
from .run_store import RunIntegrityError, RunStore


REVERIFICATION_SCHEMA_VERSION = "opentraces.bench.reverification.v0"


class StoredVerifierMismatch(ValueError):
    """The requested verifier identity is not bound to the finalized run."""


@dataclass(frozen=True, slots=True)
class StoredRunRecord:
    """Small listing projection for one verified finalized run."""

    run_id: str
    claim: str
    verdict: str | None
    execution_status: str
    started_at: str


class StoredEvidence:
    """Read-only, path-contained access to one verified run's evidence."""

    __slots__ = ("_run_path",)

    def __init__(self, run_path: Path) -> None:
        self._run_path = run_path.resolve()

    def _resolve(self, reference: str) -> Path:
        if not isinstance(reference, str) or not reference:
            raise RunIntegrityError("stored evidence reference must be a non-empty string")
        relative = Path(reference)
        if relative.is_absolute() or ".." in relative.parts:
            raise RunIntegrityError("stored evidence reference escapes the finalized run")
        try:
            candidate = (self._run_path / relative).resolve(strict=True)
            candidate.relative_to(self._run_path)
        except (OSError, ValueError) as exc:
            raise RunIntegrityError(f"stored evidence is missing: {reference}") from exc
        if not candidate.is_file():
            raise RunIntegrityError(f"stored evidence is not a file: {reference}")
        return candidate

    def read_bytes(self, reference: str) -> bytes:
        return self._resolve(reference).read_bytes()

    def read_text(self, reference: str, *, encoding: str = "utf-8") -> str:
        return self.read_bytes(reference).decode(encoding)

    def read_json(self, reference: str) -> Any:
        return json.loads(self.read_text(reference))

    def validate_ref(self, reference: str) -> str:
        self._resolve(reference)
        return reference


def _verified_run_path(store: RunStore, run_id: str) -> Path:
    if (
        not isinstance(run_id, str)
        or not run_id.startswith("run_")
        or Path(run_id).name != run_id
        or Path(run_id).is_absolute()
    ):
        raise RunIntegrityError("invalid finalized run id")
    run_path = store.root / run_id
    store.verify(run_path)
    return run_path


def _read_result(run_path: Path) -> Mapping[str, Any]:
    result = json.loads((run_path / "result.json").read_text(encoding="utf-8"))
    if not isinstance(result, Mapping):
        raise RunIntegrityError("result.json must contain an object")
    return result


def list_stored_runs(store: RunStore) -> list[StoredRunRecord]:
    """List verified finalized runs, excluding staging and recovery attempts."""

    if not store.root.is_dir():
        return []
    records: list[StoredRunRecord] = []
    for run_path in sorted(
        (path for path in store.root.iterdir() if path.is_dir() and path.name.startswith("run_")),
        key=lambda path: path.name,
    ):
        store.verify(run_path)
        result = _read_result(run_path)
        scenario = result.get("scenario")
        if not isinstance(scenario, Mapping):
            raise RunIntegrityError(f"stored run {run_path.name} has no scenario object")
        records.append(
            StoredRunRecord(
                run_id=run_path.name,
                claim=str(scenario.get("claim") or ""),
                verdict=(str(result["verdict"]) if result.get("verdict") is not None else None),
                execution_status=str(result.get("execution_status") or ""),
                started_at=str(result.get("started_at") or ""),
            )
        )
    return records


def rerender_stored_run(
    store: RunStore,
    run_id: str,
    *,
    output_path: Path | None = None,
) -> Path:
    """Verify and deterministically render a finalized run's frozen bytes."""

    run_path = _verified_run_path(store, run_id)
    return render_evidence_page(run_path, output_path)


def _callable_identity(verifier: Callable[[StoredEvidence], object]) -> tuple[str, str]:
    name = f"{verifier.__module__}.{verifier.__qualname__}"
    source_value = inspect.getsourcefile(verifier)
    if source_value is None:
        raise StoredVerifierMismatch("cannot locate the requested verifier source")
    try:
        digest = hashlib.sha256(Path(source_value).resolve().read_bytes()).hexdigest()
    except OSError as exc:
        raise StoredVerifierMismatch("cannot read the requested verifier source") from exc
    return name, f"sha256:{digest}"


def _canonical_verifier(
    verifier: Callable[[StoredEvidence], object],
) -> Callable[[StoredEvidence], object]:
    """Return the exact callable whose identity and body may be trusted.

    A wrapper can forge the bound verifier's public name and point ``__wrapped__``
    at its source while executing a different outer body.  Stored reverification
    has no need for decorator adaptation, so it rejects that split identity
    instead of hashing one callable and invoking another.
    """

    try:
        unwrapped = inspect.unwrap(verifier)
    except (TypeError, ValueError) as exc:
        raise StoredVerifierMismatch("cannot resolve the requested verifier callable") from exc
    if unwrapped is not verifier:
        raise StoredVerifierMismatch("wrapped callable cannot be used for stored reverification")
    return verifier


def _stored_verifier(result: Mapping[str, Any], *, name: str, digest: str) -> Mapping[str, Any]:
    verifiers = result.get("verifiers")
    if not isinstance(verifiers, list):
        raise StoredVerifierMismatch("stored run has no verifier records")
    named = [row for row in verifiers if isinstance(row, Mapping) and row.get("name") == name]
    if not named:
        raise StoredVerifierMismatch(f"verifier {name!r} is not bound to the stored run")
    for row in named:
        source_ref = row.get("source_ref")
        if isinstance(source_ref, Mapping) and source_ref.get("digest") == digest:
            return row
    raise StoredVerifierMismatch("requested verifier source digest is not bound to the stored run")


def reverify_stored_run(
    store: RunStore,
    run_id: str,
    *,
    verifier_name: str,
    verifier_digest: str,
    verifier: Callable[[StoredEvidence], object],
) -> dict[str, Any]:
    """Run one explicitly pinned verifier against stored evidence only.

    The callable receives ``StoredEvidence`` rather than ``BenchRun``.  There is
    no runner, lease, process, emulator, scenario, or network object in this
    module's call graph.
    """

    run_path = _verified_run_path(store, run_id)
    result = _read_result(run_path)
    target = _canonical_verifier(verifier)
    _stored_verifier(result, name=verifier_name, digest=verifier_digest)
    actual_name, actual_digest = _callable_identity(target)
    if actual_name != verifier_name:
        raise StoredVerifierMismatch("requested verifier name differs from the callable identity")
    if actual_digest != verifier_digest:
        raise StoredVerifierMismatch(
            "requested verifier source digest differs from the callable source"
        )

    evidence = StoredEvidence(run_path)
    evidence_refs: list[str] = []
    reason: dict[str, str] | None = None
    try:
        returned = target(evidence)
        if isinstance(returned, Mapping):
            raw_refs = returned.get("evidence_refs", [])
            if not isinstance(raw_refs, list) or not all(isinstance(ref, str) for ref in raw_refs):
                raise RunIntegrityError("reverifier evidence_refs must be a list of stored paths")
            evidence_refs = [evidence.validate_ref(ref) for ref in raw_refs]
        status = "pass"
    except AssertionError as exc:
        status = "fail"
        reason = sanitize_reason("assertion_failed", str(exc) or "assertion failed")
    except Exception as exc:
        status = "error"
        reason = sanitize_reason("verifier_error", f"{type(exc).__name__}: {exc}")

    return {
        "schema_version": REVERIFICATION_SCHEMA_VERSION,
        "run_id": run_id,
        "verifier": {"name": verifier_name, "digest": verifier_digest},
        "status": status,
        "evidence_refs": evidence_refs,
        "reason": reason,
    }
