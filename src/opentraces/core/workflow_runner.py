"""Run harness for local dataset workflow skills.

One execution seam lives here (the M1 engine collapse, #185):

* ``execute_workflow`` — the single workflow-execution primitive. It runs a
  deterministic ``script`` executor that subprocess-runs
  ``scripts/build_rows.py`` from the workflow package, with the versioned
  ``opentraces.workflow.run_packet.v1`` run packet handed over on the
  environment (``OT_RUN_PACKET``/``OT_DATASET_OUTPUT``). ``_execute_script`` is
  the ONE script-execution seam; #188 routes its subprocess boundary through the
  shared isolation primitive (``core.isolation.run_isolated``) with a scrubbed
  env and an honest ``sandbox_tier``. Used directly by non-dataset consumers
  like the branch-context PR consumer in ``core.branch_context``.
* ``run_dataset_workflow`` — the dataset-bound runner. It is lifecycle
  bookkeeping (locks, run dirs, cursors, append, run records) *around*
  ``execute_workflow``: it builds the one versioned run packet (a superset that
  additionally carries the dataset fields templates and the lifecycle read),
  writes the dataset run artifacts, then hands execution to
  ``execute_workflow``. The ``current-agent`` executor stays a lifecycle-only
  branch (writes ``RUN.md`` instructions, executes no script) — it is the
  labeled human-in-the-loop path, not a second executor.

The previously-dead ``claude-code-headless`` executor (a permanent stub that
never produced rows) and its fake-rows environment test-injection hook were
removed in the collapse.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from opentraces.core._time import utc_now_str
from pathlib import Path
from typing import Any

import yaml

from opentraces_schema import DatasetRunRecord

from . import paths
from .datasets import (
    AppendSummary,
    append_rows,
    digest_payload,
    load_dataset,
    read_json,
    read_source_provenance,
    save_manifest,
    source_provenance_for_query,
    write_source_provenance,
)
from .isolation import SANDBOX_TIER_NONE, IsolationReport, run_isolated
from .workflow_judgment import (
    OT_JUDGMENT_SIDECAR_ENV,
    RC_NEEDS_JUDGMENT,
    read_sidecar_requests,
)
from .workflows import (
    WorkflowIntegrityError,
    WorkflowPackage,
    load_workflow,
    resolve_workflow_reference,
    verify_workflow_digest,
)


class ExecutorUnavailableError(RuntimeError):
    pass


class DatasetRunLockError(RuntimeError):
    pass


class WorkflowScriptError(RuntimeError):
    """Raised when a workflow's script executor exits non-zero."""


class WorkflowNeedsJudgmentError(RuntimeError):
    """Raised when a workflow's builder exits ``rc=10`` with a judgment sidecar.

    This is the #186 handshake, NOT a failure: the builder needs recorded
    judgments to produce deterministic rows. Carries the workflow name and the
    structured ``judgment_requests`` the builder wrote so the CLI can render the
    ``opentraces.workflow.needs_judgment.v1`` envelope and exit ``rc=10``.
    """

    def __init__(self, workflow_name: str, judgment_requests: list[dict[str, Any]]):
        self.workflow_name = workflow_name
        self.judgment_requests = judgment_requests
        super().__init__(
            f"workflow '{workflow_name}' needs {len(judgment_requests)} judgment(s)"
        )


@dataclass(frozen=True)
class DatasetRunResult:
    run_id: str
    run_dir: Path
    status: str
    run_record: DatasetRunRecord
    append_summary: AppendSummary
    cursor_advanced: bool


def _is_deferred_provenance(provenance: dict[str, Any] | None) -> bool:
    """A dataset created via the fast ``dataset new`` path defers the bucket
    snapshot (``capture_mode == "deferred"``). The run is the moment the bucket
    is actually projected into rows, so the run-time provenance must reflect the
    real bucket state at that point."""
    if not isinstance(provenance, dict):
        return True
    manifest = provenance.get("bucket_manifest")
    if not isinstance(manifest, dict):
        return True
    if manifest.get("capture_mode") == "deferred":
        return True
    return "digest" not in manifest


def _ensure_run_source_provenance(
    dataset, *, dry_run: bool
) -> dict[str, Any] | None:
    """Return the source provenance for this run, capturing the real bucket
    snapshot if the stored provenance was deferred at ``dataset new`` time.

    On a real (non-dry) run the captured provenance is persisted to
    ``source_provenance.json`` so downstream ``_build_row_provenance`` (which
    re-reads it from disk per row) records the same real bucket lineage.
    """
    stored = read_source_provenance(dataset.path)
    if not _is_deferred_provenance(stored):
        return stored
    candidate_query = dataset.manifest.candidate_query
    if candidate_query is None:
        return stored
    refreshed = source_provenance_for_query(
        candidate_query,
        include_bucket_snapshot=True,
    )
    if refreshed is None:
        return stored
    if not dry_run:
        write_source_provenance(dataset.path, refreshed)
    return refreshed


def run_dataset_workflow(
    name: str,
    *,
    dry_run: bool = False,
    executor: str | None = None,
    scope: dict[str, Any] | None = None,
    limit: int | None = None,
    scheduled: bool = False,
    privacy_tier: str | None = None,
    trail_freshness_policy: str = "warn",
    answers: dict[str, Any] | None = None,
    strict: bool = False,
) -> DatasetRunResult:
    dataset = load_dataset(name)
    selected_executor = executor or (
        dataset.manifest.executor.development if dry_run else dataset.manifest.executor.default
    )
    if selected_executor not in {"current-agent", "script"}:
        raise ValueError(f"unsupported executor: {selected_executor}")

    run_id = _new_run_id()
    run_dir = dataset.path / ".opentraces" / "runs" / run_id
    run_dir.mkdir(parents=True)
    started_at = utc_now_str()
    schema = read_json(dataset.path / dataset.manifest.schema_ref.path)
    schema_digest = dataset.manifest.schema_ref.digest or digest_payload(schema)
    workflow_digest = dataset.manifest.workflow.digest
    source_provenance = _ensure_run_source_provenance(dataset, dry_run=dry_run)
    trail_freshness = _trail_freshness_for_dataset(dataset, scope or {"scope": "all-projects"})
    trail_warnings = [
        item for item in trail_freshness if item.get("severity") == "warning"
    ]
    if trail_freshness_policy not in {"warn", "fail", "ignore"}:
        raise ValueError("trail_freshness_policy must be warn, fail, or ignore")
    if trail_freshness_policy == "fail" and trail_warnings:
        projects = ", ".join(
            sorted({str(item.get("project_slug")) for item in trail_warnings})
        )
        raise ValueError(
            "Trace Trail projection is stale for dataset composition"
            + (f": {projects}" if projects else "")
        )
    output_path = run_dir / "output_rows.jsonl"
    # For the ``script`` executor, resolve the workflow package up front so the
    # single execution seam (``execute_workflow``) runs the exact package the
    # dataset pins. ``current-agent`` executes no script, so it never resolves
    # (and must not require) an installed package.
    workflow_package = (
        _workflow_package_for_dataset(dataset)
        if selected_executor == "script"
        else None
    )
    # Run-time integrity re-verify (#187): the recomputed digest of the package
    # about to execute must match the digest the dataset pinned. Non-strict
    # warns and proceeds; strict is fatal BEFORE any run artifact is written or
    # the subprocess runs. current-agent resolves no package, so there is
    # nothing to verify there.
    if workflow_package is not None:
        verify_workflow_digest(
            workflow_package.digest,
            dataset.manifest.workflow.digest,
            workflow_name=dataset.manifest.workflow.skill,
            strict=strict,
        )
    # ONE versioned run packet (opentraces.workflow.run_packet.v1). The dataset
    # runner builds it as a SUPERSET: the versioned base shape plus the dataset
    # fields templates read (``scope`` is in the base; ``candidate_query`` is
    # added here) and the lifecycle/provenance fields (``source_provenance``,
    # ``security``, ``privacy_tier``, ``trail_freshness``...). Adding keys keeps
    # it frozen-envelope safe (still .v1); nothing is removed.
    dataset_context = {
        "run_id": run_id,
        "dataset_name": dataset.name,
        "dataset_path": str(dataset.path),
        "dry_run": dry_run,
        "scheduled": scheduled,
        "workflow": dataset.manifest.workflow.model_dump(mode="json"),
        "candidate_query": (
            dataset.manifest.candidate_query.model_dump(mode="json")
            if dataset.manifest.candidate_query
            else None
        ),
        "source_provenance": source_provenance,
        "limit": limit,
        "privacy_tier": privacy_tier,
        "security": dataset.manifest.security.model_dump(mode="json"),
        "trail_freshness_policy": trail_freshness_policy,
        "trail_freshness": trail_freshness,
    }
    # #186: recorded judgments are the fourth contract input. Persisting them in
    # the run packet is what makes the projection f(workflow, bucket, answers) —
    # the builder reads packet["answers"] to finalize rows deterministically.
    if answers:
        dataset_context["answers"] = answers
    run_packet = _build_workflow_packet(
        workflow_name=dataset.manifest.workflow.skill,
        workflow_digest=workflow_digest,
        workflow_path=str(workflow_package.path) if workflow_package else None,
        schema_path=str(dataset.path / dataset.manifest.schema_ref.path),
        scope=scope or {"scope": "all-projects"},
        output_path=output_path,
        executor=selected_executor,
        started_at=started_at,
        dataset_context=dataset_context,
    )
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
        # current-agent is raw agent emission (the LLM's output is not a recorded
        # input), so it is honestly labeled reconstructable=False and carries no
        # isolation tier — no script ran in the shared isolation primitive.
        _write_run_summary(
            run_dir,
            run_record,
            append_summary,
            cursor_advanced=False,
            reconstructable=False,
            sandbox_tier=SANDBOX_TIER_NONE,
        )
        return DatasetRunResult(
            run_id=run_id,
            run_dir=run_dir,
            status="instructions",
            run_record=run_record,
            append_summary=append_summary,
            cursor_advanced=False,
        )

    try:
        # The dataset runner is lifecycle bookkeeping AROUND the single
        # execution seam: hand the resolved package + superset run packet to
        # ``execute_workflow`` (which owns ``_execute_script``, the one
        # subprocess boundary). ``current-agent`` returned above and never
        # reaches here.
        execution = execute_workflow(
            dataset.manifest.workflow.skill,
            scope=run_packet["scope"],
            output_path=output_path,
            executor="script",
            run_dir=run_dir,
            run_packet=run_packet,
            workflow_package=workflow_package,
            strict=strict,
            # The dataset runner already ran the authoritative digest verify
            # above (against the manifest pin, before writing artifacts); do not
            # re-verify inside the seam and double-warn.
            verify_digest=False,
        )
        rows = _read_output_rows(output_path)
        with _dataset_lock(lock_path, run_id):
            append_summary = append_rows(
                name,
                rows,
                run_id=run_id,
                dry_run=dry_run,
                privacy_tier=run_packet.get("privacy_tier"),
                run_provenance={
                    "run_id": run_id,
                    "scheduled": scheduled,
                    "executor": selected_executor,
                    "scope": run_packet["scope"],
                    "limit": limit,
                    # #188 honesty labels: a script run IS reconstructable, and
                    # the achieved isolation tier is what the primitive reported.
                    "reconstructable": execution.reconstructable,
                    "isolation": {"sandbox_tier": execution.sandbox_tier},
                },
                trail_freshness=trail_freshness,
            )
            cursor_advanced = False
            if not dry_run:
                _advance_cursor(dataset.path, dataset.manifest, run_id)
                cursor_advanced = True
    except WorkflowNeedsJudgmentError:
        # Not a failure: the builder asked for judgments (#186). Leave the run
        # packet + sidecar in place and propagate rc=10 to the CLI without
        # marking the run failed.
        raise
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
        _write_run_summary(
            run_dir,
            failed_record,
            empty_summary,
            cursor_advanced=False,
            reconstructable=True,
            sandbox_tier=SANDBOX_TIER_NONE,
        )
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
    _write_run_summary(
        run_dir,
        run_record,
        append_summary,
        cursor_advanced=cursor_advanced,
        reconstructable=execution.reconstructable,
        sandbox_tier=execution.sandbox_tier,
    )
    return DatasetRunResult(
        run_id=run_id,
        run_dir=run_dir,
        status="ok",
        run_record=run_record,
        append_summary=append_summary,
        cursor_advanced=cursor_advanced,
    )


def _workflow_package_for_dataset(dataset) -> WorkflowPackage:
    source = (dataset.manifest.workflow.config or {}).get("source")
    if isinstance(source, str) and source:
        return resolve_workflow_reference(source)
    return load_workflow(dataset.manifest.workflow.skill)


def _trail_freshness_for_dataset(dataset, scope: dict[str, Any]) -> list[dict[str, Any]]:
    project_slugs: set[str] = set()
    project = scope.get("project")
    if isinstance(project, str) and project:
        project_slugs.add(project)
    query = dataset.manifest.candidate_query
    if query is not None:
        query_project = query.args.get("project")
        if isinstance(query_project, str) and query_project:
            project_slugs.add(query_project)
    try:
        from .trace_index import default_index_path, trail_freshness_warnings

        return trail_freshness_warnings(
            index_path=default_index_path(),
            project_slugs=project_slugs or None,
            include_current=True,
        )
    except Exception:
        return []


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
        f"Privacy tier for appended rows: `{run_packet.get('privacy_tier') or 'dataset default'}`.\n\n"
        f"Security tools required by this dataset: "
        f"`{', '.join((run_packet.get('security') or {}).get('required_tools') or []) or 'none'}`; "
        f"enabled: `{', '.join((run_packet.get('security') or {}).get('enabled_tools') or []) or 'none'}`.\n\n"
        f"Trace Trail freshness policy: `{run_packet.get('trail_freshness_policy')}`.\n\n"
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
        finished_at=utc_now_str(),
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
    reconstructable: bool = True,
    sandbox_tier: str = SANDBOX_TIER_NONE,
) -> None:
    validation_payload = {
        "validation_error_count": append_summary.validation_error_count,
        "validation_errors": append_summary.validation_errors,
    }
    summary_payload = {
        "run": run_record.model_dump(mode="json"),
        "append": append_summary.__dict__,
        "cursor_advanced": cursor_advanced,
        # #188 honesty labels (additive): whether this run's rows can be
        # reconstructed by replaying the recorded inputs, and the achieved
        # isolation tier of the script executor.
        "reconstructable": reconstructable,
        "isolation": {"sandbox_tier": sandbox_tier},
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
        "last_successful_run_at": utc_now_str(),
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




# ---------------------------------------------------------------------------
# Dataset-free workflow execution primitive
# ---------------------------------------------------------------------------


_WORKFLOW_SCRIPT_RELPATH = Path("scripts") / "build_rows.py"


@dataclass(frozen=True)
class WorkflowExecutionResult:
    """Outcome of one ``execute_workflow`` invocation.

    ``reconstructable`` / ``sandbox_tier`` are the #188 honesty labels: a
    ``script`` run (including a recorded-answer #186 run) is reconstructable,
    and ``sandbox_tier`` is the isolation the shared primitive actually achieved
    (``none`` / ``jail`` in M1).
    """

    workflow_name: str
    workflow_digest: str
    executor: str
    output_path: Path
    row_count: int
    started_at: str
    finished_at: str
    scope: dict[str, Any]
    reconstructable: bool = True
    sandbox_tier: str = SANDBOX_TIER_NONE


def execute_workflow(
    workflow_name: str,
    *,
    scope: dict[str, Any],
    output_path: Path,
    executor: str = "script",
    extra_env: dict[str, str] | None = None,
    run_dir: Path | None = None,
    run_packet: dict[str, Any] | None = None,
    workflow_package: WorkflowPackage | None = None,
    answers: dict[str, Any] | None = None,
    strict: bool = False,
    verify_digest: bool = True,
) -> WorkflowExecutionResult:
    """Run a workflow's deterministic builder, write rows to ``output_path``.

    This is the SINGLE workflow-execution seam (M1 collapse, #185).
    ``run_dataset_workflow`` wraps it with dataset lifecycle bookkeeping;
    non-dataset consumers (e.g. the branch-context PR consumer) call it
    directly. It loads the workflow package, writes a versioned
    ``run_packet.json`` next to the output, invokes the ONE script executor
    (``_execute_script``), then returns the row count and provenance.

    Parameters
    ----------
    workflow_name:
        A workflow installed under ``~/.opentraces/workflows/<name>``. Use
        ``install_workflow`` (or ``opentraces workflow create --template``)
        first.
    scope:
        Free-form scope dict embedded in ``run_packet.json``. Consumers
        define the shape (e.g. branch_context callers pass ``{"kind":
        "branch_context", "commits": [...]}``).
    output_path:
        Where the workflow's script will write JSONL rows. Parent dir is
        created. Caller chooses the location (e.g. cached path under
        ``.opentraces/branch_runs/<workflow>/<digest>.jsonl``).
    executor:
        Currently only ``"script"`` is supported. The script executor runs
        ``<workflow.path>/scripts/build_rows.py`` as a subprocess with
        ``OT_RUN_PACKET`` and ``OT_DATASET_OUTPUT`` env vars set.
    run_dir, run_packet, workflow_package:
        Lifecycle hooks for the dataset runner. When ``run_dataset_workflow``
        drives this seam it supplies its own ``run_dir`` (the dataset run
        directory), the already-built superset ``run_packet`` (and thus owns
        writing the dataset run artifacts), and the pre-resolved
        ``workflow_package`` (which may resolve a config-pinned source, not
        just an installed name). Direct primitive callers leave these ``None``
        and get the minimal versioned packet written next to the output.
    strict, verify_digest:
        Run-time integrity controls (#187). When ``verify_digest`` and a
        ``run_packet`` with a pinned ``workflow_digest`` are present, the bytes
        about to execute are re-verified against that pin before the subprocess:
        ``strict`` makes a mismatch fatal (:class:`WorkflowIntegrityError`),
        otherwise it warns. The dataset runner sets ``verify_digest=False``
        because it verifies against the manifest pin itself.
    """

    if executor != "script":
        raise ValueError(
            f"unsupported executor: {executor} (execute_workflow only supports 'script')"
        )

    workflow = workflow_package if workflow_package is not None else load_workflow(workflow_name)
    # Run-time integrity re-verify (#187) at the single execution seam: when a
    # caller supplies a run packet with a pinned ``workflow_digest``, the bytes
    # about to run must recompute to it. Fatal under strict BEFORE the
    # subprocess; warn otherwise. The dataset runner verifies against the
    # manifest pin itself and passes ``verify_digest=False`` to avoid a
    # double-warn. Pure primitive callers pass no run packet (nothing pinned).
    if verify_digest and run_packet is not None:
        verify_workflow_digest(
            workflow.digest,
            run_packet.get("workflow_digest"),
            workflow_name=workflow.name,
            strict=strict,
        )
    output_path = output_path.expanduser()
    run_dir = run_dir if run_dir is not None else output_path.parent
    run_dir.mkdir(parents=True, exist_ok=True)

    if run_packet is None:
        started_at = utc_now_str()
        schema_path = workflow.path / "schemas" / "row.schema.json"
        run_packet = _build_workflow_packet(
            workflow_name=workflow.name,
            workflow_digest=workflow.digest,
            workflow_path=str(workflow.path),
            schema_path=str(schema_path) if schema_path.exists() else None,
            scope=scope,
            output_path=output_path,
            executor=executor,
            started_at=started_at,
            answers=answers,
        )
        _write_workflow_packet(run_dir, run_packet)
    else:
        # The dataset runner already built the superset packet and wrote the
        # dataset run artifacts; do not re-author or overwrite them here.
        started_at = str(run_packet.get("started_at") or utc_now_str())

    output_path.write_text("", encoding="utf-8")

    isolation = _execute_script(
        workflow, run_dir, output_path, run_packet, extra_env=extra_env
    )

    rows = _read_output_rows(output_path)
    finished_at = utc_now_str()
    return WorkflowExecutionResult(
        workflow_name=workflow.name,
        workflow_digest=workflow.digest,
        executor=executor,
        output_path=output_path,
        row_count=len(rows),
        started_at=started_at,
        finished_at=finished_at,
        scope=scope,
        # A script / recorded-answer run IS reconstructable; the sandbox_tier is
        # whatever the shared isolation primitive honestly reported (#188).
        reconstructable=True,
        sandbox_tier=isolation.sandbox_tier,
    )


def _build_workflow_packet(
    *,
    workflow_name: str,
    workflow_digest: str,
    workflow_path: str | None,
    schema_path: str | None,
    scope: dict[str, Any],
    output_path: Path,
    executor: str,
    started_at: str,
    dataset_context: dict[str, Any] | None = None,
    answers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the ONE versioned run packet (opentraces.workflow.run_packet.v1).

    The base shape is the primitive packet. ``dataset_context`` (when the
    dataset runner supplies it) merges in the dataset-only keys — a strict
    superset that only ADDS keys, so the envelope stays frozen at ``.v1``.
    ``answers`` (#186) is persisted when present so the builder can finalize its
    rows deterministically and replay is f(workflow, bucket, answers).
    """

    packet: dict[str, Any] = {
        "schema_version": "opentraces.workflow.run_packet.v1",
        "workflow_name": workflow_name,
        "workflow_digest": workflow_digest,
        "workflow_path": workflow_path,
        "schema_path": schema_path,
        "executor": executor,
        "scope": scope,
        "output_path": str(output_path),
        "started_at": started_at,
    }
    if answers:
        packet["answers"] = answers
    if dataset_context:
        packet.update(dataset_context)
    return packet


def _write_workflow_packet(
    run_dir: Path,
    packet: dict[str, Any],
) -> None:
    (run_dir / "run_packet.json").write_text(
        json.dumps(packet, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    # _build_workflow_packet already resolved + existence-checked the schema.
    schema_path = packet.get("schema_path")
    if schema_path:
        (run_dir / "schema_snapshot.json").write_text(
            Path(schema_path).read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _execute_script(
    workflow: WorkflowPackage,
    run_dir: Path,
    output_path: Path,
    run_packet: dict[str, Any],
    extra_env: dict[str, str] | None = None,
) -> IsolationReport:
    """Run a workflow's ``build_rows.py`` through the shared isolation primitive.

    This is the ONE subprocess seam (#185). #188 routes it through
    ``core.isolation.run_isolated``, which is the actual subprocess boundary: the
    child env is built from an ALLOWLIST (parent secrets never inherited), only
    the ``OT_*`` contract vars reach it, and ``$HOME`` is redirected to a
    throwaway dir. BUNDLED (first-party ``source_type=="package"``) templates
    still reach the operator's bucket via an explicit ``OT_OPENTRACES_DIR`` and
    run with network allowed; THIRD-PARTY (``file``) sources get full
    network-deny and NO bucket var. Returns the honest :class:`IsolationReport`.

    #186 handshake: the child is handed an ``OT_JUDGMENT_SIDECAR`` path; when the
    builder needs judgments it writes its JudgmentRequests there and exits
    ``rc=10``. That combination raises :class:`WorkflowNeedsJudgmentError` (a
    handshake, not a failure) rather than :class:`WorkflowScriptError`.
    """
    script = workflow.path / _WORKFLOW_SCRIPT_RELPATH
    if not script.exists():
        raise ExecutorUnavailableError(
            f"workflow '{workflow.name}' has no {_WORKFLOW_SCRIPT_RELPATH} "
            "(script executor requires a deterministic builder)"
        )

    bundled = workflow.source_type == "package"
    sidecar_path = run_dir / "judgment_requests.json"
    # ONLY the OT_* contract vars reach the scrubbed child (plus the isolation
    # primitive's own PATH/LANG/... allowlist). Nothing else — not the parent's
    # secrets, not the parent HOME.
    allow_env: dict[str, str] = {
        "OT_RUN_PACKET": str(run_dir / "run_packet.json"),
        "OT_DATASET_OUTPUT": str(output_path),
        "OT_WORKFLOW_PATH": str(workflow.path),
        OT_JUDGMENT_SIDECAR_ENV: str(sidecar_path),
    }
    if bundled:
        # First-party templates import opentraces and read the bucket; the
        # scrubbed HOME would hide it, so hand over the real bucket root.
        allow_env["OT_OPENTRACES_DIR"] = str(paths.OPENTRACES_DIR)
    if extra_env:
        allow_env.update(extra_env)

    network = "allow" if bundled else "deny"
    source_trust = "trusted" if bundled else "untrusted"

    log_path = run_dir / "log.txt"
    try:
        result = run_isolated(
            [sys.executable, str(script)],
            cwd=str(workflow.path),
            allow_env=allow_env,
            network=network,
            source_trust=source_trust,
        )
    except OSError as exc:
        raise ExecutorUnavailableError(
            f"unable to invoke workflow script {script}: {exc}"
        ) from exc

    log_payload = (
        f"# stdout\n{result.stdout or ''}\n\n"
        f"# stderr\n{result.stderr or ''}\n"
    )
    log_path.write_text(log_payload, encoding="utf-8")

    # #186: rc=10 + a written sidecar is the judgment handshake, not a failure.
    if result.returncode == RC_NEEDS_JUDGMENT and sidecar_path.exists():
        requests = read_sidecar_requests(sidecar_path)
        raise WorkflowNeedsJudgmentError(workflow.name, requests)
    if result.returncode != 0:
        raise WorkflowScriptError(
            f"workflow '{workflow.name}' script exited "
            f"{result.returncode}: see {log_path}"
        )
    return result.achieved
