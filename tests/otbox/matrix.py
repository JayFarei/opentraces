"""Journey × checkpoint matrix runner (plan 062 R6).

For every journey TOML that declares ``from_checkpoints = [...]``, run
the journey once per declared base checkpoint. Resolve each checkpoint
once per matrix run (cache + snapshot-fork), then fork a box per
dependent journey from the snapshot.

Emits one row per (journey, base) pair plus an aggregate report keyed
back to the journey catalog.
"""

from __future__ import annotations

import fnmatch
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .checkpoints import CheckpointError, resolve_checkpoint
from .drivers.base import Driver
from .journey import JourneyResult, available_journeys, run_journey
from .jtbd import load_jtbd_map

TIER1_FLAG = "OT_OTBOX_TIER1"
TIER1_OPT_IN_REASON = f"set {TIER1_FLAG}=1 to opt into Tier 1 matrix runs"


@dataclass
class MatrixRow:
    journey: str
    base_checkpoint: str
    verdict: str  # PASS | FAIL | SKIP | ERROR
    reason: str = ""
    duration_s: float = 0.0
    checkpoint_cache_hit: bool | None = None
    box_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MatrixReport:
    rows: list[MatrixRow] = field(default_factory=list)
    started_at: str = ""
    duration_s: float = 0.0
    journeys_run: int = 0
    pass_count: int = 0
    fail_count: int = 0
    skip_count: int = 0
    error_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [r.to_dict() for r in self.rows]
        return d


def _filtered_journeys(
    journey_pattern: str | None,
    lane: str | None,
    tier: int | None,
) -> list[dict]:
    journeys = available_journeys()
    if journey_pattern:
        journeys = [j for j in journeys if fnmatch.fnmatch(j["name"], journey_pattern)]
    if lane:
        journeys = [j for j in journeys if j["lane"] == lane]
    if tier is not None:
        journeys = [j for j in journeys if j["tier"] == tier]
    return journeys


def _eligible_pairs(
    journeys: list[dict],
    checkpoint_filter: str | None,
) -> list[tuple[dict, str | None]]:
    """Expand each journey into one (journey, base-checkpoint) pair.

    A journey with ``from_checkpoints = ["a", "b"]`` runs twice (once
    per base). A journey with an empty list runs once with base = None
    (legacy seed-based path).
    """
    pairs: list[tuple[dict, str | None]] = []
    for j in journeys:
        bases = j.get("from_checkpoints") or [None]
        for base in bases:
            if checkpoint_filter and base is not None and not fnmatch.fnmatch(base, checkpoint_filter):
                continue
            if checkpoint_filter and base is None:
                continue  # checkpoint-filtered runs exclude legacy-seed journeys
            pairs.append((j, base))
    return pairs


def _check_journey_trajectories(journeys: list[dict]) -> list[tuple[str, str]]:
    """Plan 063 SSoT gate: journey TOMLs naming unknown trajectories.

    Returns a list of ``(journey_name, unknown_trajectory)`` violations.
    Resolving against 063 is a fast in-process parse, so we always run
    the check; the caller fails the matrix when violations exist.
    """
    jtbd = load_jtbd_map()
    known = jtbd.trajectory_slugs()
    violations: list[tuple[str, str]] = []
    for j in journeys:
        for traj in j.get("trajectories", []) or []:
            if str(traj) not in known:
                violations.append((j["name"], str(traj)))
    return violations


def run_matrix(
    driver: Driver,
    *,
    journey_pattern: str | None = None,
    checkpoint_filter: str | None = None,
    lane: str | None = None,
    tier: int | None = None,
) -> MatrixReport:
    """Run the (journey, checkpoint) matrix and return an aggregate report."""
    report = MatrixReport()
    report.started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    start = time.monotonic()

    journeys = _filtered_journeys(journey_pattern, lane, tier)
    pairs = _eligible_pairs(journeys, checkpoint_filter)

    # Plan 063 SSoT gate: a journey TOML naming an unknown trajectory
    # fails the matrix loudly (never silently). Run before any boxes
    # are provisioned so the failure is cheap.
    drift = _check_journey_trajectories(journeys)
    if drift:
        report.duration_s = round(time.monotonic() - start, 3)
        for journey_name, traj in drift:
            report.rows.append(MatrixRow(
                journey=journey_name,
                base_checkpoint="(jtbd-gate)",
                verdict="ERROR",
                reason=f"unknown trajectory in journey TOML: {traj!r} (see plan 063)",
                duration_s=0.0,
            ))
        report.journeys_run = len({r.journey for r in report.rows})
        report.error_count = sum(1 for r in report.rows if r.verdict == "ERROR")
        return report

    if os.environ.get(TIER1_FLAG) != "1":
        runnable_pairs: list[tuple[dict, str | None]] = []
        for journey, base in pairs:
            if int(journey.get("tier", 0)) == 1:
                report.rows.append(MatrixRow(
                    journey=journey["name"],
                    base_checkpoint=base or "(none)",
                    verdict="SKIP",
                    reason=TIER1_OPT_IN_REASON,
                    duration_s=0.0,
                ))
            else:
                runnable_pairs.append((journey, base))
        pairs = runnable_pairs

    # Group by base checkpoint so each base is resolved once per run; the
    # underlying cache + snapshot-fork makes subsequent forks cheap.
    by_base: dict[str | None, list[dict]] = {}
    for journey, base in pairs:
        by_base.setdefault(base, []).append(journey)

    for base, journey_group in by_base.items():
        for journey in journey_group:
            row_start = time.monotonic()
            try:
                if base is None:
                    # Legacy seed path: this matrix run doesn't seed
                    # automatically. The journey TOML must be runnable
                    # against a freshly-provisioned box, OR declare
                    # from_checkpoints. We mark SKIP with a clear note.
                    row = MatrixRow(
                        journey=journey["name"], base_checkpoint="(none)",
                        verdict="SKIP",
                        reason="no from_checkpoints; legacy seed flows are out of matrix scope",
                        duration_s=0.0,
                    )
                else:
                    cp_result = resolve_checkpoint(driver, base)
                    box = cp_result.box
                    j_result: JourneyResult = run_journey(driver, box, journey["name"])
                    row = MatrixRow(
                        journey=journey["name"],
                        base_checkpoint=base,
                        verdict=j_result.verdict,
                        reason=j_result.reason,
                        duration_s=round(time.monotonic() - row_start, 3),
                        checkpoint_cache_hit=cp_result.cache_hit,
                        box_id=box.box_id,
                    )
                    driver.teardown(box)
            except CheckpointError as exc:
                row = MatrixRow(
                    journey=journey["name"],
                    base_checkpoint=base or "(none)",
                    verdict="ERROR",
                    reason=f"checkpoint failed: {exc}",
                    duration_s=round(time.monotonic() - row_start, 3),
                )
            except Exception as exc:  # noqa: BLE001 - surface and continue
                row = MatrixRow(
                    journey=journey["name"],
                    base_checkpoint=base or "(none)",
                    verdict="ERROR",
                    reason=f"{type(exc).__name__}: {exc}",
                    duration_s=round(time.monotonic() - row_start, 3),
                )
            report.rows.append(row)

    report.duration_s = round(time.monotonic() - start, 3)
    report.journeys_run = len({r.journey for r in report.rows})
    report.pass_count = sum(1 for r in report.rows if r.verdict == "PASS")
    report.fail_count = sum(1 for r in report.rows if r.verdict == "FAIL")
    report.skip_count = sum(1 for r in report.rows if r.verdict == "SKIP")
    report.error_count = sum(1 for r in report.rows if r.verdict == "ERROR")
    return report


def write_report(report: MatrixReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")
