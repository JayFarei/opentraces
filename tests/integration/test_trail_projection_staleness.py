"""Cluster A — trail-projection self-healing & ref-advance staleness tests.

These cover MERGED-A items A1 and A2:

* A1 — when ``trail_sources.ref_sha`` is corrupted (or stale for any reason),
  the next ``refresh_index`` rebuild must repopulate the projection (no
  ``--force-rebuild``); the post-rebuild cache must hold real units.
* A2 — when the trail event log advances *during* the rebuild (because a
  watcher / post-commit hook is appending), we must capture the post-rebuild
  ref and surface a ``trail_event_ref_advanced_during_rebuild`` limitation.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import uuid
from pathlib import Path

import pytest


def _init_repo(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)
    authored_text = "    return 'staleness sentinel'\n"
    (repo / "app.py").write_text("def value():\n" + authored_text)
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "patch"], cwd=repo, check=True)
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True,
    ).strip()


def _register_project(project: Path, project_id: str) -> str:
    """Register the project in the (test-isolated) opentraces config."""
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
        authored_text="    return 'staleness sentinel'\n",
        commit_ref=commit_sha,
        writer="test",
        capture_method=["hook_posttooluse"],
    )


def test_trail_projection_self_heals_after_force_stale(tmp_path):
    """A1 — rebuild after staleness, no ``--force-rebuild`` required."""
    from opentraces.core.trace_index import (
        default_index_path,
        query_index,
        rebuild_index,
        refresh_index,
    )

    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    slug = _register_project(project, uuid.uuid4().hex)
    _seed_trail(project, commit_sha=commit_sha, trace_id="trace-staleness-self-heal")

    rebuild_index()

    # Sanity: patch unit is present after a clean rebuild.
    pre = query_index(facet_filters=("unit.type=patch",))
    assert any(packet.trace_id == "trace-staleness-self-heal" for packet in pre)

    # Corrupt trail_sources to simulate a stale cache. The cached SHA no
    # longer matches the real event-log ref.
    fake_sha = "feedface" * 5  # 40-char hex sentinel
    db_path = default_index_path()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "update trail_sources set ref_sha=? where project_slug=?",
            (fake_sha, slug),
        )
        conn.commit()

    # Trigger a normal refresh path — no --force-rebuild allowed.
    refresh_index()

    # Cache must be self-healed: patch units are still queryable, and the
    # ref_sha must NOT still equal the corrupted sentinel.
    post = query_index(facet_filters=("unit.type=patch",))
    assert any(
        packet.trace_id == "trace-staleness-self-heal" for packet in post
    ), "patch units must reappear after a stale-cache refresh"

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select ref_sha from trail_sources where project_slug=?",
            (slug,),
        ).fetchone()
        assert row is not None, "trail_sources row must be repopulated"
        assert row[0] != fake_sha, "trail_sources ref_sha must have been rewritten"


def test_trail_projection_records_post_rebuild_ref_when_advancing(tmp_path, monkeypatch):
    """A2 — ref advancing during rebuild captures post-rebuild ref + limitation."""
    from opentraces.core import trace_index
    from opentraces.core.trace_index import (
        default_index_path,
        rebuild_index,
        refresh_index,
    )

    project = tmp_path / "demo"
    commit_sha = _init_repo(project)
    slug = _register_project(project, uuid.uuid4().hex)
    _seed_trail(project, commit_sha=commit_sha, trace_id="trace-advance-during-rebuild")

    rebuild_index()

    db_path = default_index_path()
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "select ref_sha from trail_sources where project_slug=?",
            (slug,),
        ).fetchone()
    assert before is not None
    pre_ref_sha = before[0]

    # Simulate ref-advance during the rebuild: monkey-patch
    # ``_build_trail_units`` to side-effect a second TrailEvent batch, which
    # advances ``refs/opentraces/local/events/v1`` between the pre- and
    # post-rebuild ref reads inside ``_rebuild_trail_projection``.
    original_build_trail_units = trace_index._build_trail_units

    def build_then_advance(repo, project_slug):
        units = original_build_trail_units(repo, project_slug)
        # Second TrailEvent batch -> ref advances mid-rebuild.
        _seed_trail(
            repo,
            commit_sha=commit_sha,
            trace_id="trace-advance-extra-row",
        )
        return units

    monkeypatch.setattr(trace_index, "_build_trail_units", build_then_advance)

    # Force the next refresh to *also* see a stale row so that
    # ``_refresh_trail_projection`` triggers ``_rebuild_trail_projection``.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "update trail_sources set ref_sha='deadbeef' where project_slug=?",
            (slug,),
        )
        conn.commit()

    refresh_index()

    # The recorded ref_sha must reflect the post-rebuild head, not the one
    # that was current when we entered ``_rebuild_trail_projection``.
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            select ref_sha, limitations_json
              from trail_sources
             where project_slug=?
            """,
            (slug,),
        ).fetchone()

    assert row is not None
    new_ref, limitations_json = row
    assert new_ref != "deadbeef"
    assert new_ref != pre_ref_sha, (
        "post-rebuild ref must reflect the advance, not the pre-rebuild ref"
    )
    limitations = json.loads(limitations_json or "[]")
    assert "trail_event_ref_advanced_during_rebuild" in limitations
