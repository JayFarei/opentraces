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
4. The parse FAILS CLOSED (PR #63): a ``|``-line that is not the column
   header or separator must parse as a claim row or it is recorded as a
   malformed-row violation (line number + snippet), never silently
   dropped; and the header status-counts line ("N verified, N partial,
   ... — N rows") must reconcile with the parsed tally, which also
   catches whole-row deletions and stale headers.

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

# A claim row has exactly these 6 cells (mirrors _ROW_RE's groups).
EXPECTED_CELLS = 6

# Table separator: |---|---|...  (optionally :-aligned)
_SEPARATOR_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")

# Something that LOOKS like a claim id (used to name the offending row in
# malformed-row violations even when the row itself doesn't parse).
_CLAIM_ID_RE = re.compile(r"^[A-Z]+-\d+$")

# Header status-counts line, e.g.:
#   - Status counts (2026-06-11, ...): 26 verified, 15 partial,
#     8 open, 7 tracked, 0 waived — 56 rows.
# (may wrap across physical lines; parsed up to the next blank line)
_COUNTS_HEAD_RE = re.compile(r"Status counts[^:\n]*:")
_COUNT_PAIR_RE = re.compile(r"(\d+)\s+(verified|partial|open|waived|tracked)\b")
_ROW_TOTAL_RE = re.compile(r"(\d+)\s+rows?\b")


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


@dataclass(frozen=True)
class MalformedRow:
    """A ``|``-line inside the ledger that should be a claim row but
    doesn't parse. Recorded, never dropped (PR #63 fail-closed)."""

    line_no: int
    snippet: str
    reason: str
    claim_id: str | None  # first cell, when it looks like a claim id


@dataclass(frozen=True)
class HeaderCounts:
    """The declared status tally from the ledger's status-counts line."""

    counts: tuple[tuple[str, int], ...]  # (status, declared) pairs
    total: int | None
    line_no: int

    def declared(self, status: str) -> int:
        return dict(self.counts).get(status, 0)


@dataclass(frozen=True)
class LedgerParse:
    """Full fail-closed parse result: rows + everything that did NOT
    parse + the header's declared tally."""

    rows: tuple[ClaimRow, ...]
    malformed: tuple[MalformedRow, ...]
    header_counts: HeaderCounts | None


def is_pytest_verifier(verifier: str) -> bool:
    """Verifiers are either catalogue journey names or pytest node-id
    prefixes; the latter always start with ``tests/``."""
    return verifier.startswith("tests/")


def _split_cell(cell: str) -> tuple[str, ...]:
    cell = cell.strip()
    if cell in _EMPTY_CELL:
        return ()
    return tuple(p.strip().strip("`") for p in cell.split(",") if p.strip())


def _table_cells(stripped: str) -> list[str]:
    """``| a | b |`` -> ``["a", "b"]`` (leading/trailing pipes removed)."""
    inner = stripped
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [c.strip() for c in inner.split("|")]


def _parse_header_counts(text: str) -> HeaderCounts | None:
    m = _COUNTS_HEAD_RE.search(text)
    if m is None:
        return None
    line_no = text.count("\n", 0, m.start()) + 1
    tail = text[m.end():]
    cut = tail.find("\n\n")  # the counts line may wrap; ends at a blank line
    region = tail if cut == -1 else tail[:cut]
    counts = tuple(
        (status, int(n)) for n, status in _COUNT_PAIR_RE.findall(region)
    )
    total_m = _ROW_TOTAL_RE.search(region)
    total = int(total_m.group(1)) if total_m else None
    return HeaderCounts(counts=counts, total=total, line_no=line_no)


def parse_claims_doc(text: str) -> LedgerParse:
    """Fail-closed parse: every line that starts with ``|`` and is not the
    column header or separator must parse as a claim row, or it is
    recorded in ``malformed`` (line number + snippet), never dropped."""
    rows: list[ClaimRow] = []
    malformed: list[MalformedRow] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if _SEPARATOR_RE.match(stripped):
            continue
        cells = _table_cells(stripped)
        if cells and cells[0].lower() == "id":
            continue  # column-header row
        m = _ROW_RE.match(stripped)
        if m:
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
            continue
        claim_id = cells[0] if cells and _CLAIM_ID_RE.match(cells[0]) else None
        if len(cells) != EXPECTED_CELLS:
            reason = f"expected {EXPECTED_CELLS} cells, found {len(cells)}"
        else:
            reason = "does not match the claim-row grammar"
        snippet = stripped if len(stripped) <= 100 else stripped[:97] + "..."
        malformed.append(
            MalformedRow(
                line_no=line_no, snippet=snippet, reason=reason, claim_id=claim_id
            )
        )
    return LedgerParse(
        rows=tuple(rows),
        malformed=tuple(malformed),
        header_counts=_parse_header_counts(text),
    )


@lru_cache(maxsize=1)
def parse_claims_ledger() -> LedgerParse:
    """Parse the vendored ledger once per process; results are cached."""
    if not CLAIMS_PATH.exists():
        raise FileNotFoundError(f"claims ledger not found at {CLAIMS_PATH}")
    return parse_claims_doc(CLAIMS_PATH.read_text())


@lru_cache(maxsize=1)
def load_claims_ledger() -> list[ClaimRow]:
    """Backward-compatible row view over :func:`parse_claims_ledger`.

    Gate callers that want the fail-closed structural checks should pass
    the full ``parse_claims_ledger()`` result to :func:`validate_ledger`.
    """
    return list(parse_claims_ledger().rows)


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


def _structural_problems(parse: LedgerParse) -> list[str]:
    """Malformed-row violations + header-counts reconciliation. Catches
    rows the old parser silently dropped (PR #63) AND whole-row deletions
    or stale headers via the declared-vs-parsed tally."""
    problems: list[str] = []
    for bad in parse.malformed:
        ident = f" ({bad.claim_id})" if bad.claim_id else ""
        problems.append(
            f"line {bad.line_no}{ident}: malformed ledger row, {bad.reason}: "
            f"{bad.snippet!r}"
        )

    hc = parse.header_counts
    if hc is None:
        problems.append(
            "ledger header: status-counts line missing "
            "(expected '- Status counts (...): N verified, ... N rows.')"
        )
        return problems

    tally: dict[str, int] = {}
    for row in parse.rows:
        tally[row.status] = tally.get(row.status, 0) + 1
    for status in sorted(STATUS_VOCAB):
        declared = hc.declared(status)
        parsed = tally.get(status, 0)
        if declared != parsed:
            problems.append(
                f"ledger header (line {hc.line_no}): declares {declared} "
                f"{status} but {parsed} parsed"
            )
    if hc.total is None:
        problems.append(
            f"ledger header (line {hc.line_no}): row total missing "
            "(expected '... N rows.')"
        )
    elif hc.total != len(parse.rows):
        problems.append(
            f"ledger header (line {hc.line_no}): declares {hc.total} rows "
            f"but {len(parse.rows)} parsed"
        )
    return problems


def validate_ledger(
    rows: list[ClaimRow] | LedgerParse,
    *,
    journey_names: set[str] | None = None,
    quarantined: set[str] | None = None,
    repo_root: Path = REPO_ROOT,
) -> list[str]:
    """Return a list of human-readable problems; empty means the ledger is
    structurally sound. Pure function over injected world state so the unit
    tests can probe each rule without the real catalogue.

    Pass a :class:`LedgerParse` (from ``parse_claims_ledger()``) to ALSO
    run the fail-closed structural checks: malformed-row violations and
    the header status-counts reconciliation. A bare ``list[ClaimRow]``
    keeps the row-rule-only behavior for synthetic unit probes."""
    journey_names = journey_names if journey_names is not None else catalogue_journey_names()
    quarantined = quarantined if quarantined is not None else active_quarantined_journeys()

    problems: list[str] = []
    if isinstance(rows, LedgerParse):
        problems.extend(_structural_problems(rows))
        rows = list(rows.rows)

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
