"""Build compact command trajectory eval rows from raw evidence rows.

Redaction is intentionally NOT this template's job. The append-path floor
(``security/dataset_rows.py::sanitize_dataset_row``, invoked by
``datasets.append_rows`` over every row) is the single, non-overridable
sanitization story for bundled workflow templates -- see docs/adr/0008 §1.8
and issue #189. This module only *shapes* text into a compact, single-line,
length-bounded form for a readable row; it never scrubs secrets, home paths,
or numbers itself, because a second hand-rolled redaction path is exactly the
drift the floor exists to prevent. A template that genuinely needs mid-flight
scrubbing (this one does not -- the append path already sanitizes every row)
should call the CLI sanitize surface (``opentraces security sanitize``)
instead of hand-rolling its own.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_SOURCE = (
    Path.home()
    / ".opentraces"
    / "datasets"
    / "skill-command-trajectories"
    / "data"
    / "train.jsonl"
)
BODY_PREFIX = "Base directory for this skill:"
BODY_SKILL_RE = re.compile(r"Base directory for this skill:\s+\S*/skills/([^\s/]+)")


def compact_text(value: object, *, limit: int = 1600) -> str:
    """Coerce ``value`` to a single-line, length-bounded string.

    Formatting only: collapses newlines/whitespace and truncates. No
    redaction happens here -- see the module docstring.
    """
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def is_injected_body(value: object) -> bool:
    return str(value or "").lstrip().startswith(BODY_PREFIX)


def body_skill_name(value: object) -> str:
    match = BODY_SKILL_RE.search(str(value or ""))
    return match.group(1) if match else ""


def normalize_path(value: str) -> str:
    """Return ``value`` as a plain string.

    Home-path / username scrubbing is the append-path floor's job
    (``path_anonymizer``), not this template's -- see the module docstring.
    """
    return value or ""


def unique_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def choose_intent(command: dict[str, Any]) -> tuple[str, str, bool, str, bool]:
    args = command.get("args") or ""
    trigger = command.get("trigger_text") or ""
    intent = command.get("intent_text") or ""
    args_is_body = is_injected_body(args)
    trigger_is_body = is_injected_body(trigger)
    body_name = body_skill_name(args) or body_skill_name(trigger)
    skill_name = str(command.get("skill_name") or "")

    if args and not args_is_body:
        return compact_text(args), "command_args", args_is_body, body_name, False
    if trigger and not trigger_is_body:
        return compact_text(trigger), "trigger_text", args_is_body, body_name, bool(body_name and body_name != skill_name)
    if intent and not is_injected_body(intent):
        return compact_text(intent), "unit_intent_text", args_is_body, body_name, bool(body_name and body_name != skill_name)
    return "", "missing", args_is_body, body_name, bool(body_name and body_name != skill_name)


def outcome_success_label(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "unknown"


def build_row(raw: dict[str, Any], *, raw_row_index: int) -> dict[str, Any]:
    command = raw.get("command") or {}
    attribution = raw.get("attribution") or {}
    trajectory = raw.get("trajectory") or {}
    summary = trajectory.get("summary") or {}
    outcome = raw.get("outcome") or {}
    quality = raw.get("quality") or {}

    command_intent, intent_source, has_body_args, body_skill, body_mismatch = choose_intent(command)
    skill_name = compact_text(command.get("skill_name"), limit=200)
    command_name = compact_text(command.get("command_name"), limit=200) or (
        f"/{skill_name}" if command.get("source") == "claude_slash_command" and skill_name else "Skill"
    )
    files = unique_sorted([normalize_path(str(path)) for path in summary.get("files_modified") or []])
    tools = unique_sorted([compact_text(tool, limit=120) for tool in summary.get("tools") or []])
    excluded = attribution.get("excluded_step_indices") or []
    limitations = list(quality.get("limitations") or [])
    if has_body_args and "source_args_were_injected_skill_body" not in limitations:
        limitations.append("source_args_were_injected_skill_body")
    if body_mismatch and "source_skill_body_mismatch" not in limitations:
        limitations.append("source_skill_body_mismatch")
    if not command_intent and "missing_clean_command_intent" not in limitations:
        limitations.append("missing_clean_command_intent")

    raw_row_id = str(raw.get("row_id") or "")
    raw_row_key = f"raw-row-{raw_row_index:06d}"
    safe_trace_id = f"raw:{raw_row_key}"
    safe_unit_id = f"raw-unit:{raw_row_key}"
    start = int(attribution.get("start_step_index") or attribution.get("anchor_step_index") or 0)
    end = int(attribution.get("end_step_index") or start)
    write_count = int(summary.get("write_operation_count") or 0)
    evidence_refs = {
        "raw_row_key": raw_row_key,
        "raw_row_index": raw_row_index,
        "raw_dataset": "skill-command-trajectories",
    }
    compact_summary = compact_text(f"{skill_name}: {command_intent}", limit=240)

    return {
        "source_trace_id": safe_trace_id,
        "source_unit_id": safe_unit_id,
        "summary": compact_summary,
        "eval_task_type": "command_trajectory_attribution",
        "source_raw_row_id": raw_row_key,
        "project_slug": str(raw.get("project_slug") or ""),
        "session_id": str(raw.get("session_id") or ""),
        "command_name": command_name,
        "command_source": compact_text(command.get("source"), limit=120),
        "command_intent": command_intent,
        "command_intent_source": intent_source,
        "expected_skill_name": skill_name,
        "expected_trajectory_start_step": start,
        "expected_trajectory_end_step": end,
        "expected_span_length_steps": max(0, end - start + 1),
        "expected_excluded_steps_json": json.dumps(excluded, sort_keys=True),
        "expected_has_write_operations": write_count > 0,
        "expected_write_operation_count": write_count,
        "expected_files_modified_count": len(files),
        "expected_files_modified_json": json.dumps(files, sort_keys=True),
        "expected_tools_json": json.dumps(tools, sort_keys=True),
        "expected_has_patch_refs": bool(summary.get("patch_refs")),
        "outcome_success_label": outcome_success_label(outcome.get("success")),
        "outcome_committed": bool(outcome.get("committed")),
        "outcome_commit_sha": compact_text(outcome.get("commit_sha"), limit=120),
        # Plan 080: outcome.patch removed; patches[] is the authoritative reference.
        "outcome_patch_count": len(raw.get("patches") or []),
        "label_confidence": compact_text(attribution.get("confidence"), limit=80) or "medium",
        "source_has_injected_body_args": has_body_args,
        "source_skill_body_name": body_skill,
        "source_skill_body_mismatch": body_mismatch,
        "limitations_json": json.dumps(unique_sorted([str(item) for item in limitations]), sort_keys=True),
        "evidence_refs_json": json.dumps(evidence_refs, sort_keys=True),
    }


def read_rows(source: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as stream:
        for raw_row_index, raw_line in enumerate(stream, start=1):
            line = raw_line.strip()
            if not line:
                continue
            rows.append(build_row(json.loads(line), raw_row_index=raw_row_index))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    rows = read_rows(args.source.expanduser(), limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
    print(f"emitted {len(rows)} row(s) -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
