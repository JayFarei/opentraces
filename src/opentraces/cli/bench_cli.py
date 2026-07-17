"""``opentraces bench`` — execute Python-authored claims on disposable boxes."""

from __future__ import annotations

import ast
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

import click

from ..core import paths
from ..core.arena.atlas import (
    AtlasIntegrityError,
    build_atlas,
    cross_check_atlas,
    guarantees_source_digest,
)
from ..core.arena.atlas_page import render_atlas_page
from ..core.arena.atlas_views import (
    build_agent_summary,
    format_pr_evidence_link,
    query_atlas,
)
from ..core.arena.contract import result_exit_code, validate_result
from ..core.arena.fleet import (
    LOCAL_CONTAINER,
    RecipeInputs,
    collect_selected_nodeids,
    execute_fleet,
)
from ..core.arena.origin import (
    OriginJoinError,
    attach_explicit_bench_labels,
    origin_claim_token,
)
from ..core.arena.page import render_evidence_page
from ..core.arena.retrieval import (
    StoredVerifierMismatch,
    list_stored_runs,
    rerender_stored_run,
    reverify_stored_run,
    verified_stored_result,
)
from ..core.arena.run_store import RunIntegrityError, RunStore


def _playwright_browser_cache(home: Path, *, platform: str | None = None) -> Path:
    """Resolve Playwright's host cache before pytest redirects ``HOME``."""

    selected = platform or sys.platform
    if selected == "darwin":
        return home / "Library" / "Caches" / "ms-playwright"
    if selected == "win32":
        return home / "AppData" / "Local" / "ms-playwright"
    return home / ".cache" / "ms-playwright"


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
    run_ids: tuple[str, ...] = ()


def run_pytest(target: str, *, repository: Path, env: dict[str, str]) -> PytestOutcome:
    descriptor, report_name = tempfile.mkstemp(prefix="opentraces-bench-pytest-", suffix=".jsonl")
    os.close(descriptor)
    report_path = Path(report_name)
    run_descriptor, run_report_name = tempfile.mkstemp(
        prefix="opentraces-bench-runs-", suffix=".jsonl"
    )
    os.close(run_descriptor)
    run_report_path = Path(run_report_name)
    child_env = dict(env)
    child_env["OT_BENCH_PYTEST_PHASE_REPORT"] = str(report_path)
    child_env["OT_BENCH_PENDING_RUN_REPORT"] = str(run_report_path)
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
        run_ids: list[str] = []
        for line in run_report_path.read_text(encoding="utf-8").splitlines():
            try:
                report = json.loads(line)
            except json.JSONDecodeError:
                continue
            run_id = report.get("run_id") if isinstance(report, dict) else None
            if isinstance(run_id, str) and run_id.startswith("run_"):
                run_ids.append(run_id)
    finally:
        report_path.unlink(missing_ok=True)
        run_report_path.unlink(missing_ok=True)
    return PytestOutcome(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        phase_reports=phase_reports,
        run_ids=tuple(dict.fromkeys(run_ids)),
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


def _repository_root() -> Path:
    observed = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    return (
        Path(observed.stdout.strip()).resolve()
        if observed.returncode == 0
        else Path.cwd().resolve()
    )


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


def _run_store(store_root: Path | None) -> RunStore:
    return RunStore(store_root or paths.bucket_dir() / "runs" / "v1")


def _load_exact_callable(name: str) -> Callable[..., object]:
    """Resolve one explicit dotted callable without maintaining a registry."""

    if not name or "<locals>" in name:
        raise click.ClickException("verifier name must identify an importable exact callable")
    parts = name.split(".")
    module = None
    attributes: list[str] = []
    for boundary in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:boundary])
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name and not module_name.startswith(f"{exc.name}."):
                raise click.ClickException(f"could not import verifier {name!r}: {exc}") from exc
            continue
        except Exception as exc:
            raise click.ClickException(f"could not import verifier {name!r}: {exc}") from exc
        attributes = parts[boundary:]
        break
    if module is None:
        raise click.ClickException(f"could not import verifier {name!r}")
    target: object = module
    try:
        for attribute in attributes:
            target = getattr(target, attribute)
    except AttributeError as exc:
        raise click.ClickException(f"could not resolve verifier {name!r}") from exc
    if not callable(target):
        raise click.ClickException(f"verifier {name!r} is not callable")
    return target


@bench_group.command("list")
@click.option(
    "--store-root",
    type=click.Path(path_type=Path),
    help="Override bucket/runs/v1 (primarily for isolated retrieval).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit only machine-readable runs.")
def bench_list(store_root: Path | None, as_json: bool) -> None:
    """List verified finalized runs; omit staging and recovery attempts."""

    try:
        records = [asdict(record) for record in list_stored_runs(_run_store(store_root))]
    except (RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps({"count": len(records), "runs": records}, sort_keys=True))
        return
    for record in records:
        click.echo(
            f"{record['run_id']} {record['verdict'] or 'error'} "
            f"{record['execution_status']} {record['claim']}"
        )


@bench_group.command("render")
@click.argument("run_id")
@click.option(
    "--store-root",
    type=click.Path(path_type=Path),
    help="Override bucket/runs/v1 (primarily for isolated retrieval).",
)
@click.option("--output", type=click.Path(path_type=Path), help="Write the regenerated page here.")
@click.option("--json", "as_json", is_flag=True, help="Emit only a machine-readable summary.")
def bench_render(
    run_id: str,
    store_root: Path | None,
    output: Path | None,
    as_json: bool,
) -> None:
    """Re-render a page from one verified finalized run without execution."""

    try:
        page = rerender_stored_run(_run_store(store_root), run_id, output_path=output)
    except (RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = {"status": "ok", "run_id": run_id, "page": str(page)}
    if as_json:
        click.echo(json.dumps(summary, sort_keys=True))
    else:
        click.echo(f"rendered {run_id}: {page}")


@bench_group.command("reverify")
@click.argument("run_id")
@click.option(
    "--store-root",
    type=click.Path(path_type=Path),
    help="Override bucket/runs/v1 (primarily for isolated retrieval).",
)
@click.option("--verifier-name", required=True, help="Exact importable verifier callable name.")
@click.option(
    "--verifier-digest", required=True, help="Exact sha256 source digest bound to the run."
)
@click.option("--json", "as_json", is_flag=True, help="Emit only the reverification envelope.")
def bench_reverify(
    run_id: str,
    store_root: Path | None,
    verifier_name: str,
    verifier_digest: str,
    as_json: bool,
) -> None:
    """Re-run one exact verifier over stored evidence, with no box or scenario."""

    store = _run_store(store_root)
    try:
        verified_stored_result(store, run_id)
    except (RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    verifier = _load_exact_callable(verifier_name)
    try:
        result = reverify_stored_run(
            store,
            run_id,
            verifier_name=verifier_name,
            verifier_digest=verifier_digest,
            verifier=verifier,
        )
    except (RunIntegrityError, StoredVerifierMismatch, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(result, sort_keys=True))
    else:
        click.echo(f"{run_id} {result['status']} {verifier_name} {verifier_digest}")
    if result["status"] == "fail":
        raise click.exceptions.Exit(1)
    if result["status"] == "error":
        raise click.exceptions.Exit(2)


def _load_json_object(path: Path, *, name: str) -> Mapping[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"could not read {name}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise click.ClickException(f"{name} must contain a JSON object")
    return payload


def _load_guarantees_source(
    path: Path,
) -> tuple[list[Mapping[str, Any]], str]:
    try:
        source = Path(path).read_bytes()
        payload = json.loads(source.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"could not read guarantees input: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise click.ClickException("guarantees input must contain a JSON object")
    guarantees = payload.get("guarantees")
    if not isinstance(guarantees, list) or any(
        not isinstance(guarantee, Mapping) for guarantee in guarantees
    ):
        raise click.ClickException("guarantees input must contain a guarantees object array")
    return guarantees, guarantees_source_digest(source)


def _verified_results(
    store: RunStore,
) -> tuple[list[Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    """Read result bytes only after their run has passed external-index verification."""

    records = list_stored_runs(store)
    results: list[Mapping[str, Any]] = []
    storage_integrity: dict[str, Mapping[str, Any]] = {}
    for record in records:
        run_path = store.root / record.run_id
        store.verify(run_path)
        payload = json.loads((run_path / "result.json").read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise RunIntegrityError(f"stored run {record.run_id} result is not an object")
        validate_result(payload)
        storage_integrity[record.run_id] = store.verified_integrity(run_path)
        results.append(payload)
    return results, storage_integrity


def _verified_atlas(
    atlas_path: Path,
    *,
    guarantees_path: Path,
    store_root: Path | None,
) -> Mapping[str, Any]:
    atlas = _load_json_object(atlas_path, name="atlas")
    guarantees, guarantees_digest = _load_guarantees_source(guarantees_path)
    results, storage_integrity = _verified_results(_run_store(store_root))
    cross_check_atlas(
        atlas,
        guarantees=guarantees,
        guarantees_digest=guarantees_digest,
        results=results,
        storage_integrity_by_run_id=storage_integrity,
    )
    return atlas


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return destination


@bench_group.group("atlas")
def bench_atlas() -> None:
    """Build and consume honest projections of stored fleet evidence."""


@bench_atlas.command("build")
@click.argument("guarantees_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--store-root",
    type=click.Path(path_type=Path),
    help="Override bucket/runs/v1 (primarily for isolated retrieval).",
)
@click.option("--product-commit", required=True, help="Exact product commit the atlas describes.")
@click.option(
    "--capabilities-digest",
    required=True,
    help="Exact opentraces.capabilities.v0 manifest digest.",
)
@click.option("--output", required=True, type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit only a machine-readable summary.")
def bench_atlas_build(
    guarantees_path: Path,
    store_root: Path | None,
    product_commit: str,
    capabilities_digest: str,
    output: Path,
    as_json: bool,
) -> None:
    """Generate and cross-check an atlas from guarantees and verified results."""

    try:
        guarantees, guarantees_digest = _load_guarantees_source(guarantees_path)
        results, storage_integrity = _verified_results(_run_store(store_root))
        atlas = build_atlas(
            guarantees=guarantees,
            guarantees_digest=guarantees_digest,
            results=results,
            storage_integrity_by_run_id=storage_integrity,
            product_commit=product_commit,
            capabilities_digest=capabilities_digest,
        )
        checked = cross_check_atlas(
            atlas,
            guarantees=guarantees,
            guarantees_digest=guarantees_digest,
            results=results,
            storage_integrity_by_run_id=storage_integrity,
        )
        destination = _write_json(output, atlas)
    except (AtlasIntegrityError, RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = {
        "status": "ok",
        "cross_check": checked,
        "row_count": len(atlas["rows"]),
        "output": str(destination),
    }
    if as_json:
        click.echo(json.dumps(summary, sort_keys=True))
    else:
        click.echo(f"atlas cross-check: ok ({summary['row_count']} rows) -> {destination}")


@bench_atlas.command("render")
@click.argument("atlas_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--guarantees",
    "guarantees_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--store-root", type=click.Path(path_type=Path))
@click.option("--output", required=True, type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit only a machine-readable summary.")
def bench_atlas_render(
    atlas_path: Path,
    guarantees_path: Path,
    store_root: Path | None,
    output: Path,
    as_json: bool,
) -> None:
    """Render the deterministic human atlas page."""

    try:
        atlas = _verified_atlas(
            atlas_path,
            guarantees_path=guarantees_path,
            store_root=store_root,
        )
        destination = render_atlas_page(atlas, output)
    except (AtlasIntegrityError, RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    summary = {"status": "ok", "output": str(destination)}
    if as_json:
        click.echo(json.dumps(summary, sort_keys=True))
    else:
        click.echo(f"atlas page: {destination}")


@bench_atlas.command("summary")
@click.argument("atlas_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--guarantees",
    "guarantees_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--store-root", type=click.Path(path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit only the agent summary envelope.")
def bench_atlas_summary(
    atlas_path: Path,
    guarantees_path: Path,
    store_root: Path | None,
    as_json: bool,
) -> None:
    """Emit the compact latest-failure and named-hole view for agents."""

    try:
        summary = build_agent_summary(
            _verified_atlas(
                atlas_path,
                guarantees_path=guarantees_path,
                store_root=store_root,
            )
        )
    except (AtlasIntegrityError, RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(summary, sort_keys=True))
        return
    click.echo(json.dumps(summary, ensure_ascii=False, sort_keys=True))


@bench_atlas.command("query")
@click.argument("atlas_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--guarantees",
    "guarantees_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--store-root", type=click.Path(path_type=Path))
@click.option("--state", "states", multiple=True, help="Match an exact atlas state (repeatable).")
@click.option(
    "--id", "guarantee_ids", multiple=True, help="Match an exact guarantee id (repeatable)."
)
@click.option("--json", "as_json", is_flag=True, help="Emit only matching rows.")
def bench_atlas_query(
    atlas_path: Path,
    guarantees_path: Path,
    store_root: Path | None,
    states: tuple[str, ...],
    guarantee_ids: tuple[str, ...],
    as_json: bool,
) -> None:
    """Query atlas rows by exact state or exact guarantee id."""

    try:
        rows = query_atlas(
            _verified_atlas(
                atlas_path,
                guarantees_path=guarantees_path,
                store_root=store_root,
            ),
            states=states or None,
            guarantee_ids=guarantee_ids or None,
        )
    except (AtlasIntegrityError, RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {"count": len(rows), "rows": rows}
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
        return
    for row in rows:
        click.echo(
            f"{row.get('id')} {row.get('state')} "
            f"{row.get('latest_run_id') or '-'} {row.get('evidence_ref') or '-'}"
        )


@bench_atlas.command("pr-link")
@click.argument("atlas_path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("guarantee_id")
@click.option(
    "--guarantees",
    "guarantees_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--store-root", type=click.Path(path_type=Path))
@click.option("--page-url", required=True, help="Published human atlas page URL.")
@click.option("--json", "as_json", is_flag=True, help="Emit only the evidence-link projection.")
def bench_atlas_pr_link(
    atlas_path: Path,
    guarantee_id: str,
    guarantees_path: Path,
    store_root: Path | None,
    page_url: str,
    as_json: bool,
) -> None:
    """Format one bound atlas row as stable PR evidence."""

    try:
        atlas = _verified_atlas(
            atlas_path,
            guarantees_path=guarantees_path,
            store_root=store_root,
        )
        rows = query_atlas(atlas, guarantee_ids=(guarantee_id,))
        if len(rows) != 1:
            raise AtlasIntegrityError(f"atlas has no unique row {guarantee_id!r}")
        row = rows[0]
        link = format_pr_evidence_link(row, page_url=page_url)
    except (AtlasIntegrityError, RunIntegrityError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    payload = {
        "id": row.get("id"),
        "run_id": row.get("latest_run_id"),
        "evidence_ref": row.get("evidence_ref"),
        "link": link,
    }
    if as_json:
        click.echo(json.dumps(payload, sort_keys=True))
    else:
        click.echo(link)


@bench_group.command("run")
@click.argument("target")
@click.option(
    "--store-root",
    type=click.Path(path_type=Path),
    help=(
        "Override bucket/runs/v1 (primarily for isolated execution). "
        "May diverge from the canonical store only when --origin is omitted."
    ),
)
@click.option(
    "--origin",
    "origin_address",
    help=(
        "Attach verified labels to an existing trace, point, or span address. "
        "Requires the default/canonical run store (do not pass a divergent --store-root)."
    ),
)
@click.option("--json", "as_json", is_flag=True, help="Emit only a machine-readable summary.")
def bench_run(
    target: str,
    store_root: Path | None,
    origin_address: str | None,
    as_json: bool,
) -> None:
    """Run one pytest scenario target (PATH::TEST) on a disposable box.

    Store constraint (honesty contract, PR #331 / #332): ``--origin`` attaches
    durable verified labels to a trace address, so it requires the
    default/canonical run store (``bucket/runs/v1``). ``--store-root`` may
    diverge from that canonical store only when ``--origin`` is omitted;
    combining ``--origin`` with a genuinely divergent ``--store-root`` is
    rejected fail-closed (Click exit 2, before any side effect). Rationale: a
    raw machine-local store path is not a portable durable label address, so
    labels minted into a divergent store could not round-trip through ordinary
    trace reads.
    """

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
    repository = _repository_root()
    claim = discover_claim(target)
    build_local_wheels(repository)
    store = RunStore(store_root or canonical_store_root)
    before = _pending_ids(store) | _finalized_ids(store)
    env = dict(os.environ)
    env["OT_BENCH_RUN_ROOT"] = str(store.root)
    env["OT_BENCH_REPOSITORY"] = str(repository)
    env["OT_BENCH_SCENARIOS"] = "1"
    runtime_home = Path.home()
    env["OT_BENCH_REAL_HOME"] = str(runtime_home)
    env.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_playwright_browser_cache(runtime_home)))
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


@bench_group.command("fleet")
@click.argument("targets", nargs=-1, required=True)
@click.option("--marker", help="Use pytest's marker expression for selection.")
@click.option("--concurrency", type=click.IntRange(min=1), default=1, show_default=True)
@click.option(
    "--placement",
    type=click.Choice([LOCAL_CONTAINER.name]),
    default=LOCAL_CONTAINER.name,
    show_default=True,
)
@click.option(
    "--store-root",
    type=click.Path(path_type=Path),
    help="Override bucket/runs/v1 (primarily for isolated execution).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit only a machine-readable summary.")
def bench_fleet(
    targets: tuple[str, ...],
    marker: str | None,
    concurrency: int,
    placement: str,
    store_root: Path | None,
    as_json: bool,
) -> None:
    """Run a pytest-selected scenario set concurrently on isolated boxes."""

    repository = _repository_root()
    selected = collect_selected_nodeids(
        repository=repository,
        targets=targets,
        marker=marker,
    )
    wheels = build_local_wheels(repository)
    store = RunStore(store_root or paths.bucket_dir() / "runs" / "v1")
    base_env = dict(os.environ)
    base_env.update(
        {
            "OT_BENCH_RUN_ROOT": str(store.root),
            "OT_BENCH_REPOSITORY": str(repository),
            "OT_BENCH_SCENARIOS": "1",
            "OT_BENCH_REAL_HOME": str(Path.home()),
            "OT_BENCH_DEFER_FINALIZE": "1",
        }
    )
    base_env.setdefault(
        "PLAYWRIGHT_BROWSERS_PATH",
        str(_playwright_browser_cache(Path.home())),
    )

    def run_attempt(nodeid: str, recipe: RecipeInputs) -> Path:
        with tempfile.TemporaryDirectory(prefix="opentraces-bench-recipe-") as temporary:
            recipe_root = Path(temporary) / "inputs"
            recipe.materialize(recipe_root)
            env = dict(base_env)
            env["OT_BENCH_RECIPE_ROOT"] = str(recipe_root)
            outcome = run_pytest(nodeid, repository=repository, env=env)
            run_ids = tuple(getattr(outcome, "run_ids", ()))
            if len(run_ids) != 1:
                raise click.ClickException(
                    f"scenario {nodeid} produced {len(run_ids)} pending runs; expected one"
                )
            run_path, _result = _finalize_after_pytest(store, run_ids[0], outcome)
            return run_path

    fleet = execute_fleet(
        selected,
        store=store,
        concurrency=concurrency,
        placement=placement,
        prepare_recipe=lambda: RecipeInputs.capture(wheels),
        run_attempt=run_attempt,
    )
    attempts = [
        {
            "nodeid": attempt.nodeid,
            "run_id": attempt.run_id,
            "run_ref": f"runs/v1/{attempt.run_id}",
            "verdict": attempt.verdict,
            "execution_status": attempt.execution_status,
            "provider": attempt.provider,
        }
        for attempt in fleet.attempts
    ]
    summary = {
        "status": (
            "error"
            if any(row["execution_status"] != "complete" for row in attempts)
            else "failed"
            if any(row["verdict"] == "fail" for row in attempts)
            else "ok"
        ),
        "placement": fleet.placement.name,
        "concurrency": concurrency,
        "observed_max_lease_concurrency": fleet.observed_max_lease_concurrency,
        "recipe_digest": fleet.recipe.digest,
        "attempts": attempts,
        "coverage_holes": [
            {"code": hole.code, "message": hole.message} for hole in fleet.coverage_holes
        ],
    }
    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(
            f"bench fleet: {len(attempts)} run(s), {summary['status']}, "
            f"placement={fleet.placement.name}"
        )
        for row in attempts:
            click.echo(f"{row['nodeid']} {row['verdict'] or 'error'} {row['run_ref']}")
    if summary["status"] == "error":
        raise click.exceptions.Exit(2)
    if summary["status"] == "failed":
        raise click.exceptions.Exit(1)
