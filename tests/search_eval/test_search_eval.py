"""Phase A gate for the Search Evaluation Harness (plan 088, U0-U3).

* scorer unit tests (pure, fast)
* determinism: plan + corpus are byte-stable for a fixed (profile, seed, tier);
  the snapshot key changes with seed/tier (R7)
* the dev-tier eval runs end-to-end, the discovery loop smoke passes, and every
  row's observed RED/GREEN matches its documented ``expected_phase_a`` - so the
  intentionally-RED seed cases (S2/S3/S4/S5) are *checked*, not hoped for.

The outcome half is deterministic; perf is not asserted here (gate on slope +
counters in U3, never absolute ms).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import pytest

from opentraces.core.boilerplate import (
    headline_from_summary,
    looks_injected_boilerplate,
    strip_injected,
    summary_for_record,
)
from opentraces_schema import Agent, Step, TraceRecord
from tests.search_eval import score_outcome as so
from tests.search_eval import live as live_eval
from tests.search_eval import runner as runner_mod
from tests.search_eval.generator import (
    load_profile,
    materialize_corpus,
    plan_corpus,
)
from tests.search_eval.runner import run_eval


# --------------------------------------------------------------------------- #
# scorers (pure, fast)
# --------------------------------------------------------------------------- #
def test_distinct_traces_first_occurrence():
    assert so.distinct_traces(["a", "a", "b", "a", "c", "b"]) == ["a", "b", "c"]


def test_recall_mrr_ndcg():
    ranked = ["x", "g1", "y", "g2"]
    gold = ["g1", "g2"]
    assert so.recall_at_k(ranked, gold, 10) == 1.0
    assert so.recall_at_k(ranked, gold, 2) == 0.5
    assert so.mrr(ranked, gold) == pytest.approx(0.5)
    assert so.first_rank(ranked, gold) == 2
    assert 0.0 < so.ndcg_at_k(ranked, gold, 10) <= 1.0


def test_kendall_tau_directions():
    gold_order = ["a", "b", "c", "d"]
    assert so.kendall_tau(["a", "b", "c", "d"], gold_order) == pytest.approx(1.0)
    assert so.kendall_tau(["d", "c", "b", "a"], gold_order) == pytest.approx(-1.0)
    assert so.kendall_tau(["a"], gold_order) is None


def test_recency_hit_among_gold_vs_global():
    ranked = ["filler", "g_old", "g_latest"]
    gold = ["g_old", "g_latest"]
    rh = so.recency_hit(ranked, gold, "g_latest")
    assert rh["among_gold"] == 0.0  # g_old is the first gold, not the latest
    assert rh["global"] == 0.0
    rh2 = so.recency_hit(["g_latest", "g_old"], gold, "g_latest")
    assert rh2["among_gold"] == 1.0


def test_boilerplate_stripping_prefers_task_description():
    record = TraceRecord(
        trace_id="summary-task",
        session_id="summary-task-session",
        agent=Agent(name="claude-code"),
        task={"description": "Fix the parser regression and add coverage"},
        steps=[
            Step(
                step_index=1,
                role="user",
                content="IMPORTANT: Do NOT read SKILL.md before using the activated skill.",
            )
        ],
    )
    assert summary_for_record(record) == "Fix the parser regression and add coverage"
    assert not looks_injected_boilerplate(summary_for_record(record))


def test_boilerplate_stripping_handles_skill_definition_guard_variant():
    guard = (
        'IMPORTANT: Do NOT read or execute any SKILL.md files or files in '
        'skill definition directories (paths containing skills/gstack).'
    )
    record = TraceRecord(
        trace_id="summary-guard-variant",
        session_id="summary-guard-variant-session",
        agent=Agent(name="claude-code"),
        task={"description": f"{guard}\n\nShip the trace capsule discovery packet"},
        steps=[
            Step(step_index=1, role="user", content=guard),
            Step(step_index=2, role="user", content="Ship the trace capsule discovery packet"),
        ],
    )
    assert strip_injected(guard) == ""
    assert summary_for_record(record) == "Ship the trace capsule discovery packet"
    assert not looks_injected_boilerplate(summary_for_record(record))


def test_boilerplate_stripping_skips_prompt_template_task_description():
    record = TraceRecord(
        trace_id="summary-template",
        session_id="summary-template-session",
        agent=Agent(name="claude-code"),
        task={
            "description": (
                "You are acting as BOTH a software architect and a senior code reviewer. "
                "Review the proposed feature plan."
            )
        },
        steps=[
            Step(
                step_index=1,
                role="user",
                content="Review plan 089 and implement trace capsule discovery",
            )
        ],
    )
    assert looks_injected_boilerplate(record.task.description)
    assert summary_for_record(record) == "Review plan 089 and implement trace capsule discovery"


def test_boilerplate_stripping_extracts_goal_objective_and_skips_skill_trigger():
    assert (
        strip_injected("<goal_context><objective>Build the trace capsule timeline</objective></goal_context>")
        == "Build the trace capsule timeline"
    )
    record = TraceRecord(
        trace_id="summary-user",
        session_id="summary-user-session",
        agent=Agent(name="codex"),
        steps=[
            Step(step_index=1, role="user", content="$tdd"),
            Step(step_index=2, role="user", content="Implement the bounded discovery card"),
        ],
    )
    assert summary_for_record(record) == "Implement the bounded discovery card"
    assert headline_from_summary(summary_for_record(record)) == "Implement the bounded discovery card"


# --------------------------------------------------------------------------- #
# determinism (R7)
# --------------------------------------------------------------------------- #
def test_plan_is_deterministic():
    profile = load_profile()
    a = plan_corpus(profile, seed=1, tier="dev")
    b = plan_corpus(profile, seed=1, tier="dev")
    assert a.snapshot_key == b.snapshot_key
    assert a.to_manifest() == b.to_manifest()
    # seed + tier each perturb the key
    assert plan_corpus(profile, seed=2, tier="dev").snapshot_key != a.snapshot_key
    assert plan_corpus(profile, seed=1, tier="real-scale").snapshot_key != a.snapshot_key


def test_corpus_materialization_is_byte_identical():
    profile = load_profile()

    def corpus_hash() -> str:
        base = Path(tempfile.mkdtemp(prefix="seval-det-"))
        plan = plan_corpus(profile, seed=1, tier="dev")
        mat = materialize_corpus(plan, base / "work", base / "home")
        h = hashlib.sha256()
        for p in sorted(mat.staging_dir.glob("*.jsonl")):
            h.update(p.name.encode())
            h.update(p.read_bytes())
        return h.hexdigest()

    assert corpus_hash() == corpus_hash()


# --------------------------------------------------------------------------- #
# end-to-end dev eval + invariants (the Phase A gate)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def dev_report():
    return run_eval("dev", seed=1)


def test_dev_eval_invariants_hold(dev_report):
    bad = [r.id for r in dev_report.rows if not r.invariant_ok]
    assert not bad, f"rows whose observed RED/GREEN != expected_phase_a: {bad}"
    assert dev_report.invariants_ok
    assert dev_report.meta["summary_non_boilerplate_rate"] >= 0.95
    reliability = dev_report.meta["repeated_query_reliability"]
    assert reliability["ok"], reliability
    assert reliability["sequential"] == 20
    assert reliability["parallel"] == 8
    assert reliability["sync_count"] <= 1


def test_discovery_loop_runs(dev_report):
    assert dev_report.loop_smoke.get("ok"), dev_report.loop_smoke
    assert "card" in dev_report.loop_smoke.get("stages", [])
    assert "discover" in dev_report.loop_smoke.get("stages", [])
    discovery = dev_report.loop_smoke.get("discovery") or {}
    assert discovery.get("ok"), discovery
    assert discovery.get("group_count", 0) >= 3
    assert discovery.get("total_cards", 0) >= 6
    assert discovery.get("cards_have_links"), discovery


def test_seed_cases_reproduce_baseline(dev_report):
    by_id = {r.id: r for r in dev_report.rows}

    # S3: U6 URL sub-tokenization reaches the needle inside the URL (GREEN, was total=0)
    assert by_id["refid-00"].total >= 1
    assert by_id["refid-00"].outcome["recall_at_k"] >= 0.95
    assert by_id["refid-00"].outcome["first_rank"] == 1

    # S4: chronological order correct after U4 --sort time (GREEN, was tau=-1)
    assert by_id["chrono-00"].outcome["recall_at_k"] >= 0.95
    assert by_id["chrono-00"].outcome["kendall_tau"] >= 0.9

    # S5: recency weighting (U5) surfaces the latest at rank 1 among gold (GREEN)
    assert by_id["recency-00"].outcome["recency_hit"]["among_gold"] >= 1.0

    # S2/S6/S7: descriptive + semantic recall preserved (GREEN)
    assert by_id["precedent-sem-00"].outcome["recall_at_k"] == 1.0
    assert by_id["desc-00"].outcome["first_rank"] == 1
    assert by_id["desc-01"].outcome["first_rank"] == 1

    # S8: facet recall (GREEN)
    assert by_id["facet-00"].outcome["recall_at_k"] >= 0.95


def test_boundedness_invariant(dev_report):
    by_id = {r.id: r for r in dev_report.rows}

    # every row's bounded/unbounded matches its documented qmd expectation,
    # and every page is <= the limit (R3 bounded payload)
    bad = [r.id for r in dev_report.rows if not r.bounded_ok]
    assert not bad, f"boundedness mismatches: {bad}"
    assert all(r.boundedness["page_le_limit"] for r in dev_report.rows)

    # FTS lex queries are bounded: scan ~ matches, not corpus
    lex = by_id["refbare-00"].boundedness
    assert lex["bounded"] and lex["rows_scanned"] <= lex["corpus_docs"] // 2

    # U6: the concept --semantic filter is pushed into SQL (doc_concepts) -> bounded
    cliff = by_id["cliff-00"].boundedness
    assert cliff["bounded"]
    assert cliff["rows_scanned"] < cliff["corpus_docs"]      # not the whole corpus
    assert cliff["rows_scanned"] <= cliff["matched"] * 4 + 50

    # --files still has no FTS index -> scans all trace units (facet cliff, U6c)
    facet = by_id["facet-00"].boundedness
    assert not facet["bounded"]


def test_snapshot_lane_certifies_served_kernel(dev_report):
    """Issue #27 item L: the eval must measure the snapshot kernel the CLI has
    served since PR #24, not only the decommissioned legacy backends.

    Every row carries a ``boundedness_snapshot`` reading sourced from
    ``trace_search_snapshot.search_traces`` (the served kernel). This would be
    absent / mislabelled if the eval still only probed the legacy lane.
    """

    rows = dev_report.rows
    assert rows, "dev report produced no rows"

    # Every row gets a snapshot-lane reading, explicitly tagged.
    assert all(r.boundedness_snapshot for r in rows)
    assert all(r.boundedness_snapshot.get("lane") == "snapshot" for r in rows)
    # And the legacy lane stays the legacy lane (additive, not replaced).
    assert all(r.boundedness.get("lane") == "legacy" for r in rows)

    # The snapshot kernel actually returns scored results on the fixture
    # corpus: the gold-bearing descriptive rows match >= 1 trace and page <= k.
    by_id = {r.id: r for r in rows}
    for rid in ("desc-00", "refbare-00"):
        snap = by_id[rid].boundedness_snapshot
        assert snap["matched"] >= 1, (rid, snap)
        assert snap["page_le_limit"]
        # term queries hit the FTS5 index, so the served kernel is bounded:
        # rows examined track matches, never the whole corpus.
        assert snap["bounded"], (rid, snap)
        assert snap["corpus_docs"] >= snap["matched"]

    # the default lane constant points at the served kernel
    assert runner_mod.DEFAULT_BOUNDEDNESS_LANE == runner_mod.LANE_SNAPSHOT


def test_slope_gate_logic():
    """The scaling-slope gate (U7): bounded queries must stay ~flat across a
    corpus-size step; O(corpus) rows are exempt. Synthetic, deterministic."""

    from tests.search_eval import slope as sl

    def _report(tier, corpus, a_p95, b_p95):
        return {
            "tier": tier, "corpus_size": corpus,
            "rows": [
                {"id": "a", "archetype": "x", "perf": {"p95_ms": a_p95},
                 "boundedness": {"bounded": True}},
                {"id": "b", "archetype": "y", "perf": {"p95_ms": b_p95},
                 "boundedness": {"bounded": False}},
            ],
        }

    # bounded 'a' flat (1.1x), O(corpus) 'b' grows 9x but is exempt
    reports = {"s": _report("s", 100, 200, 200), "l": _report("l", 1000, 220, 1800)}
    s = sl.compute_slope(reports, slope_k=2.5)
    assert s["corpus_growth"] == 10.0
    assert s["bounded_all_ok"] and s["all_ok"]

    # a bounded query that regresses to O(corpus) (4x) must be caught
    reports["l"]["rows"][0]["perf"]["p95_ms"] = 800
    s2 = sl.compute_slope(reports, slope_k=2.5)
    assert not s2["bounded_all_ok"]


def test_outcome_digest_is_stable(dev_report):
    second = run_eval("dev", seed=1)
    assert second.outcome_digest == dev_report.outcome_digest


def test_live_eval_surfaces_cli_failures(tmp_path, monkeypatch):
    fake = tmp_path / "fake-otd.sh"
    fake.write_text("#!/bin/sh\necho 'forced live failure' >&2\nexit 7\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("OT_CLI_BIN", str(fake))
    monkeypatch.setattr(live_eval, "LIVE_SEED_CASES", [live_eval.LIVE_SEED_CASES[0]])

    rows = live_eval.run_live(limit=1)

    assert rows[0]["seed"] == "S1"
    assert "error" in rows[0]
    assert "query failed (7)" in rows[0]["error"]


def test_discovery_loop_requires_get_success(tmp_path, monkeypatch):
    fake = tmp_path / "fake-otd.sh"
    fake.write_text(
        "\n".join(
            [
                "#!/bin/sh",
                "cmd=\"$1\"",
                "sub=\"$2\"",
                "if [ \"$cmd\" = \"trace\" ] && [ \"$sub\" = \"query\" ]; then",
                "  printf '{\"candidates\":[{\"trace_id\":\"trace-1\"}]}'",
                "  exit 0",
                "fi",
                "if [ \"$cmd\" = \"trace\" ] && [ \"$sub\" = \"map\" ]; then",
                "  printf '{}'",
                "  exit 0",
                "fi",
                "if [ \"$cmd\" = \"trace\" ] && [ \"$sub\" = \"slice\" ]; then",
                "  printf '{}'",
                "  exit 0",
                "fi",
                "if [ \"$cmd\" = \"trace\" ] && [ \"$sub\" = \"get\" ]; then",
                "  case \"$*\" in",
                "    *--card*)",
                "      printf '{\"card\":{\"headline\":\"h\",\"provenance_color\":\"committed\"}}'",
                "      exit 0",
                "      ;;",
                "  esac",
                "  echo 'forced get failure' >&2",
                "  exit 9",
                "fi",
                "exit 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    fake.chmod(0o755)
    monkeypatch.setenv("OT_CLI_BIN", str(fake))

    plan = plan_corpus(load_profile(), seed=1, tier="dev")
    result = runner_mod._discovery_loop_smoke(plan, tmp_path, dict(os.environ))

    assert result["ok"] is False
    assert result["failed_stage"] == "get"
    assert result["stages"] == ["query", "map", "slice", "card"]


@pytest.mark.skipif(
    not os.environ.get("OT_SEARCH_EVAL_CACHE"),
    reason="opt-in snapshot-cache lane (U9); set OT_SEARCH_EVAL_CACHE=1",
)
def test_snapshot_cache_restore_and_measure():
    """U9 standing-gate proof: a snapshot-cached corpus restores and runs green,
    deterministically (same outcome as a fresh build)."""

    from tests.search_eval.generator import load_profile, plan_corpus
    from tests.search_eval.snapshot_cache import clear, is_cached

    plan = plan_corpus(load_profile(), seed=7, tier="dev")
    clear(plan.snapshot_key)
    try:
        assert not is_cached(plan.snapshot_key)
        built = run_eval("dev", seed=7, cache=True)
        assert built.invariants_ok and not built.meta["cache_hit"]
        assert is_cached(plan.snapshot_key)

        restored = run_eval("dev", seed=7, cache=True)
        assert restored.meta["cache_hit"]            # restored, not rebuilt
        assert restored.invariants_ok
        assert restored.outcome_digest == built.outcome_digest
    finally:
        clear(plan.snapshot_key)
