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
from ..slicing.contract import SLICING_SCHEMA_VERSION
from ..slicing.models import Trajectory
from ..trace_slices import (
    TraceMaterializationRef,
    materialize_trajectory,
    slice_by_steps,
)
from ..trails.lineage import parse_trail_ref
from ..trails.slices import trace_slice_id_for
from .contract import VERDICTS, validate_result
from .run_store import RunDraft, RunIntegrityError, RunStore


ARENA_LABEL_SCHEMA_VERSION = "opentraces.arena.label.v0"
ARENA_LABEL_KIND = "bench"
COMPLETE_RUN_DIGEST_DOMAIN = b"opentraces.arena.complete-run.v0\x00"
LABEL_ID_DOMAIN = b"opentraces.arena.label-id.v0\x00"
MATERIALIZED_SLICE_DIGEST_DOMAIN = b"opentraces.arena.materialized-slice.v0\x00"
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
_SLICE_LABEL_FIELDS = _LABEL_FIELDS | {"slice_pin"}
_MATERIALIZED_SLICE_FIELDS = {
    "slice_id",
    "trace_id",
    "generation_index",
    "source",
    "template",
    "start_step_index",
    "end_step_index",
    "map",
    "steps",
    "map_node_refs",
    "trace_patch_refs",
    "git_anchor_refs",
    "metadata",
    "limitations",
}
_SLICE_PIN_COMMON_FIELDS = {
    "provenance_kind",
    "materialized_ref",
    "materialized_digest",
    "artifact_digest",
    "slice_id",
    "trace_id",
    "generation_index",
    "start_step_index",
    "end_step_index",
    "source",
    "map_node_refs",
    "trace_patch_refs",
    "git_anchor_refs",
    "limitations",
}
_SLICE_PIN_TRAJECTORY_FIELDS = _SLICE_PIN_COMMON_FIELDS | {
    "slicing_schema_version",
    "trajectory",
    "trajectory_position_range",
    "coordinate_translation",
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
    if _subject_trace_id({"kind": str(kind), "address": address}) in {".", ".."}:
        raise LabelContractError("subject trace address cannot be a path segment")
    return {"kind": kind, "address": address}


def _subject_trace_id(subject: Mapping[str, str]) -> str:
    return subject["address"].split(":", 1)[0]


def _slice_bounds(subject: Mapping[str, str]) -> tuple[int, int]:
    match = _SLICE_ADDRESS.fullmatch(subject["address"])
    if subject.get("kind") != "slice" or match is None:
        raise LabelContractError("slice subject address must be trace:A-B with A <= B")
    return int(match.group("start")), int(match.group("end"))


def _validate_string_list(value: object, *, name: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LabelContractError(f"slice pin {name} must be an array of strings")
    return list(value)


def _validate_trajectory(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"start", "end", "kind", "label"}:
        raise LabelContractError("slice pin trajectory must contain start, end, kind, and label")
    start = value.get("start")
    end = value.get("end")
    kind = value.get("kind")
    label = value.get("label")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end < start
    ):
        raise LabelContractError("slice pin trajectory positions must satisfy 0 <= start <= end")
    if not isinstance(kind, str) or not kind or not isinstance(label, str):
        raise LabelContractError("slice pin trajectory kind and label must be strings")
    return {"start": start, "end": end, "kind": kind, "label": label}


def _validate_slice_pin(pin: object, *, subject: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(pin, Mapping):
        raise LabelContractError("slice subject requires a slice pin")
    provenance_kind = pin.get("provenance_kind")
    expected_fields = (
        _SLICE_PIN_TRAJECTORY_FIELDS
        if provenance_kind == "trajectory"
        else _SLICE_PIN_COMMON_FIELDS
        if provenance_kind == "explicit"
        else set()
    )
    if not expected_fields or set(pin) != expected_fields:
        raise LabelContractError("slice pin field set does not match its provenance kind")

    slice_id = pin.get("slice_id")
    trace_id = pin.get("trace_id")
    generation_index = pin.get("generation_index")
    start_step_index = pin.get("start_step_index")
    end_step_index = pin.get("end_step_index")
    source = pin.get("source")
    if not isinstance(slice_id, str) or not slice_id:
        raise LabelContractError("slice pin slice_id must be a non-empty string")
    if not isinstance(trace_id, str) or not trace_id:
        raise LabelContractError("slice pin trace_id must be a non-empty string")
    if (
        not isinstance(generation_index, int)
        or isinstance(generation_index, bool)
        or generation_index < 0
    ):
        raise LabelContractError("slice pin generation_index must be a non-negative integer")
    if (
        not isinstance(start_step_index, int)
        or isinstance(start_step_index, bool)
        or not isinstance(end_step_index, int)
        or isinstance(end_step_index, bool)
        or start_step_index < 0
        or end_step_index < start_step_index
    ):
        raise LabelContractError("slice pin step range must satisfy 0 <= start <= end")
    expected_source = (
        "slicer_trajectory" if provenance_kind == "trajectory" else "manual_step_range"
    )
    if source != expected_source:
        raise LabelContractError(f"{provenance_kind} slice pin source must be {expected_source}")
    expected_subject = {
        "kind": "slice",
        "address": f"{trace_id}:{start_step_index}-{end_step_index}",
    }
    if dict(subject) != expected_subject:
        raise LabelIntegrityError("slice subject boundary does not match its pin")
    parsed_trace, point, span, reserved = parse_trail_ref(expected_subject["address"])
    if (parsed_trace, point, span, reserved) != (
        trace_id,
        None,
        (start_step_index, end_step_index),
        "span",
    ):
        raise LabelIntegrityError("slice subject does not round-trip through the address grammar")

    expected_slice_id = trace_slice_id_for(
        trace_id=trace_id,
        generation_index=generation_index,
        start_step_index=start_step_index,
        end_step_index=end_step_index,
        source=source,
    )
    if slice_id != expected_slice_id:
        raise LabelIntegrityError("slice_id does not reproduce from the pinned identity")
    expected_ref = f"artifacts/slices/{slice_id}.json"
    if pin.get("materialized_ref") != expected_ref:
        raise LabelContractError("slice pin materialized_ref must be canonical")

    canonical: dict[str, Any] = {
        "provenance_kind": provenance_kind,
        "materialized_ref": expected_ref,
        "materialized_digest": _validate_digest(
            pin.get("materialized_digest"), name="materialized slice"
        ),
        "artifact_digest": _validate_digest(pin.get("artifact_digest"), name="slice artifact"),
        "slice_id": slice_id,
        "trace_id": trace_id,
        "generation_index": generation_index,
        "start_step_index": start_step_index,
        "end_step_index": end_step_index,
        "source": source,
        "map_node_refs": _validate_string_list(pin.get("map_node_refs"), name="map_node_refs"),
        "trace_patch_refs": _validate_string_list(
            pin.get("trace_patch_refs"), name="trace_patch_refs"
        ),
        "git_anchor_refs": _validate_string_list(
            pin.get("git_anchor_refs"), name="git_anchor_refs"
        ),
        "limitations": _validate_string_list(pin.get("limitations"), name="limitations"),
    }
    if provenance_kind == "trajectory":
        trajectory = _validate_trajectory(pin.get("trajectory"))
        position_range = pin.get("trajectory_position_range")
        if position_range != {"start": trajectory["start"], "end": trajectory["end"]}:
            raise LabelIntegrityError(
                "trajectory position range does not match the frozen trajectory"
            )
        if pin.get("slicing_schema_version") != SLICING_SCHEMA_VERSION:
            raise LabelContractError("trajectory slice pin has an unsupported slicing schema")
        if pin.get("coordinate_translation") != "array_position_to_step_index":
            raise LabelContractError("trajectory slice pin has an invalid coordinate translation")
        canonical.update(
            {
                "slicing_schema_version": SLICING_SCHEMA_VERSION,
                "trajectory": trajectory,
                "trajectory_position_range": dict(position_range),
                "coordinate_translation": "array_position_to_step_index",
            }
        )
    return canonical


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


def _materialize_slice(
    trace_ref: TraceMaterializationRef,
    *,
    subject: Mapping[str, str] | None,
    trajectory: Trajectory | Mapping[str, Any] | None,
) -> tuple[dict[str, str], dict[str, Any], str]:
    trace_id = trace_ref.record.trace_id
    if trajectory is not None:
        materialized = materialize_trajectory(trace_ref, trajectory)
        provenance_kind = "trajectory"
    else:
        if subject is None:
            raise LabelContractError("explicit slice materialization requires a slice subject")
        canonical_subject = _validate_subject(subject)
        if canonical_subject["kind"] != "slice":
            raise LabelContractError("slice materialization requires a slice subject")
        if _subject_trace_id(canonical_subject) != trace_id:
            raise LabelIntegrityError(
                "slice subject trace does not match the materialization record"
            )
        start, end = _slice_bounds(canonical_subject)
        materialized = slice_by_steps(
            trace_ref.trace_map,
            trace_ref.record,
            start_step_index=start,
            end_step_index=end,
        )
        provenance_kind = "explicit"

    materialized_subject = {
        "kind": "slice",
        "address": (
            f"{materialized['trace_id']}:"
            f"{materialized['start_step_index']}-{materialized['end_step_index']}"
        ),
    }
    canonical_materialized_subject = _validate_subject(materialized_subject)
    if subject is not None and _validate_subject(subject) != canonical_materialized_subject:
        raise LabelIntegrityError("slice subject boundary disagrees with materialization")
    return canonical_materialized_subject, materialized, provenance_kind


def _validate_materialized_slice(
    materialized: object,
    *,
    provenance_kind: str,
) -> dict[str, Any]:
    if not isinstance(materialized, Mapping) or set(materialized) != _MATERIALIZED_SLICE_FIELDS:
        raise LabelIntegrityError("materialized slice artifact field set is invalid")
    payload = dict(materialized)
    metadata = payload.get("metadata")
    if provenance_kind == "trajectory":
        if payload.get("source") != "slicer_trajectory" or payload.get("template") is not None:
            raise LabelIntegrityError("trajectory artifact has invalid source or template")
        if not isinstance(metadata, Mapping) or set(metadata) != {
            "slicing_schema_version",
            "trajectory",
            "trajectory_position_range",
            "coordinate_translation",
        }:
            raise LabelIntegrityError("trajectory artifact metadata field set is invalid")
        trajectory = _validate_trajectory(metadata.get("trajectory"))
        if metadata.get("slicing_schema_version") != SLICING_SCHEMA_VERSION:
            raise LabelIntegrityError("trajectory artifact has an unsupported slicing schema")
        if metadata.get("trajectory_position_range") != {
            "start": trajectory["start"],
            "end": trajectory["end"],
        }:
            raise LabelIntegrityError("trajectory artifact position range is inconsistent")
        if metadata.get("coordinate_translation") != "array_position_to_step_index":
            raise LabelIntegrityError("trajectory artifact coordinate translation is invalid")
    else:
        if (
            payload.get("source") != "manual_step_range"
            or payload.get("template") is not None
            or metadata != {}
        ):
            raise LabelIntegrityError("explicit slice artifact must use canonical step slicing")
    return payload


def stage_slice_artifact(
    draft: RunDraft,
    trace_ref: TraceMaterializationRef,
    *,
    trajectory: Trajectory | Mapping[str, Any] | None = None,
    subject: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Materialize and stage the exact slice a verifier will grade.

    The artifact is written before run finalization so RunStore's integrity
    manifest and complete-run digest cover its exact bytes. ``slice_id`` is an
    address identity rather than a content digest; an attempted same-id write
    with different bytes therefore fails closed instead of overwriting.
    """

    canonical_subject, materialized, provenance_kind = _materialize_slice(
        trace_ref,
        subject=subject,
        trajectory=trajectory,
    )
    _validate_materialized_slice(materialized, provenance_kind=provenance_kind)
    artifact_ref = f"artifacts/slices/{materialized['slice_id']}.json"
    artifact_path = draft.path / artifact_ref
    canonical_bytes = _canonical_json(materialized, pretty=True).encode("utf-8")
    if artifact_path.is_file() and artifact_path.read_bytes() != canonical_bytes:
        raise LabelIntegrityError("slice_id collision has different canonical materialized bytes")
    draft.write_json(artifact_ref, materialized)
    return {
        "artifact_ref": artifact_ref,
        "subject": canonical_subject,
        "slice_id": materialized["slice_id"],
    }


def _slice_pin_for_run(
    run_path: Path,
    *,
    subject: Mapping[str, str],
    materialized: Mapping[str, Any],
    provenance_kind: str,
) -> dict[str, Any]:
    validated = _validate_materialized_slice(
        materialized,
        provenance_kind=provenance_kind,
    )
    artifact_ref = f"artifacts/slices/{validated['slice_id']}.json"
    artifact_path = run_path / artifact_ref
    if not artifact_path.is_file() or not artifact_path.resolve().is_relative_to(
        run_path.resolve()
    ):
        raise LabelIntegrityError("materialized slice artifact is missing from the run")
    artifact_bytes = artifact_path.read_bytes()
    try:
        stored = json.loads(artifact_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LabelIntegrityError("materialized slice artifact is invalid JSON") from exc
    if not isinstance(stored, dict):
        raise LabelIntegrityError("materialized slice artifact must be a JSON object")
    _validate_materialized_slice(stored, provenance_kind=provenance_kind)
    if _canonical_json(stored) != _canonical_json(validated):
        raise LabelIntegrityError(
            "stored slice does not match fresh materialization from "
            "the authoritative current Trace Map"
        )

    pin: dict[str, Any] = {
        "provenance_kind": provenance_kind,
        "materialized_ref": artifact_ref,
        "materialized_digest": _digest(MATERIALIZED_SLICE_DIGEST_DOMAIN, stored),
        "artifact_digest": f"sha256:{hashlib.sha256(artifact_bytes).hexdigest()}",
        "slice_id": stored["slice_id"],
        "trace_id": stored["trace_id"],
        "generation_index": stored["generation_index"],
        "start_step_index": stored["start_step_index"],
        "end_step_index": stored["end_step_index"],
        "source": stored["source"],
        "map_node_refs": list(stored["map_node_refs"]),
        "trace_patch_refs": list(stored["trace_patch_refs"]),
        "git_anchor_refs": list(stored["git_anchor_refs"]),
        "limitations": list(stored["limitations"]),
    }
    if provenance_kind == "trajectory":
        metadata = stored["metadata"]
        pin.update(
            {
                "slicing_schema_version": metadata["slicing_schema_version"],
                "trajectory": dict(metadata["trajectory"]),
                "trajectory_position_range": dict(metadata["trajectory_position_range"]),
                "coordinate_translation": metadata["coordinate_translation"],
            }
        )
    return _validate_slice_pin(pin, subject=subject)


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
    slice_pin: Mapping[str, Any] | None = None,
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
        canonical_verifier = _validated_verifier(
            verifier,
            ordinal=ordinal,
            run_path=run_path,
        )
        if (
            slice_pin is not None
            and slice_pin["materialized_ref"] not in canonical_verifier["evidence_refs"]
        ):
            raise LabelIntegrityError(
                "slice artifact must be named in the grading verifier evidence_refs"
            )
        row_without_id = {
            "schema_version": ARENA_LABEL_SCHEMA_VERSION,
            "kind": ARENA_LABEL_KIND,
            "subject": dict(subject),
            "claim": claim,
            "verdict": result["verdict"],
            "verifier": canonical_verifier,
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
        if slice_pin is not None:
            row_without_id["slice_pin"] = dict(slice_pin)
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
    trace_ref: TraceMaterializationRef | None = None,
    trajectory: Trajectory | Mapping[str, Any] | None = None,
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
    slice_pin: dict[str, Any] | None = None
    if canonical_subject["kind"] == "slice":
        if trace_ref is None:
            raise LabelIntegrityError("slice mint requires a materialization reference")
        fresh_subject, materialized, provenance_kind = _materialize_slice(
            trace_ref,
            subject=canonical_subject,
            trajectory=trajectory,
        )
        if fresh_subject != canonical_subject:
            raise LabelIntegrityError("fresh materialization changed the slice subject")
        slice_pin = _slice_pin_for_run(
            resolved_path,
            subject=canonical_subject,
            materialized=materialized,
            provenance_kind=provenance_kind,
        )
    elif trajectory is not None or trace_ref is not None:
        raise LabelContractError("trace subjects cannot carry slice materialization inputs")
    return _mint_rows(
        result,
        subject=canonical_subject,
        run_digest=run_digest,
        run_path=resolved_path,
        slice_pin=slice_pin,
    )


def validate_label(label: object) -> dict[str, Any]:
    """Validate the exact frozen label shape without touching storage."""

    if not isinstance(label, Mapping):
        raise LabelContractError("label must be an object")
    if label.get("schema_version") != ARENA_LABEL_SCHEMA_VERSION:
        raise LabelContractError("unsupported arena label schema_version")
    if label.get("kind") != ARENA_LABEL_KIND:
        raise LabelContractError("arena label kind must be bench")
    subject = _validate_subject(label.get("subject"))
    expected_fields = _SLICE_LABEL_FIELDS if subject["kind"] == "slice" else _LABEL_FIELDS
    if set(label) != expected_fields:
        raise LabelContractError("label field set does not match opentraces.arena.label.v0")
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
    if subject["kind"] == "slice":
        row_without_id["slice_pin"] = _validate_slice_pin(
            label.get("slice_pin"),
            subject=subject,
        )
    expected_id = _label_id(row_without_id)
    if label.get("label_id") != expected_id:
        raise LabelIntegrityError("label_id does not match the canonical label content")
    return {
        "schema_version": ARENA_LABEL_SCHEMA_VERSION,
        "label_id": expected_id,
        **{key: value for key, value in row_without_id.items() if key != "schema_version"},
    }


def _canonical_subject_trace(trace_id: str) -> tuple[str, TraceRecord]:
    candidates: dict[tuple[str, str], tuple[str, TraceRecord]] = {}
    root = traces_v1_root()
    if root.is_dir():
        pattern = f"*/{_path_part(trace_id)}/trace.json"
        for path in sorted(root.glob(pattern)):
            try:
                record = TraceRecord.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
                raise LabelIntegrityError("slice subject trace is not a valid TraceRecord") from exc
            if record.trace_id != trace_id:
                raise LabelIntegrityError("slice subject trace path contains a different trace")
            project_key = path.parent.parent.name
            candidates[(project_key, _canonical_json(record.model_dump(mode="json")))] = (
                project_key,
                record,
            )

    from ..bucket_trace_records import iter_trace_record_objects

    for obj in iter_trace_record_objects():
        if obj.trace_id == trace_id:
            candidates[(obj.project_slug, _canonical_json(obj.record.model_dump(mode="json")))] = (
                obj.project_slug,
                obj.record,
            )
    if not candidates:
        raise LabelIntegrityError("slice subject canonical TraceRecord is missing")
    if len(candidates) != 1:
        raise LabelIntegrityError("slice subject canonical TraceRecord is ambiguous")
    return next(iter(candidates.values()))


def authoritative_trace_materialization_ref(
    project_slug: str,
    record: TraceRecord,
) -> TraceMaterializationRef:
    """Rebuild the current materialization map from registered Trail state.

    A canonical project registration makes its source repository authoritative.
    Record-only materialization is retained only for traces whose project has no
    registration, never as a fallback for a broken registered source.
    """

    from .. import paths

    identity_path = paths.PROJECTS_DIR / project_slug / "project.json"
    if not identity_path.exists():
        return TraceMaterializationRef.from_record(record)
    identity = _read_object(identity_path, name="canonical project identity")
    source_path = identity.get("path") or identity.get("project_dir")
    if not isinstance(source_path, str) or not source_path:
        raise LabelIntegrityError("canonical project identity has no source repository")
    source_repo = Path(source_path).expanduser().resolve()
    if not source_repo.is_dir():
        raise LabelIntegrityError("canonical project source repository is unavailable")
    try:
        from ..trails import build_trail_query_projection_for_trace

        projection = build_trail_query_projection_for_trace(source_repo, record.trace_id)
        return TraceMaterializationRef.from_record(record, trail_projection=projection)
    except Exception as exc:
        raise LabelIntegrityError(
            "authoritative current Trace Map could not be rebuilt from Trail world-state"
        ) from exc


def _trace_ref_for_label(row: Mapping[str, Any]) -> TraceMaterializationRef:
    pin = row["slice_pin"]
    project_slug, record = _canonical_subject_trace(pin["trace_id"])
    if record.generation_index != pin["generation_index"]:
        raise LabelIntegrityError("slice pin generation does not match the canonical trace")
    return authoritative_trace_materialization_ref(project_slug, record)


def verify_labels(
    labels: Iterable[object],
    *,
    store: RunStore | None = None,
) -> bool:
    """Batch-reverify labels, hashing each shared run/subject pin once."""

    canonical_rows = [validate_label(label) for label in labels]
    resolved_store = store or RunStore()
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for row in canonical_rows:
        key = (
            row["run"]["id"],
            _canonical_json(row["subject"]),
            row["run"]["complete_digest"],
            _canonical_json(row.get("slice_pin")),
        )
        groups.setdefault(key, []).append(row)

    for (run_id, _subject_key, run_digest, _slice_pin_key), group in groups.items():
        run_path = resolved_store.root / run_id
        trace_ref: TraceMaterializationRef | None = None
        trajectory: Mapping[str, Any] | None = None
        if group[0]["subject"]["kind"] == "slice":
            trace_ref = _trace_ref_for_label(group[0])
            if group[0]["slice_pin"]["provenance_kind"] == "trajectory":
                trajectory = group[0]["slice_pin"]["trajectory"]
        expected_rows = mint_labels_for_run(
            run_path,
            subject=group[0]["subject"],
            store=resolved_store,
            expected_complete_run_digest=run_digest,
            trace_ref=trace_ref,
            trajectory=trajectory,
        )
        expected_by_id = {row["label_id"]: row for row in expected_rows}
        for row in group:
            expected = expected_by_id.get(row["label_id"])
            if expected is None or _canonical_json(expected) != _canonical_json(row):
                raise LabelIntegrityError("label does not reproduce from the verified run")
    return True


def verify_label(label: object, *, store: RunStore | None = None) -> bool:
    """Reverify the complete run and reproduce one exact label row from it."""

    return verify_labels([label], store=store)


def _decode_rows(
    path: Path,
    *,
    expected_trace_id: str | None = None,
) -> list[dict[str, Any]]:
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
        except (json.JSONDecodeError, LabelContractError, LabelIntegrityError) as exc:
            raise LabelIntegrityError(
                f"invalid labels companion row {line_number}: {path}"
            ) from exc
        if expected_trace_id is not None and _subject_trace_id(row["subject"]) != expected_trace_id:
            raise LabelIntegrityError("label companion subject does not match the requested trace")
        if row["label_id"] in seen_ids:
            raise LabelIntegrityError(f"duplicate label_id in companion: {row['label_id']}")
        seen_ids.add(row["label_id"])
        rows.append(row)
    if [row["label_id"] for row in rows] != sorted(row["label_id"] for row in rows):
        raise LabelIntegrityError("labels companion rows are not in canonical order")
    return rows


def read_labels(project_slug: str, trace_id: str) -> list[dict[str, Any]]:
    """Read and contract-check one trace's label companion."""

    return _decode_rows(
        trace_v1_labels_path(project_slug, trace_id),
        expected_trace_id=trace_id,
    )


def attach_labels(
    *,
    project_slug: str,
    trace_id: str,
    labels: Iterable[Mapping[str, Any]],
    store: RunStore | None = None,
) -> Path:
    """Merge verified rows into a deterministic sibling companion."""

    trace_path = trace_v1_json_path(project_slug, trace_id)
    if not trace_path.resolve().is_relative_to(traces_v1_root().resolve()):
        raise LabelContractError("label companion path must remain inside traces/v1")
    if trace_path.is_file():
        try:
            subject_record = TraceRecord.model_validate_json(trace_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
            raise LabelIntegrityError("label subject trace is not a valid TraceRecord") from exc
    else:
        # Normal ingestion writes the canonical v2 record before the additive
        # traces/v1 envelope. Accept that already-persisted spine as existence
        # proof so #290 can attach in the same ingest transaction; the later
        # per-trace projection places trace.json beside this companion.
        from ..bucket_trace_records import read_trace_record_object, trace_record_path

        stored = read_trace_record_object(trace_record_path(project_slug, trace_id))
        if stored is None:
            raise LabelIntegrityError("label subject trace does not exist in the bucket")
        subject_record = stored.record
    if subject_record.trace_id != trace_id:
        raise LabelIntegrityError("label subject trace id does not match its bucket path")
    resolved_store = store or RunStore()
    path = trace_v1_labels_path(project_slug, trace_id)
    existing_rows = _decode_rows(path, expected_trace_id=trace_id)
    verify_labels(existing_rows, store=resolved_store)
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


def _authoritative_label_companion(
    trace_id: str,
    companions: list[Path],
) -> Path:
    """Resolve the single owning companion without decoding the rest.

    The address is a bare trace, and a trace is owned by exactly one project, so
    a bounded read decodes only that project's companion instead of every
    matching companion in the corpus (#323). Directory listing is cheap; the
    per-companion gzip decode is the cost this avoids. A companion set that
    cannot be bound to one canonical owning project fails closed rather than
    silently unioning across projects.
    """

    if len(companions) == 1:
        return companions[0]
    root = traces_v1_root()
    pattern = f"*/{_path_part(trace_id)}/trace.json"
    owning_dirs: set[Path] = set()
    for owner in sorted(root.glob(pattern)):
        try:
            record = TraceRecord.model_validate_json(owner.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError, ValidationError) as exc:
            raise LabelIntegrityError("label subject trace is not a valid TraceRecord") from exc
        if record.trace_id != trace_id:
            raise LabelIntegrityError("label subject trace path contains a different trace")
        owning_dirs.add(owner.parent)
    if len(owning_dirs) != 1:
        raise LabelIntegrityError("label companion owning project is ambiguous")
    companion = next(iter(owning_dirs)) / "labels.jsonl.gz"
    if companion not in set(companions):
        raise LabelIntegrityError("owning project has no label companion for the trace")
    return companion


def label_summary_for_trace(trace_id: str, *, limit: int = 8) -> dict[str, Any]:
    """Return a bounded summary from the owning companion for a normal read."""

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("label summary limit must be a non-negative integer")
    _validate_subject({"kind": "trace", "address": trace_id})
    root = traces_v1_root()
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        companions = sorted(root.glob(f"*/{_path_part(trace_id)}/labels.jsonl.gz"))
        if companions:
            companion = _authoritative_label_companion(trace_id, companions)
            rows = _decode_rows(companion, expected_trace_id=trace_id)
    verify_labels(rows, store=RunStore())
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
    "stage_slice_artifact",
    "validate_label",
    "verify_label",
    "verify_labels",
]
