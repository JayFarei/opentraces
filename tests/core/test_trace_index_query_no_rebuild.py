"""Plan 087 U1 — the query hot path must never trigger a full reindex/refresh.

These pin the steady-state contract:

* ``query_index_page`` reads the warm cache directly; it calls neither
  ``refresh_index`` nor ``rebuild_index`` when the DB already exists.
* ``query_search_projection_page`` never forces ``rebuild_index_first=True``
  through ``build_search_projection``; the missing-projection branch builds
  from the existing index only.

The autouse ``_isolate_opentraces_global_state`` fixture (tests/conftest.py)
redirects ``~/.opentraces`` into a tmp HOME, so these tests are hermetic
without any extra monkeypatching of paths.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

from opentraces.core import search_projection as sp
from opentraces.core import trace_index as ti


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "auth.py").write_text("def login():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    (repo / "auth.py").write_text("def login():\n    return 'auth token'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "patch"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
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
        file_path="auth.py",
        authored_text="    return 'auth token'\n",
        commit_ref=commit_sha,
        writer="test",
        capture_method=["hook_posttooluse"],
    )


def _warm_index_and_projection(tmp_path: Path) -> Path:
    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    _register_project(project, uuid.uuid4().hex)
    _seed_trail(project, commit_sha=commit_sha, trace_id="trace-warm-cache")
    ti.rebuild_index()
    sp.build_search_projection()
    return ti.default_index_path()


def test_query_path_never_full_rebuilds_index_source(tmp_path, monkeypatch):
    """R4 — query_index_page against a warm DB touches no refresh/rebuild."""
    db_path = _warm_index_and_projection(tmp_path)
    assert db_path.exists()

    refresh_calls = {"n": 0}
    rebuild_calls = {"n": 0}
    real_refresh = ti.refresh_index
    real_rebuild = ti.rebuild_index

    def spy_refresh(*args, **kwargs):
        refresh_calls["n"] += 1
        return real_refresh(*args, **kwargs)

    def spy_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        return real_rebuild(*args, **kwargs)

    monkeypatch.setattr(ti, "refresh_index", spy_refresh)
    monkeypatch.setattr(ti, "rebuild_index", spy_rebuild)

    page = ti.query_index_page(lex="auth")

    assert refresh_calls["n"] == 0, (
        f"query_index_page called refresh_index {refresh_calls['n']} times "
        "(U1 expected 0 on the warm-cache path)."
    )
    assert rebuild_calls["n"] == 0, (
        f"query_index_page called rebuild_index {rebuild_calls['n']} times "
        "(U1 expected 0 on the warm-cache path)."
    )
    # The page object should still be returned (read succeeded).
    assert page is not None


def test_query_projection_never_rebuild_index_first(tmp_path, monkeypatch):
    """R4 — query_search_projection_page never forces a full reindex."""
    _warm_index_and_projection(tmp_path)

    calls: list[bool] = []
    rebuild_calls = {"n": 0}
    real_build = sp.build_search_projection
    real_rebuild = ti.rebuild_index

    def spy_build(*args, **kwargs):
        calls.append(bool(kwargs.get("rebuild_index_first", False)))
        return real_build(*args, **kwargs)

    def spy_rebuild(*args, **kwargs):
        rebuild_calls["n"] += 1
        return real_rebuild(*args, **kwargs)

    monkeypatch.setattr(sp, "build_search_projection", spy_build)
    monkeypatch.setattr(ti, "rebuild_index", spy_rebuild)

    sp.query_search_projection_page(semantic="auth")

    assert not any(calls), (
        "build_search_projection was called with rebuild_index_first=True on the "
        f"warm-projection query path (calls={calls!r})."
    )
    assert rebuild_calls["n"] == 0, (
        f"rebuild_index called {rebuild_calls['n']} times from warm-projection query."
    )


def test_query_projection_missing_builds_from_existing_index(tmp_path, monkeypatch):
    """R4 — missing-projection branch builds from the existing index, no reindex."""
    from opentraces.core import paths

    _warm_index_and_projection(tmp_path)

    # Force the missing-projection branch: delete current.json pointer.
    root = paths.search_projection_root(sp.SEARCH_PROJECTION_VERSION)
    current = root / "current.json"
    if current.exists():
        current.unlink()

    calls: list[bool] = []
    real_build = sp.build_search_projection

    def spy_build(*args, **kwargs):
        calls.append(bool(kwargs.get("rebuild_index_first", False)))
        return real_build(*args, **kwargs)

    monkeypatch.setattr(sp, "build_search_projection", spy_build)

    sp.query_search_projection_page(semantic="auth", build_if_missing=True)

    assert calls, "expected build_search_projection to be invoked for missing projection"
    assert not any(calls), (
        "missing-projection rebuild must NOT pass rebuild_index_first=True "
        f"(calls={calls!r}); it should build from the existing index."
    )
