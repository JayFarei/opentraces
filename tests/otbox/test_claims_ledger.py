"""Claims-ledger gate — the vendored release-claims SSoT must stay sound.

Mirrors test_jtbd_ssot.py: parse the vendored markdown, cross-check it
against the live catalogue + quarantine, and fail CI loudly the moment a
claim's verifier stops resolving. Runs in the nightly otbox sweep.

Layers:
1. THE GATE — the live ledger parses and validates clean.
2. Rule kills — each validation rule demonstrably catches a planted
   violation (a gate that cannot fail is vacuous).
3. Derivation — per-claim status derives from a run-ledger JSON
   (executed evidence), never from the ledger file itself.
"""

from __future__ import annotations

import pytest

from .claims_ledger import (
    AXES,
    CLAIMS_PATH,
    STATUS_VOCAB,
    ClaimRow,
    NO_JOURNEY_EVIDENCE,
    derive_claim_status,
    derive_claim_statuses,
    load_claims_ledger,
    validate_ledger,
)


@pytest.fixture(autouse=True)
def _isolate_opentraces_global_state():
    """Override conftest.py's HOME-isolation; SSoT checks don't touch HOME."""
    yield


# -- 1. the gate ---------------------------------------------------------------

def test_claims_ledger_parses():
    assert CLAIMS_PATH.exists(), f"vendored claims ledger missing: {CLAIMS_PATH}"
    rows = load_claims_ledger()
    assert len(rows) >= 46, f"ledger shrank to {len(rows)} rows (expected >= 46)"
    ids = [r.id for r in rows]
    assert len(ids) == len(set(ids)), "duplicate claim ids"


def test_claims_ledger_validates_clean():
    problems = validate_ledger(load_claims_ledger())
    detail = "\n".join(f"  {p}" for p in problems)
    assert not problems, f"{len(problems)} claims-ledger violation(s):\n{detail}"


def test_status_vocab_is_exhaustive():
    outside = [r.id for r in load_claims_ledger() if r.status not in STATUS_VOCAB]
    assert outside == []


def test_every_axis_has_rows():
    by_axis = {r.axis for r in load_claims_ledger()}
    assert by_axis == set(AXES), f"axes without rows: {set(AXES) - by_axis}"


def test_tracked_rows_carry_issue_refs():
    for row in load_claims_ledger():
        if row.status == "tracked":
            assert row.issues, f"{row.id}: tracked without an issue ref"


# -- 2. rule kills --------------------------------------------------------------

_WORLD = {"journey_names": {"real-journey", "qj"}, "quarantined": {"qj"}}


def _row(**kw) -> ClaimRow:
    base = dict(
        id="CAP-1", claim="x", axis="A", status="open", verifiers=(), issues=()
    )
    base.update(kw)
    return ClaimRow(**base)


def test_kill_unknown_status():
    problems = validate_ledger([_row(status="believed")], **_WORLD)
    assert any("unknown status" in p for p in problems)


def test_kill_empty_verifiers_on_verified_and_partial():
    for status in ("verified", "partial"):
        problems = validate_ledger([_row(status=status)], **_WORLD)
        assert any("no verifiers" in p for p in problems), status


def test_kill_unknown_journey_verifier():
    problems = validate_ledger(
        [_row(status="partial", verifiers=("ghost-journey",))], **_WORLD
    )
    assert any("not a catalogue journey" in p for p in problems)


def test_kill_quarantined_verifier_backing_verified_claim():
    problems = validate_ledger(
        [_row(status="verified", verifiers=("qj",))], **_WORLD
    )
    assert any("quarantined journey" in p for p in problems)
    assert any("ONLY verifiers are quarantined" in p for p in problems)


def test_kill_missing_pytest_verifier_file():
    problems = validate_ledger(
        [_row(status="partial", verifiers=("tests/does_not_exist.py::test_x",))],
        **_WORLD,
    )
    assert any("pytest verifier file missing" in p for p in problems)


def test_kill_tracked_without_issue():
    problems = validate_ledger([_row(status="tracked")], **_WORLD)
    assert any("no issue ref" in p for p in problems)


def test_kill_malformed_issue_ref():
    problems = validate_ledger(
        [_row(status="tracked", issues=("issue-42",))], **_WORLD
    )
    assert any("malformed issue ref" in p for p in problems)


def test_kill_axis_prefix_mismatch():
    problems = validate_ledger([_row(id="BKT-1", axis="A")], **_WORLD)
    assert any("does not match axis" in p for p in problems)


def test_clean_row_produces_no_problems():
    problems = validate_ledger(
        [_row(status="verified", verifiers=("real-journey",))], **_WORLD
    )
    assert problems == []


# -- 3. run-ledger derivation -----------------------------------------------------

_SYNTHETIC_LEDGER = {
    "schema_version": "otbox.run_ledger.v1",
    "rows": [
        {"journey": "j-pass-1", "verdict": "PASS"},
        {"journey": "j-pass-2", "verdict": "PASS"},
        {"journey": "j-skip", "verdict": "SKIP"},
        {"journey": "j-fail", "verdict": "FAIL"},
    ],
}


def test_derive_status_weakest_link():
    verdicts = {r["journey"]: r["verdict"] for r in _SYNTHETIC_LEDGER["rows"]}
    assert derive_claim_status(
        _row(verifiers=("j-pass-1", "j-pass-2")), verdicts
    ) == "verified"
    assert derive_claim_status(
        _row(verifiers=("j-pass-1", "j-skip")), verdicts
    ) == "partial"
    assert derive_claim_status(
        _row(verifiers=("j-skip", "j-fail")), verdicts
    ) == "pending"
    assert derive_claim_status(
        _row(verifiers=("j-never-ran",)), verdicts
    ) == "pending"
    # pytest-only and verifier-less claims are attested by the pytest
    # lanes, not the journey run ledger.
    assert derive_claim_status(
        _row(verifiers=("tests/otbox/test_determinism.py",)), verdicts
    ) == NO_JOURNEY_EVIDENCE
    assert derive_claim_status(_row(), verdicts) == NO_JOURNEY_EVIDENCE


def test_derive_claim_statuses_over_synthetic_ledger():
    rows = [
        _row(id="CAP-1", status="verified", verifiers=("j-pass-1",)),
        _row(id="CAP-2", status="partial", verifiers=("j-pass-1", "j-fail")),
        _row(id="CAP-3", status="open"),
    ]
    derived = derive_claim_statuses(_SYNTHETIC_LEDGER, rows)
    assert derived == {
        "CAP-1": "verified",
        "CAP-2": "partial",
        "CAP-3": NO_JOURNEY_EVIDENCE,
    }
