from __future__ import annotations

import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNEYS_PATH = REPO_ROOT / "tests" / "perf" / "journeys.toml"
SCENARIOS_DIR = REPO_ROOT / "tests" / "perf" / "scenarios"


def _load_journeys() -> list[dict]:
    with JOURNEYS_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    return list(data.get("journeys", []))


def _scenario_names() -> set[str]:
    names: set[str] = set()
    for path in SCENARIOS_DIR.glob("*.toml"):
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        names.add(data.get("name") or path.stem)
    return names


def _assert_pytest_target_exists(target: str) -> None:
    parts = target.split("::")
    file_path = REPO_ROOT / parts[0]
    assert file_path.is_file(), f"referenced test target missing file: {target}"
    text = file_path.read_text()
    for symbol in parts[1:]:
        needle = f"def {symbol}("
        class_needle = f"class {symbol}"
        assert needle in text or class_needle in text, (
            f"referenced test target missing symbol {symbol!r}: {target}"
        )


def test_journey_manifest_is_consistent() -> None:
    journeys = _load_journeys()
    assert journeys, "tests/perf/journeys.toml must define at least one journey"

    ids = [journey["id"] for journey in journeys]
    assert len(ids) == len(set(ids)), "journey ids must be unique"

    known_scenarios = _scenario_names()
    referenced_scenarios: set[str] = set()

    for journey in journeys:
        assert journey["priority"] in {"primary", "secondary"}
        assert isinstance(journey["profiled"], bool)
        assert journey["doc_refs"], f"{journey['id']} must reference at least one doc/source file"
        assert isinstance(journey["notes"], str) and journey["notes"].strip()

        for doc_ref in journey["doc_refs"]:
            assert (REPO_ROOT / doc_ref).is_file(), (
                f"{journey['id']} references missing doc/source file: {doc_ref}"
            )

        perf_scenarios = list(journey.get("perf_scenarios", []))
        if journey["profiled"]:
            assert perf_scenarios, f"profiled journey {journey['id']} must map to perf scenarios"
        for name in perf_scenarios:
            assert name in known_scenarios, f"{journey['id']} references unknown perf scenario {name}"
            referenced_scenarios.add(name)

        for target in journey.get("user_smoke_tests", []):
            _assert_pytest_target_exists(target)
        for target in journey.get("live_tests", []):
            _assert_pytest_target_exists(target)

    assert referenced_scenarios == known_scenarios, (
        "every perf scenario should be mapped into at least one documented journey; "
        f"missing={sorted(known_scenarios - referenced_scenarios)}"
    )
