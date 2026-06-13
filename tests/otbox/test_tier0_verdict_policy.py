"""Tier-0 SKIP disposition policy (issue #52).

A SKIP in the tier-0 sweep is only legitimate when it comes from the
capability gate AND every missing capability is host/opt-in-conditional
(tmux, termctrl, tier1, real_repl, live_hf, fetched_artifacts). Everything
else — missing core caps (cli/git = broken box), precondition/checkpoint
pin conflicts (authoring bugs), any future SKIP reason — is graveyard
formation and must hard-fail instead of showing as a green dot.
"""

from __future__ import annotations

from tests.otbox.journey import available_journeys
from tests.otbox.lanes import HOST_CONDITIONAL_CAPS, tier0_skip_disposition


def test_host_conditional_missing_caps_disposition_is_skip():
    assert (
        tier0_skip_disposition(
            "missing capabilities: ['tmux', 'termctrl']", {"tmux", "termctrl"}
        )
        == "skip"
    )
    # Every host-conditional cap on its own is a legitimate skip.
    for cap in sorted(HOST_CONDITIONAL_CAPS):
        assert (
            tier0_skip_disposition(f"missing capabilities: ['{cap}']", {cap})
            == "skip"
        ), cap


def test_core_capability_missing_disposition_is_fail():
    # cli/git missing means the box itself is broken — never a green skip.
    assert (
        tier0_skip_disposition("missing capabilities: ['git']", {"git"}) == "fail"
    )
    assert (
        tier0_skip_disposition(
            "missing capabilities: ['cli', 'tmux']", {"cli", "tmux"}
        )
        == "fail"
    )


def test_precondition_conflict_disposition_is_fail():
    # Checkpoint pin conflicts are catalogue-authoring drift — machine-
    # independent, never legitimate.
    assert (
        tier0_skip_disposition(
            "preconditions unsatisfied by pinned checkpoint c-foo: "
            "requires_survival_states=['reverted']",
            set(),
        )
        == "fail"
    )


def test_empty_missing_set_disposition_is_fail():
    # A capability-gate reason with no actual missing caps is incoherent —
    # treat it as graveyard formation.
    assert tier0_skip_disposition("missing capabilities: []", set()) == "fail"


def test_tier0_catalogue_requires_vocabulary_classified():
    """Sentinel: every tier-0 journey's `requires` beyond cli/git must be a
    classified host-conditional capability. A new unclassified capability in
    a tier-0 TOML fails the suite here, forcing a deliberate policy update."""
    tier0 = [j for j in available_journeys() if j["tier"] == 0]
    assert tier0, "no tier-0 journeys found — catalogue loading is broken"
    for meta in tier0:
        extra = set(meta["requires"]) - {"cli", "git"}
        assert extra <= HOST_CONDITIONAL_CAPS, (
            f"{meta['name']}: unclassified tier-0 capabilities {sorted(extra - HOST_CONDITIONAL_CAPS)}; "
            "add to HOST_CONDITIONAL_CAPS (host/opt-in) or rethink the journey's tier/lane"
        )
