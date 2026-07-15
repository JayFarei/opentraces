"""Captured-evidence joins from bench runs to their invoking traces."""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from opentraces_schema import TraceRecord

from .contract import VERDICTS, validate_result
from .labels import (
    LabelContractError,
    LabelIntegrityError,
    authoritative_trace_materialization_ref,
    attach_labels,
    mint_labels_for_run,
    stage_slice_artifact,
)
from .run_store import RunDraft, RunIntegrityError, RunStore
from ..trace_slices import TraceMaterializationRef


_RUN_ID_TEXT = r"run_[0-9]{8}T[0-9]{12}Z_[0-9a-f]{12}"
_RUN_ID = re.compile(_RUN_ID_TEXT)
_HUMAN_FIRST_LINE = re.compile(
    rf"bench_run_(?P<run_id>{_RUN_ID_TEXT}) "
    rf"(?P<verdict>{'|'.join(sorted(VERDICTS))}) "
    r"(?P<claim>\S(?:.*\S)?)"
)
_ORIGIN_CLAIM_JSON_PREFIX = "json:"
_SHELL_CONTROL = re.compile(r"^[;&|]+$")
_SHELL_TOOL_MARKERS = ("bash", "shell", "terminal", "exec_command")


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


def origin_claim_token(claim: str) -> str:
    """Encode claims that cannot be copied byte-for-byte onto one physical line."""

    if "\n" in claim or "\r" in claim or claim.startswith(_ORIGIN_CLAIM_JSON_PREFIX):
        return _ORIGIN_CLAIM_JSON_PREFIX + json.dumps(claim, ensure_ascii=False)
    return claim


def _decode_origin_claim(token: str) -> str | None:
    if not token.startswith(_ORIGIN_CLAIM_JSON_PREFIX):
        return token
    try:
        claim = json.loads(token.removeprefix(_ORIGIN_CLAIM_JSON_PREFIX))
    except json.JSONDecodeError:
        return None
    return claim if isinstance(claim, str) and claim else None


def _from_human(content: str) -> tuple[str, str, str] | None:
    first_line = content.splitlines()[0] if content.splitlines() else ""
    match = _HUMAN_FIRST_LINE.fullmatch(first_line)
    if match is None:
        return None
    claim = _decode_origin_claim(match.group("claim"))
    if claim is None:
        return None
    return match.group("run_id"), match.group("verdict"), claim


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


def _bench_argv(tokens: list[str]) -> bool:
    while tokens and ("=" in tokens[0] and not tokens[0].startswith("=")):
        name, _separator, _value = tokens[0].partition("=")
        if not name.replace("_", "a").isalnum():
            break
        tokens = tokens[1:]
    if tokens and tokens[0] == "env":
        return _bench_argv(tokens[1:])
    if tokens and tokens[0] == "command":
        return _bench_argv(tokens[1:])
    if len(tokens) >= 3 and Path(tokens[0]).name in {"opentraces", "ot"}:
        return tokens[1:3] == ["bench", "run"]
    if (
        len(tokens) >= 5
        and Path(tokens[0]).name.startswith("python")
        and tokens[1:5] == ["-m", "opentraces", "bench", "run"]
    ):
        return True
    if len(tokens) >= 4 and Path(tokens[0]).name == "uv" and tokens[1] == "run":
        return _bench_argv(tokens[2:])
    return False


def _command_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for token in tokens:
        if _SHELL_CONTROL.fullmatch(token):
            if segments[-1]:
                segments.append([])
            continue
        segments[-1].append(token)
    return [segment for segment in segments if segment]


def _call_invokes_bench(call: object) -> bool:
    tool_name = str(getattr(call, "tool_name", "")).lower()
    if not any(marker in tool_name for marker in _SHELL_TOOL_MARKERS):
        return False
    tool_input = getattr(call, "input", None)
    if not isinstance(tool_input, dict):
        return False
    argv = tool_input.get("argv")
    if isinstance(argv, list) and all(isinstance(part, str) for part in argv):
        if _bench_argv(list(argv)):
            return True
    command = tool_input.get("command") or tool_input.get("cmd")
    if not isinstance(command, str):
        return False
    return any(_bench_argv(segment) for segment in _command_segments(command))


def detect_bench_invocations(record: TraceRecord) -> list[BenchInvocation]:
    """Detect only frozen bench outputs in captured tool observations."""

    invocations: list[BenchInvocation] = []
    for step in getattr(record, "steps", []) or []:
        calls_by_id: dict[str, object] = {}
        ambiguous_ids: set[str] = set()
        for call in step.tool_calls or []:
            call_id = str(call.tool_call_id)
            if call_id in calls_by_id:
                ambiguous_ids.add(call_id)
            else:
                calls_by_id[call_id] = call
        for call_id in ambiguous_ids:
            calls_by_id.pop(call_id, None)
        for observation in step.observations or []:
            linked_call = calls_by_id.get(observation.source_call_id)
            if linked_call is None or not _call_invokes_bench(linked_call):
                continue
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


def _explicit_subject(
    address: str,
) -> tuple[str, dict[str, str], TraceMaterializationRef | None]:
    from ..bucket_envelope import trace_v2_summary_by_id
    from ..bucket_layout import trace_v1_json_path
    from ..trace_corpus import load_record, resolve
    from ..trails.lineage import parse_trail_ref

    trace_id, point, span, reserved = parse_trail_ref(address)
    if not trace_id or reserved not in {None, "span"}:
        raise OriginJoinError("explicit origin address is not a trace, point, or span")
    candidate = resolve(trace_id)
    if candidate is not None:
        stored = load_record(candidate)
        if stored is None:
            raise OriginJoinError(
                "explicit origin address does not resolve to a valid stored trace"
            )
        project_slug = stored.project_slug
        record = stored.record
    else:
        # Compatibility fallback for old envelope-only buckets that predate the
        # canonical TraceRecord object/pointer store.
        summary = trace_v2_summary_by_id(trace_id)
        if summary is None:
            raise OriginJoinError("explicit origin address does not resolve to a stored trace")
        project_slug = summary.get("project_slug")
        if not isinstance(project_slug, str) or not project_slug:
            raise OriginJoinError("explicit origin address has no owning project")
        trace_path = trace_v1_json_path(project_slug, trace_id)
        try:
            record = TraceRecord.model_validate_json(trace_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise OriginJoinError(
                "explicit origin address does not resolve to a valid trace"
            ) from exc
    if record.trace_id != trace_id:
        raise OriginJoinError("explicit origin address disagrees with the stored trace")
    step_indices = {step.step_index for step in record.steps}
    if point is not None:
        if point not in step_indices:
            raise OriginJoinError("explicit origin address names a missing trace point")
        subject = {"kind": "slice", "address": f"{trace_id}:{point}-{point}"}
    elif span is not None:
        start, end = span
        if start > end or start not in step_indices or end not in step_indices:
            raise OriginJoinError("explicit origin address names a missing or invalid trace span")
        subject = {"kind": "slice", "address": f"{trace_id}:{start}-{end}"}
    else:
        subject = {"kind": "trace", "address": trace_id}
    trace_ref: TraceMaterializationRef | None = None
    if subject["kind"] == "slice":
        try:
            trace_ref = authoritative_trace_materialization_ref(project_slug, record)
        except LabelIntegrityError as exc:
            raise OriginJoinError(
                "explicit origin authoritative Trace Map could not be rebuilt"
            ) from exc
    return project_slug, subject, trace_ref


def stage_explicit_origin_slice(
    draft: RunDraft,
    *,
    address: str,
) -> dict[str, object]:
    """Stage a slice origin while its run is still mutable.

    Slice subjects are immutable run evidence, so the canonical materialized
    slice must land before ``RunDraft.finalize`` seals the integrity manifest.
    Whole-trace origins preserve their existing label shape and stage nothing.
    """

    _project_slug, subject, trace_ref = _explicit_subject(address)
    if subject["kind"] == "trace":
        return {"subject": subject, "artifact_ref": None}
    if trace_ref is None:  # pragma: no cover - guarded by _explicit_subject
        raise OriginJoinError("explicit slice origin has no materialization reference")
    try:
        return stage_slice_artifact(draft, trace_ref, subject=subject)
    except (LabelContractError, LabelIntegrityError, ValueError) as exc:
        raise OriginJoinError(f"explicit origin slice could not be staged: {exc}") from exc


def attach_explicit_bench_labels(
    run_path: Path | str,
    *,
    address: str,
    store: RunStore | None = None,
) -> OriginAttachment:
    """Resolve an explicit origin and attach labels through the shared mint path."""

    resolved_path = Path(run_path).resolve()
    resolved_store = store or RunStore(resolved_path.parent)
    project_slug, subject, trace_ref = _explicit_subject(address)
    try:
        labels = mint_labels_for_run(
            resolved_path,
            subject=subject,
            store=resolved_store,
            trace_ref=trace_ref,
        )
        attach_labels(
            project_slug=project_slug,
            trace_id=subject["address"].split(":", 1)[0],
            labels=labels,
            store=resolved_store,
        )
    except (LabelContractError, LabelIntegrityError, RunIntegrityError) as exc:
        raise OriginJoinError(f"explicit origin label refused: {exc}") from exc
    return OriginAttachment(
        run_id=resolved_path.name,
        address=subject["address"],
        resolution="explicit",
    )


__all__ = [
    "BenchInvocation",
    "OriginAttachment",
    "OriginJoinError",
    "attach_captured_bench_labels",
    "attach_explicit_bench_labels",
    "detect_bench_invocations",
    "origin_claim_token",
    "stage_explicit_origin_slice",
]
