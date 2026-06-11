"""Freshness gate for the committed B0 acceptance report (issue #61).

Mirrors ``test_capture_freshness.py`` exactly (acceptance criterion 4 +
design anchor 4):

  * SKIPs clean when no report is committed (the OSS default — no
    ``tests/otbox/captures/_acceptance/report.json``).
  * warns-only (``warnings.warn``, never fails) on:
      - a report older than 90 days,
      - scenario-digest drift vs the current acceptance TOMLs,
      - synthetic (non-real-agent) provenance on the committed path,
      - an installed agent binary whose major/minor is ahead of the
        report's ``binary_version``.
  * FAILs ONLY on a schema-invalid committed report — and the schema is
    STRICT (exactly the five acceptance arcs with their spec-journey
    bindings, typed row fields, summary arithmetic consistent with the
    rows, digest coverage for all five scenarios; see
    ``acceptance.validate_report_schema``).

CI never gates the SCORE value itself — only schema validity is a hard
failure. The score is operator-reviewed off the committed report + the
footage gallery.
"""

from __future__ import annotations

import shutil
import warnings

import pytest

from tests.otbox import acceptance


def test_no_committed_report_is_acceptable():
    """Default-CI smoke: with no committed report the gate SKIPs clean.

    Always-green anchor so this file has at least one passing test even
    when the captures tree carries no acceptance report (OSS default).
    """
    report = acceptance.load_committed_report()
    if report is None:
        pytest.skip("no committed acceptance report (OSS default)")
    # When a report IS committed, the schema check below is the real gate.
    assert acceptance.validate_report_schema(report) == []


def test_committed_report_schema_is_the_only_hard_failure():
    """A schema-invalid committed report FAILs; a valid one passes.

    This is the single hard failure the gate raises. Everything else
    (age, digest drift, binary-ahead) is warn-only.
    """
    report = acceptance.load_committed_report()
    if report is None:
        pytest.skip("no committed acceptance report (OSS default)")
    problems = acceptance.validate_report_schema(report)
    assert problems == [], (
        "committed acceptance report is schema-invalid:\n  "
        + "\n  ".join(problems)
    )


def test_freshness_signals_are_warn_only():
    """Age / digest-drift / synthetic-provenance / binary-ahead warn only."""
    report = acceptance.load_committed_report()
    if report is None:
        pytest.skip("no committed acceptance report (OSS default)")
    # A schema-invalid report is caught by the dedicated test above; bail
    # out of the freshness walk rather than indexing into a bad shape.
    if acceptance.validate_report_schema(report):
        pytest.skip("report is schema-invalid (covered by the schema test)")

    prov = report["provenance"]

    # 1. Age.
    captured_at = prov.get("captured_at", "")
    if acceptance.report_is_stale_by_age(
        captured_at, threshold_days=acceptance.STALE_AFTER_DAYS
    ):
        warnings.warn(
            f"acceptance report is older than {acceptance.STALE_AFTER_DAYS} "
            f"days (captured_at={captured_at!r}); re-run "
            f"`make otbox-acceptance AGENT=<agent>`",
            stacklevel=1,
        )

    # 2. Scenario-digest drift.
    committed_digests = prov.get("scenario_digests", {})
    live_digests = acceptance.live_scenario_digests()
    for name in acceptance.scenario_digest_drift(committed_digests, live_digests):
        warnings.warn(
            f"acceptance report scenario_digest for {name!r} drifted vs the "
            f"current TOML; re-run `make otbox-acceptance`",
            stacklevel=1,
        )

    # 3. Synthetic provenance on the COMMITTED path. Schema-wise an echo
    #    report is valid (it must be — echo is the default-CI proof), but
    #    the committed evidence is supposed to be a real-agent run, so a
    #    synthetic agent here warns loudly.
    agent = prov.get("agent", "")
    if agent not in {"claude", "codex", "pi"}:
        warnings.warn(
            f"committed acceptance report has synthetic provenance "
            f"(agent={agent!r}); the committed report should come from "
            f"`make otbox-acceptance AGENT=<real-agent>`",
            stacklevel=1,
        )

    # 4. Installed binary ahead of the report's.
    report_version = prov.get("binary_version", "")
    binary_name = {"claude": "claude", "codex": "codex", "pi": "pi"}.get(agent)
    installed = shutil.which(binary_name) if binary_name else None
    if installed:
        from tests.otbox.simulated_users.prep import _detect_binary_version

        installed_version = _detect_binary_version(installed)
        if acceptance.binary_version_ahead(report_version, installed_version):
            warnings.warn(
                f"installed {agent} binary {installed_version!r} is ahead of "
                f"the acceptance report's {report_version!r}; re-run "
                f"`make otbox-acceptance AGENT={agent}`",
                stacklevel=1,
            )

    # The gate is informational past schema validity — reaching here is a pass.
    assert True


# ---------------------------------------------------------------------------
# explicit gate-behaviour proofs (don't depend on an OSS-committed report)
# ---------------------------------------------------------------------------
def test_echo_report_round_trips_through_the_gate(tmp_path):
    """A freshly-built echo report writes, reloads, and passes the schema
    gate — proving the committed-report path is real, not just skipped."""
    report = acceptance.run_acceptance_echo()
    target = tmp_path / "report.json"
    acceptance.write_report(report, target)

    reloaded = acceptance.load_committed_report(target)
    assert reloaded is not None
    assert acceptance.validate_report_schema(reloaded) == []
    # The echo report is honestly stamped as synthetic, never a real agent.
    assert reloaded["provenance"]["agent"] == "echo"
    assert reloaded["provenance"]["binary_version"] == "otbox-echo 1.0.0"


def test_schema_invalid_committed_report_is_a_hard_failure(tmp_path):
    """The ONLY hard failure: a committed report that fails schema
    validation. We mutate a valid report and assert the predicate the gate
    keys on returns violations (which the gate turns into an assert)."""
    report = acceptance.run_acceptance_echo()
    # Corrupt the schema_version — the gate must catch this.
    report["schema_version"] = "opentraces.otbox.acceptance_report.vWRONG"
    target = tmp_path / "report.json"
    acceptance.write_report(report, target)

    reloaded = acceptance.load_committed_report(target)
    problems = acceptance.validate_report_schema(reloaded)
    assert problems, "schema-invalid report must surface violations"


def test_no_report_path_returns_none(tmp_path):
    """The OSS-default SKIP hinges on load_committed_report returning None
    for a missing file."""
    assert acceptance.load_committed_report(tmp_path / "absent.json") is None
