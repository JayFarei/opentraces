"""Issue #120 — bound the whole-log walk behind ``trail blame commit`` and
``trail graph``.

Both commands only ever read ``anchors_for_commit_with_survival`` for a bounded
set of commit SHAs (blame: the resolved commit; graph: the already-paginated
commit window). #120 adds ``build_trail_query_projection_for_commits``, which
reads ONLY the projection's event types — and only the commit-keyed
anchor/search events that reference a wanted sha — via ``read_events_scoped``
instead of walking the whole canonical event log.

Two contracts are pinned here:

1. EQUIVALENCE (byte-identity of OBSERVED output): for every commit, the
   commit-scoped projection's ``anchors_for_commit_with_survival`` rows are
   deep-equal to the full builder's. This is the exact row list blame emits and
   graph joins. The graph case is also pinned over the whole commit set.

2. BOUNDED (not just fast): on a ``git_anchor_search_completed``-dominated log,
   the scoped builder JSON-parses only a tiny fraction of the event blobs the
   full builder parses — proving the read is bounded by the wanted commits, not
   by history.

The canonical event log is unchanged; the scoped projection is a rebuildable
read accelerator only.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces.core.trails import event_log as event_log_mod
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    build_trail_query_projection,
    build_trail_query_projection_for_commits,
)
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


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


def _seed_multi_commit_world(repo: Path, n_commits: int = 4) -> list[str]:
    """Seed a multi-commit, multi-trace world: each commit carries a real git
    commit + a patch/anchor pair (some commits carry two patches across two
    traces) so ``anchors_by_commit`` rows exercise the survival sync path.
    Returns the ordered list of commit SHAs."""
    _init_repo(repo)
    shas: list[str] = []
    for i in range(n_commits):
        fname = f"f{i}.py"
        text = f"line-{i}\nbody\n"
        (repo / fname).write_text(text)
        sha = _commit(repo, f"commit {i}")
        shas.append(sha)
        drafts = _patch_anchor_drafts(
            trace_id=f"t-{i}",
            patch_id=f"patch{i:04d}aa",
            anchor_commit=sha,
            file_path=fname,
            text=text,
        )
        # Every other commit gets a second patch from a second trace so the
        # per-commit row LIST (not just presence) is non-trivial and ordered.
        if i % 2 == 0:
            drafts += _patch_anchor_drafts(
                trace_id=f"t-{i}-b",
                patch_id=f"patch{i:04d}bb",
                anchor_commit=sha,
                file_path=fname,
                text=text,
            )
        append_event_batch(repo, drafts, writer="test-fixture")
    return shas


def test_commit_scoped_equals_full_per_commit(tmp_path: Path) -> None:
    """EQUIVALENCE: per-commit blame rows are deep-equal between the scoped and
    full builders — the exact list ``trail blame commit`` emits."""
    shas = _seed_multi_commit_world(tmp_path, n_commits=4)
    full = build_trail_query_projection(tmp_path)

    for sha in shas:
        scoped = build_trail_query_projection_for_commits(tmp_path, {sha})
        scoped_rows = scoped.anchors_for_commit_with_survival(sha)
        full_rows = full.anchors_for_commit_with_survival(sha)
        assert scoped_rows == full_rows, f"row mismatch for {sha[:8]}"
        # Not just a superset — the scoped read must not leak OTHER commits'
        # anchors into this commit's row list.
        for row in scoped_rows:
            assert row.get("commit_sha") == sha


def test_commit_scoped_graph_window_equals_full(tmp_path: Path) -> None:
    """EQUIVALENCE (graph join): building the projection over the whole commit
    window yields ``anchors_by_commit`` equal to the full builder's, restricted
    to those SHAs — the exact evidence ``trail graph`` renders."""
    shas = _seed_multi_commit_world(tmp_path, n_commits=4)
    all_shas = set(shas)
    scoped = build_trail_query_projection_for_commits(tmp_path, all_shas)
    full = build_trail_query_projection(tmp_path)

    for sha in shas:
        assert scoped.anchors_for_commit_with_survival(
            sha
        ) == full.anchors_for_commit_with_survival(sha)

    # anchors_by_commit (pre-survival rows) restricted to the window must match.
    scoped_by_commit = {s: scoped.anchors_by_commit.get(s) for s in all_shas}
    full_by_commit = {s: full.anchors_by_commit.get(s) for s in all_shas}
    assert scoped_by_commit == full_by_commit


def test_unknown_commit_returns_empty(tmp_path: Path) -> None:
    """A sha with no anchors yields an empty row list (no crash, no leakage)."""
    _seed_multi_commit_world(tmp_path, n_commits=3)
    scoped = build_trail_query_projection_for_commits(tmp_path, {"deadbeef" * 5})
    assert scoped.anchors_for_commit_with_survival("deadbeef" * 5) == []


def test_commit_scoped_read_is_bounded(tmp_path: Path, monkeypatch) -> None:
    """BOUNDED: on a search-event-dominated log, the scoped builder JSON-parses
    far fewer blobs than the full builder — proving the read is bounded by the
    wanted commit, not by history.

    We seed ONE real commit with a patch/anchor pair, then a large number of
    ``git_anchor_search_completed`` summary events for OTHER (synthetic) commit
    heads. The scoped builder for the real commit's sha must not parse those
    foreign search events (its commit-sha prefilter drops them), while the full
    builder parses every event blob.
    """
    _init_repo(tmp_path)
    (tmp_path / "real.py").write_text("alpha\nbody\n")
    real_sha = _commit(tmp_path, "real commit")
    append_event_batch(
        tmp_path,
        _patch_anchor_drafts(
            trace_id="t-real",
            patch_id="realpatch01",
            anchor_commit=real_sha,
            file_path="real.py",
            text="alpha\nbody\n",
        ),
        writer="test-fixture",
    )

    # Flood the log with foreign search-completed summary events keyed to OTHER
    # commit heads (never the real commit).
    n_noise = 200
    noise_drafts: list[TrailEventDraft] = []
    for i in range(n_noise):
        foreign_head = f"{i:040x}"
        noise_drafts.append(
            TrailEventDraft(
                event_type="git_anchor_search_completed",
                trace_id=None,
                step_index=None,
                capture_method=["reconcile"],
                payload={
                    "schema_version": "opentraces.trail.anchor_search.v2",
                    "summary": True,
                    "search_head": {"algo": "sha1", "hex": foreign_head},
                    "algorithms_attempted": ["exact_range_hash"],
                    "searched": 1,
                    "anchored": 0,
                    "unknown": 1,
                    "results": [
                        {
                            "trace_patch_id": f"tracepatch-sha256:noise{i:05d}",
                            "result": "unknown",
                            "created_anchor_ids": [],
                        }
                    ],
                },
            )
        )
    append_event_batch(tmp_path, noise_drafts, writer="test-fixture")

    # Total number of event blobs on disk — the full builder must read every
    # one of these (it walks the whole canonical log); the scoped builder must
    # JSON-parse only a tiny fraction.
    head = event_log_mod._ref_head(tmp_path)
    total_blobs = len(event_log_mod._list_event_blob_entries(tmp_path, head))
    assert total_blobs >= n_noise  # the noise flood dominates the log

    # Spy on the JSON-parse count for the SCOPED read (the load-bearing per-blob
    # cost). The scoped read never uses the cross-process snapshot, so the spy
    # observes its real parse work; ``read_events_scoped`` parses via
    # ``TrailEvent.model_validate_json``.
    orig_validate = event_log_mod.TrailEvent.model_validate_json
    counter = {"n": 0}

    def spy(raw, *args, **kwargs):
        counter["n"] += 1
        return orig_validate(raw, *args, **kwargs)

    monkeypatch.setattr(event_log_mod.TrailEvent, "model_validate_json", spy)
    scoped = build_trail_query_projection_for_commits(tmp_path, {real_sha})
    scoped_parses = counter["n"]
    scoped_rows = scoped.anchors_for_commit_with_survival(real_sha)
    monkeypatch.undo()

    # Observed output is identical to the full builder for the real commit.
    full = build_trail_query_projection(tmp_path)
    full_rows = full.anchors_for_commit_with_survival(real_sha)
    assert scoped_rows == full_rows
    assert len(scoped_rows) == 1

    # BOUNDED: the scoped builder JSON-parses only a tiny fraction of the
    # ``total_blobs`` on the log (its commit-sha prefilter drops the foreign
    # search-event flood). Bounded by the wanted commit, not by history.
    assert scoped_parses < total_blobs // 4, (
        f"scoped parsed {scoped_parses} of {total_blobs} blobs; "
        "expected it to skip the foreign search-event flood"
    )


def test_graph_commit_mode_joins_only_the_paginated_window(
    tmp_path: Path, monkeypatch
) -> None:
    """CALLER REGRESSION (#120): commit-primary ``trail graph`` must attach
    trail evidence to the PAGINATED window only — never the full git history.

    The scoped builder (tested above) is correct in isolation; the shipped bug
    was in the CALLER (``load_commits_from_repo``), which ran the trail-evidence
    join BEFORE ``_paginate`` and so handed the scoped builder EVERY commit's
    SHA — re-admitting the whole ``git_anchor_search_completed`` flood the
    scoping is meant to avoid. The builder-level tests stayed green through that
    bug because they call the builder directly with an already-narrow SHA set.
    This pins the ordering: the join must see ``<= limit`` commits, not all of
    history. Moving ``_attach_trail_evidence`` back before ``_paginate`` fails
    this test.
    """
    from opentraces.clients.text import graph_renderer

    shas = _seed_multi_commit_world(tmp_path, n_commits=5)
    assert len(shas) > 2  # the world genuinely spans more than one page

    captured: dict = {}
    real_attach = graph_renderer._attach_trail_evidence

    def _spy(commits, project_cwd):
        captured["count"] = len(commits)
        return real_attach(commits, project_cwd)

    monkeypatch.setattr(graph_renderer, "_attach_trail_evidence", _spy)

    opts = graph_renderer.RenderOptions(mode="commit", limit=2, page=1)
    graph_renderer.load_commits_from_repo(tmp_path, opts)

    assert "count" in captured, "the trail-evidence join was never invoked"
    assert captured["count"] <= opts.limit, (
        f"trail-evidence join saw {captured['count']} commits but the rendered "
        f"page is {opts.limit}; pagination MUST precede the join, else the "
        "bounded commit-scoped read is fed the full git history (the #120 "
        "flood regression)."
    )


def test_graph_trace_pivot_joins_only_the_paginated_window(
    tmp_path: Path, monkeypatch
) -> None:
    """F0 hang cure (#110): trace-primary (``--trace``) ``trail graph`` must
    bound the pivot to the PAGINATED window, mirroring commit mode.

    The pre-cure pivot path attached attribution + trail evidence to EVERY
    commit in history before filtering to the pivot — handing the commit-scoped
    trail-evidence builder the full git-history SHA set and re-admitting the
    ``git_anchor_search_completed`` flood the #120 scoping exists to avoid. This
    pins the ordering: with pagination first, the join sees ``<= limit`` commits.
    """
    from opentraces.clients.text import graph_renderer

    shas = _seed_multi_commit_world(tmp_path, n_commits=5)
    assert len(shas) > 2  # the world spans more than one page

    captured: dict = {}
    real_attach = graph_renderer._attach_trail_evidence

    def _spy(commits, project_cwd):
        captured["count"] = len(commits)
        return real_attach(commits, project_cwd)

    monkeypatch.setattr(graph_renderer, "_attach_trail_evidence", _spy)

    # ``t-4`` is the newest commit's trace, so it falls inside the recent window.
    opts = graph_renderer.RenderOptions(
        mode="trace", pivot_trace_id="t-4", limit=2, page=1
    )
    result = graph_renderer.load_commits_from_repo(tmp_path, opts)

    assert "count" in captured, "the trail-evidence join was never invoked"
    assert captured["count"] <= opts.limit, (
        f"pivot trail-evidence join saw {captured['count']} commits but the "
        f"paginated window is {opts.limit}; pagination MUST precede the join in "
        "trace-pivot mode too, else the pivot re-admits the full git history."
    )
    # The pivot still resolves within the window.
    assert any(
        any(t.trace_id == "t-4" for t in c.traces) for c in result
    )
