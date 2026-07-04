"""Regression tests for the Codex external review of PR #217 (issue #210).

Three confirmed criticals on the seal-family W2 companion-integrity lane:

  1. ``opentraces ctx backfill --dry-run --apply`` accepted both flags and
     silently executed ``apply_backfill`` anyway (``dry_run`` was never
     checked). ``--dry-run`` must NEVER be able to write against the user's
     real bucket -- the two flags must be rejected as mutually exclusive
     before any planning or writing happens.
  2. A missing/corrupt ``bucket/events/v1/batches`` mirror was read as proof
     of zero context events ("legitimately empty"), which can silently
     misdrive backfill planning. Per issue #210's "a skip is a lie"
     principle, an unreadable mirror must be a hard, blocking audit error.
  3. The backfill planner picked ``reproject`` OR ``drop_dangling`` for an
     inconsistent trace, never both -- a trace with BOTH a stale/extra
     companion and an unrecoverable dangling id needed two backfill runs,
     breaking the "second run is a byte-identical no-op" idempotency
     contract. ``extra_in_companion`` mismatches were silently planned as no
     action. And a failed ``sync_events_mirror()`` inside codex-recovery was
     swallowed (bare ``except Exception: pass``), which could leave the
     companion ahead of the shared bucket-wide mirror with no planned
     repair.

These tests must be RED against the pre-fix code and GREEN after.
"""
from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from opentraces_schema import Agent, Step, TraceRecord

from opentraces.cli.ctx import ctx_group
from opentraces.core.bucket_envelope import project_per_trace_exports
from opentraces.core.bucket_events import sync_events_mirror
from opentraces.core.bucket_trace_records import write_trace_record
from opentraces.core.context_backfill import apply_backfill, plan_backfill
from opentraces.core.context_companions_audit import (
    audit_bucket,
    load_event_mirror_context_index,
)
from opentraces.core.paths import bucket_dir


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    (path / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def _claude_record(trace_id: str, *, n_steps: int = 2) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"sess-{trace_id}",
        agent=Agent(name="claude-code", version="otbox-fake-1.0"),
        steps=[Step(step_index=i, role="agent") for i in range(1, n_steps + 1)],
    )


def _codex_record(trace_id: str) -> TraceRecord:
    from opentraces_schema import Environment, Outcome, VCS

    return TraceRecord(
        trace_id=trace_id,
        session_id=f"thread-{trace_id}",
        agent=Agent(name="codex-cli", version="0.120.0", model="openai/gpt-5-codex"),
        environment=Environment(vcs=VCS(type="git", branch="main", base_commit="abc123")),
        outcome=Outcome(commit_sha="def456"),
        steps=[
            Step(step_index=1, role="user", content="Fix the bug", call_type="main"),
            Step(step_index=2, role="agent", content="Done.", call_type="main"),
        ],
        metadata={"source": "codex_cli_rollout"},
    )


class _World:
    def __init__(self, tmp_path: Path):
        self.project_dir = tmp_path / "proj"
        _init_git_repo(self.project_dir)
        self.slug = "demo-project"


def _companion_bytes(slug: str, trace_id: str) -> bytes:
    path = bucket_dir() / "traces" / "v1" / slug / trace_id / "context.jsonl.gz"
    return path.read_bytes() if path.exists() else b""


def _ensure_empty_valid_bucket() -> None:
    """A legitimately-empty-but-valid bucket: zero traces, mirror present.

    Needed so ``plan_backfill()`` itself succeeds (returns a trivial empty
    plan) -- otherwise a CLI test asserting a non-zero exit for
    ``--dry-run --apply`` would be RED for the WRONG reason (no bucket at
    all raising ``FileNotFoundError`` before the flag combination is even
    checked), not because the mutual-exclusivity guard fired.
    """
    root = bucket_dir()
    (root / "traces" / "v1").mkdir(parents=True, exist_ok=True)
    (root / "events" / "v1" / "batches").mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Finding 1: --dry-run --apply must be rejected, never fall through to a write
# --------------------------------------------------------------------------- #


class TestDryRunApplyMutuallyExclusive:
    def test_dry_run_and_apply_together_is_rejected(self, tmp_path, monkeypatch):
        _ensure_empty_valid_bucket()
        called = {"apply": False}

        def _boom(*args, **kwargs):
            called["apply"] = True
            raise AssertionError(
                "apply_backfill must never be called when --dry-run and "
                "--apply are both passed"
            )

        monkeypatch.setattr(
            "opentraces.core.context_backfill.apply_backfill", _boom
        )

        runner = CliRunner()
        result = runner.invoke(
            ctx_group, ["backfill", "--dry-run", "--apply"], catch_exceptions=True
        )

        assert not called["apply"], "apply_backfill was invoked despite --dry-run"
        assert result.exit_code != 0, (
            f"expected a non-zero exit for --dry-run --apply, got 0. "
            f"output={result.output!r}"
        )

    def test_dry_run_and_apply_together_json_mode(self, tmp_path, monkeypatch):
        _ensure_empty_valid_bucket()
        called = {"apply": False}

        def _boom(*args, **kwargs):
            called["apply"] = True
            raise AssertionError("must not reach apply_backfill")

        monkeypatch.setattr(
            "opentraces.core.context_backfill.apply_backfill", _boom
        )

        runner = CliRunner()
        result = runner.invoke(
            ctx_group,
            ["backfill", "--dry-run", "--apply", "--json"],
            catch_exceptions=True,
        )
        assert not called["apply"]
        assert result.exit_code != 0


# --------------------------------------------------------------------------- #
# Finding 2: a missing/corrupt event mirror must block, never read as "empty"
# --------------------------------------------------------------------------- #


class TestMirrorUnavailableIsBlocking:
    def test_missing_batches_dir_is_blocking_not_empty(self, tmp_path):
        bucket_root = tmp_path / "bucket"
        (bucket_root / "traces" / "v1").mkdir(parents=True)
        # events/v1/batches deliberately absent -- the mirror was never
        # synced, NOT proven to contain zero context events.
        assert not (bucket_root / "events" / "v1" / "batches").exists()

        with pytest.raises(FileNotFoundError):
            load_event_mirror_context_index(bucket_root)

        # The audit built on top of it must propagate the same blocking
        # failure -- never silently classify every trace legitimately_empty.
        with pytest.raises(FileNotFoundError):
            audit_bucket(bucket_root)

    def test_corrupt_batch_is_blocking_not_skipped(self, tmp_path):
        bucket_root = tmp_path / "bucket"
        (bucket_root / "traces" / "v1").mkdir(parents=True)
        batches_dir = bucket_root / "events" / "v1" / "batches"
        batches_dir.mkdir(parents=True)
        # Not valid gzip -- a corrupt/truncated batch file.
        (batches_dir / "0001-deadbeef.jsonl.gz").write_bytes(b"not actually gzip data")

        with pytest.raises(ValueError):
            load_event_mirror_context_index(bucket_root)

        with pytest.raises(ValueError):
            audit_bucket(bucket_root)

    def test_ctx_backfill_cli_reports_clean_error_on_missing_mirror(self, tmp_path, monkeypatch):
        """The CLI must surface the blocking error cleanly, not a traceback."""
        bucket_root = bucket_dir()
        (bucket_root / "traces" / "v1").mkdir(parents=True, exist_ok=True)
        assert not (bucket_root / "events" / "v1" / "batches").exists()

        runner = CliRunner()
        result = runner.invoke(ctx_group, ["backfill", "--dry-run"], catch_exceptions=True)
        assert result.exit_code != 0
        assert result.exception is None or not isinstance(
            result.exception, FileNotFoundError
        ), (
            "the CLI must catch the blocking mirror error and exit cleanly, "
            f"not raise a raw exception through Click: {result.exception!r}"
        )


# --------------------------------------------------------------------------- #
# Finding 3: mixed defects healed in ONE apply pass + extra_in_companion +
# swallowed mirror-sync failure surfaced
# --------------------------------------------------------------------------- #


def _make_mixed_defect_case(world: _World) -> tuple[str, list[str], str]:
    """One trace with BOTH a stale companion (reproject-worthy) AND an
    unrecoverable dangling id (drop_dangling-worthy) at the same time.
    """
    from opentraces.capture.claude_code.context_tree_capture import (
        emit_context_tree_events_from_record,
    )
    from tests.context_tree.test_context_tree_stamp_after_persist import _run_fixture

    trace_id = "trace-mixed-1"
    record = _claude_record(trace_id, n_steps=2)

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

    # An unrecoverable dangling id: resolves to nothing anywhere, not even
    # the mirror, once synced below.
    dangling_id = "sha256:" + "ab" * 32
    record.steps.append(Step(step_index=99, role="agent", context_node_id=dangling_id))

    write_trace_record(
        record, project_slug=world.slug, source_layer="canonical", legacy_mirror=True,
    )
    # Companion written stale/empty (events=[]) -- exactly like the
    # reproject fixture -- BEFORE the events reach the shared mirror.
    project_per_trace_exports(
        None,
        project_slug=world.slug,
        trace_id=trace_id,
        record=record,
        events=[],
        events_authoritative=True,
    )
    sync_events_mirror(world.project_dir, repo_id=world.slug)

    legit_ids = sorted(summary["step_node_id_map"].values())
    return trace_id, legit_ids, dangling_id


def _make_extra_in_companion_case(world: _World) -> tuple[str, str]:
    """Companion claims a node the mirror has never heard of."""
    trace_id = "trace-extra-in-companion-1"
    record = _claude_record(trace_id, n_steps=1)
    write_trace_record(
        record, project_slug=world.slug, source_layer="canonical", legacy_mirror=True,
    )
    project_per_trace_exports(
        None,
        project_slug=world.slug,
        trace_id=trace_id,
        record=record,
        events=[],
        events_authoritative=True,
    )
    # Establish a real (empty) mirror -- events/v1/{batches,index.json} must
    # exist for the audit AND ``_apply_reproject``'s
    # ``read_events_mirror_batches()`` to run at all post-fix; this trace
    # itself still contributes nothing to it (zero events were ever
    # appended for it). NOTE: sync_events_mirror()'s "missing ref" branch
    # writes index.json but does NOT mkdir the batches dir (this synthetic
    # project's git repo has no live Trail event-log ref) -- mkdir it
    # directly too, to model "a mirror sync ran at some point".
    sync_events_mirror(world.project_dir, repo_id=world.slug)
    (bucket_dir() / "events" / "v1" / "batches").mkdir(parents=True, exist_ok=True)
    phantom_id = "sha256:" + "cd" * 32
    context_path = bucket_dir() / "traces" / "v1" / world.slug / trace_id / "context.jsonl.gz"
    with gzip.open(context_path, "wt", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "event_type": "context_node_observed",
                    "payload": {"node_id": phantom_id, "trace_id": trace_id},
                }
            )
            + "\n"
        )
    return trace_id, phantom_id


def _make_codex_recover_case(world: _World) -> str:
    trace_id = "trace-codex-recover-1"
    record = _codex_record(trace_id)
    write_trace_record(
        record, project_slug=world.slug, source_layer="canonical", legacy_mirror=True,
    )
    project_per_trace_exports(
        None, project_slug=world.slug, trace_id=trace_id, record=record,
    )
    # Establish the (empty) mirror up front -- realistic precondition (a
    # watcher tick / prior sync already ran); the codex-recover path's own
    # sync_events_mirror() call below is what actually lands this trace's
    # newly recovered nodes. sync_events_mirror() itself is a no-op here
    # (no live Trail events exist yet for this synthetic project's git
    # ref), so mkdir the mirror directly instead.
    (bucket_dir() / "events" / "v1" / "batches").mkdir(parents=True, exist_ok=True)
    return trace_id


class TestMixedDefectsHealedInOnePass:
    def test_mixed_stale_and_dangling_planned_and_healed_together(self, tmp_path):
        world = _World(tmp_path)
        trace_id, legit_ids, dangling_id = _make_mixed_defect_case(world)

        baseline = audit_bucket(bucket_dir())
        by_id = {t.trace_id: t for t in baseline.traces}
        assert by_id[trace_id].classification == "inconsistent"
        assert dangling_id in by_id[trace_id].dangling_ids

        plan = plan_backfill()
        # Both actions must be planned for the SAME trace in the SAME pass.
        assert trace_id in {a.trace_id for a in plan.reproject}, (
            "stale-companion defect must still be planned for reproject"
        )
        assert trace_id in {a.trace_id for a in plan.drop_dangling}, (
            "an unrecoverable dangling id alongside a stale companion must "
            "ALSO be planned for drop_dangling in the same pass"
        )

        result = apply_backfill(plan, project_paths={world.slug: world.project_dir})
        assert any(a["trace_id"] == trace_id for a in result["reproject"])
        assert any(a["trace_id"] == trace_id for a in result["drop_dangling"])

        healed = audit_bucket(bucket_dir())
        healed_by_id = {t.trace_id: t for t in healed.traces}
        assert healed_by_id[trace_id].classification == "consistent"
        assert dangling_id not in healed_by_id[trace_id].stamped_ids
        assert set(legit_ids) <= set(healed_by_id[trace_id].companion_node_ids)

        # Idempotency: a second plan/apply pass is a byte-identical no-op.
        bytes_1 = _companion_bytes(world.slug, trace_id)
        plan_2 = plan_backfill()
        assert trace_id not in {a.trace_id for a in plan_2.all_actions()}
        result_2 = apply_backfill(plan_2, project_paths={world.slug: world.project_dir})
        assert not any(a["trace_id"] == trace_id for a in result_2["reproject"])
        assert not any(a["trace_id"] == trace_id for a in result_2["drop_dangling"])
        bytes_2 = _companion_bytes(world.slug, trace_id)
        assert bytes_1 == bytes_2

    def test_extra_in_companion_only_is_planned_not_silently_dropped(self, tmp_path):
        world = _World(tmp_path)
        trace_id, phantom_id = _make_extra_in_companion_case(world)

        baseline = audit_bucket(bucket_dir())
        by_id = {t.trace_id: t for t in baseline.traces}
        assert by_id[trace_id].classification == "inconsistent"
        assert phantom_id in by_id[trace_id].companion_node_ids

        plan = plan_backfill()
        assert trace_id in {a.trace_id for a in plan.reproject}, (
            "extra_in_companion-only defects must not be silently unplanned"
        )

        result = apply_backfill(plan, project_paths={world.slug: world.project_dir})
        assert any(a["trace_id"] == trace_id for a in result["reproject"])

        healed = audit_bucket(bucket_dir())
        healed_by_id = {t.trace_id: t for t in healed.traces}
        assert phantom_id not in healed_by_id[trace_id].companion_node_ids


class TestMirrorSyncFailureSurfaced:
    def test_swallowed_sync_failure_is_surfaced_and_not_half_applied(
        self, tmp_path, monkeypatch
    ):
        world = _World(tmp_path)
        codex_id = _make_codex_recover_case(world)

        plan = plan_backfill()
        assert codex_id in {a.trace_id for a in plan.codex_recover}

        def _boom(*args, **kwargs):
            raise RuntimeError("simulated sync_events_mirror failure")

        # ``_apply_codex_recover`` does a LOCAL ``from .bucket_events import
        # sync_events_mirror`` at call time, so the patch target is the
        # bucket_events module's own name, not context_backfill's namespace.
        monkeypatch.setattr(
            "opentraces.core.bucket_events.sync_events_mirror", _boom
        )

        result = apply_backfill(plan, project_paths={world.slug: world.project_dir})

        # Must be surfaced -- never silently absorbed.
        assert result.get("mirror_sync_failures"), (
            f"sync_events_mirror failure was swallowed: {result!r}"
        )
        assert result["mirror_sync_failures"][0]["trace_id"] == codex_id

        # Must NOT be half-applied: writing the companion now (while the
        # shared mirror never learned about the new nodes) would leave a
        # permanent companion/mirror divergence with no planned repair.
        assert not any(r["trace_id"] == codex_id for r in result["codex_recover"])

        after = audit_bucket(bucket_dir())
        after_by_id = {t.trace_id: t for t in after.traces}
        assert after_by_id[codex_id].classification == "legitimately_empty", (
            "a failed mirror sync must leave the trace untouched, ready for "
            "a clean retry -- never a companion that has outrun the mirror"
        )


# --------------------------------------------------------------------------- #
# Real-bucket apply crash follow-up (PR #217): the reproject phase's
# full-mirror strict materialization (list(read_events_mirror_batches()))
# crashed the whole apply on ONE truncated non-context line, before writing
# anything. The scoped reader must (a) skip-but-COUNT corrupt non-context
# lines, (b) hard-block on corrupt context-looking lines (skip-is-a-lie),
# and (c) never materialize the whole mirror through pydantic.
# --------------------------------------------------------------------------- #


def _seed_corrupt_batch(name: str, line: str) -> Path:
    """Write a valid-gzip mirror batch whose single line is corrupt JSON."""
    batches = bucket_dir() / "events" / "v1" / "batches"
    batches.mkdir(parents=True, exist_ok=True)
    path = batches / name
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(line + "\n")
    return path


# Mimics the real-bucket defect: an attribution event truncated mid-string
# ("EOF while parsing a string"). No context event-type marker, no planned
# trace_id substring.
_TRUNCATED_NON_CONTEXT_LINE = (
    '{"schema_version": "opentraces.trail_event.v1", '
    '"event_type": "file_attribution", "ATTRIBUTION_VERSION": "1.0", '
    '"payload": {"text": "truncated mid-str'
)

# A truncated CONTEXT event line -- the data a reproject may NEED.
_TRUNCATED_CONTEXT_LINE = (
    '{"schema_version": "opentraces.trail_event.v1", '
    '"event_type": "context_node_observed", '
    '"payload": {"node_id": "sha256:feedfeed", "trace_id": "trace-somewhere", '
    '"content": "truncated mid-str'
)


class TestMirrorCorruptionPolicyOnApply:
    def test_corrupt_non_context_line_is_counted_not_fatal(self, tmp_path):
        """Apply must survive the real-bucket shape: one truncated
        attribution line in the mirror, with real reproject + drop_dangling
        work planned. The corrupt line is skipped but surfaced."""
        world = _World(tmp_path)
        trace_id, legit_ids, dangling_id = _make_mixed_defect_case(world)
        corrupt_batch = _seed_corrupt_batch(
            "999999999999-corrupt-nonctx.jsonl.gz", _TRUNCATED_NON_CONTEXT_LINE
        )
        assert corrupt_batch.exists()

        plan = plan_backfill()
        assert trace_id in {a.trace_id for a in plan.reproject}
        assert trace_id in {a.trace_id for a in plan.drop_dangling}

        # RED against the full-mirror strict reader: this raised a pydantic
        # ValidationError on the truncated line before writing anything.
        result = apply_backfill(plan, project_paths={world.slug: world.project_dir})

        corrupt = result.get("mirror_corrupt_lines")
        assert corrupt, (
            f"corrupt mirror line was not surfaced in the apply report: {result!r}"
        )
        assert corrupt[0]["batch"] == "999999999999-corrupt-nonctx.jsonl.gz"
        assert corrupt[0]["line"] == 1

        # And the planned work actually healed despite the corruption.
        healed = audit_bucket(bucket_dir())
        healed_by_id = {t.trace_id: t for t in healed.traces}
        assert healed_by_id[trace_id].classification == "consistent"
        assert dangling_id not in healed_by_id[trace_id].stamped_ids
        assert set(legit_ids) <= set(healed_by_id[trace_id].companion_node_ids)

    def test_corrupt_context_line_blocks_apply_before_any_write(self, tmp_path):
        """A corrupt line that LOOKS like a context event may be the very
        data the reproject needs -- blocking, fail-closed, zero writes,
        and the error names the batch file."""
        world = _World(tmp_path)
        trace_id, legit_ids, dangling_id = _make_mixed_defect_case(world)

        # Plan BEFORE the corruption lands (the audit would otherwise block
        # planning already -- this test targets the APPLY-path guard).
        plan = plan_backfill()
        assert trace_id in {a.trace_id for a in plan.reproject}
        bytes_before = _companion_bytes(world.slug, trace_id)
        trace_json = (
            bucket_dir() / "traces" / "v1" / world.slug / trace_id / "trace.json"
        )
        trace_json_before = trace_json.read_bytes()

        _seed_corrupt_batch(
            "999999999999-corrupt-ctx.jsonl.gz", _TRUNCATED_CONTEXT_LINE
        )

        # RED discriminator: the old reader also raised here, but as a bare
        # pydantic ValidationError that never named the batch file. The
        # hardened reader raises a ValueError naming it.
        with pytest.raises(ValueError, match="corrupt-ctx"):
            apply_backfill(plan, project_paths={world.slug: world.project_dir})

        # Fail-closed: zero writes happened.
        assert _companion_bytes(world.slug, trace_id) == bytes_before
        assert trace_json.read_bytes() == trace_json_before

    def test_corrupt_context_line_blocks_the_audit_too(self, tmp_path):
        """Same skip-is-a-lie rule at the audit/plan level: a corrupt
        context-looking line means the mirror's context truth is unreadable
        -- the audit must block, not silently under-count."""
        _ensure_empty_valid_bucket()
        _seed_corrupt_batch(
            "999999999999-corrupt-ctx.jsonl.gz", _TRUNCATED_CONTEXT_LINE
        )
        with pytest.raises(ValueError, match="corrupt-ctx"):
            audit_bucket(bucket_dir())

    def test_corrupt_non_context_line_does_not_block_the_audit(self, tmp_path):
        """The dual of the above: a truncated NON-context line (the real
        bucket's actual defect) must not block planning -- the audit's
        subject is context truth only."""
        _ensure_empty_valid_bucket()
        _seed_corrupt_batch(
            "999999999999-corrupt-nonctx.jsonl.gz", _TRUNCATED_NON_CONTEXT_LINE
        )
        report = audit_bucket(bucket_dir())
        assert report.total_traces == 0
