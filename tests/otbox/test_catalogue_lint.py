"""Catalogue lint gate (otbox 2.0 phase 2).

Three layers:
1. THE GATE — the live catalogue has zero non-quarantined violations.
2. Lint kills — the lint demonstrably catches each planted violation class
   (a lint that cannot fail is vacuous).
3. Quarantine hygiene — expired/unknown/oversized quarantine entries fail.
"""

from __future__ import annotations

import datetime as _dt
import re
import textwrap
from pathlib import Path

from .catalogue_lint import (
    MAX_QUARANTINE_ENTRIES,
    PRECONDITION_VOCAB,
    QUARANTINE_PATH,
    lint_catalogue,
    load_quarantine,
)

TODAY = _dt.date(2026, 6, 10)


# -- 1. the gate ---------------------------------------------------------------

def test_catalogue_is_lint_clean_outside_quarantine():
    failing, quarantined = lint_catalogue(today=_dt.date.today())
    detail = "\n".join(str(v) for v in failing)
    assert not failing, (
        f"{len(failing)} catalogue lint violation(s) outside quarantine "
        f"(quarantined: {len(quarantined)}):\n{detail}"
    )


def test_quarantine_within_cap_and_loadable():
    entries = load_quarantine()
    assert len(entries) <= MAX_QUARANTINE_ENTRIES
    for e in entries:
        assert e.issue.startswith("http")
        assert e.expires >= e.added


# -- 2. lint kills --------------------------------------------------------------

def _write_journey(tmp_path: Path, body: str, name: str = "planted") -> Path:
    cat = tmp_path / "journeys"
    cat.mkdir(exist_ok=True)
    (cat / f"{name}.toml").write_text(textwrap.dedent(body))
    return cat


def _rules(tmp_path: Path) -> set[str]:
    failing, _ = lint_catalogue(
        catalogue_dir=tmp_path / "journeys",
        quarantine_path=tmp_path / "no-quarantine.toml",
        today=TODAY,
    )
    return {v.rule for v in failing}


def test_lint_kills_unknown_assertion_kind(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        [[steps]]
        id = "s1"
        argv = ["status"]
        [[assertions]]
        kind = "totally_made_up"
        step = "s1"
    """)
    assert "unknown-assertion-kind" in _rules(tmp_path)


def test_lint_kills_unregistered_checkpoint(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        from_checkpoints = ["c-does-not-exist"]
    """)
    assert "unregistered-checkpoint" in _rules(tmp_path)


def test_lint_kills_unknown_precondition(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        [preconditions]
        invented_key = true
    """)
    assert "unknown-precondition" in _rules(tmp_path)


def test_lint_kills_unknown_step_ref(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        [[steps]]
        id = "declared"
        argv = ["status"]
        [[assertions]]
        kind = "returncode"
        step = "undeclared"
        equals = 0
    """)
    assert "unknown-step-ref" in _rules(tmp_path)


def test_lint_kills_self_comparing_equals_var(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        [[steps]]
        id = "s1"
        argv = ["status"]
        [[assertions]]
        kind = "stdout_json_equals_var"
        step = "s1"
        path = "digest"
        equals_var = "s1:digest"
    """)
    assert "self-comparing-var" in _rules(tmp_path)


def test_clean_journey_produces_no_violations(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        [[steps]]
        id = "s1"
        argv = ["status"]
        [[assertions]]
        kind = "returncode"
        step = "s1"
        equals = 0
    """)
    assert _rules(tmp_path) == set()


# -- 3. quarantine hygiene -------------------------------------------------------

def _write_quarantine(tmp_path: Path, body: str) -> Path:
    q = tmp_path / "Q.toml"
    q.write_text(textwrap.dedent(body))
    return q


def test_expired_quarantine_entry_fails(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        from_checkpoints = ["c-does-not-exist"]
    """)
    q = _write_quarantine(tmp_path, """
        [[entries]]
        journeys = ["planted"]
        rules = ["unregistered-checkpoint"]
        reason = "x"
        issue = "https://example.com/1"
        added = 2026-01-01
        expires = 2026-02-01
    """)
    failing, quarantined = lint_catalogue(
        catalogue_dir=tmp_path / "journeys", quarantine_path=q, today=TODAY
    )
    rules = {v.rule for v in failing}
    # the entry no longer downgrades (expired) AND is itself a violation
    assert "quarantine-expired" in rules
    assert "unregistered-checkpoint" in rules
    assert not quarantined


def test_unexpired_quarantine_downgrades_only_matching_rules(tmp_path):
    _write_journey(tmp_path, """
        name = "planted"
        from_checkpoints = ["c-does-not-exist"]
        [preconditions]
        invented_key = true
    """)
    q = _write_quarantine(tmp_path, """
        [[entries]]
        journeys = ["planted"]
        rules = ["unregistered-checkpoint"]
        reason = "x"
        issue = "https://example.com/1"
        added = 2026-06-01
        expires = 2026-12-01
    """)
    failing, quarantined = lint_catalogue(
        catalogue_dir=tmp_path / "journeys", quarantine_path=q, today=TODAY
    )
    assert {v.rule for v in quarantined} == {"unregistered-checkpoint"}
    assert {v.rule for v in failing} == {"unknown-precondition"}


def test_quarantine_unknown_journey_fails(tmp_path):
    (tmp_path / "journeys").mkdir()
    q = _write_quarantine(tmp_path, """
        [[entries]]
        journeys = ["ghost-journey"]
        reason = "x"
        issue = "https://example.com/1"
        added = 2026-06-01
        expires = 2026-12-01
    """)
    failing, _ = lint_catalogue(
        catalogue_dir=tmp_path / "journeys", quarantine_path=q, today=TODAY
    )
    assert "quarantine-unknown-journey" in {v.rule for v in failing}


# -- 4. vocab drift sentinel ------------------------------------------------------

def test_precondition_vocab_matches_resolver_source():
    """Every preconditions.get("<key>") the resolver reads must be in
    PRECONDITION_VOCAB — adding a key there without widening the vocab would
    make lint reject journeys the runner actually supports."""
    src = (Path(__file__).parent / "journey.py").read_text()
    body = src.split("def _checkpoint_satisfies", 1)[1].split("\ndef ", 1)[0]
    keys = set(re.findall(r"preconditions\.get\(\s*[\"']([a-z_]+)[\"']", body))
    # the for-loop flag block lists names as plain strings
    for m in re.finditer(r"for flag in \(([^)]*)\)", body):
        keys |= set(re.findall(r"[\"']([a-z_]+)[\"']", m.group(1)))
    assert keys <= PRECONDITION_VOCAB, f"resolver keys missing from vocab: {keys - PRECONDITION_VOCAB}"
