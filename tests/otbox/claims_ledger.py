"""Parse the vendored claims ledger as the executable SSoT for release claims.

``tests/otbox/claims-ledger.md`` enumerates every falsifiable release claim
(axes A-I: capture, bucket, discovery, trails, context-tree, security,
datasets, agent-CLI, non-functional), its status, and the verifiers that
bind it. This module reads the ledger and exposes it to the release gate so:

1. A row with a status outside the vocabulary fails the gate.
2. A verified/partial row with no verifiers — or verifiers that don't
   resolve (journey missing from the catalogue, journey quarantined,
   pytest file absent) — fails the gate.
3. A tracked row without an issue ref (``#NNN`` or ``TBD-<tag>``) fails.

Vendored in-repo for the same reason as ``jtbd-command-map.md``: a gate
whose SSoT lives in the gitignored kb/ cannot run in any clean checkout.
The narrative draft remains in kb/; THIS module reads only the vendored
copy. Sibling map: ``claims_map.py`` (spec journeys J1-J18) stays at
journey granularity — this ledger is claim-granular.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAIMS_PATH = Path(__file__).resolve().parent / "claims-ledger.md"

STATUS_VOCAB = frozenset({"verified", "partial", "open", "waived", "tracked"})

# Axis letter -> (claim-id prefix, human name). The gate enforces the
# prefix/axis pairing so a row cannot silently drift into the wrong table.
AXES: dict[str, tuple[str, str]] = {
    "A": ("CAP", "capture"),
    "B": ("BKT", "bucket-privacy"),
    "C": ("DSC", "discovery"),
    "D": ("TRL", "trails"),
    "E": ("CTX", "context-tree"),
    "F": ("SEC", "security"),
    "G": ("DST", "datasets"),
    "H": ("AGT", "agent-cli"),
    "I": ("NF", "non-functional"),
}

# Issue refs: a real GitHub issue (#NNN) or a TBD placeholder for an issue
# decided-but-not-yet-filed (e.g. TBD-B0).
_ISSUE_RE = re.compile(r"^(#\d+|TBD-[A-Za-z0-9-]+)$")

# Ledger rows: | CAP-1 | claim text | A | verified | v1, v2 | #25 |
_ROW_RE = re.compile(
    r"^\|\s*([A-Z]+-\d+)\s*\|"  # claim id
    r"\s*([^|]+?)\s*\|"          # claim text
    r"\s*([A-Z])\s*\|"           # axis letter
    r"\s*([a-z-]+)\s*\|"         # status
    r"\s*([^|]*?)\s*\|"          # verifiers (comma-separated, may be —)
    r"\s*([^|]*?)\s*\|"          # issue refs (may be —)
    r"\s*$"
)

_EMPTY_CELL = {"", "—", "-", "–"}


@dataclass(frozen=True)
class ClaimRow:
    """One falsifiable release claim from the vendored ledger."""

    id: str
    claim: str
    axis: str
    status: str
    verifiers: tuple[str, ...]
    issues: tuple[str, ...]

    @property
    def journey_verifiers(self) -> tuple[str, ...]:
        return tuple(v for v in self.verifiers if not is_pytest_verifier(v))

    @property
    def pytest_verifiers(self) -> tuple[str, ...]:
        return tuple(v for v in self.verifiers if is_pytest_verifier(v))


def is_pytest_verifier(verifier: str) -> bool:
    """Verifiers are either catalogue journey names or pytest node-id
    prefixes; the latter always start with ``tests/``."""
    return verifier.startswith("tests/")


def _split_cell(cell: str) -> tuple[str, ...]:
    cell = cell.strip()
    if cell in _EMPTY_CELL:
        return ()
    return tuple(p.strip().strip("`") for p in cell.split(",") if p.strip())


def _parse_claims_doc(text: str) -> list[ClaimRow]:
    rows: list[ClaimRow] = []
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        m = _ROW_RE.match(line)
        if not m:
            continue
        cid, claim, axis, status, verifiers_cell, issue_cell = m.groups()
        rows.append(
            ClaimRow(
                id=cid,
                claim=claim.strip(),
                axis=axis,
                status=status,
                verifiers=_split_cell(verifiers_cell),
                issues=_split_cell(issue_cell),
            )
        )
    return rows


@lru_cache(maxsize=1)
def load_claims_ledger() -> list[ClaimRow]:
    """Parse the vendored ledger once per process; results are cached."""
    if not CLAIMS_PATH.exists():
        raise FileNotFoundError(f"claims ledger not found at {CLAIMS_PATH}")
    return _parse_claims_doc(CLAIMS_PATH.read_text())


# ---------------------------------------------------------------------------
# Validation (the gate)
# ---------------------------------------------------------------------------
def catalogue_journey_names() -> set[str]:
    from .journey import available_journeys

    return {j["name"] for j in available_journeys()}


def active_quarantined_journeys(today=None) -> set[str]:
    """Journeys under an unexpired quarantine entry. Expired entries are a
    catalogue-lint failure in their own right, so they don't grant cover."""
    import datetime as _dt

    from .catalogue_lint import load_quarantine

    today = today or _dt.date.today()
    out: set[str] = set()
    for entry in load_quarantine():
        if entry.expires >= today:
            out.update(entry.journeys)
    return out


def validate_ledger(
    rows: list[ClaimRow],
    *,
    journey_names: set[str] | None = None,
    quarantined: set[str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    """Return a list of human-readable problems; empty means the ledger is
    structurally sound. Pure function over injected world state so the unit
    tests can probe each rule without the real catalogue."""
    journey_names = journey_names if journey_names is not None else catalogue_journey_names()
    quarantined = quarantined if quarantined is not None else active_quarantined_journeys()

    problems: list[str] = []
    seen: set[str] = set()
    for row in rows:
        prefix = f"{row.id}:"
        if row.id in seen:
            problems.append(f"{prefix} duplicate claim id")
        seen.add(row.id)

        axis = AXES.get(row.axis)
        if axis is None:
            problems.append(f"{prefix} unknown axis {row.axis!r}")
        elif not row.id.startswith(axis[0] + "-"):
            problems.append(
                f"{prefix} id prefix does not match axis {row.axis} "
                f"(expected {axis[0]}-*)"
            )

        if row.status not in STATUS_VOCAB:
            problems.append(f"{prefix} unknown status {row.status!r}")

        if row.status in ("verified", "partial") and not row.verifiers:
            problems.append(f"{prefix} {row.status} row has no verifiers")

        for v in row.journey_verifiers:
            if v not in journey_names:
                problems.append(f"{prefix} verifier {v!r} is not a catalogue journey")
            elif row.status in ("verified", "partial") and v in quarantined:
                problems.append(
                    f"{prefix} {row.status} claim backed by quarantined journey {v!r}"
                )

        for v in row.pytest_verifiers:
            file_part = v.split("::", 1)[0]
            if not (repo_root / file_part).exists():
                problems.append(f"{prefix} pytest verifier file missing: {file_part}")

        if row.status == "verified" and row.journey_verifiers:
            resolvable = [
                v for v in row.journey_verifiers
                if v in journey_names and v not in quarantined
            ]
            if not resolvable and not row.pytest_verifiers:
                problems.append(
                    f"{prefix} verified claim's ONLY verifiers are quarantined/unknown"
                )

        if row.status == "tracked" and not row.issues:
            problems.append(f"{prefix} tracked row carries no issue ref")
        for ref in row.issues:
            if not _ISSUE_RE.match(ref):
                problems.append(f"{prefix} malformed issue ref {ref!r}")

    return problems


# ---------------------------------------------------------------------------
# Run-ledger derivation (executed evidence -> per-claim status)
# ---------------------------------------------------------------------------
# Derived statuses mirror claims_map.spec_status semantics: a claim's
# executed status comes from the run ledger, never from this file — the
# ledger row only says what counts as evidence.
NO_JOURNEY_EVIDENCE = "no-journey-evidence"


def derive_claim_status(row: ClaimRow, verdicts: dict[str, str]) -> str:
    """verified > partial > pending from the claim's journey verifiers'
    executed verdicts. Claims with no journey verifiers (pytest-only or
    none) derive ``no-journey-evidence`` — the pytest lanes attest those."""
    journeys = row.journey_verifiers
    if not journeys:
        return NO_JOURNEY_EVIDENCE
    states = [verdicts.get(j, "PENDING") for j in journeys]
    if all(s == "PASS" for s in states):
        return "verified"
    if any(s == "PASS" for s in states):
        return "partial"
    return "pending"


def derive_claim_statuses(
    run_ledger: dict, rows: list[ClaimRow] | None = None
) -> dict[str, str]:
    """Per-claim derived status from a run-ledger JSON dict
    (``otbox.run_ledger.v1`` shape, see ledger.py)."""
    rows = rows if rows is not None else load_claims_ledger()
    verdicts = {
        r["journey"]: r.get("verdict", "PENDING")
        for r in run_ledger.get("rows", [])
    }
    return {row.id: derive_claim_status(row, verdicts) for row in rows}
