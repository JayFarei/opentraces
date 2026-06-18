"""Non-overridable dataset reader security floor (issue #84).

The dataset reader's redaction floor must run over every projected row
regardless of the workflow author's declared security contract. A third-party
workflow contract may only ADD tools; it can never narrow the row below the
floor, and the default ``tier="off"`` no longer ships rows verbatim.

Red-before-green proof (codex finding #5): the secret is an ENTROPY-ONLY,
non-regex high-entropy token, asserted at the ``sanitize_dataset_row(...).row``
level — NOT through the publish gate, whose ``scan_serialized`` would mask a
regex/entropy leak. Pre-fix a narrowing ``tools=["regex"]`` contract runs only
``regex`` over the dict (entropy/business_logic suppressed), so the entropy-only
token SURVIVES — that is the red assertion. (The pre-fix code did already run
path_anonymizer unconditionally whenever any tool ran, so the seeded path was
redacted even pre-fix; the entropy token, not the path, is what goes red.) The
``tier="off"`` path is fully red pre-fix: it shipped the row VERBATIM, so token
and path both survived. Post-fix the floor union redacts the token at every
tier, and path_anonymizer is now an explicit floor member rather than an
incidental always-on step.
"""

from __future__ import annotations

from opentraces.security.dataset_rows import (
    DATASET_ROW_FLOOR,
    DATASET_ROW_TOOLS,
    _TIER_TOOLS,
    sanitize_dataset_row,
)

# A high-entropy token regex alone does NOT catch but the entropy detector does
# (verified empirically). The whole point of red-before-green: under a
# ``["regex"]`` contract this survives pre-fix and is redacted post-fix.
ENTROPY_ONLY_TOKEN = "Zx9Qw3Vb7Np2Kr8Lf4Dj6Hs1Tg5Mc0Ya"
# A username inside a path that is NOT the current OS user, so path_anonymizer's
# auto-detection (not the explicit USER override) is what must redact it.
SEEDED_PATH = "/Users/seededvictim/work/secret/notes.txt"


def _row() -> dict:
    return {
        "summary": f"token is {ENTROPY_ONLY_TOKEN} and file at {SEEDED_PATH}",
    }


def test_floor_constant_is_row_runnable():
    """Every floor tool must be a row-runnable tool (no impossible floor)."""
    assert set(DATASET_ROW_FLOOR) <= set(DATASET_ROW_TOOLS)
    assert DATASET_ROW_FLOOR == ("regex", "entropy", "business_logic", "path_anonymizer")


def test_floor_superset_of_tier_medium():
    """No-drift guard: the floor must cover the strongest tier mapping."""
    assert set(_TIER_TOOLS["medium"]) <= set(DATASET_ROW_FLOOR)
    assert set(_TIER_TOOLS["high"]) <= set(DATASET_ROW_FLOOR)


def test_narrowing_contract_still_redacts_secret():
    """A ``["regex"]`` contract cannot suppress the entropy floor (the bug)."""
    out = sanitize_dataset_row(_row(), tools=["regex"])
    # Entropy ran despite the regex-only author contract -> token redacted.
    assert ENTROPY_ONLY_TOKEN not in out.row["summary"]
    # path_anonymizer ran -> seeded username redacted.
    assert "seededvictim" not in out.row["summary"]
    assert set(DATASET_ROW_FLOOR) <= set(out.security.effective_tools)
    assert out.security.floor_satisfied is True


def test_tier_off_runs_floor():
    """The DEFAULT tier='off' + no contract must run the floor (not verbatim)."""
    out = sanitize_dataset_row(_row(), privacy_tier="off", tools=None)
    assert ENTROPY_ONLY_TOKEN not in out.row["summary"]
    assert "seededvictim" not in out.row["summary"]
    assert out.security.filtered is True
    assert set(DATASET_ROW_FLOOR) <= set(out.security.effective_tools)
    assert out.security.floor_satisfied is True


def test_author_can_only_add():
    """An author contract is additive: effective == union(floor, requested)."""
    out = sanitize_dataset_row(_row(), tools=["business_logic"])
    assert set(out.security.requested_tools) == {"business_logic"}
    assert set(out.security.effective_tools) == set(DATASET_ROW_FLOOR) | {"business_logic"}
    # The floor is never dropped below itself.
    assert set(DATASET_ROW_FLOOR) <= set(out.security.effective_tools)


def test_empty_contract_cannot_drop_floor():
    """An empty (all-disabled) contract still gets the full floor (finding #2).

    Mirrors a policy where ``allow_disable_required=true`` disabled every tool:
    at the resolve layer that surfaces as ``tools=[]`` (or ``None``), and the
    floor union must STILL run — no flag can remove a floor tool.
    """
    for requested in ([], None):
        out = sanitize_dataset_row(_row(), privacy_tier="off", tools=requested)
        assert set(DATASET_ROW_FLOOR) <= set(out.security.effective_tools)
        assert ENTROPY_ONLY_TOKEN not in out.row["summary"]


def test_manifest_records_author_vs_effective():
    """Per-row security records author-declared vs floor-resolved distinctly."""
    out = sanitize_dataset_row(_row(), tools=["regex"])
    assert out.security.requested_tools == ("regex",)
    assert set(out.security.effective_tools) == set(DATASET_ROW_FLOOR) | {"regex"}
    assert out.security.floor == DATASET_ROW_FLOOR
    assert out.security.floor_satisfied is True


def test_path_anonymizer_is_idempotent():
    """Sanitizing an already-sanitized row must not re-hash a hashed path."""
    once = sanitize_dataset_row(_row(), tools=["regex"])
    twice = sanitize_dataset_row(once.row, tools=["regex"])
    assert once.row["summary"] == twice.row["summary"]
