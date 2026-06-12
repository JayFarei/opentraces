"""Unit tests for the B0 acceptance scorer + report schema (issue #61).

Covers (acceptance criterion 5):
  * scorer math — ``calls_vs_par``, ``arc_completed`` derivation, ``score``
    rollup;
  * report schema round-trip (``opentraces.otbox.acceptance_report.v1``);
  * each freshness predicate the gate (test_acceptance_report.py) relies on.

Deterministic-first: no real agent, no network. Everything here runs in
default CI.
"""

from __future__ import annotations

import pytest

from tests.otbox import acceptance


# ---------------------------------------------------------------------------
# scorer math
# ---------------------------------------------------------------------------
def test_arc_completed_true_when_all_turns_pass_and_paths_present():
    row = acceptance.score_journey(
        scenario="acceptance-j1-onboarding",
        spec_journey="J1-enroll-machine",
        verdict="PASS",
        turn_count=1,
        par_calls=4,
        expected_paths_present=True,
        wall_clock_s=12.5,
        binary_version="claude 2.1.170",
        agent="claude",
    )
    assert row["arc_completed"] is True
    assert row["score"] == pytest.approx(1.0)


def test_arc_completed_false_when_verdict_not_pass():
    row = acceptance.score_journey(
        scenario="acceptance-j6-find-past-work",
        spec_journey="J6-find-past-work",
        verdict="FAIL",
        turn_count=0,
        par_calls=4,
        expected_paths_present=True,
        wall_clock_s=4.0,
        binary_version="claude 2.1.170",
        agent="claude",
    )
    assert row["arc_completed"] is False
    # A non-completed arc scores 0 regardless of call efficiency.
    assert row["score"] == pytest.approx(0.0)


def test_arc_completed_false_when_expected_paths_missing():
    row = acceptance.score_journey(
        scenario="acceptance-j1-onboarding",
        spec_journey="J1-enroll-machine",
        verdict="PASS",
        turn_count=1,
        par_calls=4,
        expected_paths_present=False,
        wall_clock_s=10.0,
        binary_version="claude 2.1.170",
        agent="claude",
    )
    assert row["arc_completed"] is False
    assert row["score"] == pytest.approx(0.0)


def test_calls_vs_par_ratio_and_none_par():
    # turn_count under par -> ratio < 1 (efficient).
    row = acceptance.score_journey(
        scenario="x", spec_journey="J", verdict="PASS", turn_count=2,
        par_calls=4, expected_paths_present=True, wall_clock_s=1.0,
        binary_version="v", agent="claude",
    )
    assert row["calls_vs_par"] == pytest.approx(0.5)

    # par_calls=None -> calls_vs_par is None (not divide-by-zero, not a crash).
    row_none = acceptance.score_journey(
        scenario="x", spec_journey="J", verdict="PASS", turn_count=2,
        par_calls=None, expected_paths_present=True, wall_clock_s=1.0,
        binary_version="v", agent="claude",
    )
    assert row_none["calls_vs_par"] is None
    # Score still rolls up purely on arc_completed when par is unknown.
    assert row_none["score"] == pytest.approx(1.0)


def test_score_rollup_penalises_over_par_calls():
    # Completing the arc but using 2x the par budget scores below 1.0.
    over = acceptance.score_journey(
        scenario="x", spec_journey="J", verdict="PASS", turn_count=8,
        par_calls=4, expected_paths_present=True, wall_clock_s=1.0,
        binary_version="v", agent="claude",
    )
    at_par = acceptance.score_journey(
        scenario="x", spec_journey="J", verdict="PASS", turn_count=4,
        par_calls=4, expected_paths_present=True, wall_clock_s=1.0,
        binary_version="v", agent="claude",
    )
    assert over["arc_completed"] is True
    assert 0.0 < over["score"] < at_par["score"] <= 1.0


def test_row_carries_required_fields():
    row = acceptance.score_journey(
        scenario="acceptance-j7-explain-commit",
        spec_journey="J7-explain-commit-pr",
        verdict="PASS", turn_count=3, par_calls=3,
        expected_paths_present=True, wall_clock_s=33.3,
        binary_version="claude 2.1.170", agent="claude",
    )
    for key in (
        "scenario", "spec_journey", "arc_completed", "calls_vs_par",
        "wall_clock_s", "turn_count", "par_calls", "score",
    ):
        assert key in row, key
    assert row["wall_clock_s"] == pytest.approx(33.3)
    assert row["turn_count"] == 3
    assert row["par_calls"] == 3


# ---------------------------------------------------------------------------
# report schema round-trip
# ---------------------------------------------------------------------------
def _sample_digests() -> dict[str, str]:
    return {
        name: "a" * 64 for name in acceptance.acceptance_scenario_names()
    }


def _sample_rows() -> list[dict]:
    # The strict committed-report schema requires the exact five arcs.
    return [
        acceptance.score_journey(
            scenario=spec.name, spec_journey=spec.spec_journey,
            verdict="PASS", turn_count=2, par_calls=4,
            expected_paths_present=True, wall_clock_s=10.0,
            binary_version="claude 2.1.170", agent="claude",
        )
        for spec in acceptance.ACCEPTANCE_SCENARIOS
    ]


def _sample_report() -> dict:
    return acceptance.build_report(
        rows=_sample_rows(),
        agent="claude",
        binary_version="claude 2.1.170",
        scenario_digests=_sample_digests(),
    )


def test_build_report_is_schema_valid_and_round_trips():
    report = _sample_report()
    assert report["schema_version"] == acceptance.REPORT_SCHEMA_VERSION
    assert acceptance.validate_report_schema(report) == []

    # Round-trips through JSON without losing or reshaping anything.
    import json

    again = json.loads(json.dumps(report))
    assert acceptance.validate_report_schema(again) == []
    assert again == report


def test_provenance_block_carries_required_fields():
    report = _sample_report()
    prov = report["provenance"]
    for key in (
        "captured_at", "agent", "binary_version", "scenario_digests",
        "opentraces_schema_version", "opentraces_cli_version",
    ):
        assert key in prov, key
    assert prov["scenario_digests"]["acceptance-j1-onboarding"] == "a" * 64


def test_validate_report_schema_rejects_garbage():
    assert acceptance.validate_report_schema({}) != []
    assert acceptance.validate_report_schema(
        {"schema_version": "wrong", "journeys": [], "provenance": {}}
    ) != []
    # Missing a required provenance field is a violation.
    bad = _sample_report()
    del bad["provenance"]["captured_at"]
    assert acceptance.validate_report_schema(bad) != []


# ---------------------------------------------------------------------------
# strict-validator teeth (adversary findings 1 + 2: a hollow, partial,
# inconsistent, or mistyped report must FAIL the committed gate)
# ---------------------------------------------------------------------------
def test_validate_rejects_empty_journeys_with_valid_provenance():
    """Zero rows + valid provenance is NOT a valid committed report —
    a machine that never ran the arcs cannot produce a passing report."""
    hollow = acceptance.build_report(
        rows=[], agent="claude", binary_version="claude 2.1.170",
        scenario_digests=_sample_digests(),
    )
    problems = acceptance.validate_report_schema(hollow)
    assert any("missing acceptance scenario rows" in p for p in problems)


def test_validate_requires_exactly_the_five_acceptance_rows():
    # Missing one arc -> invalid.
    partial = _sample_report()
    dropped = partial["journeys"].pop()
    partial["summary"]["journeys"] -= 1
    partial["summary"]["arcs_completed"] -= 1
    problems = acceptance.validate_report_schema(partial)
    assert any(dropped["scenario"] in p for p in problems)

    # A duplicated arc -> invalid even at 5 rows.
    duped = _sample_report()
    duped["journeys"][1] = dict(duped["journeys"][0])
    problems = acceptance.validate_report_schema(duped)
    assert any("duplicate scenario rows" in p for p in problems)

    # An unknown extra scenario -> invalid.
    alien = _sample_report()
    extra = dict(alien["journeys"][0], scenario="not-an-acceptance-arc")
    alien["journeys"].append(extra)
    alien["summary"]["journeys"] += 1
    alien["summary"]["arcs_completed"] += 1
    problems = acceptance.validate_report_schema(alien)
    assert any("not an acceptance scenario" in p for p in problems)


def test_validate_rejects_wrong_spec_journey_binding():
    report = _sample_report()
    report["journeys"][0]["spec_journey"] = "J6-find-past-work"
    assert any(
        "spec_journey" in p
        for p in acceptance.validate_report_schema(report)
    )


def test_validate_rejects_summary_inconsistency():
    report = _sample_report()
    report["summary"]["arcs_completed"] = 0  # rows say 5
    assert any(
        "arcs_completed" in p
        for p in acceptance.validate_report_schema(report)
    )

    report = _sample_report()
    report["summary"]["mean_score"] = 0.1234  # rows derive 1.0
    assert any(
        "mean_score" in p for p in acceptance.validate_report_schema(report)
    )

    report = _sample_report()
    report["summary"]["journeys"] = 99
    assert any(
        "summary.journeys" in p
        for p in acceptance.validate_report_schema(report)
    )


def test_validate_rejects_mistyped_row_fields():
    report = _sample_report()
    report["journeys"][0]["calls_vs_par"] = "fast"
    assert any(
        "calls_vs_par" in p for p in acceptance.validate_report_schema(report)
    )

    report = _sample_report()
    report["journeys"][0]["turn_count"] = "3"
    assert any(
        "turn_count" in p for p in acceptance.validate_report_schema(report)
    )

    report = _sample_report()
    report["journeys"][0]["score"] = 1.5
    assert any(
        "score" in p for p in acceptance.validate_report_schema(report)
    )


def test_validate_rejects_skip_verdict_rows():
    """A SKIP drive (never ran) must not pass as a committed report row."""
    report = _sample_report()
    report["journeys"][0]["verdict"] = "SKIP"
    assert any(
        "verdict" in p for p in acceptance.validate_report_schema(report)
    )


def test_validate_requires_digest_coverage_for_all_arcs():
    report = _sample_report()
    del report["provenance"]["scenario_digests"]["acceptance-j10-publish-dataset"]
    problems = acceptance.validate_report_schema(report)
    assert any("acceptance-j10-publish-dataset" in p for p in problems)

    report = _sample_report()
    report["provenance"]["scenario_digests"]["acceptance-j1-onboarding"] = ""
    assert any(
        "non-string/empty" in p
        for p in acceptance.validate_report_schema(report)
    )


# ---------------------------------------------------------------------------
# freshness predicates (the gate consumes these)
# ---------------------------------------------------------------------------
def test_age_predicate_flags_old_reports():
    import datetime

    old = (
        datetime.datetime.now(datetime.timezone.utc)
        - datetime.timedelta(days=200)
    ).isoformat()
    assert acceptance.report_is_stale_by_age(old, threshold_days=90) is True

    fresh = datetime.datetime.now(datetime.timezone.utc).isoformat()
    assert acceptance.report_is_stale_by_age(fresh, threshold_days=90) is False
    # Malformed timestamps never crash the predicate.
    assert acceptance.report_is_stale_by_age("not-a-date", threshold_days=90) is False


def test_scenario_digest_drift_predicate():
    committed = {"acceptance-j1-onboarding": "a" * 64}
    live = {"acceptance-j1-onboarding": "z" * 64}
    drift = acceptance.scenario_digest_drift(committed, live)
    assert "acceptance-j1-onboarding" in drift

    no_drift = acceptance.scenario_digest_drift(committed, committed)
    assert no_drift == []


def test_binary_version_ahead_predicate():
    # Installed minor ahead of report's -> flagged.
    assert acceptance.binary_version_ahead("claude 2.1.170", "claude 2.2.0") is True
    # Same version -> not flagged.
    assert acceptance.binary_version_ahead("claude 2.1.170", "claude 2.1.170") is False
    # Installed behind -> not flagged.
    assert acceptance.binary_version_ahead("claude 2.2.0", "claude 2.1.170") is False
    # Unparseable versions never crash -> treated as not-ahead.
    assert acceptance.binary_version_ahead("weird", "also-weird") is False


# ---------------------------------------------------------------------------
# echo-mode end-to-end (default-CI, no real agent, no network)
# ---------------------------------------------------------------------------
def test_run_acceptance_echo_covers_all_five_journeys():
    report = acceptance.run_acceptance_echo()
    assert acceptance.validate_report_schema(report) == []
    names = {r["scenario"] for r in report["journeys"]}
    assert names == set(acceptance.acceptance_scenario_names())
    specs = {r["spec_journey"] for r in report["journeys"]}
    assert specs == {
        "J1-enroll-machine", "J6-find-past-work", "J7-explain-commit-pr",
        "J10-publish-dataset", "J13-agent-drives-cli",
    }
    # Echo report is honestly synthetic.
    assert report["provenance"]["agent"] == "echo"


def test_acceptance_scenarios_bind_to_real_spec_journeys():
    """Every acceptance scenario binds to a real claims_map spec journey."""
    from tests.otbox.claims_map import SPEC_JOURNEYS

    for spec in acceptance.ACCEPTANCE_SCENARIOS:
        assert spec.spec_journey in SPEC_JOURNEYS, spec.spec_journey


# ---------------------------------------------------------------------------
# CLI verb — echo mode + binary-absent SKIP (default-CI contract)
# ---------------------------------------------------------------------------
def test_cli_acceptance_echo_writes_valid_report(tmp_path):
    from tests.otbox.cli import main

    out = tmp_path / "report.json"
    rc = main(["acceptance", "--echo", "--json", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    import json

    report = json.loads(out.read_text())
    assert acceptance.validate_report_schema(report) == []


def test_cli_acceptance_skips_clean_without_binary(tmp_path, monkeypatch):
    """Real-agent mode with no binary on PATH SKIPs (exit 0), writes nothing."""
    import shutil as _shutil

    from tests.otbox.cli import main

    monkeypatch.setattr(_shutil, "which", lambda _name: None)
    out = tmp_path / "report.json"
    rc = main(["acceptance", "--agent", "claude", "--json", "--out", str(out)])
    assert rc == 0
    assert not out.exists()


def test_echo_default_report_path_is_not_the_committed_path():
    """Echo (synthetic) reports default to a separate, gitignored path so a
    synthetic report never sits on the committed-report path by default."""
    assert acceptance.ECHO_REPORT_PATH != acceptance.REPORT_PATH
    assert acceptance.ECHO_REPORT_PATH.name == "echo-report.json"


# ---------------------------------------------------------------------------
# CLI verb — real-mode drive stubs (no real agent, no checkpoint forking)
# ---------------------------------------------------------------------------
def _stub_real_mode(monkeypatch, tmp_path, fake_runner, binary_for):
    """Patch the real-mode collaborators cmd_acceptance lazily imports."""
    import shutil as _shutil
    from types import SimpleNamespace

    from tests.otbox import checkpoints as checkpoints_mod
    from tests.otbox import drivers as drivers_mod
    from tests.otbox.simulated_users import runner as runner_mod

    box = SimpleNamespace(
        project=tmp_path / "project", logs=tmp_path / "logs", notes={}
    )
    box.project.mkdir(parents=True, exist_ok=True)
    driver = SimpleNamespace(teardown=lambda _box: None)

    monkeypatch.setattr(
        _shutil, "which",
        lambda name: f"/fake/bin/{name}" if name == binary_for else None,
    )
    monkeypatch.setattr(drivers_mod, "get_driver", lambda _name: driver)
    monkeypatch.setattr(
        checkpoints_mod, "resolve_checkpoint",
        lambda _driver, _name: SimpleNamespace(box=box),
    )
    monkeypatch.setattr(runner_mod, "run_simulated_session", fake_runner)
    return box


def test_cli_acceptance_runner_skip_emits_skip_envelope_not_report(
    tmp_path, monkeypatch, capsys
):
    """A drive that returns SKIP (termctrl/tmux preflight) must surface as a
    skip envelope and write NO report — a machine that never ran the arcs
    cannot produce a schema-valid 'ok' report."""
    import json
    from types import SimpleNamespace

    from tests.otbox.cli import main

    def fake_runner(*_args, **_kwargs):
        return SimpleNamespace(
            verdict="SKIP", turn_verdict="SKIP", turn_count=0,
            binary_version="",
            error_message="termctrl not installed; cargo install terminal-control",
        )

    _stub_real_mode(monkeypatch, tmp_path, fake_runner, binary_for="claude")
    out = tmp_path / "report.json"
    rc = main(["acceptance", "--agent", "claude", "--json", "--out", str(out)])
    assert rc == 0
    assert not out.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "skipped"
    assert "termctrl" in payload["reason"]


def test_cli_acceptance_agent_override_threads_through_drive_and_rows(
    tmp_path, monkeypatch, capsys
):
    """`--agent codex` must prep/drive/attribute as codex end-to-end, even
    though the scenario TOMLs declare a default agent of claude."""
    import json
    from types import SimpleNamespace

    from tests.otbox.cli import main

    seen_agents: list[str] = []
    seen_drive_kwargs: list[dict] = []

    def fake_runner(*_args, **kwargs):
        seen_agents.append(kwargs.get("agent"))
        seen_drive_kwargs.append(kwargs)
        return SimpleNamespace(
            verdict="PASS", turn_verdict="PASS", turn_count=3,
            binary_version="codex 0.139.0", error_message="",
        )

    _stub_real_mode(monkeypatch, tmp_path, fake_runner, binary_for="codex")
    out = tmp_path / "report.json"
    rc = main([
        "acceptance", "--agent", "codex",
        "--scenario", "acceptance-j1-onboarding", "--json", "--out", str(out),
    ])
    assert rc == 0
    assert seen_agents == ["codex"]
    assert seen_drive_kwargs[0]["export_mp4"] is True
    assert str(seen_drive_kwargs[0]["record_dir"]).startswith(str(tmp_path))
    report = json.loads(out.read_text())
    assert report["journeys"][0]["agent"] == "codex"
    assert report["provenance"]["agent"] == "codex"
    # A single-scenario run is honest-but-partial: the envelope must say it
    # does NOT pass the strict committed-report schema.
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_valid"] is False
    assert payload["gallery_path"].startswith(str(tmp_path))


# ---------------------------------------------------------------------------
# J10 end-state evidence (the verify regex must require a real dataset)
# ---------------------------------------------------------------------------
def test_j10_end_state_rejects_empty_dataset_list():
    """The constant `datasets` envelope key over an EMPTY list must not
    satisfy the J10 arc; only a manifest path or a non-empty array does."""
    import re

    from tests.otbox.simulated_users.scenario import load_scenario

    turn = load_scenario("acceptance-j10-publish-dataset").turns[0]
    empty = '{\n  "status": "ok",\n  "datasets": []\n}'
    assert re.search(turn.verify_regex, empty) is None

    nonempty = '{\n  "status": "ok",\n  "datasets": [\n    {"name": "x"}\n  ]\n}'
    assert re.search(turn.verify_regex, nonempty)

    manifest = "/box/home/.opentraces/datasets/x/.opentraces/manifest.yaml"
    assert re.search(turn.verify_regex, manifest)
