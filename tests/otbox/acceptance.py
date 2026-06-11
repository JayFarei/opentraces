"""B0 acceptance scoring harness (issue #61, plan 095 U9).

Five acceptance scenarios drive a REAL agent through five spec-journey
job arcs (J1 / J6 / J7 / J10 / J13 from ``claims_map.py``). Each scenario
TOML lives under ``simulated_users/scenarios/acceptance-*.toml`` and binds
to exactly one spec journey here. This module:

  * scores a completed drive into one per-journey row (``score_journey``);
  * rolls those rows into a schema-versioned report
    (``opentraces.otbox.acceptance_report.v1`` — ``build_report``);
  * validates a committed report's schema (``validate_report_schema``);
  * exposes the three freshness predicates the gate
    (``test_acceptance_report.py``) consumes (age / scenario-digest drift /
    installed-binary-ahead).

Scoring is DELIBERATELY deterministic-first (acceptance criterion 2 + plan
095 boundaries lines 137-139): ``arc_completed`` from the drive verdict +
expected-paths presence, ``calls_vs_par`` from turn/command counts, and
wall-clock from the drive duration. waste / run-intel enrichment is an
optional add-on only when a QA trace exists — the report never DEPENDS on
it (B1 scoring stays out of scope).

The ``acceptance`` CLI verb (``tests.otbox.cli``) is a sibling of
``capture-refresh``: it reuses the same scenario parser, the same drive
core, and the same ``--echo`` synthetic-harness / binary-absent SKIP
semantics. With ``--echo`` it produces a valid report against a
deterministic synthetic harness (no real agent, no network) so the whole
scoring -> report -> schema path runs in default CI. With a real binary
absent and ``--echo`` not requested it SKIPs cleanly (exit-0 skip
envelope), never FAILs — the ``test_real_agent_optin.py`` contract.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .simulated_users.scenario import load_scenario, scenario_digest

REPORT_SCHEMA_VERSION = "opentraces.otbox.acceptance_report.v1"

# The committed report lives here once the maintainer runs the
# machine-gated ritual; the freshness gate flips from SKIP to active on
# the next CI run after this is committed (acceptance criterion 4).
ACCEPTANCE_DIR: Path = Path(__file__).resolve().parent / "captures" / "_acceptance"
REPORT_PATH: Path = ACCEPTANCE_DIR / "report.json"
# Echo-mode (synthetic harness) reports default HERE, never to REPORT_PATH:
# a synthetic report must not be one accidental `git add` away from posing
# as the committed real-agent report. The path is gitignored.
ECHO_REPORT_PATH: Path = ACCEPTANCE_DIR / "echo-report.json"

STALE_AFTER_DAYS = 90


# ---------------------------------------------------------------------------
# scenario registry — scenario name -> {spec_journey, base_checkpoint}
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AcceptanceScenario:
    """One acceptance scenario's binding.

    ``spec_journey`` is the ``claims_map.py`` SPEC_JOURNEYS key the arc
    verifies. ``base_checkpoint`` is the world the drive forks from — J1 /
    J13 orient surfaces start clean (``c-installed-source``); J6 / J7 / J10
    need a captured world to discover / explain / project from
    (``c-captured-real-session``). The base checkpoint is an EXECUTION
    concern owned here, not baked into the (portable) scenario TOML.
    """

    name: str
    spec_journey: str
    base_checkpoint: str


ACCEPTANCE_SCENARIOS: tuple[AcceptanceScenario, ...] = (
    AcceptanceScenario(
        "acceptance-j1-onboarding", "J1-enroll-machine", "c-installed-source"
    ),
    AcceptanceScenario(
        "acceptance-j6-find-past-work", "J6-find-past-work",
        "c-captured-real-session",
    ),
    AcceptanceScenario(
        "acceptance-j7-explain-commit", "J7-explain-commit-pr",
        "c-captured-real-session",
    ),
    AcceptanceScenario(
        "acceptance-j10-publish-dataset", "J10-publish-dataset",
        "c-captured-real-session",
    ),
    AcceptanceScenario(
        "acceptance-j13-free-navigation", "J13-agent-drives-cli",
        "c-captured-real-session",
    ),
)


def acceptance_scenario_names() -> list[str]:
    return [s.name for s in ACCEPTANCE_SCENARIOS]


def acceptance_scenario(name: str) -> AcceptanceScenario:
    for s in ACCEPTANCE_SCENARIOS:
        if s.name == name:
            return s
    raise KeyError(f"no acceptance scenario {name!r}; "
                   f"known: {acceptance_scenario_names()}")


# ---------------------------------------------------------------------------
# scorer
# ---------------------------------------------------------------------------
def score_journey(
    *,
    scenario: str,
    spec_journey: str,
    verdict: str,
    turn_count: int,
    par_calls: int | None,
    expected_paths_present: bool,
    wall_clock_s: float,
    binary_version: str,
    agent: str,
    waste: dict | None = None,
    run_intel: dict | None = None,
) -> dict[str, Any]:
    """Score one completed drive into a per-journey report row.

    ``arc_completed`` is the load-bearing deterministic verdict: the drive
    must have PASSed (every turn's ``expect_regex`` matched AND every
    ``verify_command`` assertion held — the #49 end-state lesson) AND every
    declared ``expected_paths`` entry must exist. A completed arc scores in
    ``(0, 1]``, scaled down when the agent burned more than its ``par_calls``
    budget; a non-completed arc scores ``0.0``.

    ``calls_vs_par`` is ``turn_count / par_calls`` (``None`` when the
    scenario declares no par). waste / run-intel are optional enrichment —
    present only when a QA trace existed; the score never depends on them.
    """
    arc_completed = verdict == "PASS" and expected_paths_present

    calls_vs_par: float | None
    if par_calls and par_calls > 0:
        calls_vs_par = round(turn_count / par_calls, 4)
    else:
        calls_vs_par = None

    if not arc_completed:
        score = 0.0
    elif calls_vs_par is None or calls_vs_par <= 1.0:
        score = 1.0
    else:
        # Over par: 1.0 at par, decaying toward 0 as the agent overshoots.
        # par/turns keeps the score in (0, 1) and is monotone in efficiency.
        score = round(1.0 / calls_vs_par, 4)

    row: dict[str, Any] = {
        "scenario": scenario,
        "spec_journey": spec_journey,
        "arc_completed": arc_completed,
        "calls_vs_par": calls_vs_par,
        "wall_clock_s": round(float(wall_clock_s), 3),
        "turn_count": int(turn_count),
        "par_calls": par_calls,
        "score": score,
        "verdict": verdict,
        "binary_version": binary_version,
        "agent": agent,
    }
    # Optional enrichment — only emitted when a QA trace produced them.
    if waste is not None:
        row["waste"] = waste
    if run_intel is not None:
        row["run_intel"] = run_intel
    return row


# ---------------------------------------------------------------------------
# report builder + schema validator
# ---------------------------------------------------------------------------
def build_report(
    *,
    rows: list[dict],
    agent: str,
    binary_version: str,
    scenario_digests: dict[str, str],
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Roll per-journey rows into a schema-versioned acceptance report."""
    try:
        from opentraces import __version__ as cli_version
    except Exception:  # noqa: BLE001 - never fail report build on version probe
        cli_version = "unknown"
    try:
        from opentraces_schema import SCHEMA_VERSION as schema_version
    except Exception:  # noqa: BLE001
        schema_version = "unknown"

    when = captured_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
    scores = [r["score"] for r in rows if isinstance(r.get("score"), (int, float))]
    completed = sum(1 for r in rows if r.get("arc_completed"))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "summary": {
            "journeys": len(rows),
            "arcs_completed": completed,
            "mean_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
        },
        "journeys": rows,
        "provenance": {
            "captured_at": when,
            "agent": agent,
            "binary_version": binary_version,
            "scenario_digests": dict(scenario_digests),
            "opentraces_schema_version": schema_version,
            "opentraces_cli_version": cli_version,
        },
    }


_REQUIRED_ROW_KEYS = {
    "scenario", "spec_journey", "arc_completed", "calls_vs_par",
    "wall_clock_s", "turn_count", "par_calls", "score", "verdict",
}
_REQUIRED_PROVENANCE_KEYS = {
    "captured_at", "agent", "binary_version", "scenario_digests",
    "opentraces_schema_version", "opentraces_cli_version",
}
# A committed report row records a drive that RAN: PASS or FAIL. SKIP rows
# (binary/termctrl absent) must never reach a committed report — the CLI
# emits a skip envelope instead, and this rejects one that sneaks through.
_COMMITTED_ROW_VERDICTS = {"PASS", "FAIL"}


def _is_number(value: Any) -> bool:
    # bool is an int subclass — a True score/turn_count is a type bug.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _validate_row_types(row: dict, where: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(row.get("arc_completed"), bool):
        problems.append(f"{where} arc_completed must be a bool")
    if not (_is_number(row.get("score")) and 0.0 <= row["score"] <= 1.0):
        problems.append(f"{where} score must be a number in [0, 1]")
    if not (isinstance(row.get("turn_count"), int)
            and not isinstance(row.get("turn_count"), bool)):
        problems.append(f"{where} turn_count must be an int")
    if row.get("par_calls") is not None and not (
        isinstance(row.get("par_calls"), int)
        and not isinstance(row.get("par_calls"), bool)
        and row["par_calls"] >= 1
    ):
        problems.append(f"{where} par_calls must be an int >= 1 or null")
    if row.get("calls_vs_par") is not None and not _is_number(
        row.get("calls_vs_par")
    ):
        problems.append(f"{where} calls_vs_par must be a number or null")
    if not _is_number(row.get("wall_clock_s")):
        problems.append(f"{where} wall_clock_s must be a number")
    if row.get("verdict") not in _COMMITTED_ROW_VERDICTS:
        problems.append(
            f"{where} verdict must be one of "
            f"{sorted(_COMMITTED_ROW_VERDICTS)}, got {row.get('verdict')!r}"
        )
    return problems


def validate_report_schema(report: Any) -> list[str]:
    """Return a list of schema violations (empty == valid).

    The ONLY hard failure the freshness gate raises is a schema-invalid
    committed report, so this predicate is the gate's teeth. It is STRICT
    by design: exactly the five acceptance scenario rows (correct
    spec_journey bindings, no dupes, no extras), typed row fields, a
    summary that is arithmetically consistent with the rows, and
    provenance digests covering every acceptance scenario. A partial or
    hollow report (e.g. zero rows with valid provenance) must FAIL here —
    otherwise a machine that never ran the arcs could commit a "valid"
    report.
    """
    problems: list[str] = []
    if not isinstance(report, dict):
        return [f"report is not an object: {type(report).__name__}"]
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        problems.append(
            f"schema_version {report.get('schema_version')!r} != "
            f"{REPORT_SCHEMA_VERSION!r}"
        )
    expected_bindings = {s.name: s.spec_journey for s in ACCEPTANCE_SCENARIOS}
    journeys = report.get("journeys")
    if not isinstance(journeys, list):
        problems.append("'journeys' must be a list")
        journeys = []
    seen_scenarios: list[str] = []
    for idx, row in enumerate(journeys):
        if not isinstance(row, dict):
            problems.append(f"journeys[{idx}] is not an object")
            continue
        missing = _REQUIRED_ROW_KEYS - set(row)
        if missing:
            problems.append(
                f"journeys[{idx}] ({row.get('scenario')}) missing keys: "
                f"{sorted(missing)}"
            )
        where = f"journeys[{idx}]"
        problems.extend(_validate_row_types(row, where))
        scenario = row.get("scenario")
        if scenario not in expected_bindings:
            problems.append(
                f"{where} scenario {scenario!r} is not an acceptance scenario"
            )
        else:
            seen_scenarios.append(scenario)
            if row.get("spec_journey") != expected_bindings[scenario]:
                problems.append(
                    f"{where} spec_journey {row.get('spec_journey')!r} != "
                    f"{expected_bindings[scenario]!r} for {scenario!r}"
                )
    duplicates = sorted(
        {name for name in seen_scenarios if seen_scenarios.count(name) > 1}
    )
    if duplicates:
        problems.append(f"duplicate scenario rows: {duplicates}")
    absent = sorted(set(expected_bindings) - set(seen_scenarios))
    if absent:
        problems.append(
            f"missing acceptance scenario rows: {absent} "
            f"(a committed report must cover all "
            f"{len(expected_bindings)} arcs)"
        )

    summary = report.get("summary")
    if not isinstance(summary, dict):
        problems.append("'summary' must be an object")
    else:
        rows = [r for r in journeys if isinstance(r, dict)]
        if summary.get("journeys") != len(rows):
            problems.append(
                f"summary.journeys {summary.get('journeys')!r} != "
                f"row count {len(rows)}"
            )
        derived_completed = sum(
            1 for r in rows if r.get("arc_completed") is True
        )
        if summary.get("arcs_completed") != derived_completed:
            problems.append(
                f"summary.arcs_completed {summary.get('arcs_completed')!r} "
                f"!= derived {derived_completed}"
            )
        scores = [r["score"] for r in rows if _is_number(r.get("score"))]
        derived_mean = round(sum(scores) / len(scores), 4) if scores else 0.0
        mean = summary.get("mean_score")
        if not _is_number(mean) or abs(mean - derived_mean) > 1e-6:
            problems.append(
                f"summary.mean_score {mean!r} != derived {derived_mean}"
            )

    prov = report.get("provenance")
    if not isinstance(prov, dict):
        problems.append("'provenance' must be an object")
    else:
        missing = _REQUIRED_PROVENANCE_KEYS - set(prov)
        if missing:
            problems.append(f"provenance missing keys: {sorted(missing)}")
        digests = prov.get("scenario_digests")
        if not isinstance(digests, dict):
            problems.append("provenance.scenario_digests must be an object")
        else:
            bad_digests = sorted(
                name for name, digest in digests.items()
                if not (isinstance(digest, str) and digest)
            )
            if bad_digests:
                problems.append(
                    f"provenance.scenario_digests non-string/empty for: "
                    f"{bad_digests}"
                )
            uncovered = sorted(set(expected_bindings) - set(digests))
            if uncovered:
                problems.append(
                    f"provenance.scenario_digests missing scenarios: "
                    f"{uncovered}"
                )
    return problems


# ---------------------------------------------------------------------------
# freshness predicates (consumed by test_acceptance_report.py)
# ---------------------------------------------------------------------------
def report_is_stale_by_age(captured_at: str, *, threshold_days: int) -> bool:
    """True iff ``captured_at`` is more than ``threshold_days`` old.

    Malformed timestamps return False — the gate warns on drift, never
    crashes on a hand-edited report.
    """
    try:
        when = datetime.datetime.fromisoformat(
            str(captured_at).replace("Z", "+00:00")
        )
    except (ValueError, TypeError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - when).days > threshold_days


def scenario_digest_drift(
    committed: dict[str, str], live: dict[str, str]
) -> list[str]:
    """Return scenario names whose committed digest != the live TOML digest."""
    drifted: list[str] = []
    for name, committed_digest in committed.items():
        live_digest = live.get(name)
        if live_digest is not None and committed_digest != live_digest:
            drifted.append(name)
    return sorted(drifted)


_VERSION_RE = re.compile(r"(\d+)\.(\d+)")


def _major_minor(version: str) -> tuple[int, int] | None:
    m = _VERSION_RE.search(version or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def binary_version_ahead(report_version: str, installed_version: str) -> bool:
    """True iff the installed binary's major/minor is ahead of the report's.

    Unparseable versions return False — never crash the gate on a version
    string we don't recognise.
    """
    report_mm = _major_minor(report_version)
    installed_mm = _major_minor(installed_version)
    if report_mm is None or installed_mm is None:
        return False
    return installed_mm > report_mm


def live_scenario_digests(names: list[str] | None = None) -> dict[str, str]:
    """Compute the current scenario_digest for each acceptance scenario."""
    names = names or acceptance_scenario_names()
    out: dict[str, str] = {}
    for name in names:
        try:
            out[name] = scenario_digest(load_scenario(name))
        except Exception:  # noqa: BLE001 - a missing/broken TOML is the gate's job
            continue
    return out


# ---------------------------------------------------------------------------
# echo-mode synthetic drive — default-CI end-to-end without a real agent
# ---------------------------------------------------------------------------
@dataclass
class DriveOutcome:
    """The minimal drive facts the scorer needs, agnostic of drive source.

    Produced either by the real ``run_simulated_session`` path (the
    machine-gated ritual) or the ``--echo`` synthetic harness (default CI).
    """

    verdict: str
    turn_count: int
    wall_clock_s: float
    binary_version: str
    expected_paths_present: bool
    error_message: str = ""


def synthetic_echo_outcome(scenario_name: str) -> DriveOutcome:
    """Deterministic synthetic drive for ``--echo`` mode.

    Mirrors the ``echo-meta`` precedent: no real binary, no network, fully
    deterministic. Reports a completed arc that lands every expected path,
    at exactly the scenario's par budget, so the scoring -> report ->
    schema-validate path exercises end-to-end in default CI. The binary
    version is the synthetic ``otbox-echo`` stamp so a committed echo report
    is never mistaken for a real-agent capture.
    """
    scenario = load_scenario(scenario_name)
    par = scenario.par_calls or len(scenario.turns)
    return DriveOutcome(
        verdict="PASS",
        turn_count=par,
        wall_clock_s=0.0,
        binary_version="otbox-echo 1.0.0",
        expected_paths_present=True,
    )


def run_acceptance_echo() -> dict[str, Any]:
    """Drive all 5 acceptance scenarios through the synthetic harness and
    build a valid report. This is the default-CI end-to-end path."""
    rows: list[dict] = []
    digests = live_scenario_digests()
    for spec in ACCEPTANCE_SCENARIOS:
        scenario = load_scenario(spec.name)
        outcome = synthetic_echo_outcome(spec.name)
        rows.append(
            score_journey(
                scenario=spec.name,
                spec_journey=spec.spec_journey,
                verdict=outcome.verdict,
                turn_count=outcome.turn_count,
                par_calls=scenario.par_calls,
                expected_paths_present=outcome.expected_paths_present,
                wall_clock_s=outcome.wall_clock_s,
                binary_version=outcome.binary_version,
                agent="echo",
            )
        )
    return build_report(
        rows=rows,
        agent="echo",
        binary_version="otbox-echo 1.0.0",
        scenario_digests=digests,
    )


# ---------------------------------------------------------------------------
# report IO
# ---------------------------------------------------------------------------
def write_report(report: dict, path: Path | None = None) -> Path:
    """Write a report deterministically (sorted keys) to ``path``."""
    target = path or REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return target


def load_committed_report(path: Path | None = None) -> dict | None:
    """Load the committed report, or ``None`` when none is committed."""
    target = path or REPORT_PATH
    if not target.exists():
        return None
    return json.loads(target.read_text())
