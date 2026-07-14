"""Verified arena labels over finalized bench runs.

The label companion is a derived read over two immutable records: a finalized
bench run and a trace/slice subject.  It never mutates the TraceRecord spine.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from opentraces_schema import TraceRecord
from pydantic import ValidationError

from .._bucket_io import (
    _atomic_write_gzip,
    _canonical_json,
    _read_gzip_bytes,
)
from ..bucket_layout import (
    _path_part,
    trace_v1_json_path,
    trace_v1_labels_path,
    traces_v1_root,
)
from .contract import VERDICTS, validate_result
from .run_store import RunIntegrityError, RunStore


ARENA_LABEL_SCHEMA_VERSION = "opentraces.arena.label.v0"
ARENA_LABEL_KIND = "bench"
COMPLETE_RUN_DIGEST_DOMAIN = b"opentraces.arena.complete-run.v0\x00"
LABEL_ID_DOMAIN = b"opentraces.arena.label-id.v0\x00"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_TRACE_ADDRESS = re.compile(r"[^\s:]+")
_SLICE_ADDRESS = re.compile(r"(?P<trace>[^\s:]+):(?P<start>[0-9]+)-(?P<end>[0-9]+)")
_PRODUCT_PIN_FIELDS = {"commit", "worktree", "dirty_diff_digest"}
_LABEL_FIELDS = {
    "schema_version",
    "label_id",
    "kind",
    "subject",
    "claim",
    "verdict",
    "verifier",
    "run",
    "product_pin",
    "run_facts",
}
_VERIFIER_FIELDS = {
    "ordinal",
    "name",
    "source_ref",
    "status",
    "evidence_refs",
}


class LabelContractError(ValueError):
    """A value cannot be represented by ``opentraces.arena.label.v0``."""


class LabelIntegrityError(RuntimeError):
    """A label no longer agrees with its verified run or companion."""


def _digest(domain: bytes, payload: Mapping[str, Any]) -> str:
    encoded = _canonical_json(dict(payload)).encode("utf-8")
    return f"sha256:{hashlib.sha256(domain + encoded).hexdigest()}"


def _read_object(path: Path, *, name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabelIntegrityError(f"{name} is missing or invalid JSON") from exc
    if not isinstance(payload, dict):
        raise LabelIntegrityError(f"{name} must be a JSON object")
    return payload


def _validate_digest(value: object, *, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise LabelContractError(f"{name} must be a sha256 digest")
    return value


def _validate_subject(subject: object) -> dict[str, str]:
    if not isinstance(subject, Mapping) or set(subject) != {"kind", "address"}:
        raise LabelContractError("subject must contain exactly kind and address")
    kind = subject.get("kind")
    address = subject.get("address")
    if not isinstance(address, str) or address != address.strip():
        raise LabelContractError("subject address must be a canonical non-empty string")
    if kind == "trace":
        if _TRACE_ADDRESS.fullmatch(address) is None:
            raise LabelContractError("trace subject address must name one bare trace")
    elif kind == "slice":
        match = _SLICE_ADDRESS.fullmatch(address)
        if match is None or int(match.group("start")) > int(match.group("end")):
            raise LabelContractError("slice subject address must be trace:A-B with A <= B")
    else:
        raise LabelContractError("subject kind must be trace or slice")
    return {"kind": kind, "address": address}


def _subject_trace_id(subject: Mapping[str, str]) -> str:
    return subject["address"].split(":", 1)[0]


def _validate_product_pin(pin: object) -> dict[str, Any]:
    if not isinstance(pin, Mapping) or set(pin) != _PRODUCT_PIN_FIELDS:
        raise LabelContractError("product pin must contain commit, worktree, and dirty_diff_digest")
    commit = pin.get("commit")
    worktree = pin.get("worktree")
    dirty = pin.get("dirty_diff_digest")
    if not isinstance(commit, str) or not commit:
        raise LabelContractError("product pin commit must be a non-empty string")
    if worktree not in {"clean", "dirty"}:
        raise LabelContractError("product pin worktree must be clean or dirty")
    if worktree == "clean" and dirty is not None:
        raise LabelContractError("clean product pin must have a null dirty digest")
    if worktree == "dirty":
        _validate_digest(dirty, name="dirty product pin")
    return {
        "commit": commit,
        "worktree": worktree,
        "dirty_diff_digest": dirty,
    }


def _run_digest_material(run_path: Path, store: RunStore) -> dict[str, str]:
    expected_path = (store.root / run_path.name).resolve()
    if run_path.resolve() != expected_path:
        raise RunIntegrityError("finalized run path is outside the store namespace")
    store.verify(run_path)
    index = _read_object(store.index_root / f"{run_path.name}.json", name="run index")
    if index.get("run_id") != run_path.name:
        raise LabelIntegrityError("run index id does not match the run directory")
    result_digest = index.get("result_digest")
    integrity_digest = index.get("integrity_digest")
    try:
        return {
            "result_digest": _validate_digest(result_digest, name="result digest"),
            "integrity_digest": _validate_digest(integrity_digest, name="integrity digest"),
        }
    except LabelContractError as exc:
        raise LabelIntegrityError(str(exc)) from exc


def complete_run_digest(
    run_path: Path | str,
    *,
    store: RunStore | None = None,
) -> str:
    """Hash the verified result/integrity pair under a frozen domain."""

    resolved_path = Path(run_path).resolve()
    resolved_store = store or RunStore(resolved_path.parent)
    material = _run_digest_material(resolved_path, resolved_store)
    return _digest(COMPLETE_RUN_DIGEST_DOMAIN, material)


def _validated_verifier(
    verifier: object,
    *,
    ordinal: int,
    run_path: Path | None = None,
) -> dict[str, Any]:
    if not isinstance(verifier, Mapping):
        raise LabelContractError("verifier records must be objects")
    name = verifier.get("name")
    status = verifier.get("status")
    source_ref = verifier.get("source_ref")
    evidence_refs = verifier.get("evidence_refs")
    if not isinstance(name, str) or not name:
        raise LabelContractError("verifier name must be a non-empty string")
    if status not in VERDICTS:
        raise LabelContractError("verifier status must be pass, fail, or skip")
    if not isinstance(source_ref, Mapping) or set(source_ref) != {"path", "digest"}:
        raise LabelContractError("verifier source_ref must contain path and digest")
    source_path = source_ref.get("path")
    if not isinstance(source_path, str) or not source_path:
        raise LabelContractError("verifier source path must be a non-empty string")
    source_digest = _validate_digest(source_ref.get("digest"), name="verifier source digest")
    if not isinstance(evidence_refs, list) or not all(
        isinstance(ref, str) and ref for ref in evidence_refs
    ):
        raise LabelContractError("verifier evidence_refs must be non-empty strings")
    if run_path is not None:
        run_root = run_path.resolve()
        for ref in evidence_refs:
            candidate = (run_root / ref).resolve()
            if not candidate.is_relative_to(run_root) or not candidate.is_file():
                raise LabelIntegrityError(
                    f"verifier evidence ref is not persisted in the run: {ref}"
                )
    return {
        "ordinal": ordinal,
        "name": name,
        "source_ref": {"path": source_path, "digest": source_digest},
        "status": status,
        "evidence_refs": list(evidence_refs),
    }


def _label_id(row_without_id: Mapping[str, Any]) -> str:
    return "lbl_" + _digest(LABEL_ID_DOMAIN, row_without_id).removeprefix("sha256:")


def _mint_rows(
    result: Mapping[str, Any],
    *,
    subject: Mapping[str, str],
    run_digest: str,
    run_path: Path,
) -> list[dict[str, Any]]:
    if result.get("execution_status") != "complete" or result.get("verdict") not in VERDICTS:
        raise LabelContractError("a label requires an adjudicated verdict")
    verifiers = result.get("verifiers")
    if not isinstance(verifiers, list) or not verifiers:
        raise LabelContractError("a label requires at least one verifier record")
    scenario = result.get("scenario")
    claim = scenario.get("claim") if isinstance(scenario, Mapping) else None
    if not isinstance(claim, str) or not claim:
        raise LabelContractError("label claim must be a non-empty string")
    pins = result.get("pins")
    product_pin = _validate_product_pin(pins.get("product") if isinstance(pins, Mapping) else None)
    evidence = result.get("evidence")
    recordings = result.get("recordings")
    if not isinstance(evidence, Mapping) or not isinstance(evidence.get("complete"), bool):
        raise LabelContractError("run evidence completeness must be recorded")
    if not isinstance(recordings, Mapping) or not isinstance(recordings.get("rewatchable"), bool):
        raise LabelContractError("run recording completeness must be recorded")

    rows: list[dict[str, Any]] = []
    for ordinal, verifier in enumerate(verifiers, start=1):
        row_without_id = {
            "schema_version": ARENA_LABEL_SCHEMA_VERSION,
            "kind": ARENA_LABEL_KIND,
            "subject": dict(subject),
            "claim": claim,
            "verdict": result["verdict"],
            "verifier": _validated_verifier(
                verifier,
                ordinal=ordinal,
                run_path=run_path,
            ),
            "run": {
                "id": result["run_id"],
                "ref": f"runs/v1/{result['run_id']}",
                "complete_digest": run_digest,
            },
            "product_pin": product_pin,
            "run_facts": {
                "evidence_complete": evidence["complete"],
                "rewatchable": recordings["rewatchable"],
            },
        }
        rows.append(
            {
                "schema_version": row_without_id["schema_version"],
                "label_id": _label_id(row_without_id),
                **{key: value for key, value in row_without_id.items() if key != "schema_version"},
            }
        )
    return rows


def mint_labels_for_run(
    run_path: Path | str,
    *,
    subject: Mapping[str, str],
    store: RunStore | None = None,
    expected_complete_run_digest: str | None = None,
) -> list[dict[str, Any]]:
    """Mint deterministic per-verifier rows from one verified finalized run."""

    resolved_path = Path(run_path).resolve()
    resolved_store = store or RunStore(resolved_path.parent)
    canonical_subject = _validate_subject(subject)
    run_digest = complete_run_digest(resolved_path, store=resolved_store)
    if expected_complete_run_digest is not None and run_digest != expected_complete_run_digest:
        raise LabelIntegrityError("complete run digest does not match the expected pin")
    result = _read_object(resolved_path / "result.json", name="result.json")
    validate_result(result)
    if result["run_id"] != resolved_path.name:
        raise LabelIntegrityError("result run_id does not match the run directory")
    return _mint_rows(
        result,
        subject=canonical_subject,
        run_digest=run_digest,
        run_path=resolved_path,
    )


def validate_label(label: object) -> dict[str, Any]:
    """Validate the exact frozen label shape without touching storage."""

    if not isinstance(label, Mapping) or set(label) != _LABEL_FIELDS:
        raise LabelContractError("label field set does not match opentraces.arena.label.v0")
    if label.get("schema_version") != ARENA_LABEL_SCHEMA_VERSION:
        raise LabelContractError("unsupported arena label schema_version")
    if label.get("kind") != ARENA_LABEL_KIND:
        raise LabelContractError("arena label kind must be bench")
    subject = _validate_subject(label.get("subject"))
    claim = label.get("claim")
    verdict = label.get("verdict")
    if not isinstance(claim, str) or not claim:
        raise LabelContractError("label claim must be a non-empty string")
    if verdict not in VERDICTS:
        raise LabelContractError("label verdict must be pass, fail, or skip")

    verifier = label.get("verifier")
    if not isinstance(verifier, Mapping) or set(verifier) != _VERIFIER_FIELDS:
        raise LabelContractError("label verifier field set is invalid")
    ordinal = verifier.get("ordinal")
    if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 1:
        raise LabelContractError("verifier ordinal must be a positive integer")
    canonical_verifier = _validated_verifier(verifier, ordinal=ordinal)

    run = label.get("run")
    if not isinstance(run, Mapping) or set(run) != {"id", "ref", "complete_digest"}:
        raise LabelContractError("label run pin field set is invalid")
    run_id = run.get("id")
    if not isinstance(run_id, str) or not run_id:
        raise LabelContractError("label run id must be a non-empty string")
    if run.get("ref") != f"runs/v1/{run_id}":
        raise LabelContractError("label run ref must be canonical")
    run_digest = _validate_digest(run.get("complete_digest"), name="complete run digest")
    product_pin = _validate_product_pin(label.get("product_pin"))
    facts = label.get("run_facts")
    if (
        not isinstance(facts, Mapping)
        or set(facts) != {"evidence_complete", "rewatchable"}
        or not isinstance(facts.get("evidence_complete"), bool)
        or not isinstance(facts.get("rewatchable"), bool)
    ):
        raise LabelContractError("label run_facts must record two independent booleans")

    row_without_id = {
        "schema_version": ARENA_LABEL_SCHEMA_VERSION,
        "kind": ARENA_LABEL_KIND,
        "subject": subject,
        "claim": claim,
        "verdict": verdict,
        "verifier": canonical_verifier,
        "run": {
            "id": run_id,
            "ref": run["ref"],
            "complete_digest": run_digest,
        },
        "product_pin": product_pin,
        "run_facts": dict(facts),
    }
    expected_id = _label_id(row_without_id)
    if label.get("label_id") != expected_id:
        raise LabelIntegrityError("label_id does not match the canonical label content")
    return {
        "schema_version": ARENA_LABEL_SCHEMA_VERSION,
        "label_id": expected_id,
        **{key: value for key, value in row_without_id.items() if key != "schema_version"},
    }


def verify_label(label: object, *, store: RunStore | None = None) -> bool:
    """Reverify the complete run and reproduce the exact label row from it."""

    canonical = validate_label(label)
    resolved_store = store or RunStore()
    run_id = canonical["run"]["id"]
    run_path = resolved_store.root / run_id
    rows = mint_labels_for_run(
        run_path,
        subject=canonical["subject"],
        store=resolved_store,
        expected_complete_run_digest=canonical["run"]["complete_digest"],
    )
    expected = next(
        (row for row in rows if row["label_id"] == canonical["label_id"]),
        None,
    )
    if expected is None or _canonical_json(expected) != _canonical_json(canonical):
        raise LabelIntegrityError("label does not reproduce from the verified run")
    return True


def _decode_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        text = _read_gzip_bytes(path).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise LabelIntegrityError(f"invalid labels companion: {path}") from exc
    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            row = validate_label(payload)
            if row["label_id"] in seen_ids:
                raise LabelIntegrityError(f"duplicate label_id in companion: {row['label_id']}")
            seen_ids.add(row["label_id"])
            rows.append(row)
        except (json.JSONDecodeError, LabelContractError, LabelIntegrityError) as exc:
            raise LabelIntegrityError(
                f"invalid labels companion row {line_number}: {path}"
            ) from exc
    if [row["label_id"] for row in rows] != sorted(row["label_id"] for row in rows):
        raise LabelIntegrityError("labels companion rows are not in canonical order")
    return rows


def read_labels(project_slug: str, trace_id: str) -> list[dict[str, Any]]:
    """Read and contract-check one trace's label companion."""

    return _decode_rows(trace_v1_labels_path(project_slug, trace_id))


def attach_labels(
    *,
    project_slug: str,
    trace_id: str,
    labels: Iterable[Mapping[str, Any]],
    store: RunStore | None = None,
) -> Path:
    """Merge verified rows into a deterministic sibling companion."""

    trace_path = trace_v1_json_path(project_slug, trace_id)
    if not trace_path.is_file():
        raise LabelIntegrityError("label subject trace does not exist in the bucket")
    try:
        subject_record = TraceRecord.model_validate_json(trace_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise LabelIntegrityError("label subject trace is not a valid TraceRecord") from exc
    if subject_record.trace_id != trace_id:
        raise LabelIntegrityError("label subject trace id does not match its bucket path")
    resolved_store = store or RunStore()
    path = trace_v1_labels_path(project_slug, trace_id)
    existing_rows = _decode_rows(path)
    for row in existing_rows:
        verify_label(row, store=resolved_store)
    by_id = {row["label_id"]: row for row in existing_rows}
    for raw in labels:
        row = validate_label(raw)
        if _subject_trace_id(row["subject"]) != trace_id:
            raise LabelContractError("label subject does not match the companion trace")
        verify_label(row, store=resolved_store)
        existing = by_id.get(row["label_id"])
        if existing is not None and _canonical_json(existing) != _canonical_json(row):
            raise LabelIntegrityError("label_id collision has non-identical content")
        by_id[row["label_id"]] = row
    body = b"".join(
        (_canonical_json(by_id[label_id]) + "\n").encode("utf-8") for label_id in sorted(by_id)
    )
    _atomic_write_gzip(path, body)
    return path


def label_summary_for_trace(trace_id: str, *, limit: int = 8) -> dict[str, Any]:
    """Return a bounded summary from sibling companions for a normal read."""

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("label summary limit must be a non-negative integer")
    root = traces_v1_root()
    rows_by_id: dict[str, dict[str, Any]] = {}
    if root.is_dir():
        pattern = f"*/{_path_part(trace_id)}/labels.jsonl.gz"
        for path in sorted(root.glob(pattern)):
            for row in _decode_rows(path):
                existing = rows_by_id.get(row["label_id"])
                if existing is not None and _canonical_json(existing) != _canonical_json(row):
                    raise LabelIntegrityError("cross-project label_id collision")
                rows_by_id[row["label_id"]] = row
    rows = [rows_by_id[label_id] for label_id in sorted(rows_by_id)]
    return {
        "count": len(rows),
        "items": [
            {
                "label_id": row["label_id"],
                "verdict": row["verdict"],
                "verifier": row["verifier"]["name"],
                "subject": row["subject"],
                "run_ref": row["run"]["ref"],
            }
            for row in rows[:limit]
        ],
        "truncated": len(rows) > limit,
    }


__all__ = [
    "ARENA_LABEL_KIND",
    "ARENA_LABEL_SCHEMA_VERSION",
    "LabelContractError",
    "LabelIntegrityError",
    "attach_labels",
    "complete_run_digest",
    "label_summary_for_trace",
    "mint_labels_for_run",
    "read_labels",
    "validate_label",
    "verify_label",
]
