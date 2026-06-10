"""Unit tests for the B0 harness-version staleness check.

All CI-safe: the installed-binary probe is injected, no real agent
binaries or boxes are touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.otbox.capture_versions import (
    SCHEMA_VERSION,
    check_capture_versions,
    format_check_report,
    parse_major_minor,
)


def test_parse_major_minor_handles_harness_formats():
    assert parse_major_minor("2.1.143 (Claude Code)") == (2, 1)
    assert parse_major_minor("codex-cli 0.132.0") == (0, 132)
    assert parse_major_minor("1.2") == (1, 2)
    assert parse_major_minor("") is None
    assert parse_major_minor(None) is None
    assert parse_major_minor("no digits here") is None


def _manifest(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "otbox.captures_manifest.v1",
                "release_tag": "otbox-captures-test",
                "generated_at": "2026-01-01T00:00:00+00:00",
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )
    return path


_BASE_ENTRY = {
    "base_checkpoint": "c-installed-source",
    "captured_at": "2026-05-17T18:14:00+00:00",
    "metadata_sha256": "0" * 64,
    "snapshot_sha256": "0" * 64,
    "snapshot_size_bytes": 1,
    "source": "real-agent",
}


def test_check_versions_statuses(tmp_path):
    manifest = _manifest(
        tmp_path,
        [
            {**_BASE_ENTRY, "agent": "claude", "scenario": "s-current",
             "binary_version": "2.1.143 (Claude Code)"},
            {**_BASE_ENTRY, "agent": "codex", "scenario": "s-stale",
             "binary_version": "codex-cli 0.132.0"},
            {**_BASE_ENTRY, "agent": "pi", "scenario": "s-absent",
             "binary_version": "pi 1.0.0"},
            {**_BASE_ENTRY, "agent": "echo", "scenario": "s-echo",
             "binary_version": "echo-meta"},
            {**_BASE_ENTRY, "agent": "hermes", "scenario": "s-unknown",
             "binary_version": "weird"},
        ],
    )
    installed = {
        "claude": "2.1.170 (Claude Code)",   # same major.minor -> current
        "codex": "codex-cli 0.138.0",         # minor ahead -> stale
        "pi": "",                              # not on PATH -> binary_absent
        "hermes": "also weird",                # unparseable -> unknown
    }
    report = check_capture_versions(
        manifest, _installed_probe=lambda b: installed.get(b, "")
    )
    assert report["schema_version"] == SCHEMA_VERSION
    by_scenario = {r["scenario"]: r["status"] for r in report["rows"]}
    assert by_scenario == {
        "s-current": "current",
        "s-stale": "stale",
        "s-absent": "binary_absent",
        "s-echo": "versionless",
        "s-unknown": "unknown",
    }
    assert report["stale_scenarios"] == ["s-stale"]
    assert report["summary"] == {
        "current": 1,
        "stale": 1,
        "binary_absent": 1,
        "unknown": 1,
        "versionless": 1,
    }


def test_check_versions_major_bump_is_stale(tmp_path):
    manifest = _manifest(
        tmp_path,
        [{**_BASE_ENTRY, "agent": "claude", "scenario": "s1",
          "binary_version": "2.9.0 (Claude Code)"}],
    )
    report = check_capture_versions(
        manifest, _installed_probe=lambda b: "3.0.1 (Claude Code)"
    )
    assert report["rows"][0]["status"] == "stale"


def test_check_versions_installed_older_is_current(tmp_path):
    """A downgraded local harness must not flag the captures stale."""
    manifest = _manifest(
        tmp_path,
        [{**_BASE_ENTRY, "agent": "codex", "scenario": "s1",
          "binary_version": "codex-cli 0.140.0"}],
    )
    report = check_capture_versions(
        manifest, _installed_probe=lambda b: "codex-cli 0.138.0"
    )
    assert report["rows"][0]["status"] == "current"


def test_check_versions_empty_manifest(tmp_path):
    report = check_capture_versions(tmp_path / "missing.json")
    assert report["rows"] == []
    assert report["stale_scenarios"] == []


def test_format_report_names_the_remedy(tmp_path):
    manifest = _manifest(
        tmp_path,
        [{**_BASE_ENTRY, "agent": "codex", "scenario": "codex-linear-edit",
          "binary_version": "codex-cli 0.132.0"}],
    )
    report = check_capture_versions(
        manifest, _installed_probe=lambda b: "codex-cli 0.999.0"
    )
    text = format_check_report(report)
    assert "STALE" in text
    assert "make capture-refresh SCENARIO=codex-linear-edit" in text


def test_real_manifest_check_runs_clean():
    """Against the committed manifest with the probe stubbed out: every row
    must land in a known status and the envelope shape must hold (the CLI
    surface depends on it)."""
    report = check_capture_versions(_installed_probe=lambda b: "")
    assert report["schema_version"] == SCHEMA_VERSION
    allowed = {"current", "stale", "binary_absent", "unknown", "versionless"}
    assert report["rows"], "committed manifest should have entries"
    assert {r["status"] for r in report["rows"]} <= allowed
    # With no binaries installed, nothing can be stale.
    assert report["stale_scenarios"] == []
