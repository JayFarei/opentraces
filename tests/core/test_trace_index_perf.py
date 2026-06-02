"""Cluster G — P1: refresh-time performance contracts on Trace Index.

The previous behavior of ``_build_trail_units`` called ``sync_patch`` per
anchor (via ``TrailQueryProjection.with_current_survival``). On a real
project with 315 anchors that turned a ~6 second refresh into a 10+
minute one. P1 defers survival enrichment to query time so refresh stays
O(units), not O(units × git ops).

These tests pin two contracts:

1. ``_build_trail_units`` makes zero ``sync_patch`` calls.
2. A 24-trace synthetic projection over 312 anchors completes quickly.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from unittest.mock import patch

from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
)
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True
    )


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _seed_patch_anchor(
    repo: Path,
    *,
    trace_id: str,
    patch_id: str,
    anchor_commit: str,
    file_path: str,
    text: str,
) -> None:
    append_event_batch(
        repo,
        _patch_anchor_drafts(
            trace_id=trace_id,
            patch_id=patch_id,
            anchor_commit=anchor_commit,
            file_path=file_path,
            text=text,
        ),
        writer="test-fixture",
    )


def _patch_anchor_drafts(
    *,
    trace_id: str,
    patch_id: str,
    anchor_commit: str,
    file_path: str,
    text: str,
) -> list[TrailEventDraft]:
    return [
        TrailEventDraft(
            event_type="trace_patch_created",
            trace_id=trace_id,
            step_index=1,
            capture_method=["hook_posttooluse"],
            payload={
                "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                "file_path": file_path,
                "affected_range": {"start_line": 1, "end_line": 2},
                "authored_text": text,
                "raw_authored_hash": sha256_text(text),
                "git_clean_hash": sha256_text(text.strip()),
                "limitations": [],
            },
        ),
        TrailEventDraft(
            event_type="git_anchor_created",
            trace_id=trace_id,
            step_index=1,
            capture_method=["manual_attach"],
            payload={
                "git_anchor_id": f"gitanchor-sha256:{patch_id}",
                "trace_patch_id": f"tracepatch-sha256:{patch_id}",
                "commit_id": {"algo": "sha1", "hex": anchor_commit},
                "path": file_path,
                "range": {"start_line": 1, "end_line": 2},
                "relation": "anchored_in_git",
                "evidence_tier": "exact_range_hash",
                "evidence_firmness": "firm",
                "limitations": [],
            },
        ),
    ]


def test_build_trail_units_does_not_call_sync_patch(tmp_path: Path) -> None:
    """``_build_trail_units`` must not call ``sync_patch`` per anchor.

    P1 contract: refresh becomes O(units), not O(units × git ops). We
    patch ``sync_patch`` to count invocations and assert zero calls
    during the trail-units build.
    """
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("alpha\nline\n")
    head = _commit(tmp_path, "seed")
    _seed_patch_anchor(
        tmp_path,
        trace_id="t-1",
        patch_id="abc12345",
        anchor_commit=head,
        file_path="a.py",
        text="alpha\nline\n",
    )

    from opentraces.core.trace_index import _build_trail_units

    with patch(
        "opentraces.core.trails.sync_patch",
        autospec=True,
    ) as sync_mock:
        units = _build_trail_units(tmp_path, project_slug="t-proj")

    assert sync_mock.call_count == 0, (
        f"_build_trail_units called sync_patch {sync_mock.call_count} times "
        "(P1 expected 0)."
    )
    # We should still produce git_anchor units — just without survival
    # enrichment baked in.
    git_anchor_units = [u for u in units if u.unit_type == "git_anchor"]
    assert git_anchor_units, "expected at least one git_anchor unit from build"
    # Each git_anchor unit should still carry a ``trail.survival_state``
    # facet, with the lazy default of ``unknown`` until cache populates.
    for unit in git_anchor_units:
        survival_facets = [
            f for f in unit.facets if f.name == "trail.survival_state"
        ]
        assert survival_facets, "git_anchor unit missing trail.survival_state facet"
        assert survival_facets[0].value == "unknown", (
            "P1 expected survival_state=unknown at refresh time; "
            f"got {survival_facets[0].value!r}"
        )


def test_refresh_index_fast_for_312_anchor_projection(tmp_path: Path) -> None:
    """A 24-trace × 13-anchor projection must build trail units quickly.

    The real ``~/.opentraces/projects/`` fixture had ~315 anchors and
    refresh blew past 10 minutes. With P1 the refresh is O(units) and
    a synthetic 24-trace × 13-anchor (~312 anchors) projection should not
    need one Git commit per anchor to prove that contract.
    """
    _init_repo(tmp_path)
    (tmp_path / "main.py").write_text("first\nsecond\n")
    head = _commit(tmp_path, "seed")

    # 24 traces × 13 anchors each → 312 anchors total. Write all events in
    # one batch so this test measures projection cost, not append-log setup.
    drafts: list[TrailEventDraft] = []
    for trace_index in range(24):
        for anchor_index in range(13):
            file_path = f"file_{trace_index}_{anchor_index}.py"
            text = f"line a {trace_index}-{anchor_index}\nline b\n"
            drafts.extend(
                _patch_anchor_drafts(
                    trace_id=f"t-{trace_index:02d}",
                    patch_id=f"{trace_index:02d}{anchor_index:02d}{'0' * 56}"[:64],
                    anchor_commit=head,
                    file_path=file_path,
                    text=text,
                )
            )
    append_event_batch(tmp_path, drafts, writer="test-fixture")

    from opentraces.core.trace_index import _build_trail_units

    t0 = time.time()
    units = _build_trail_units(tmp_path, project_slug="proj")
    elapsed = time.time() - t0
    git_anchor_units = [u for u in units if u.unit_type == "git_anchor"]
    assert len(git_anchor_units) == 24 * 13, len(git_anchor_units)
    assert elapsed < 5.0, (
        f"P1 projection gate: _build_trail_units took {elapsed:.1f}s for "
        f"{len(git_anchor_units)} anchors (target <5s)."
    )


# ---------------------------------------------------------------------------
# Plan 087 U0 — no-op refresh must do zero per-home subprocess work.
# ---------------------------------------------------------------------------


def _build_many_project_homes(n: int) -> list[str]:
    """Create ``n`` registered project homes, each a real repo with an event log.

    Returns the list of slugs. The autouse ``_isolate_opentraces_global_state``
    fixture (tests/conftest.py) has already redirected ``~/.opentraces`` and
    ``paths.PROJECTS_DIR`` into a tmp HOME, so the homes land under the
    isolated PROJECTS_DIR and the config writes are hermetic.
    """
    from opentraces.core import paths
    from opentraces.core.config import (
        Config,
        ProjectRegistration,
        _make_slug,
        save_config,
    )

    projects: dict[str, ProjectRegistration] = {}
    slugs: list[str] = []
    for i in range(n):
        project_id = f"{i:032d}"
        slug = _make_slug(f"proj-{i:03d}", project_id)
        slugs.append(slug)

        # The project home that _iter_project_homes() walks lives under
        # PROJECTS_DIR/<slug>; it is the working repo the trail projection reads.
        home = paths.PROJECTS_DIR / slug
        home.mkdir(parents=True, exist_ok=True)
        _init_repo(home)
        (home / "a.py").write_text("alpha\nbeta\n")
        head = _commit(home, "seed")
        _seed_patch_anchor(
            home,
            trace_id=f"t-{i:03d}",
            patch_id=f"{i:03d}{'0' * 61}"[:64],
            anchor_commit=head,
            file_path="a.py",
            text="alpha\nbeta\n",
        )
        # _project_sources_by_slug() maps slug -> repo via the config registry;
        # point the registration at the same home dir so the projection reads it.
        projects[str(home.resolve())] = ProjectRegistration(
            project_id=project_id,
            slug=slug,
        )

    save_config(Config(projects=projects))
    return slugs


def test_no_op_refresh_index_zero_show_ref_subprocesses(monkeypatch) -> None:
    """R3 — a steady-state refresh_index() spawns zero ``git show-ref`` calls.

    The dominant ~90s no-op cost was one ``git show-ref`` subprocess per
    project home. After U0 the ref SHA is read directly from
    ``.git/refs/...`` / ``packed-refs``, so a no-op refresh over ~200 homes
    must spawn zero ``show-ref`` subprocesses and finish fast.
    """
    from opentraces.core import trace_index as ti

    _build_many_project_homes(200)

    # Warm the cache once.
    ti.rebuild_index()

    # Count any subprocess invocation whose argv mentions 'show-ref'.
    real_run = subprocess.run
    show_ref_calls = {"n": 0}

    def counting_run(args, *a, **kw):
        argv = args if isinstance(args, (list, tuple)) else [args]
        if any("show-ref" == str(tok) for tok in argv):
            show_ref_calls["n"] += 1
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", counting_run)

    t0 = time.time()
    ti.refresh_index()  # no source/ref changes since rebuild
    elapsed = time.time() - t0

    assert show_ref_calls["n"] == 0, (
        f"no-op refresh_index() spawned {show_ref_calls['n']} 'git show-ref' "
        "subprocesses (U0 expected 0 — ref SHA must be read directly)."
    )
    assert elapsed < 2.0, (
        f"no-op refresh_index() took {elapsed:.2f}s over 200 homes (U0 target <2s)."
    )


def test_refresh_index_no_trails_skips_project_home_iteration(monkeypatch) -> None:
    """R3 variant — refresh_index(refresh_trails=False) reads no project home ref."""
    from opentraces.core import trace_index as ti

    _build_many_project_homes(20)
    ti.rebuild_index()

    ref_sha_calls = {"n": 0}
    real_ref_sha = ti._trail_event_ref_sha

    def spy_ref_sha(*args, **kwargs):
        ref_sha_calls["n"] += 1
        return real_ref_sha(*args, **kwargs)

    monkeypatch.setattr(ti, "_trail_event_ref_sha", spy_ref_sha)

    ti.refresh_index(refresh_trails=False)

    assert ref_sha_calls["n"] == 0, (
        "refresh_index(refresh_trails=False) read a project-home event ref "
        f"{ref_sha_calls['n']} times (U0 expected 0 — trail loop must be skipped)."
    )


def _build_many_unregistered_homes(n: int) -> list[str]:
    """Create ``n`` project homes on disk that are NOT in the config registry.

    These are the dominant case on the live bucket: most of the ~279 project
    homes under ``PROJECTS_DIR`` have no registered source repo in the config,
    so ``_project_sources_by_slug()`` returns nothing for them. The R3-fix
    must make a no-op refresh over these homes cheap (no per-home delete sweep
    when there is nothing to clean).

    Unlike ``_build_many_project_homes`` these homes are bare dirs (no repo, no
    config registration), so they hit the unregistered/missing-repo else-branch
    in ``_refresh_index_locked``.
    """
    from opentraces.core import paths

    slugs: list[str] = []
    for i in range(n):
        slug = f"unreg-{i:04d}-{'0' * 32}"
        slugs.append(slug)
        home = paths.PROJECTS_DIR / slug
        home.mkdir(parents=True, exist_ok=True)
    return slugs


def test_noop_refresh_under_2s_on_many_homes(monkeypatch) -> None:
    """R3 — a true no-op refresh over many unregistered homes finishes <2s.

    Seeds >=200 project homes with NO registered source repo plus a populated
    index, then times the SECOND refresh (steady state, nothing changed). The
    unregistered-home else-branch must not run a multi-statement delete sweep
    per home.
    """
    from opentraces.core import trace_index as ti

    _build_many_unregistered_homes(220)

    # Warm the cache once (this populates the index + runs the first refresh).
    ti.rebuild_index()
    ti.refresh_index()  # first refresh — settle steady state

    t0 = time.time()
    ti.refresh_index()  # true no-op: nothing changed since the last refresh
    elapsed = time.time() - t0

    assert elapsed < 2.0, (
        f"no-op refresh_index() took {elapsed:.2f}s over 220 unregistered "
        "homes (R3 target <2s)."
    )


def test_noop_refresh_runs_zero_trail_deletes(monkeypatch) -> None:
    """R3 — a no-op refresh issues zero per-home trail-projection deletes.

    When no project has trail rows and no ref moved, the unregistered-home
    branch must skip ``_delete_trail_projection_for_project`` entirely.
    """
    from opentraces.core import trace_index as ti

    _build_many_unregistered_homes(50)
    ti.rebuild_index()
    ti.refresh_index()  # settle

    delete_calls = {"n": 0}
    real_delete = ti._delete_trail_projection_for_project

    def counting_delete(conn, project_slug):
        delete_calls["n"] += 1
        return real_delete(conn, project_slug)

    monkeypatch.setattr(ti, "_delete_trail_projection_for_project", counting_delete)

    ti.refresh_index()  # true no-op

    assert delete_calls["n"] == 0, (
        f"no-op refresh_index() ran {delete_calls['n']} per-home trail deletes "
        "(R3 expected 0 — guard must skip homes with nothing to clean)."
    )


def test_unregistered_project_still_pruned_when_rows_exist(monkeypatch) -> None:
    """R3 guard must not over-skip: a de-registered project loses its projection.

    Register a project, refresh (creates trail rows), then unregister it and
    refresh again. Its patch/git_anchor units and trail_sources row MUST be
    deleted, proving the EXISTS guard still fires when rows actually exist.
    """
    import sqlite3

    from opentraces.core import trace_index as ti
    from opentraces.core.config import Config, save_config

    slugs = _build_many_project_homes(3)
    ti.rebuild_index()
    ti.refresh_index()

    target = slugs[0]
    db_path = ti.default_index_path()

    with sqlite3.connect(db_path) as conn:
        trail_rows_before = conn.execute(
            "select count(*) from trail_sources where project_slug = ?",
            (target,),
        ).fetchone()[0]
        unit_rows_before = conn.execute(
            "select count(*) from units where project_slug = ? "
            "and unit_type in ('patch','git_anchor')",
            (target,),
        ).fetchone()[0]
    assert trail_rows_before, "expected a trail_sources row for the target after refresh"
    assert unit_rows_before, "expected trail units for the target after refresh"

    # Unregister the target by writing a config WITHOUT its registration.
    from opentraces.core import paths
    from opentraces.core.config import ProjectRegistration, _make_slug

    projects: dict[str, ProjectRegistration] = {}
    for i, slug in enumerate(slugs):
        if slug == target:
            continue
        project_id = f"{i:032d}"
        home = paths.PROJECTS_DIR / slug
        projects[str(home.resolve())] = ProjectRegistration(
            project_id=project_id, slug=slug
        )
    save_config(Config(projects=projects))

    ti.refresh_index()

    with sqlite3.connect(db_path) as conn:
        trail_rows_after = conn.execute(
            "select count(*) from trail_sources where project_slug = ?",
            (target,),
        ).fetchone()[0]
        unit_rows_after = conn.execute(
            "select count(*) from units where project_slug = ? "
            "and unit_type in ('patch','git_anchor')",
            (target,),
        ).fetchone()[0]

    assert trail_rows_after == 0, (
        "de-registered project still has a trail_sources row "
        f"({trail_rows_after}); R3 guard over-skipped the prune."
    )
    assert unit_rows_after == 0, (
        "de-registered project still has patch/git_anchor units "
        f"({unit_rows_after}); R3 guard over-skipped the prune."
    )


def test_noop_refresh_issues_no_subprocess(monkeypatch) -> None:
    """R3 — lock the Phase-1 win: a no-op refresh spawns zero subprocesses.

    Covers regression of the git-show-ref fan-out removal alongside the
    R3 delete-guard.
    """
    from opentraces.core import trace_index as ti

    _build_many_unregistered_homes(80)
    ti.rebuild_index()
    ti.refresh_index()  # settle

    real_run = subprocess.run
    run_calls = {"n": 0}

    def counting_run(args, *a, **kw):
        run_calls["n"] += 1
        return real_run(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", counting_run)

    ti.refresh_index()  # true no-op

    assert run_calls["n"] == 0, (
        f"no-op refresh_index() spawned {run_calls['n']} subprocesses "
        "(R3 expected 0)."
    )


def test_scoped_refresh_leaves_other_trail_sources_intact(monkeypatch) -> None:
    """R3 variant — a scoped refresh must not delete other projects' trail rows."""
    import sqlite3

    from opentraces.core import trace_index as ti

    slugs = _build_many_project_homes(5)
    ti.rebuild_index()

    db_path = ti.default_index_path()
    with sqlite3.connect(db_path) as conn:
        before = {
            str(row[0])
            for row in conn.execute("select project_slug from trail_sources")
        }
    assert before, "expected trail_sources rows after rebuild"

    target = slugs[0]
    ti.refresh_index(trail_project_slugs={target})

    with sqlite3.connect(db_path) as conn:
        after = {
            str(row[0])
            for row in conn.execute("select project_slug from trail_sources")
        }

    assert before <= after, (
        "scoped refresh deleted other projects' trail_sources rows; "
        f"before={sorted(before)} after={sorted(after)}"
    )


# ---------------------------------------------------------------------------
# Plan 087 — a malformed index DB self-heals on the read path.
# ---------------------------------------------------------------------------


def test_query_index_self_heals_malformed_db() -> None:
    """A corrupt/malformed index DB rebuilds once on query instead of crashing.

    Plan 087 U1 removed the query-time refresh that used to mask a malformed
    DB (e.g. stale ``-wal``/``-shm`` sidecars after a snapshot restore). The
    read path now catches ``sqlite3.DatabaseError``, discards the bad DB +
    sidecars, rebuilds from the retained trace stores, and serves — rather
    than surfacing a sqlite error to ``trace query``.
    """
    from opentraces.core import trace_index as ti

    _build_many_project_homes(2)
    ti.rebuild_index()
    db_path = ti.default_index_path()

    # Simulate corruption: overwrite the DB with non-sqlite bytes and leave a
    # bogus WAL sidecar (the exact shape that triggers "database disk image is
    # malformed" on the next WAL open).
    db_path.write_bytes(b"not a sqlite database at all\n" * 8)
    db_path.with_name(db_path.name + "-wal").write_bytes(b"\x00\x01\x02bogus-wal")

    # Must not raise; self-heals and returns a usable page.
    page = ti.query_index_page(lex="alpha")
    assert page is not None

    # The healed DB is a valid sqlite file again.
    import sqlite3

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
