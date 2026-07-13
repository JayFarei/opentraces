"""``opentraces bench`` — execute Python-authored claims on disposable boxes."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from ..core import paths
from ..core.arena.contract import result_exit_code
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
        raise click.UsageError(
            "bench run requires one pytest function target (path.py::test_name)"
        )
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
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--wheel-dir",
            str(dist),
            str(repository / "packages" / "opentraces-schema"),
            str(repository),
        ],
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


def run_pytest(target: str, *, repository: Path, env: dict[str, str]) -> int:
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "opentraces.core.arena.pytest_plugin", target],
        cwd=repository,
        env=env,
        check=False,
    )
    return completed.returncode


def _finalized_ids(store: RunStore) -> set[str]:
    if not store.root.is_dir():
        return set()
    return {
        child.name
        for child in store.root.iterdir()
        if child.is_dir() and (child / "result.json").is_file()
    }


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
@click.option("--json", "as_json", is_flag=True, help="Emit only a machine-readable summary.")
def bench_run(target: str, store_root: Path | None, as_json: bool) -> None:
    """Run one pytest scenario target (PATH::TEST) on a disposable box."""

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
    store = RunStore(store_root or paths.bucket_dir() / "runs" / "v1")
    before = _finalized_ids(store)
    env = dict(os.environ)
    env["OT_BENCH_RUN_ROOT"] = str(store.root)
    env["OT_BENCH_REPOSITORY"] = str(repository)
    pytest_rc = run_pytest(target, repository=repository, env=env)
    created = sorted(_finalized_ids(store) - before)
    if not created:
        raise click.ClickException(
            f"scenario produced no finalized run (pytest exit {pytest_rc}); raw pytest output is above"
        )
    if len(created) != 1:
        raise click.ClickException(f"expected one finalized run, observed {len(created)}")
    run_path = store.root / created[0]
    result: dict[str, Any] = json.loads((run_path / "result.json").read_text(encoding="utf-8"))
    exit_code = result_exit_code(result)
    summary = {
        "status": "ok" if exit_code == 0 else "failed",
        "claim": claim,
        "verdict": result["verdict"],
        "execution_status": result["execution_status"],
        "run_id": result["run_id"],
        "run_path": str(run_path),
        "result_ref": str(run_path / "result.json"),
    }
    if as_json:
        click.echo(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    else:
        click.echo(f"claim: {claim}")
        click.echo(f"verdict: {result['verdict'] or 'error'}")
        click.echo(f"run: {run_path}")
        click.echo(f"result: {run_path / 'result.json'}")
    if exit_code:
        raise click.exceptions.Exit(exit_code)
