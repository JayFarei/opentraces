"""Integration tests for the issue #210 (seal-family W2) context backfill.

Builds a small synthetic bucket (never touches the real ``~/.opentraces``
bucket -- the autouse ``tests/conftest.py`` HOME isolation redirects
``bucket_dir()`` to a per-test tmp dir) covering the three actionable defect
classes plus the two invariants the issue's acceptance bar names:

  - ``reproject``: events already landed in the shared event mirror, the
    per-trace companion just went stale.
  - ``codex_recover``: a legitimately-empty codex-cli trace whose steps are
    record-derivable into a real Context Tree.
  - ``drop_dangling``: a stamped ``context_node_id`` that resolves to nothing
    anywhere -- a genuine, unrecoverable append failure.
  - Never-fabricate: a true zero-event trace stays ``legitimately_empty``.
  - Idempotency: a second ``apply_backfill`` pass over an already-fixed
    bucket is a byte-identical no-op.
  - Append-only: the codex-recover path only appends to the canonical Git
    event log ref -- the pre-existing history stays reachable.

These are the audit's OWN independent read (``context_companions_audit``),
never the backfill's self-report, per the loopcraft verify condition.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from opentraces_schema import Agent, Environment, Outcome, Step, TraceRecord, VCS

from opentraces.core.bucket_envelope import project_per_trace_exports
from opentraces.core.bucket_events import sync_events_mirror
from opentraces.core.bucket_trace_records import write_trace_record
from opentraces.core.context_backfill import apply_backfill, plan_backfill
from opentraces.core.context_companions_audit import audit_bucket
from opentraces.core.paths import bucket_dir
from opentraces.core.trails.event_log import EVENT_LOG_REF


def _write_record(world: "_World", record: TraceRecord) -> None:
    """Mirror ``ingest.write_trace_to_bucket``'s dual write for a test trace:
    the canonical TraceRecord object store FIRST, then the per-trace
    envelope projection -- ``_apply_codex_recover`` (and real ingest) reads
    the record back through ``read_trace_record_object``, a separate store
    from the ``trace.json`` companion export.
    """

    write_trace_record(
        record, project_slug=world.slug, source_layer="canonical", legacy_mirror=True,
    )


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _ref_sha(repo: Path) -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", EVENT_LOG_REF],
        cwd=repo, capture_output=True, text=True,
    )
    return proc.stdout.strip() or None


def _is_ancestor(repo: Path, older: str, newer: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", older, newer],
        cwd=repo,
    )
    return proc.returncode == 0


def _codex_record(trace_id: str, *, n_steps: int = 2) -> TraceRecord:
    steps = [
        Step(step_index=1, role="user", content="Fix the bug", call_type="main"),
        Step(step_index=2, role="agent", content="Done.", call_type="main"),
    ]
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"thread-{trace_id}",
        agent=Agent(name="codex-cli", version="0.120.0", model="openai/gpt-5-codex"),
        environment=Environment(vcs=VCS(type="git", branch="main", base_commit="abc123")),
        outcome=Outcome(commit_sha="def456"),
        steps=steps[:n_steps],
        metadata={"source": "codex_cli_rollout"},
    )


def _claude_record(trace_id: str, *, n_steps: int = 5) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"sess-{trace_id}",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[Step(step_index=i, role="agent") for i in range(1, n_steps + 1)],
    )


class _World:
    """Owns the one synthetic project + bucket used across the fixtures."""

    def __init__(self, tmp_path: Path):
        self.project_dir = tmp_path / "proj"
        _init_git_repo(self.project_dir)
        self.slug = "demo-project"


def _make_reproject_case(world: _World) -> tuple[str, list[str]]:
    """A trace whose companion is stale: mirror has events, companion doesn't."""

    from opentraces.capture.claude_code.context_tree_capture import (
        emit_context_tree_events_from_record,
    )
    from tests.context_tree.test_context_tree_stamp_after_persist import _run_fixture

    trace_id = "trace-reproject-1"
    record = _claude_record(trace_id)

    transcript = _run_fixture("context-tree-linear", world.project_dir)
    summary = emit_context_tree_events_from_record(
        project_dir=world.project_dir,
        final_record=record,
        transcript_path=transcript,
    )
    assert summary["step_node_id_map"], "fixture must actually append events"
    for step in record.steps:
        nid = summary["step_node_id_map"].get(step.step_index)
        if nid:
            step.context_node_id = nid

    # Write the per-trace companion BEFORE the events reach the bucket's
    # shared mirror (events=[] and the mirror is not yet synced, so there is
    # nothing to fall back to) -- an intentionally stale/empty companion,
    # simulating the exact staleness bug this lane fixes: the projection ran
    # before the backing events landed and was never re-run.
    _write_record(world, record)
    project_per_trace_exports(
        None,
        project_slug=world.slug,
        trace_id=trace_id,
        record=record,
        events=[],
        events_authoritative=True,
    )

    # Now sync the real events into the bucket's shared mirror -- this is
    # the "events exist in the mirror, companion just hasn't caught up yet"
    # state the reproject action targets.
    sync_events_mirror(world.project_dir, repo_id=world.slug)

    stamped_ids = sorted({s.context_node_id for s in record.steps if s.context_node_id})
    return trace_id, stamped_ids


def _make_codex_recover_case(world: _World) -> str:
    """A legitimately-empty codex trace that IS record-derivable."""

    trace_id = "trace-codex-recover-1"
    record = _codex_record(trace_id)
    _write_record(world, record)
    project_per_trace_exports(
        None, project_slug=world.slug, trace_id=trace_id, record=record,
    )
    return trace_id


def _make_drop_dangling_case(world: _World) -> str:
    """A stamped id that resolves to nothing anywhere -- unrecoverable."""

    trace_id = "trace-dangling-1"
    record = _claude_record(trace_id, n_steps=2)
    record.steps[0].context_node_id = "sha256:" + "de" * 32
    _write_record(world, record)
    project_per_trace_exports(
        None, project_slug=world.slug, trace_id=trace_id, record=record,
    )
    return trace_id


def _make_legitimately_empty_case(world: _World) -> str:
    """A true zero-event trace -- must NEVER be touched or fabricated."""

    trace_id = "trace-legit-empty-1"
    record = _claude_record(trace_id, n_steps=2)
    _write_record(world, record)
    project_per_trace_exports(
        None, project_slug=world.slug, trace_id=trace_id, record=record,
    )
    return trace_id


def _companion_bytes(slug: str, trace_id: str) -> bytes:
    path = bucket_dir() / "traces" / "v1" / slug / trace_id / "context.jsonl.gz"
    return path.read_bytes() if path.exists() else b""


def test_backfill_end_to_end_on_synthetic_bucket(tmp_path: Path) -> None:
    world = _World(tmp_path)

    reproject_id, reproject_stamped_ids = _make_reproject_case(world)
    codex_id = _make_codex_recover_case(world)
    dangling_id = _make_drop_dangling_case(world)
    legit_empty_id = _make_legitimately_empty_case(world)

    # --- RED: independent audit sees exactly the defects we constructed ---
    baseline = audit_bucket(bucket_dir())
    by_id = {t.trace_id: t for t in baseline.traces}
    assert by_id[reproject_id].classification == "inconsistent"
    assert by_id[codex_id].classification == "legitimately_empty"
    assert by_id[dangling_id].classification == "inconsistent"
    assert by_id[legit_empty_id].classification == "legitimately_empty"
    baseline_legit_empty_count = baseline.legitimately_empty

    # --- Plan (read-only, dry-run shape) ---
    plan = plan_backfill()
    assert reproject_id in {a.trace_id for a in plan.reproject}
    assert codex_id in {a.trace_id for a in plan.codex_recover}
    assert dangling_id in {a.trace_id for a in plan.drop_dangling}
    assert legit_empty_id not in {a.trace_id for a in plan.all_actions()}

    ref_before = _ref_sha(world.project_dir)

    # --- Apply ---
    result = apply_backfill(plan, project_paths={world.slug: world.project_dir})
    assert not result["skipped_no_live_project"], result["skipped_no_live_project"]
    assert len(result["reproject"]) == 1
    assert len(result["codex_recover"]) == 1
    assert len(result["drop_dangling"]) == 1

    ref_after = _ref_sha(world.project_dir)
    assert ref_before is not None and ref_after is not None and ref_before != ref_after
    # Append-only: the pre-recover history stays reachable from the new tip.
    assert _is_ancestor(world.project_dir, ref_before, ref_after)

    # --- GREEN: re-observe with the SAME independent audit ---
    healed = audit_bucket(bucket_dir())
    healed_by_id = {t.trace_id: t for t in healed.traces}

    assert healed_by_id[reproject_id].classification == "consistent"
    # The re-projected companion now matches the mirror exactly (the audit's
    # own "consistent" definition) and the step-stamped ids all resolve
    # inside it (no dangling references survived the reproject).
    assert (
        healed_by_id[reproject_id].companion_node_ids
        == healed_by_id[reproject_id].mirror_node_ids
    )
    assert set(reproject_stamped_ids) <= set(healed_by_id[reproject_id].companion_node_ids)

    assert healed_by_id[codex_id].classification == "consistent"
    assert healed_by_id[codex_id].companion_context_count > 0

    # Dangling id dropped, not fabricated -- the trace becomes legitimately
    # empty (it never had real events), not falsely "consistent".
    assert healed_by_id[dangling_id].classification == "legitimately_empty"
    assert healed_by_id[dangling_id].stamped_ids == []

    # Never-fabricate: the true zero-event trace is untouched and the overall
    # legitimately-empty count did not shrink below what it started at minus
    # the one dangling trace that correctly joined it.
    assert healed_by_id[legit_empty_id].classification == "legitimately_empty"
    assert healed.legitimately_empty >= baseline_legit_empty_count

    assert healed.inconsistent == 0

    # --- Idempotency: second apply over an already-healed bucket is a no-op ---
    reproject_bytes_1 = _companion_bytes(world.slug, reproject_id)
    codex_bytes_1 = _companion_bytes(world.slug, codex_id)

    plan_2 = plan_backfill()
    assert plan_2.counts() == {
        "reproject": 0, "codex_recover": 0, "drop_dangling": 0,
        "skipped_no_live_project": 0,
    }
    result_2 = apply_backfill(plan_2, project_paths={world.slug: world.project_dir})
    assert result_2 == {
        "reproject": [], "codex_recover": [], "drop_dangling": [],
        "skipped_no_live_project": [], "mirror_sync_failures": [],
    }

    reproject_bytes_2 = _companion_bytes(world.slug, reproject_id)
    codex_bytes_2 = _companion_bytes(world.slug, codex_id)
    assert reproject_bytes_1 == reproject_bytes_2
    assert codex_bytes_1 == codex_bytes_2

    ref_final = _ref_sha(world.project_dir)
    assert ref_final == ref_after, "idempotent re-run must not append further events"


def test_codex_recover_skipped_without_live_project(tmp_path: Path) -> None:
    world = _World(tmp_path)
    codex_id = _make_codex_recover_case(world)

    # #210 review hardening: the audit now hard-blocks on a missing
    # events/v1/batches mirror rather than reading it as "zero events" (see
    # context_companions_audit.load_event_mirror_context_index). This
    # synthetic project never appended a live Trail event, so establish an
    # empty-but-present mirror directly, matching a bucket that has been
    # through at least one watcher tick / sync.
    (bucket_dir() / "events" / "v1" / "batches").mkdir(parents=True, exist_ok=True)

    plan = plan_backfill()
    assert codex_id in {a.trace_id for a in plan.codex_recover}

    # No project_paths mapping -> nothing to recover into, reported honestly.
    result = apply_backfill(plan, project_paths={})
    assert result["codex_recover"] == []
    assert any(a["trace_id"] == codex_id for a in result["skipped_no_live_project"])

    # Never fabricated: still legitimately empty, not silently marked fixed.
    after = audit_bucket(bucket_dir())
    after_by_id = {t.trace_id: t for t in after.traces}
    assert after_by_id[codex_id].classification == "legitimately_empty"
