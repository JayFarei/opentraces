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
import tempfile
from pathlib import Path

import pytest

from tests.search_eval import score_outcome as so
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


def test_discovery_loop_runs(dev_report):
    assert dev_report.loop_smoke.get("ok"), dev_report.loop_smoke


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

    # the concept --semantic cliff scans (nearly) the whole corpus -> O(corpus)
    cliff = by_id["cliff-00"].boundedness
    assert not cliff["bounded"]
    assert cliff["rows_scanned"] >= cliff["corpus_docs"] * 0.9
    assert cliff["matched"] < cliff["rows_scanned"]  # scans many, returns few

    # --files has no FTS -> scans all trace units (the facet cliff)
    facet = by_id["facet-00"].boundedness
    assert not facet["bounded"]


def test_outcome_digest_is_stable(dev_report):
    second = run_eval("dev", seed=1)
    assert second.outcome_digest == dev_report.outcome_digest
