"""Run harness for local dataset workflow skills."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from opentraces_schema import DatasetRunRecord

from .datasets import (
    AppendSummary,
    append_rows,
    digest_payload,
    load_dataset,
    read_json,
    read_source_provenance,
    save_manifest,
)


class ExecutorUnavailableError(RuntimeError):
    pass


class DatasetRunLockError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatasetRunResult:
    run_id: str
    run_dir: Path
    status: str
    run_record: DatasetRunRecord
    append_summary: AppendSummary
    cursor_advanced: bool


def run_dataset_workflow(
    name: str,
    *,
    dry_run: bool = False,
    executor: str | None = None,
    scope: dict[str, Any] | None = None,
    limit: int | None = None,
    scheduled: bool = False,
) -> DatasetRunResult:
    dataset = load_dataset(name)
    selected_executor = executor or (
        dataset.manifest.executor.development if dry_run else dataset.manifest.executor.default
    )
    if selected_executor not in {"current-agent", "claude-code-headless"}:
        raise ValueError(f"unsupported executor: {selected_executor}")

    run_id = _new_run_id()
    run_dir = dataset.path / ".opentraces" / "runs" / run_id
    run_dir.mkdir(parents=True)
    started_at = _utc_now()
    schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
    schema_digest = dataset.manifest.schema_ref.digest or digest_payload(schema)
    workflow_digest = dataset.manifest.workflow.digest
    source_provenance = read_source_provenance(dataset.path)
    output_path = run_dir / "output_rows.jsonl"
    run_packet = {
        "run_id": run_id,
        "dataset_name": dataset.name,
        "dataset_path": str(dataset.path),
        "dry_run": dry_run,
        "scheduled": scheduled,
        "executor": selected_executor,
        "schema_path": str(dataset.path / dataset.manifest.schema_ref.path),
        "output_path": str(output_path),
        "workflow": dataset.manifest.workflow.model_dump(mode="json"),
        "candidate_query": (
            dataset.manifest.candidate_query.model_dump(mode="json")
            if dataset.manifest.candidate_query
            else None
        ),
        "source_provenance": source_provenance,
        "scope": scope or {"scope": "all-projects"},
        "limit": limit,
    }
    _write_run_packet(run_dir, dataset.manifest, schema, run_packet)
    lock_path = dataset.path / ".opentraces" / ".lock"

    if selected_executor == "current-agent":
        with _dataset_lock(lock_path, run_id):
            output_path.write_text("", encoding="utf-8")
        append_summary = AppendSummary(
            dataset_name=name,
            run_id=run_id,
            dry_run=dry_run,
            emitted_count=0,
        )
        run_record = _run_record(
            run_id=run_id,
            dataset_name=name,
            dry_run=dry_run,
            executor=selected_executor,
            scope=run_packet["scope"],
            workflow_digest=workflow_digest,
            schema_digest=schema_digest,
            started_at=started_at,
            append_summary=append_summary,
            status="succeeded",
        )
        _write_run_summary(run_dir, run_record, append_summary, cursor_advanced=False)
        return DatasetRunResult(
            run_id=run_id,
            run_dir=run_dir,
            status="instructions",
            run_record=run_record,
            append_summary=append_summary,
            cursor_advanced=False,
        )

    try:
        _execute_claude_code_headless(run_packet, output_path)
        rows = _read_output_rows(output_path)
        with _dataset_lock(lock_path, run_id):
            append_summary = append_rows(name, rows, run_id=run_id, dry_run=dry_run)
            cursor_advanced = False
            if not dry_run:
                _advance_cursor(dataset.path, dataset.manifest, run_id)
                cursor_advanced = True
    except Exception as exc:
        empty_summary = AppendSummary(
            dataset_name=name,
            run_id=run_id,
            dry_run=dry_run,
            emitted_count=0,
        )
        failed_record = _run_record(
            run_id=run_id,
            dataset_name=name,
            dry_run=dry_run,
            executor=selected_executor,
            scope=run_packet["scope"],
            workflow_digest=workflow_digest,
            schema_digest=schema_digest,
            started_at=started_at,
            append_summary=empty_summary,
            status="failed",
        )
        (run_dir / "log.txt").write_text(
            f"{type(exc).__name__}: {exc}\n", encoding="utf-8"
        )
        _write_run_summary(run_dir, failed_record, empty_summary, cursor_advanced=False)
        raise
    run_record = _run_record(
        run_id=run_id,
        dataset_name=name,
        dry_run=dry_run,
        executor=selected_executor,
        scope=run_packet["scope"],
        workflow_digest=workflow_digest,
        schema_digest=schema_digest,
        started_at=started_at,
        append_summary=append_summary,
        status="succeeded",
    )
    _write_run_summary(run_dir, run_record, append_summary, cursor_advanced=cursor_advanced)
    return DatasetRunResult(
        run_id=run_id,
        run_dir=run_dir,
        status="ok",
        run_record=run_record,
        append_summary=append_summary,
        cursor_advanced=cursor_advanced,
    )


def _execute_claude_code_headless(run_packet: dict[str, Any], output_path: Path) -> None:
    fake_rows = os.environ.get("OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS")
    if fake_rows is not None:
        output_path.write_text(fake_rows.rstrip("\n") + "\n", encoding="utf-8")
        return
    executable = shutil.which("claude")
    if not executable:
        raise ExecutorUnavailableError(
            "claude-code-headless executor is unavailable. Install Claude Code or "
            "set OPENTRACES_FAKE_CLAUDE_CODE_HEADLESS_ROWS for recorded local tests."
        )
    raise ExecutorUnavailableError(
        "claude-code-headless executor seam is available, but real invocation is not "
        "enabled by default in Plan 57 tests."
    )


def _write_run_packet(
    run_dir: Path,
    manifest,
    schema: dict[str, Any],
    run_packet: dict[str, Any],
) -> None:
    (run_dir / "run_packet.json").write_text(
        json.dumps(run_packet, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "schema_snapshot.json").write_text(
        json.dumps(schema, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "manifest_snapshot.yaml").write_text(
        yaml.safe_dump(manifest.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    (run_dir / "candidate_summary.json").write_text("{}\n", encoding="utf-8")
    (run_dir / "RUN.md").write_text(_run_instructions(run_packet), encoding="utf-8")
    (run_dir / "log.txt").write_text("", encoding="utf-8")


def _run_instructions(run_packet: dict[str, Any]) -> str:
    return (
        f"# Dataset run {run_packet['run_id']}\n\n"
        f"Dataset: `{run_packet['dataset_name']}`\n\n"
        "Read `run_packet.json` and `schema_snapshot.json`. Use `ot trace query`, "
        "`ot trace slice`, `ot trace map`, and `ot trace get` as needed. Emit plain JSONL rows "
        f"matching the schema to `{run_packet['output_path']}`.\n\n"
        f"Set `OT_DATASET_OUTPUT={run_packet['output_path']}` when running helper scripts.\n"
    )


def _read_output_rows(output_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in output_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _run_record(
    *,
    run_id: str,
    dataset_name: str,
    dry_run: bool,
    executor: str,
    scope: dict[str, Any],
    workflow_digest: str,
    schema_digest: str,
    started_at: str,
    append_summary: AppendSummary,
    status: str = "succeeded",
) -> DatasetRunRecord:
    return DatasetRunRecord(
        run_id=run_id,
        dataset_name=dataset_name,
        dry_run=dry_run,
        executor=executor,
        scope=scope,
        workflow_digest=workflow_digest,
        schema_digest=schema_digest,
        started_at=started_at,
        finished_at=_utc_now(),
        candidate_count=0,
        emitted_count=append_summary.emitted_count,
        appended_count=append_summary.appended_count,
        duplicate_count=append_summary.duplicate_count,
        validation_error_count=append_summary.validation_error_count,
        status=status,
        artefacts={
            "run_packet": "run_packet.json",
            "output_rows": "output_rows.jsonl",
            "summary": "summary.json",
            "validation": "validation.json",
        },
    )


def _write_run_summary(
    run_dir: Path,
    run_record: DatasetRunRecord,
    append_summary: AppendSummary,
    *,
    cursor_advanced: bool,
) -> None:
    validation_payload = {
        "validation_error_count": append_summary.validation_error_count,
        "validation_errors": append_summary.validation_errors,
    }
    summary_payload = {
        "run": run_record.model_dump(mode="json"),
        "append": append_summary.__dict__,
        "cursor_advanced": cursor_advanced,
    }
    (run_dir / "validation.json").write_text(
        json.dumps(validation_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _advance_cursor(root: Path, manifest, run_id: str) -> None:
    cursors_path = root / ".opentraces" / "cursors.yaml"
    data = yaml.safe_load(cursors_path.read_text(encoding="utf-8")) or {"queries": {}}
    queries = data.setdefault("queries", {})
    query = manifest.candidate_query
    query_name = query.name if query else "default"
    queries[query_name] = {
        "query_fingerprint": digest_payload(
            query.model_dump(mode="json") if query else {"scope": "all-projects"}
        ),
        "last_successful_run_id": run_id,
        "last_successful_run_at": _utc_now(),
    }
    cursors_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    save_manifest(root, manifest)


class _dataset_lock:
    def __init__(self, path: Path, run_id: str) -> None:
        self.path = path
        self.run_id = run_id

    def __enter__(self) -> None:
        if self.path.exists():
            raise DatasetRunLockError(f"dataset run already in progress: {self.path}")
        self.path.write_text(self.run_id, encoding="utf-8")

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.path.exists():
            self.path.unlink()


def _new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"run_{stamp}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
