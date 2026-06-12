"""Multi-trace projection regressions (issue #42).

Two shipped bugs surfaced by the c-compacted-session / c-rewound-session
otbox checkpoints, both in ``build_context_tree_projection``:

1. The ``CONTEXT_COMPACTION_OBSERVED`` / ``CONTEXT_TREE_RECONCILED``
   branches REBOUND the ``trace_id`` filter parameter, so after the
   first trace's reconciled sentinel the projection silently dropped
   every other trace's events — on any multi-session project, ``ctx
   tree <second-trace>`` returned the ``context_tree_not_captured``
   empty state despite the events being on the log.

2. ``orphan_branch_roots_by_trace`` appended EVERY ``rewind_branch``
   node instead of only true orphan ROOTS (parent not itself a rewind
   node), diverging from the emitter's documented
   ``context_tree_reconciled.orphan_branch_roots`` semantics and
   duplicating the orphan subtree in ``ctx tree --show-orphans``.
"""
from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

from opentraces.capture.claude_code.context_tree_capture import (
    emit_context_tree_events_from_record,
)
from opentraces.core.context_tree.query import build_context_tree_projection
from opentraces_schema.models import Agent, TraceRecord


FIXTURES_DIR = Path(__file__).parent.parent / "otbox" / "fixtures" / "sessions"


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _ingest_fixture(name: str, repo: Path, workdir: Path) -> str:
    """Run a session fixture and emit its context-tree events into ``repo``.

    Returns the trace id used for the emission.
    """
    base = FIXTURES_DIR / name
    spec = importlib.util.spec_from_file_location(
        f"_multi_{name.replace('-', '_')}", base / "session.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    workdir.mkdir(parents=True, exist_ok=True)
    transcript = workdir / f"{name}.jsonl"
    mod.run(workdir, transcript)
    trace_id = f"trace-{name}"
    record = TraceRecord(
        trace_id=trace_id,
        session_id=f"sess-{name}",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[],
    )
    emit_context_tree_events_from_record(
        project_dir=repo,
        final_record=record,
        transcript_path=transcript,
    )
    return trace_id


def test_second_trace_survives_first_traces_reconciled_sentinel(tmp_path):
    """Regression: the projection must not lock onto the first trace.

    Before the issue-#42 fix, the reconciled-sentinel branch rebound the
    ``trace_id`` filter parameter, so the SECOND ingested trace projected
    as empty (``ctx tree`` returned context_tree_not_captured) on any
    project with more than one captured session.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    first = _ingest_fixture("context-tree-linear", repo, tmp_path / "w1")
    second = _ingest_fixture("context-tree-rewound", repo, tmp_path / "w2")

    projection = build_context_tree_projection(repo)
    assert projection.nodes_by_trace.get(first), "first trace must project"
    assert projection.nodes_by_trace.get(second), (
        "second trace dropped — the trace_id filter parameter was rebound "
        "by the first trace's compaction/reconciled events"
    )
    # The reconciled sentinel's active leaf must be tracked per trace.
    assert second in projection.active_leaves_by_trace


def test_explicit_trace_filter_still_scopes_projection(tmp_path):
    """The documented per-trace scoping still works after the fix."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    first = _ingest_fixture("context-tree-linear", repo, tmp_path / "w1")
    second = _ingest_fixture("context-tree-multi-turn", repo, tmp_path / "w2")

    scoped = build_context_tree_projection(repo, trace_id=second)
    assert second in scoped.nodes_by_trace
    assert first not in scoped.nodes_by_trace


def test_orphan_branch_roots_are_roots_not_every_rewind_node(tmp_path):
    """Regression: only true orphan ROOTS land in orphan_branch_roots.

    The rewound fixture has ONE orphan branch (a rewind root plus its
    descendant). Before the fix every rewind_branch node was appended,
    duplicating the subtree in ``ctx tree --show-orphans``.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    trace_id = _ingest_fixture("context-tree-rewound", repo, tmp_path / "w1")

    projection = build_context_tree_projection(repo)
    roots = projection.orphan_branch_roots_by_trace.get(trace_id, [])
    assert len(roots) == 1, (
        f"expected exactly 1 orphan branch root, got {len(roots)} — "
        "non-root rewind nodes leaked into orphan_branch_roots"
    )
    # The root's subtree covers the full orphan branch (root + leaf).
    subtree = projection.orphan_subtree(roots[0])
    assert len(subtree) >= 2
    # And the root really is a root: its parent is not a rewind node.
    root_node = projection.nodes_by_id[roots[0]]
    parent = projection.nodes_by_id.get(root_node.parent_node_id or "")
    assert parent is None or parent.branch_type != "rewind_branch"
