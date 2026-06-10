"""Executed-evidence ledger contract (otbox 2.0 phase 3)."""

from __future__ import annotations

from pathlib import Path

from .journey import ASSERTION_KINDS
from .ledger import (
    BEHAVIORAL_KINDS,
    SURFACE_KINDS,
    build_ledger,
    classify_assertions,
    effective_tier,
    journey_content_hash,
    ledger_summary,
)


def test_every_registered_kind_is_classified():
    """Adding an assertion kind without classifying it fails here — the
    ledger's quality math must never silently treat a kind as unknown."""
    classified = BEHAVIORAL_KINDS | SURFACE_KINDS
    unclassified = set(ASSERTION_KINDS) - classified
    assert not unclassified, f"unclassified assertion kinds: {sorted(unclassified)}"


def test_effective_tier_derives_from_evidence_only():
    rich = {"behavioral": 4, "negative": 1, "surface": 0}
    # not executed -> pending, regardless of assertion quality
    assert effective_tier("PENDING", rich) == "pending"
    assert effective_tier("FAIL", rich) == "pending"
    assert effective_tier("SKIP", rich) == "pending"
    # executed PASS without a fresh mutation kill caps at silver — no
    # journey can be effective-gold until the phase-5 mutation lane exists
    assert effective_tier("PASS", rich) == "silver"
    assert effective_tier("PASS", rich, kill_fresh=True) == "gold"
    assert effective_tier("PASS", {"behavioral": 0, "surface": 2, "negative": 0}) == "bronze"


def test_content_hash_ignores_comments_resets_on_semantics(tmp_path):
    a = tmp_path / "a.toml"
    a.write_text('name = "j"\n[[assertions]]\nkind = "returncode"\nequals = 0\n')
    b = tmp_path / "b.toml"
    b.write_text(
        '# a comment\nname = "j"\n\n[[assertions]]\nkind = "returncode"\nequals = 0\n'
    )
    c = tmp_path / "c.toml"
    c.write_text('name = "j"\n[[assertions]]\nkind = "returncode"\nequals = 1\n')
    assert journey_content_hash(a) == journey_content_hash(b)
    assert journey_content_hash(a) != journey_content_hash(c)


def test_skip_fails_rule_marks_lane_journeys_red():
    report = {"rows": [
        {"journey": "cli-lifecycle", "verdict": "SKIP", "base_checkpoint": "", "duration_s": 1.0},
    ]}
    ledger = build_ledger(report, source="test")
    rows = {r.journey: r for r in ledger.rows}
    # cli-lifecycle is a pr-lane sentinel: SKIP is red
    assert rows["cli-lifecycle"].red
    assert "graveyard" in rows["cli-lifecycle"].red_reason
    # local-agents journeys may be PENDING without redness
    local = [r for r in ledger.rows if r.ci_lane == "local-agents"]
    assert local and all(not r.red for r in local if r.verdict == "PENDING")


def test_pass_beats_skip_across_checkpoint_pairings():
    report = {"rows": [
        {"journey": "cli-lifecycle", "verdict": "SKIP", "base_checkpoint": "a", "duration_s": 0},
        {"journey": "cli-lifecycle", "verdict": "PASS", "base_checkpoint": "b", "duration_s": 2},
    ]}
    ledger = build_ledger(report, source="test")
    row = {r.journey: r for r in ledger.rows}["cli-lifecycle"]
    assert row.verdict == "PASS"
    assert not row.red


def test_ledger_summary_counts():
    ledger = build_ledger({"rows": []}, source="test")
    s = ledger_summary(ledger)
    assert s["rows"] == len(ledger.rows) > 0
    assert s["executed"] == 0
    assert s["effective_tiers"].get("pending") == s["rows"]


def test_classify_assertions_on_real_journey():
    from .journey import CATALOGUE_DIR
    counts = classify_assertions(CATALOGUE_DIR / "cli-lifecycle.toml")
    assert counts["unknown"] == 0
    assert counts["behavioral"] + counts["surface"] > 0
