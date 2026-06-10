"""Executed-evidence ledger (otbox 2.0 phase 3).

The 1.0 defect this kills: quality tiers were self-declared labels on TOMLs,
counted by the gate whether or not the journey could even run. Here a
journey's EFFECTIVE tier derives only from executed evidence:

* keyed by the CONTENT HASH of the normalized TOML — evidence survives
  renames, resets on content change (rename-laundering is pointless);
* pending (= red) until the journey actually executed in its lane;
* assertion quality is computed from the same ASSERTION_KINDS registry the
  runner dispatches on (surface vs behavioral vs negative-space);
* effective gold additionally requires a fresh mutation kill — the mutation
  lane lands in phase 5, so UNTIL THEN NO JOURNEY CAN BE EFFECTIVE-GOLD.
  That is honest, not a bug.

SKIP-fails rule: a journey homed in pr/nightly whose verdict is SKIP is a
red row — graveyard formation is caught within one run, no cross-run state.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .journey import CATALOGUE_DIR, available_journeys

LEDGER_SCHEMA = "otbox.run_ledger.v1"

# Assertion-kind classification. Derived from the registry vocabulary —
# the sentinel test cross-checks that every registered kind is classified,
# so adding a kind without classifying it fails the suite.
SURFACE_KINDS = frozenset({"returncode", "stdout_contains", "stderr_contains"})
NEGATIVE_KINDS = frozenset(
    {"stdout_not_contains", "stderr_not_contains", "path_not_exists",
     "stdout_json_path_absent"}
)
BEHAVIORAL_KINDS = frozenset(
    {"stdout_json", "stdout_json_equals_var", "stdout_json_array_equals",
     "stdout_json_set_contains", "stdout_json_length_equals",
     "stdout_json_greater_equal", "stdout_json_contains", "path_exists",
     "file_count_min", "duration_ms_max"}
) | NEGATIVE_KINDS


def journey_content_hash(path: Path) -> str:
    """sha256 of the normalized TOML — comment/whitespace changes don't
    reset evidence; semantic changes do."""
    doc = tomllib.loads(path.read_text())
    canonical = json.dumps(doc, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def classify_assertions(path: Path) -> dict:
    doc = tomllib.loads(path.read_text())
    counts = {"behavioral": 0, "surface": 0, "negative": 0, "unknown": 0}
    for spec in doc.get("assertions", []):
        kind = spec.get("kind", "")
        if kind in NEGATIVE_KINDS:
            counts["negative"] += 1
            counts["behavioral"] += 1
        elif kind in BEHAVIORAL_KINDS or "equals_var" in spec:
            counts["behavioral"] += 1
        elif kind in SURFACE_KINDS:
            counts["surface"] += 1
        else:
            counts["unknown"] += 1
    return counts


@dataclass
class LedgerRow:
    journey: str
    content_hash: str
    ci_lane: str
    verdict: str            # PASS | FAIL | SKIP | ERROR | PENDING
    executed_at: str
    duration_s: float
    assertions: dict
    effective_tier: str = "pending"
    red: bool = False
    red_reason: str = ""
    quarantined: bool = False
    quarantine_issue: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Ledger:
    schema_version: str = LEDGER_SCHEMA
    generated_at: str = ""
    source: str = ""        # e.g. "local-matrix", "nightly:<run-id>"
    rows: list[LedgerRow] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rows"] = [r.to_dict() for r in self.rows]
        return d


def effective_tier(verdict: str, assertions: dict, *, kill_fresh: bool = False) -> str:
    """gold > silver > bronze > pending, from executed evidence only."""
    if verdict != "PASS":
        return "pending"
    behavioral = assertions.get("behavioral", 0)
    negative = assertions.get("negative", 0)
    if behavioral >= 3 and negative >= 1 and kill_fresh:
        return "gold"
    if behavioral >= 1:
        return "silver"
    return "bronze"


def build_ledger(
    matrix_report: dict,
    *,
    source: str,
    catalogue_dir: Path | None = None,
    now: _dt.datetime | None = None,
    kills: dict[str, str] | None = None,
) -> Ledger:
    """Compact a MatrixReport dict into the run ledger.

    Every catalogue journey gets a row — journeys the matrix never touched
    are PENDING (red when their lane should have run them).
    """
    from .catalogue_lint import load_quarantine

    cat = catalogue_dir or CATALOGUE_DIR
    stamp = (now or _dt.datetime.now(_dt.timezone.utc)).isoformat()
    today = (now or _dt.datetime.now(_dt.timezone.utc)).date()
    quarantine: dict[str, str] = {}
    for entry in load_quarantine():
        if entry.expires >= today:
            for j in entry.journeys:
                quarantine[j] = entry.issue
    by_journey: dict[str, dict] = {}
    for row in matrix_report.get("rows", []):
        # Keep the strongest verdict per journey (PASS beats SKIP across
        # multiple checkpoint pairings).
        cur = by_journey.get(row["journey"])
        rank = {"PASS": 3, "FAIL": 2, "ERROR": 2, "SKIP": 1}
        if cur is None or rank.get(row["verdict"], 0) > rank.get(cur["verdict"], 0):
            by_journey[row["journey"]] = row

    ledger = Ledger(generated_at=stamp, source=source)
    for meta in available_journeys():
        name = meta["name"]
        path = cat / f"{name}.toml"
        if not path.exists():
            continue
        run = by_journey.get(name)
        verdict = run["verdict"] if run else "PENDING"
        assertions = classify_assertions(path)
        content_hash = journey_content_hash(path)
        # A kill is fresh ONLY if it was witnessed against this exact
        # journey content — change the TOML, lose the kill.
        kill_fresh = bool(kills and kills.get(name) == content_hash)
        row = LedgerRow(
            journey=name,
            content_hash=content_hash,
            ci_lane=meta["ci_lane"],
            verdict=verdict,
            executed_at=stamp if run else "",
            duration_s=float(run.get("duration_s", 0.0)) if run else 0.0,
            assertions=assertions,
            effective_tier=effective_tier(verdict, assertions, kill_fresh=kill_fresh),
        )
        # Quarantined journeys are visible debt, not red: they carry their
        # issue URL and stay pending-tier until the quarantine resolves.
        # Expiry is enforced by the catalogue lint, so a stale entry fails
        # the suite there rather than silently extending here.
        if name in quarantine:
            row.quarantined = True
            row.quarantine_issue = quarantine[name]
        # SKIP-fails rule: pr/nightly journeys must execute in their lane.
        elif meta["ci_lane"] in ("pr", "nightly") and verdict in ("SKIP", "PENDING"):
            row.red = True
            row.red_reason = (
                f"{meta['ci_lane']}-lane journey did not execute "
                f"(verdict={verdict}): graveyard formation"
            )
        elif verdict in ("FAIL", "ERROR"):
            row.red = True
            row.red_reason = run.get("reason", "") if run else verdict
        ledger.rows.append(row)
    return ledger


def ledger_summary(ledger: Ledger) -> dict:
    tiers: dict[str, int] = {}
    for r in ledger.rows:
        tiers[r.effective_tier] = tiers.get(r.effective_tier, 0) + 1
    return {
        "rows": len(ledger.rows),
        "red": sum(1 for r in ledger.rows if r.red),
        "quarantined": sum(1 for r in ledger.rows if r.quarantined),
        "effective_tiers": tiers,
        "executed": sum(1 for r in ledger.rows if r.verdict not in ("PENDING",)),
    }
