"""``opentraces bench`` — execute Python-authored claims on disposable boxes."""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from ..core import paths
from ..core.arena.contract import result_exit_code, validate_result
from ..core.arena.origin import (
    OriginJoinError,
    attach_explicit_bench_labels,
    origin_claim_token,
)
from ..core.arena.page import render_evidence_page
from ..core.arena.run_store import RunStore


def _target_path(target: str) -> Path:
    return Path(target.split("::", 1)[0]).expanduser().resolve()


def discover_claim(target: str) -> str:
    """Read one selected test's first docstring paragraph without importing it."""

    path = _target_path(target)
    function_name = target.split("::")[-1] if "::" in target else None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    candidates = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (function_name is None or node.name == function_name)
    ]
    if len(candidates) != 1:
        raise click.UsageError("bench run requires one pytest function target (path.py::test_name)")
    doc = ast.get_docstring(candidates[0], clean=True)
    if not doc or not doc.split("\n\n", 1)[0].strip():
        raise click.UsageError("bench scenario requires a non-empty first docstring paragraph")
    return doc.split("\n\n", 1)[0]


def build_local_wheels(repository: Path) -> list[Path]:
    """Build the code under review, never substitute the published package."""

    dist = repository / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    for pattern in ("opentraces-*.whl", "opentraces_schema-*.whl"):
        for stale in dist.glob(pattern):
            stale.unlink()
    uv = shutil.which("uv")
    if uv is None:
        raise click.ClickException("could not build local install wheels: uv is not installed")
    for source in (repository / "packages" / "opentraces-schema", repository):
        completed = subprocess.run(
            [uv, "build", "--wheel", "--out-dir", str(dist), str(source)],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise click.ClickException(
                "could not build local install wheels:\n" + (completed.stderr or completed.stdout)
            )
    return sorted(dist.glob("*.whl"))


@dataclass(frozen=True)
class PytestOutcome:
    returncode: int
    stdout: str
    stderr: str
    phase_reports: list[dict[str, str]]


def run_pytest(target: str, *, repository: Path, env: dict[str, str]) -> PytestOutcome:
    descriptor, report_name = tempfile.mkstemp(prefix="opentraces-bench-pytest-", suffix=".jsonl")
    os.close(descriptor)
    report_path = Path(report_name)
    child_env = dict(env)
    child_env["OT_BENCH_PYTEST_PHASE_REPORT"] = str(report_path)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-p",
                "opentraces.core.arena.pytest_plugin",
                target,
            ],
            cwd=repository,
            env=child_env,
            text=True,
            capture_output=True,
            check=False,
        )
        phase_reports: list[dict[str, str]] = []
        for line in report_path.read_text(encoding="utf-8").splitlines():
            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(report, dict)
                and report.get("when") in {"setup", "call", "teardown"}
                and report.get("outcome") in {"passed", "failed", "skipped"}
            ):
                phase_reports.append(
                    {
                        "nodeid": str(report.get("nodeid", "")),
                        "when": str(report["when"]),
                        "outcome": str(report["outcome"]),
                    }
                )
    finally:
        report_path.unlink(missing_ok=True)
    return PytestOutcome(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        phase_reports=phase_reports,
    )


def _finalized_ids(store: RunStore) -> set[str]:
    if not store.root.is_dir():
        return set()
    return {
        child.name
        for child in store.root.iterdir()
        if child.is_dir() and (child / "result.json").is_file()
    }


def _pending_ids(store: RunStore) -> set[str]:
    if not store.staging_root.is_dir():
        return set()
    return {
        child.name
        for child in store.staging_root.iterdir()
        if child.is_dir() and (child / ".pending-result.json").is_file()
    }


def _finalize_after_pytest(
    store: RunStore,
    run_id: str,
    outcome: PytestOutcome,
) -> tuple[Path, dict[str, Any]]:
    draft = store.open_pending(run_id)
    result = draft.take_staged_result()
    stdout_ref = "artifacts/pytest/stdout.txt"
    stderr_ref = "artifacts/pytest/stderr.txt"
    phase_ref = "artifacts/pytest/phases.json"
    draft.write_text(stdout_ref, outcome.stdout)
    draft.write_text(stderr_ref, outcome.stderr)
    phase_reports = list(getattr(outcome, "phase_reports", []))
    diagnostic = {
        "kind": "pytest_diagnostics",
        "media_type": "text/plain",
        "returncode": outcome.returncode,
        "stdout_ref": stdout_ref,
        "stderr_ref": stderr_ref,
    }
    if phase_reports:
        draft.write_json(phase_ref, {"reports": phase_reports})
        diagnostic["phase_report_ref"] = phase_ref
    result["artifacts"].append(diagnostic)
    if outcome.returncode != 0 and result["execution_status"] != "error":
        failed_phases = {
            str(report.get("when")) for report in phase_reports if report.get("outcome") == "failed"
        }
        call_observed = any(report.get("when") == "call" for report in phase_reports)
        refs = [stdout_ref, stderr_ref, *([phase_ref] if phase_reports else [])]
        if call_observed and failed_phases and failed_phases <= {"teardown"}:
            result["evidence"]["complete"] = False
            result["evidence"]["requirements"].append(
                {
                    "name": "pytest.cleanup",
                    "complete": False,
                    "evidence_refs": refs,
                }
            )
        else:
            if "call" in failed_phases:
                reason_code = "pytest_call_failed"
                requirement_name = "pytest.call"
            elif "setup" in failed_phases:
                reason_code = "pytest_setup_failed"
                requirement_name = "pytest.setup"
            else:
                reason_code = "pytest_failed"
                requirement_name = "pytest.process"
            result["execution_status"] = "error"
            result["verdict"] = None
            result["reason"] = {
                "code": reason_code,
                "message": f"pytest exited nonzero after scenario adjudication ({outcome.returncode})",
            }
            result["evidence"]["complete"] = False
            result["evidence"]["requirements"].append(
                {
                    "name": requirement_name,
                    "complete": False,
                    "evidence_refs": refs,
                }
            )
    validate_result(result)
    return draft.finalize(result), result


@click.group("bench")
def bench_group() -> None:
    """Run executable product claims and retain their complete private evidence."""


@bench_group.command("run")
@click.argument("target")
@click.option(
    "--store-root",
    type=click.Path(path_type=Path),
    help="Override bucket/runs/v1 (primarily for isolated execution).",
)
@click.option(
    "--origin",
    "origin_address",
    help="Attach verified labels to an existing trace, point, or span address.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit only a machine-readable summary.")
def bench_run(
    target: str,
    store_root: Path | None,
    origin_address: str | None,
    as_json: bool,
) -> None:
    """Run one pytest scenario target (PATH::TEST) on a disposable box."""

    canonical_store_root = paths.bucket_dir() / "runs" / "v1"
    if (
        origin_address is not None
        and store_root is not None
        and store_root.resolve() != canonical_store_root.resolve()
    ):
        raise click.UsageError(
            "--origin requires the default run store; "
            "omit --store-root or use the canonical run store"
        )
    repository_text = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    repository = (
        Path(repository_text.stdout.strip()).resolve()
        if repository_text.returncode == 0
        else Path.cwd().resolve()
    )
    claim = discover_claim(target)
    build_local_wheels(repository)
    store = RunStore(store_root or canonical_store_root)
    before = _pending_ids(store) | _finalized_ids(store)
    env = dict(os.environ)
    env["OT_BENCH_RUN_ROOT"] = str(store.root)
    env["OT_BENCH_REPOSITORY"] = str(repository)
    env["OT_BENCH_SCENARIOS"] = "1"
    env["OT_BENCH_REAL_HOME"] = str(Path.home())
    env["OT_BENCH_DEFER_FINALIZE"] = "1"
    if origin_address is not None:
        env["OT_BENCH_ORIGIN_ADDRESS"] = origin_address
    pytest_outcome = run_pytest(target, repository=repository, env=env)
    created = sorted(_pending_ids(store) - before)
    if not created:
        raise click.ClickException(
            "scenario produced no pending run "
            f"(pytest exit {pytest_outcome.returncode}); child output was captured"
        )
    if len(created) != 1:
        raise click.ClickException(f"expected one finalized run, observed {len(created)}")
    try:
        run_path, result = _finalize_after_pytest(store, created[0], pytest_outcome)
    except OriginJoinError as exc:
        raise click.ClickException(str(exc)) from exc
    exit_code = result_exit_code(result)
    if origin_address is not None:
        try:
            attach_explicit_bench_labels(
                run_path,
                address=origin_address,
                store=store,
            )
        except OriginJoinError as exc:
            raise click.ClickException(str(exc)) from exc
    page_path: Path | None = None
    page_error: str | None = None
    try:
        page_path = render_evidence_page(run_path)
    except Exception as exc:
        page_error = f"{type(exc).__name__}: {exc}"
    summary = {
        "status": "ok" if exit_code == 0 else "failed",
        "claim": claim,
        "verdict": result["verdict"],
        "execution_status": result["execution_status"],
        "run_id": result["run_id"],
        "run_path": str(run_path),
        "result_ref": str(run_path / "result.json"),
        "page": str(page_path) if page_path is not None else None,
        "page_error": page_error,
    }
    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"bench_run_{result['run_id']} {result['verdict'] or 'error'} "
            f"{origin_claim_token(claim)}"
        )
        click.echo(f"claim: {claim}")
        click.echo(f"verdict: {result['verdict'] or 'error'}")
        click.echo(f"run: {run_path}")
        click.echo(f"result: {run_path / 'result.json'}")
        click.echo(f"page: {page_path if page_path is not None else 'unavailable'}")
        if page_error:
            click.echo(f"page render warning: {page_error}", err=True)
    if exit_code:
        raise click.exceptions.Exit(exit_code)
