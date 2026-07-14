"""Captured-evidence joins from bench runs to their invoking traces."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from opentraces_schema import TraceRecord

from .contract import VERDICTS, validate_result
from .labels import LabelContractError, attach_labels, mint_labels_for_run
from .run_store import RunIntegrityError, RunStore


_RUN_ID_TEXT = r"run_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{12}"
_RUN_ID = re.compile(_RUN_ID_TEXT)
_HUMAN_FIRST_LINE = re.compile(
    rf"bench_run_(?P<run_id>{_RUN_ID_TEXT}) "
    rf"(?P<verdict>{'|'.join(sorted(VERDICTS))}) "
    r"(?P<claim>\S(?:.*\S)?)"
)


@dataclass(frozen=True)
class BenchInvocation:
    """One exact bench result observed in a captured tool result."""

    run_id: str
    verdict: str
    claim: str
    output_format: Literal["human", "json"]
    step_index: int
    source_call_id: str


@dataclass(frozen=True)
class OriginAttachment:
    """One verified run-to-subject join completed by a named resolution path."""

    run_id: str
    address: str
    resolution: Literal["captured", "explicit"]


class OriginJoinError(RuntimeError):
    """Captured origin evidence cannot be reproduced from a finalized run."""


def _from_human(content: str) -> tuple[str, str, str] | None:
    first_line = content.splitlines()[0] if content.splitlines() else ""
    match = _HUMAN_FIRST_LINE.fullmatch(first_line)
    if match is None:
        return None
    return match.group("run_id"), match.group("verdict"), match.group("claim")


def _from_json(content: str) -> tuple[str, str, str] | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    run_id = payload.get("run_id")
    verdict = payload.get("verdict")
    claim = payload.get("claim")
    if not isinstance(run_id, str) or _RUN_ID.fullmatch(run_id) is None:
        return None
    if verdict not in VERDICTS:
        return None
    if not isinstance(claim, str) or not claim or claim != claim.strip():
        return None
    return run_id, verdict, claim


def detect_bench_invocations(record: TraceRecord) -> list[BenchInvocation]:
    """Detect only frozen bench outputs in captured tool observations."""

    invocations: list[BenchInvocation] = []
    for step in record.steps:
        for observation in step.observations or []:
            content = observation.content
            if not isinstance(content, str) or not content:
                continue
            captured = _from_human(content)
            output_format: Literal["human", "json"] = "human"
            if captured is None:
                captured = _from_json(content)
                output_format = "json"
            if captured is None:
                continue
            run_id, verdict, claim = captured
            invocations.append(
                BenchInvocation(
                    run_id=run_id,
                    verdict=verdict,
                    claim=claim,
                    output_format=output_format,
                    step_index=step.step_index,
                    source_call_id=observation.source_call_id,
                )
            )
    return invocations


def _verified_result(
    invocation: BenchInvocation,
    *,
    store: RunStore,
) -> tuple[Path, dict]:
    run_path = store.root / invocation.run_id
    try:
        store.verify(run_path)
        payload = json.loads((run_path / "result.json").read_text(encoding="utf-8"))
        validate_result(payload)
    except (OSError, ValueError, RunIntegrityError) as exc:
        raise OriginJoinError(
            f"captured bench token does not resolve to a verified finalized run: "
            f"{invocation.run_id}"
        ) from exc
    scenario = payload.get("scenario")
    stored_claim = scenario.get("claim") if isinstance(scenario, dict) else None
    if stored_claim != invocation.claim or payload.get("verdict") != invocation.verdict:
        raise OriginJoinError("captured claim or verdict does not match the finalized run")
    return run_path, payload


def attach_captured_bench_labels(
    record: TraceRecord,
    *,
    project_slug: str,
    store: RunStore | None = None,
) -> list[OriginAttachment]:
    """Attach verified bench labels found in one already-persisted trace."""

    resolved_store = store or RunStore()
    attachments: list[OriginAttachment] = []
    seen: set[tuple[str, str, str]] = set()
    for invocation in detect_bench_invocations(record):
        evidence_key = (invocation.run_id, invocation.verdict, invocation.claim)
        if evidence_key in seen:
            continue
        seen.add(evidence_key)
        run_path, _result = _verified_result(invocation, store=resolved_store)
        try:
            labels = mint_labels_for_run(
                run_path,
                subject={"kind": "trace", "address": record.trace_id},
                store=resolved_store,
            )
        except LabelContractError as exc:
            detail = "product pin" if "product pin" in str(exc) else "stored run"
            raise OriginJoinError(f"{detail} cannot mint an origin label: {exc}") from exc
        attach_labels(
            project_slug=project_slug,
            trace_id=record.trace_id,
            labels=labels,
            store=resolved_store,
        )
        attachments.append(
            OriginAttachment(
                run_id=invocation.run_id,
                address=record.trace_id,
                resolution="captured",
            )
        )
    return attachments


__all__ = [
    "BenchInvocation",
    "OriginAttachment",
    "OriginJoinError",
    "attach_captured_bench_labels",
    "detect_bench_invocations",
]
