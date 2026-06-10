"""Claims-map contract: J1-J18 binding to verifier journeys (otbox 2.0 P3)."""

from __future__ import annotations

from .claims_map import SPEC_JOURNEYS, spec_status
from .journey import available_journeys


def test_every_spec_journey_has_existing_verifiers():
    names = {j["name"] for j in available_journeys()}
    for key, spec in SPEC_JOURNEYS.items():
        assert spec["journeys"], f"{key} maps to no journeys"
        unknown = [j for j in spec["journeys"] if j not in names]
        assert not unknown, f"{key} maps to unknown journeys: {unknown}"


def test_all_18_spec_journeys_present():
    assert len(SPEC_JOURNEYS) == 18
    assert all(k.startswith("J") for k in SPEC_JOURNEYS)


def test_spec_status_is_weakest_link():
    assert spec_status(["PASS", "PASS"]) == "verified"
    assert spec_status(["PASS", "SKIP"]) == "partial"
    assert spec_status(["SKIP", "PENDING"]) == "pending"
    assert spec_status([]) == "pending"
