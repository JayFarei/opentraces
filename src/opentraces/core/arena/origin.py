"""Captured-evidence joins from bench runs to their invoking traces."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

from opentraces_schema import TraceRecord

from .contract import VERDICTS


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


__all__ = ["BenchInvocation", "detect_bench_invocations"]
