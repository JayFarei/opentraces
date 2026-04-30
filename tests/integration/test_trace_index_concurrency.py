"""Cluster A — concurrent ``trace query`` must not surface raw SQLite errors.

Covers MERGED-A item A3:

* Two concurrent ``trace query`` invocations both succeed (exit 0) and
  neither prints ``sqlite3.OperationalError`` to stderr.
* The on-disk index DB uses WAL journal mode so concurrent readers and a
  refresh writer do not block each other.

The tests spawn real subprocesses that hit the live ``otd`` shim against a
private ``$HOME`` so they do not touch the developer's real
``~/.opentraces`` state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import uuid
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
OTD_SHIM = REPO_ROOT / "otd"


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "a@b.c"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    authored_text = "    return 'concurrent canary'\n"
    (repo / "app.py").write_text("def value():\n" + authored_text)
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "patch"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def _register_project(project: Path, project_id: str) -> str:
    from opentraces.core.config import (
        Config,
        ProjectRegistration,
        _make_slug,
        get_project_traces_dir,
        save_config,
    )

    project.mkdir(parents=True, exist_ok=True)
    (project / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": project_id})
    )
    get_project_traces_dir(project).mkdir(parents=True, exist_ok=True)
    slug = _make_slug(project.name, project_id)
    save_config(
        Config(
            projects={
                str(project.resolve()): ProjectRegistration(
                    project_id=project_id,
                    slug=slug,
                )
            }
        )
    )
    return slug


def _seed_trail(project: Path, *, commit_sha: str, trace_id: str) -> None:
    from opentraces.core.trails import append_exact_patch_trail

    append_exact_patch_trail(
        project,
        trace_id=trace_id,
        step_index=3,
        file_path="app.py",
        authored_text="    return 'concurrent canary'\n",
        commit_ref=commit_sha,
        writer="test",
        capture_method=["hook_posttooluse"],
    )


def test_concurrent_trace_query_no_traceback(tmp_path):
    """A3 — two concurrent ``trace query`` runs both succeed quietly."""
    from opentraces.core.trace_index import default_index_path, rebuild_index

    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    _register_project(project, uuid.uuid4().hex)
    _seed_trail(project, commit_sha=commit_sha, trace_id="trace-concurrent")

    # Build the index once so query subprocesses do not race the initial
    # rebuild.
    rebuild_index()
    assert default_index_path().exists()

    home = os.environ["HOME"]
    env = os.environ.copy()
    env["HOME"] = home  # honour the autouse isolation fixture's HOME

    cmd_a = [
        str(OTD_SHIM),
        "trace",
        "query",
        "--since",
        "12h",
        "--candidate-kind",
        "git_anchor",
        "--limit",
        "5",
        "--json",
    ]
    cmd_b = [
        str(OTD_SHIM),
        "trace",
        "query",
        "--since",
        "12h",
        "--candidate-kind",
        "patch",
        "--limit",
        "5",
        "--json",
    ]

    proc_a = subprocess.Popen(
        cmd_a, cwd=project, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    proc_b = subprocess.Popen(
        cmd_b, cwd=project, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    out_a, err_a = proc_a.communicate(timeout=60)
    out_b, err_b = proc_b.communicate(timeout=60)

    assert proc_a.returncode == 0, (
        f"first query exited {proc_a.returncode}: {err_a!r}"
    )
    assert proc_b.returncode == 0, (
        f"second query exited {proc_b.returncode}: {err_b!r}"
    )
    assert "sqlite3.OperationalError" not in err_a, err_a
    assert "sqlite3.OperationalError" not in err_b, err_b
    assert "Traceback" not in err_a, err_a
    assert "Traceback" not in err_b, err_b
    # Both subprocesses produced parseable JSON (the --json flag is honoured).
    json.loads(out_a)
    json.loads(out_b)


def test_index_uses_wal_journal_mode(tmp_path):
    """A3 — the index DB is WAL-mode so concurrent readers do not lock."""
    from opentraces.core.trace_index import default_index_path, rebuild_index

    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    _register_project(project, uuid.uuid4().hex)
    _seed_trail(project, commit_sha=commit_sha, trace_id="trace-wal-mode")

    rebuild_index()

    db_path = default_index_path()
    with sqlite3.connect(db_path) as conn:
        mode = conn.execute("pragma journal_mode").fetchone()[0]
    assert mode.lower() == "wal", f"index DB journal mode must be WAL, got {mode!r}"
