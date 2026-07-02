"""Agent-contract lint gate (ADR-0007 L1-L6, epic #129).

Modeled on tests/otbox/test_catalogue_lint.py. Layers:

1. THE GATE — the live CLI has zero L1-L6 violations OUTSIDE quarantine.
2. Clause kills — each clause demonstrably catches a planted violation
   (a lint that cannot fail is vacuous).
3. Quarantine hygiene — the table only shrinks and F1's own clauses (L1-L3)
   carry no quarantine entry (proving the emission fixes landed).

The execution clauses run each command as a SUBPROCESS with an isolated HOME
and a hard timeout, so a hanging command becomes a named failure, never a
wedged CI job. This whole file is default-CI-safe (no network, no daemon).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import pytest

from .agent_contract_lint import (
    COMMAND_TIMEOUT_S,
    F1_OWNED_CLAUSES,
    GUARD_COMMANDS,
    HANG_CLUSTER,
    QUARANTINE,
    QUARANTINE_BASELINE,
    RunResult,
    Violation,
    l1_terminates,
    l2_no_prompt,
    l3_valid_json,
    l4_remote_uniformity,
    l5_uniform_envelope,
    l6_self_consistency,
    lint_agent_contract,
)


# -- subprocess runners --------------------------------------------------------
def _isolated_env(home: Path) -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("OPENTRACES_", "HF_", "CLAUDE"))
    }
    env["HOME"] = str(home)
    env["OPENTRACES_NO_COLOR"] = "1"
    env["BROWSER"] = "true"
    return env


def _run(argv: list[str], *, home: Path, cwd: Path, stdin) -> RunResult:
    full = [sys.executable, "-m", "opentraces", "--json", *argv]
    try:
        proc = subprocess.run(
            full,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_S,
            env=_isolated_env(home),
            cwd=str(cwd),
            stdin=stdin,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(argv, -1, exc.stdout or "", exc.stderr or "", True)
    return RunResult(argv, proc.returncode, proc.stdout, proc.stderr, False)


def _fresh_runner(path: str) -> RunResult:
    """Run one command in its OWN fresh empty world (L1/L3/L5)."""
    home = Path(tempfile.mkdtemp(prefix="ot-acl-home-"))
    return _run(path.split(), home=home, cwd=home, stdin=subprocess.DEVNULL)


def _guard_runner(argv: list[str]) -> RunResult:
    """Run an L2 guard target with CLOSED stdin under --json."""
    home = Path(tempfile.mkdtemp(prefix="ot-acl-guard-"))
    return _run(argv, home=home, cwd=home, stdin=subprocess.DEVNULL)


# -- seeded fixture bucket (L1 part B + L6) -----------------------------------
def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "README.md").write_text("# seed\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


@pytest.fixture
def seeded_bucket(tmp_path):
    """A small hermetic bucket (3 scanned traces) under the conftest-isolated
    HOME, plus a git-repo cwd for the trail/blame verbs. Returns ``(home, repo)``.

    The conftest ``_isolate_opentraces_global_state`` fixture has already
    pointed the in-process store at ``$HOME/.opentraces``, so writing here and
    then running the CLI subprocess with the SAME HOME reads one bucket.
    """
    from opentraces.core import paths as _paths
    from opentraces.core.bucket_store import write_trace_record
    from opentraces.security import SECURITY_VERSION
    from opentraces_schema import Agent, Step, TraceRecord

    home = Path(_paths.OPENTRACES_DIR).parent
    for i in range(3):
        rec = TraceRecord(
            trace_id=f"acl-trace-{i}",
            session_id=f"acl-session-{i}",
            agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
            task={"description": "seed"},
            steps=[Step(step_index=1, role="user", content="seed")],
            outcome={"success": True, "committed": False},
        )
        rec.security.scanned = True
        rec.security.classifier_version = SECURITY_VERSION
        write_trace_record(
            rec,
            project_slug="acl-demo",
            source_layer="canonical",
            legacy_mirror=False,
            privacy_tier="medium",
        )
    repo = tmp_path / "acl-repo"
    _init_repo(repo)
    return home, repo


def _l6_docs(home: Path, repo: Path) -> dict:
    docs = {}
    for name, argv in (
        ("bucket status", ["bucket", "status"]),
        ("bucket manifest", ["bucket", "manifest"]),
        ("bucket security status", ["bucket", "security", "status"]),
    ):
        res = _run(argv, home=home, cwd=repo, stdin=subprocess.DEVNULL)
        try:
            docs[name] = json.loads(res.stdout.strip())
        except ValueError:
            docs[name] = None
    return docs


# -- 1. THE GATE ---------------------------------------------------------------
def test_agent_contract_is_lint_clean_outside_quarantine(seeded_bucket, capsys):
    home, repo = seeded_bucket
    hang_runner = lambda argv: _run(argv, home=home, cwd=repo, stdin=subprocess.DEVNULL)  # noqa: E731
    failing, quarantined = lint_agent_contract(
        sweep_runner=_fresh_runner,
        guard_runner=_guard_runner,
        l6_docs=_l6_docs(home, repo),
        hang_runner=hang_runner,
    )
    with capsys.disabled():
        print(
            f"\n[agent-contract-lint] failing={len(failing)} "
            f"quarantined={len(quarantined)} (baseline {QUARANTINE_BASELINE})"
        )
    detail = "\n".join(str(v) for v in failing)
    assert not failing, (
        f"{len(failing)} agent-contract violation(s) outside quarantine "
        f"(quarantined: {len(quarantined)}):\n{detail}"
    )


# -- 2. clause kills -----------------------------------------------------------
def test_kill_l1_hang():
    res = RunResult(["ctx", "resume", "x"], -1, "", "", timed_out=True)
    assert [v.clause for v in l1_terminates("ctx resume", res)] == ["L1"]


def test_kill_l1_traceback():
    res = RunResult(["x"], 1, "Traceback (most recent call last):\n ...", "", False)
    assert [v.clause for v in l1_terminates("x", res)] == ["L1"]


def test_kill_l2_prompt_bytes():
    res = RunResult(["setup"], 0, "│  [y/N] Aborted!", "", False)
    rules = l2_no_prompt("setup", res, full_contract=True)
    assert any("prompt byte" in v.message for v in rules)


def test_kill_l2_zero_exit_without_error_envelope():
    res = RunResult(["setup"], 0, '{"status": "ok"}', "", False)
    msgs = [v.message for v in l2_no_prompt("setup", res, full_contract=True)]
    assert any("exited 0" in m for m in msgs)
    assert any("error" in m for m in msgs)


def test_kill_l3_sentinel_under_explicit_json():
    res = RunResult(["x"], 0, "\n---OPENTRACES_JSON---\n{}", "", False)
    assert [v.clause for v in l3_valid_json("x", res)] == ["L3"]


def test_kill_l3_unparseable_json():
    res = RunResult(["x"], 0, "not json at all", "", False)
    assert [v.clause for v in l3_valid_json("x", res)] == ["L3"]


def test_kill_l4_dual_remote_flag():
    @click.command()
    @click.option("--remote-bucket", is_flag=True)
    @click.option("--force-remote-bucket", is_flag=True)
    def planted():  # pragma: no cover - never invoked
        pass

    assert [v.clause for v in l4_remote_uniformity("trace get", planted)] == ["L4"]


def test_kill_l5_missing_header():
    assert l5_uniform_envelope("bucket status", {"bucket": {}}) != []
    assert l5_uniform_envelope("ctx list", {"schema_version": "x", "traces": []}) != []
    # a clean envelope produces nothing.
    assert l5_uniform_envelope("status", {"status": "ok", "schema_version": "x"}) == []
    assert l5_uniform_envelope("status", {"ok": True, "schema_version": "x"}) == []


def test_l5_frozen_envelope_exemption():
    """Frozen consumer envelopes are exempt from L5 BY DESIGN (freeze beats
    uniformity), but the exemption validates the PAIRING (command PATH + its OWN
    frozen schema_version), never the schema_version VALUE alone. So a non-frozen
    surface emitting a frozen version string is still flagged, and a frozen
    surface emitting the wrong frozen version is still flagged."""
    from .agent_contract_lint import FROZEN_ENVELOPE_EXCEPTIONS

    # (2) The genuine frozen surfaces, each emitting its OWN frozen version, are
    # EXEMPT (no violation) — byte-identical to origin/main.
    assert l5_uniform_envelope(
        "ctx list", {"schema_version": "opentraces.ctx.list.v2", "traces": []}
    ) == []
    assert l5_uniform_envelope(
        "ctx info", {"schema_version": "opentraces.ctx.info.v2", "nodes": []}
    ) == []
    assert l5_uniform_envelope(
        "ctx tree", {"schema_version": "opentraces.context_tree.v1", "nodes": []}
    ) == []
    assert l5_uniform_envelope(
        "ctx show", {"schema_version": "opentraces.context_tree.v1", "nodes": []}
    ) == []

    # (1) KILL: a NON-frozen surface emitting a frozen schema_version string it
    # does not own is NOT exempt — it still fails L5 (Codex probe: bucket status
    # emitting a ctx frozen version must not dodge the header requirement).
    assert l5_uniform_envelope(
        "bucket status", {"schema_version": "opentraces.ctx.list.v2", "bucket": {}}
    ) != []

    # KILL: a frozen surface emitting the WRONG frozen version (one belonging to a
    # different frozen surface) is NOT exempt.
    assert l5_uniform_envelope(
        "ctx list", {"schema_version": "opentraces.context_tree.v1", "traces": []}
    ) != []

    # KILL: a frozen surface emitting a non-frozen version is NOT exempt.
    assert l5_uniform_envelope(
        "ctx list", {"schema_version": "opentraces.not.frozen.v1", "traces": []}
    ) != []

    # The exemption keys on command paths; no frozen version string is a key, and
    # the not-frozen version is owned by no surface.
    assert "opentraces.not.frozen.v1" not in FROZEN_ENVELOPE_EXCEPTIONS
    frozen_versions = frozenset().union(*FROZEN_ENVELOPE_EXCEPTIONS.values())
    assert "opentraces.not.frozen.v1" not in frozen_versions


def test_kill_l6_count_and_version_divergence():
    docs = {
        "a": {"trace_count": 1896, "security_version": "0.6.0"},
        "b": {"security": {"total": 2191, "security_version": "0.7.0"}},
    }
    clauses = [v.message for v in l6_self_consistency(docs)]
    assert any("trace counts disagree" in m for m in clauses)
    assert any("security versions disagree" in m for m in clauses)


def test_l6_agrees_on_consistent_docs():
    docs = {
        "a": {"trace_count": 3, "security_version": "0.7.0"},
        "b": {"manifest": {"trace_records": {"object_count": 3}}, "security_version": "0.7.0"},
        "c": {"security": {"total": 3, "security_version": "0.7.0"}},
    }
    assert l6_self_consistency(docs) == []


# -- 3. quarantine hygiene -----------------------------------------------------
def test_quarantine_only_shrinks():
    """The table may only shrink: a slice that lands empties entries. Growing
    it (a new deferred violator) requires bumping QUARANTINE_BASELINE in review,
    exactly like catalogue_lint's MAX cap."""
    assert len(QUARANTINE) <= QUARANTINE_BASELINE


def test_f1_owned_clauses_have_no_quarantine():
    """L1/L2/L3 are F1's responsibility; leaving one quarantined would mean the
    emission fixes did not actually land."""
    leaked = [q for q in QUARANTINE if q.clause in F1_OWNED_CLAUSES]
    assert not leaked, f"F1 clauses must be clean, not quarantined: {leaked}"


def test_quarantine_entries_are_well_formed():
    for q in QUARANTINE:
        assert q.clause in {"L1", "L2", "L3", "L4", "L5", "L6"}
        assert q.command and q.reason and q.owner_slice


def test_quarantine_matches_a_current_violator(seeded_bucket):
    """Anti-rot: every quarantine entry must still name a LIVE violation, so a
    fixed-but-forgotten entry cannot mask a future regression of the same id."""
    home, repo = seeded_bucket
    # Run the lint with an EMPTY quarantine by inspecting raw violations: a
    # quarantined entry is honest only if the same (clause, command) appears
    # in the current failing+quarantined set.
    failing, quarantined = lint_agent_contract(
        sweep_runner=_fresh_runner,
        guard_runner=_guard_runner,
        l6_docs=_l6_docs(home, repo),
        hang_runner=lambda argv: _run(argv, home=home, cwd=repo, stdin=subprocess.DEVNULL),
    )
    live = {(v.clause, v.command) for v in failing + quarantined}
    stale = [(q.clause, q.command) for q in QUARANTINE if (q.clause, q.command) not in live]
    assert not stale, f"quarantine entries no longer violate (remove them): {stale}"


# -- 4. structural sanity ------------------------------------------------------
def test_guard_and_hang_lists_are_non_empty():
    assert GUARD_COMMANDS
    assert HANG_CLUSTER
