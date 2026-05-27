"""Tests for the offline SkillOpt slice (arXiv 2605.23904).

Covers the edit-engine, budget schedules, the strict validation gate +
rejected-edit buffer, the proxy selection scorer, the deterministic proposer,
the `skill-opt-v1` workflow's scored-rollout projection over a seeded bucket,
and the `opentraces workflow optimize --dry-run` artifact contract.
"""

from __future__ import annotations

import json

from click.testing import CliRunner
from opentraces_schema import Agent, Step, TraceRecord

from opentraces.consumers.skill_opt import engine as so
from opentraces.consumers.skill_opt import proposers as opt


# ---------------------------------------------------------------------------
# 1. Patch grammar + protected region
# ---------------------------------------------------------------------------

PROTECTED_SKILL = "# S\n\nbody\n\n" + so.SLOW_START + "\nPROTECTED\n" + so.SLOW_END + "\n"


def test_append_lands_before_protected_region():
    doc, results = so.apply_edits(PROTECTED_SKILL, [{"op": "append", "content": "- rule[a]: x"}])
    assert results[0].applied is True
    assert "- rule[a]: x" in doc
    assert doc.index("- rule[a]: x") < doc.index(so.SLOW_START)
    assert "PROTECTED" in doc  # untouched


def test_insert_after_and_delete_and_replace():
    base = "# Title\n\nalpha beta\n"
    doc, r = so.apply_edits(base, [{"op": "insert_after", "target": "# Title", "content": "intro"}])
    assert "# Title\nintro" in doc and r[0].applied
    doc, r = so.apply_edits(base, [{"op": "replace", "target": "alpha", "content": "ALPHA"}])
    assert "ALPHA beta" in doc and r[0].applied
    doc, r = so.apply_edits(base, [{"op": "delete", "target": "alpha "}])
    assert "beta" in doc and "alpha" not in doc and r[0].applied


def test_missing_target_and_unknown_op_are_skipped():
    _, r = so.apply_edits("x", [{"op": "replace", "target": "zzz", "content": "y"}])
    assert r[0].applied is False and "not found" in r[0].reason
    _, r = so.apply_edits("x", [{"op": "frobnicate"}])
    assert r[0].applied is False and "unknown op" in r[0].reason


def test_step_edits_cannot_mutate_protected_region():
    for edit in (
        {"op": "replace", "target": "PROTECTED", "content": "X"},
        {"op": "delete", "target": "PROTECTED"},
        {"op": "insert_after", "target": "PROTECTED", "content": "X"},
    ):
        doc, r = so.apply_edits(PROTECTED_SKILL, [edit])
        assert r[0].applied is False and "protected" in r[0].reason
        assert doc == PROTECTED_SKILL


def test_step_edits_cannot_mutate_protected_markers():
    # the markers themselves are protected, not just the body between them
    for edit in (
        {"op": "delete", "target": so.SLOW_START},
        {"op": "delete", "target": so.SLOW_END},
        {"op": "replace", "target": so.SLOW_START, "content": "X"},
        {"op": "replace", "target": so.SLOW_END, "content": "X"},
        {"op": "insert_after", "target": so.SLOW_START, "content": "SNUCK_IN"},
    ):
        doc, r = so.apply_edits(PROTECTED_SKILL, [edit])
        assert r[0].applied is False and "protected" in r[0].reason, edit
        assert doc == PROTECTED_SKILL, edit
        assert "SNUCK_IN" not in doc


def test_insert_after_just_before_start_marker_is_allowed():
    # inserting after text that ends exactly at the start marker is outside the
    # protected span and must still work
    base = "# S\n\nintro\n" + so.SLOW_START + "\nP\n" + so.SLOW_END + "\n"
    doc, r = so.apply_edits(base, [{"op": "insert_after", "target": "intro", "content": "added"}])
    assert r[0].applied is True
    assert "added" in doc and doc.index("added") < doc.index(so.SLOW_START)


def test_slow_update_overwrites_and_creates_region():
    out = so.apply_slow_update(PROTECTED_SKILL, "NEW GUIDANCE")
    assert "NEW GUIDANCE" in out and "PROTECTED" not in out
    span = so._protected_span(out)
    assert span is not None
    created = so.apply_slow_update("# bare\n", "G")
    assert so.SLOW_START in created and "G" in created


# ---------------------------------------------------------------------------
# 2. Budget schedules
# ---------------------------------------------------------------------------


def test_cosine_schedule_decays_base_to_floor():
    assert so.budget_at(0, 8, 4, 2, "cosine") == 4
    assert so.budget_at(8, 8, 4, 2, "cosine") == 2
    mid = so.budget_at(4, 8, 4, 2, "cosine")
    assert 2 <= mid <= 4


def test_constant_and_linear_schedules():
    assert so.budget_at(3, 8, 5, 2, "constant") == 5
    assert so.budget_at(0, 8, 4, 2, "linear") == 4
    assert so.budget_at(8, 8, 4, 2, "linear") == 2


def test_clip_to_budget_keeps_top_l():
    assert so.clip_to_budget([1, 2, 3, 4, 5], 3) == [1, 2, 3]
    assert so.clip_to_budget([1, 2], 9) == [1, 2]


# ---------------------------------------------------------------------------
# 3. Validation gate + rejected-edit buffer + score cache
# ---------------------------------------------------------------------------


def test_strict_gate_rejects_tie_and_buffers():
    state = so.SkillOptState(current_skill="a")
    state.current_score = 0.5
    state.best_score = 0.5
    # equal score -> rejected (strict gate)
    out = state.propose_and_test("b", lambda s: 0.5, edits=[{"op": "append", "content": "b"}])
    assert out["accepted"] is False
    assert len(state.rejected_buffer) == 1
    assert state.rejected_buffer[0]["edits"]


def test_higher_accepted_becomes_best_lower_rejected():
    state = so.SkillOptState(current_skill="a")
    state.current_score = 0.4
    state.best_score = 0.4
    hi = state.propose_and_test("b", lambda s: 0.7)
    assert hi["accepted"] and state.best_skill == "b" and state.best_score == 0.7
    lo = state.propose_and_test("c", lambda s: 0.6)  # lower than current 0.7
    assert lo["accepted"] is False
    assert state.best_skill == "b"  # best preserved


def test_score_cache_reused():
    calls = {"n": 0}

    def ev(_s: str) -> float:
        calls["n"] += 1
        return 0.9

    state = so.SkillOptState(current_skill="a")
    state.prime(ev)
    state.propose_and_test("a", ev)  # same hash as current -> cache hit
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 4. Split + proxy scorer + proposer
# ---------------------------------------------------------------------------


def test_split_is_deterministic_and_stable():
    rows = [so.RolloutRow(f"t{i}", 0.5, failure_tags=("x",)) for i in range(20)]
    a_train, a_sel = so.split_rows_by_hash(rows, selection_fraction=0.4, seed="s")
    b_train, b_sel = so.split_rows_by_hash(rows, selection_fraction=0.4, seed="s")
    assert [r.trace_id for r in a_sel] == [r.trace_id for r in b_sel]
    assert len(a_sel) + len(a_train) == 20
    # different seed -> generally different partition
    _, c_sel = so.split_rows_by_hash(rows, selection_fraction=0.4, seed="other")
    assert {r.trace_id for r in c_sel} != set() or {r.trace_id for r in a_sel} != set()


def test_proxy_score_rises_with_coverage():
    rows = [so.RolloutRow("t1", 0.5, failure_tags=("rl.A", "tr.B"))]
    low = so.score_skill_on_rows("# nothing\n", rows)
    covered = "# s\n- " + so.RULE_MARKER.format(tag="rl.A") + "\n"
    mid = so.score_skill_on_rows(covered, rows)
    full = so.score_skill_on_rows(
        covered + "- " + so.RULE_MARKER.format(tag="tr.B") + "\n", rows
    )
    assert low < mid < full


def test_default_proposer_ranks_and_skips_covered():
    rows = [
        so.RolloutRow("t1", 0.4, failure_tags=("rl.A", "tr.B")),
        so.RolloutRow("t2", 0.5, failure_tags=("rl.A",)),
    ]
    edits = opt.default_proposer("# empty\n", rows, 4)
    # rl.A is most frequent -> ranked first
    assert so.RULE_MARKER.format(tag="rl.A") in edits[0]["content"]
    # already-covered tags are skipped
    skill_with_a = "- " + so.RULE_MARKER.format(tag="rl.A")
    edits2 = opt.default_proposer(skill_with_a, rows, 4)
    assert all("rl.A" not in e["content"] for e in edits2)


# ---------------------------------------------------------------------------
# 5. Loop + export
# ---------------------------------------------------------------------------


def _rows_for_loop():
    # rl.A appears in every row (and thus the selection split); tr.ONLY appears
    # in just one trace, so whichever split it lands in, it cannot be covered on
    # the other -> exercises a rejected/buffered step under budget=1.
    return [
        so.RolloutRow("aaa", 0.4, failure_tags=("rl.A", "tr.B")),
        so.RolloutRow("bbb", 0.5, failure_tags=("rl.A",)),
        so.RolloutRow("ccc", 0.3, failure_tags=("rl.A", "tr.B")),
        so.RolloutRow("ddd", 0.6, failure_tags=("rl.A", "tr.ONLY")),
        so.RolloutRow("eee", 0.45, failure_tags=("rl.A", "tr.B")),
    ]


def test_run_optimization_improves_and_exports(tmp_path):
    res = so.run_optimization(
        "# init\n", _rows_for_loop(), propose=opt.default_proposer,
        budget=1, budget_floor=1, schedule="constant", max_steps=8,
    )
    assert res.best_score > res.initial_score
    assert res.accepted >= 1
    artifacts = res.state.export(tmp_path / "out")
    best = (tmp_path / "out" / "best_skill.md").read_text(encoding="utf-8")
    assert best == res.best_skill
    report = json.loads((tmp_path / "out" / "edit_apply_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "opentraces.skill_opt.report.v1"
    assert report["accepted_edits"] == res.accepted
    assert "steps" in report and "rejected_buffer" in report


# ---------------------------------------------------------------------------
# 6. Workflow projection over a seeded bucket
# ---------------------------------------------------------------------------


def _trace(trace_id: str, content: str) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": content},
        steps=[Step(step_index=1, role="user", content=content)],
        outcome={"success": True, "committed": False},
    )


def _seed_bucket(n: int = 4) -> None:
    from opentraces.core.bucket_store import write_trace_record

    for i in range(n):
        write_trace_record(
            _trace(f"{i:08d}-1111-4111-8111-111111111111", f"Fix failing test {i}"),
            project_slug="demo",
            source_layer="canonical",
            legacy_mirror=False,
        )


def test_skill_opt_workflow_emits_scored_rollout_rows(tmp_path):
    from opentraces.core.workflows import create_workflow
    from opentraces.core.workflow_runner import execute_workflow

    _seed_bucket(4)
    create_workflow("skill-opt-v1", template="skill-opt-v1", replace=True)

    out = tmp_path / "rows.jsonl"
    result = execute_workflow("skill-opt-v1", scope={"kind": "skill_opt"}, output_path=out)
    assert result.row_count == 4
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        assert 0.0 <= row["score"] <= 1.0
        assert 0.0 <= row["reward"] <= 1.0
        assert "outcome_committed" in row and "survival_state" in row
        assert isinstance(row["failure_tags"], list)
        assert isinstance(row["success_tags"], list)
        assert row["project_slug"] == "demo"
        parsed = so.RolloutRow.from_json(row)
        assert parsed.reward == row["reward"]


# ---------------------------------------------------------------------------
# 7. CLI: workflow optimize --dry-run writes both artifacts
# ---------------------------------------------------------------------------


def test_workflow_optimize_dry_run_writes_artifacts(tmp_path):
    from opentraces.cli import main

    _seed_bucket(5)
    out_dir = tmp_path / "skillopt-out"
    runner = CliRunner()
    res = runner.invoke(
        main,
        ["workflow", "optimize", "--dry-run", "--out", str(out_dir), "--budget", "2"],
    )
    assert res.exit_code == 0, res.output
    assert "best_skill.md" in res.output
    assert "edit_apply_report.json" in res.output
    assert (out_dir / "best_skill.md").exists()
    report = json.loads((out_dir / "edit_apply_report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "opentraces.skill_opt.report.v1"


def test_workflow_optimize_json_output(tmp_path):
    from opentraces.cli import main

    _seed_bucket(5)
    out_dir = tmp_path / "skillopt-json"
    runner = CliRunner()
    res = runner.invoke(
        main,
        ["workflow", "optimize", "--dry-run", "--json", "--out", str(out_dir)],
    )
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["status"] == "ok"
    assert payload["rollout_rows"] == 5
    assert payload["best_skill"].endswith("best_skill.md")
    assert payload["best_score"] >= payload["initial_score"]
    assert payload["promoted"] is None  # dry-run never promotes


# ---------------------------------------------------------------------------
# 8. Slow/meta update (epoch boundary) + multi-epoch
# ---------------------------------------------------------------------------


def test_default_slow_update_covers_uncovered_tags():
    rows = [so.RolloutRow("t1", 0.4, failure_tags=("rl.A", "rl.B"))]
    guidance = so.default_slow_update("# empty\n", rows)
    assert so.RULE_MARKER.format(tag="rl.A") in guidance
    assert so.RULE_MARKER.format(tag="rl.B") in guidance
    covered = "- " + so.RULE_MARKER.format(tag="rl.A") + "\n- " + so.RULE_MARKER.format(tag="rl.B")
    assert so.default_slow_update(covered, rows) == ""  # nothing left to consolidate


def test_multi_epoch_slow_update_writes_protected_region():
    # every row carries the same 4 tags, so train and selection share them
    rows = [so.RolloutRow(f"{i:08d}", 0.4 + i * 0.02, failure_tags=("rl.A", "rl.B", "rl.C", "rl.D")) for i in range(6)]
    res = so.run_optimization(
        "# init\n" + so.SLOW_START + "\n" + so.SLOW_END + "\n",
        rows,
        propose=opt.default_proposer,
        budget=1, budget_floor=1, schedule="constant",
        max_steps=1, epochs=2, slow_update=so.default_slow_update,
    )
    # a slow/meta step ran and the protected region now carries consolidated rules
    slow_steps = [r for r in res.state.step_log if r.edits and r.edits[0].get("op") == "slow_update"]
    assert slow_steps, "expected an epoch-boundary slow/meta update step"
    span = so._protected_span(res.best_skill)
    assert span is not None
    protected_body = res.best_skill[span[0]:span[1]]
    assert so.RULE_MARKER.format(tag="rl.B") in protected_body  # consolidated into protected region
    assert res.best_score > res.initial_score


# ---------------------------------------------------------------------------
# 12. Slice 3: live re-rollout gate (deterministic fake runner)
# ---------------------------------------------------------------------------


def test_fake_rerollout_reward_tracks_marker_coverage():
    from opentraces.consumers.skill_opt.rerollout import FakeReRolloutRunner, ReRolloutTask

    task = ReRolloutTask("t1", "do it", required_markers=("rl.A", "rl.B"))
    runner = FakeReRolloutRunner()
    assert runner.run("# empty\n", task).reward == 0.0
    half = "- " + so.RULE_MARKER.format(tag="rl.A")
    assert runner.run(half, task).reward == 0.5
    full = half + "\n- " + so.RULE_MARKER.format(tag="rl.B")
    assert runner.run(full, task).reward == 1.0


def test_rerollout_gate_drives_optimization():
    from opentraces.consumers.skill_opt.rerollout import (
        FakeReRolloutRunner, ReRolloutTask, make_rerollout_gate,
    )

    # held-out tasks need rules for rl.A and rl.B; the train rows surface them
    tasks = [
        ReRolloutTask("ta", "task a", required_markers=("rl.A",), source_trace_id="x"),
        ReRolloutTask("tb", "task b", required_markers=("rl.B",), source_trace_id="y"),
    ]
    gate = make_rerollout_gate(FakeReRolloutRunner(), tasks)
    rows = [so.RolloutRow(f"{i:08d}", 0.1, reward=0.1, failure_tags=("rl.A", "rl.B")) for i in range(4)]
    res = so.run_optimization(
        "# init\n", rows, propose=opt.default_proposer,
        budget=1, schedule="constant", max_steps=4, gate_fn=gate,
    )
    # the gate is the live re-roll: accepts only when re-rolled reward rises
    assert res.initial_score == 0.0  # empty skill solves neither held-out task
    assert res.best_score == 1.0     # final skill carries rules for both tasks
    assert res.accepted >= 1
    assert so.RULE_MARKER.format(tag="rl.A") in res.best_skill


def test_rerollout_gate_rejects_skill_that_does_not_help():
    from opentraces.consumers.skill_opt.rerollout import (
        FakeReRolloutRunner, ReRolloutTask, make_rerollout_gate,
    )

    # the held-out task needs rl.Z, but the rows only surface rl.A -> proposed
    # edits never raise the re-rolled reward -> every candidate is rejected
    tasks = [ReRolloutTask("tz", "task z", required_markers=("rl.Z",))]
    gate = make_rerollout_gate(FakeReRolloutRunner(), tasks)
    rows = [so.RolloutRow(f"{i:08d}", 0.1, reward=0.1, failure_tags=("rl.A",)) for i in range(3)]
    res = so.run_optimization(
        "# init\n", rows, propose=opt.default_proposer,
        budget=2, schedule="constant", max_steps=4, gate_fn=gate,
    )
    assert res.best_score == 0.0 and res.accepted == 0
    assert res.rejected >= 1  # rejected edits buffered


# ---------------------------------------------------------------------------
# 9. Consumer contract: shared run_workflow_rows + promotion
# ---------------------------------------------------------------------------


def test_run_workflow_rows_contract(tmp_path):
    from opentraces.consumers.contract import run_workflow_rows, ensure_workflow_installed

    _seed_bucket(3)
    out = tmp_path / "rows.jsonl"
    result = run_workflow_rows("skill-opt-v1", scope={"kind": "skill_opt"}, output_path=out)
    assert result.row_count == 3
    assert all("trace_id" in row for row in result.rows)
    ensure_workflow_installed("skill-opt-v1")  # idempotent second call


def test_runner_promotes_on_non_dry_run(tmp_path):
    from opentraces.consumers.skill_opt.runner import SkillOptRequest, run, promoted_skill_path

    _seed_bucket(5)
    outcome = run(SkillOptRequest(out_dir=tmp_path / "run", dry_run=False))
    assert outcome.promoted_path == promoted_skill_path("skill-opt-v1")
    assert outcome.promoted_path.exists()
    assert outcome.promoted_path.read_text(encoding="utf-8") == outcome.best_skill_path.read_text(encoding="utf-8")


def test_workflow_optimize_promotes_without_dry_run(tmp_path):
    from opentraces.cli import main

    _seed_bucket(5)
    out_dir = tmp_path / "promote"
    res = CliRunner().invoke(main, ["workflow", "optimize", "--out", str(out_dir)])
    assert res.exit_code == 0, res.output
    assert "promoted:" in res.output


# ---------------------------------------------------------------------------
# 10. Slice 2: real outcome reward + reward-aware reflection
# ---------------------------------------------------------------------------


def test_outcome_reward_orders_committed_above_uncommitted():
    committed_alive = so.outcome_reward(success=True, committed=True, survival_state="alive_on_path")
    committed_only = so.outcome_reward(success=True, committed=True)
    uncommitted = so.outcome_reward(success=None, committed=False)
    reverted = so.outcome_reward(success=True, committed=True, survival_state="reverted")
    assert committed_alive == 1.0
    assert committed_only == 0.8
    assert uncommitted == 0.0
    assert reverted < committed_only  # reverted survival penalised
    assert 0.0 <= reverted <= 1.0


def test_split_success_failure_by_reward():
    rows = [so.RolloutRow("a", 0.9, reward=0.9), so.RolloutRow("b", 0.1, reward=0.1)]
    success, failure = so.split_success_failure(rows)
    assert [r.trace_id for r in success] == ["a"]
    assert [r.trace_id for r in failure] == ["b"]


def test_failure_tags_ranked_by_reward_deficit():
    # tag X only on a high-reward trace; tag Y on a low-reward trace -> Y ranks first
    rows = [
        so.RolloutRow("hi", 0.9, reward=0.9, failure_tags=("X",)),
        so.RolloutRow("lo", 0.1, reward=0.1, failure_tags=("Y",)),
    ]
    assert so.failure_tags_of(rows)[0] == "Y"
    weights = so.tag_deficit_weights(rows)
    assert weights["Y"] > weights["X"]


def test_gate_rewards_low_reward_failure_coverage_more():
    rows = [
        so.RolloutRow("hi", 0.9, reward=0.9, failure_tags=("X",)),
        so.RolloutRow("lo", 0.1, reward=0.1, failure_tags=("Y",)),
    ]
    cover_y = "- " + so.RULE_MARKER.format(tag="Y")
    cover_x = "- " + so.RULE_MARKER.format(tag="X")
    assert so.score_skill_on_rows(cover_y, rows) > so.score_skill_on_rows(cover_x, rows)


def test_default_proposer_consumes_rejected_buffer():
    rows = [so.RolloutRow("lo", 0.1, reward=0.1, failure_tags=("rl.A", "rl.B"))]
    rejected = [{"edits": [{"content": "- " + so.RULE_MARKER.format(tag="rl.A")}]}]
    edits = opt.default_proposer("# s\n", rows, 4, rejected)
    tags = " ".join(e["content"] for e in edits)
    assert "rl.B" in tags and "rule[rl.A]" not in tags  # rejected tag skipped


# ---------------------------------------------------------------------------
# 11. Slice 2: LLM proposer chain (deterministic fake client)
# ---------------------------------------------------------------------------


def test_llm_chain_runs_all_stages_and_covers_failures():
    client = opt.DeterministicOptimizerClient()
    proposer = opt.make_llm_proposer(client)
    rows = [
        so.RolloutRow("lo1", 0.1, reward=0.1, failure_tags=("rl.A", "rl.B")),
        so.RolloutRow("lo2", 0.2, reward=0.2, failure_tags=("rl.A",)),
        so.RolloutRow("hi", 0.9, reward=0.9, failure_tags=("rl.C",)),
    ]
    edits = proposer("# s\n", rows, 4, ())
    assert edits, "chain should propose edits for failure tags"
    # all six C.2 stages were exercised, in order
    assert client.stages == [
        "failure_analysis", "success_analysis", "merge_failure",
        "merge_success", "merge_final", "ranking",
    ]
    blob = " ".join(e["content"] for e in edits)
    assert "rule[rl.A]" in blob  # highest-deficit failure tag addressed


def test_llm_chain_respects_rejected_buffer():
    client = opt.DeterministicOptimizerClient()
    proposer = opt.make_llm_proposer(client)
    rows = [so.RolloutRow("lo", 0.1, reward=0.1, failure_tags=("rl.A", "rl.B"))]
    rejected = [{"edits": [{"content": "- " + so.RULE_MARKER.format(tag="rl.A")}]}]
    edits = proposer("# s\n", rows, 4, rejected)
    blob = " ".join(e["content"] for e in edits)
    assert "rule[rl.B]" in blob and "rule[rl.A]" not in blob


def test_run_optimization_with_llm_chain_improves():
    rows = [so.RolloutRow(f"{i:08d}", 0.1, reward=0.1, failure_tags=("rl.A", "rl.B", "rl.C", "rl.D")) for i in range(6)]
    proposer = opt.make_llm_proposer(opt.DeterministicOptimizerClient())
    res = so.run_optimization("# init\n", rows, propose=proposer, budget=2, schedule="constant", max_steps=6)
    assert res.best_score > res.initial_score
    assert res.accepted >= 1


def test_empty_bucket_non_dry_run_does_not_promote(tmp_path):
    from opentraces.consumers.skill_opt.runner import SkillOptRequest, run, promoted_skill_path

    # no traces seeded -> 0 rollout rows -> must NOT overwrite the managed skill
    outcome = run(SkillOptRequest(out_dir=tmp_path / "run", dry_run=False))
    assert outcome.rollout_rows == 0
    assert outcome.promoted_path is None
    assert not promoted_skill_path("skill-opt-v1").exists()
    assert outcome.metadata["promote_skipped_reason"]


# ---------------------------------------------------------------------------
# 13. Slice 4: versioned promotion + skill-version lineage
# ---------------------------------------------------------------------------


def test_promotion_versions_and_records_lineage(tmp_path):
    import json as _json
    from opentraces.consumers.skill_opt.runner import (
        SkillOptRequest, run, promoted_skill_path, promoted_skill_dir, lineage_path,
    )

    _seed_bucket(5)
    out1 = run(SkillOptRequest(out_dir=tmp_path / "r1", dry_run=False))
    out2 = run(SkillOptRequest(out_dir=tmp_path / "r2", dry_run=False))

    assert out1.metadata["promoted_version"] == 1
    assert out2.metadata["promoted_version"] == 2
    skills_dir = promoted_skill_dir("skill-opt-v1")
    assert (skills_dir / "v1.md").exists() and (skills_dir / "v2.md").exists()
    # current.md points at the latest promotion
    assert promoted_skill_path("skill-opt-v1").read_text(encoding="utf-8") == out2.best_skill_path.read_text(encoding="utf-8")
    # lineage is an append-only chain
    records = [_json.loads(line) for line in lineage_path().read_text(encoding="utf-8").splitlines()]
    assert [r["version"] for r in records] == [1, 2]
    assert records[0]["parent_version"] is None and records[1]["parent_version"] == 1
    assert all(r["event_type"] == "skill_promoted" for r in records)
    assert all(r["schema_version"] == "opentraces.skill_opt.lineage.v1" for r in records)
