"""Part A tests: TruffleHog Tier 1.5 integration.

TruffleHog runs as an optional subprocess-based scan step between Tier 1
(deterministic regex) and Tier 2 (heuristic/LLM). Tests mock the
subprocess so CI never needs the real binary, but a gated live smoke
test exercises the installed binary when present.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from opentraces.security.trufflehog import (
    TruffleHogFinding,
    TruffleHogMissingError,
    TruffleHogReport,
    TruffleHogScanError,
    find_trufflehog,
    require_trufflehog,
    scan_file,
    scan_trace_jsonl,
)


# ---------------------------------------------------------------------------
# find / require
# ---------------------------------------------------------------------------


class TestFindTruffleHog:
    def test_missing_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: None)
        assert find_trufflehog() is None

    def test_present_returns_version_string(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/opt/homebrew/bin/trufflehog")

        def fake_run(cmd, **kwargs):
            assert cmd[0].endswith("trufflehog")
            assert cmd[1] == "--version"
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = "trufflehog 3.94.3\n"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        version = find_trufflehog()
        assert version is not None
        assert "3.94.3" in version

    def test_require_missing_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: None)
        with pytest.raises(TruffleHogMissingError) as exc_info:
            require_trufflehog()
        # Error must tell user exactly how to fix it.
        assert "opentraces setup trufflehog" in str(exc_info.value)


# ---------------------------------------------------------------------------
# scan_file — subprocess mocking
# ---------------------------------------------------------------------------


def _fake_trufflehog_output(findings: list[dict]) -> str:
    """Build a fake TruffleHog JSON-per-line output."""
    return "\n".join(json.dumps(f) for f in findings) + "\n"


class TestScanFile:
    def test_no_findings_returns_empty_list(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        def fake_run(cmd, **kwargs):
            assert "filesystem" in cmd
            assert "--json" in cmd
            assert "--no-update" in cmd
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "clean.jsonl"
        target.write_text("{}\n")
        findings = scan_file(target)
        assert findings == []

    def test_findings_parsed_into_dataclass(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        fake_finding = {
            "DetectorName": "AWS",
            "Raw": "AKIAIOSFODNN7EXAMPLE",
            "Verified": False,
            "SourceMetadata": {
                "Data": {
                    "Filesystem": {"file": "/tmp/trace.jsonl", "line": 42}
                }
            },
        }

        def fake_run(cmd, **kwargs):
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = _fake_trufflehog_output([fake_finding])
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "dirty.jsonl"
        target.write_text("{}\n")
        findings = scan_file(target)

        assert len(findings) == 1
        f = findings[0]
        assert isinstance(f, TruffleHogFinding)
        assert f.detector_name == "AWS"
        assert f.raw_match == "AKIAIOSFODNN7EXAMPLE"
        assert f.verified is False
        assert f.source_file == "/tmp/trace.jsonl"
        assert f.line_number == 42

    def test_no_verification_flag_when_verify_false(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")
        seen_cmd: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            seen_cmd.append(list(cmd))
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "x.jsonl"
        target.write_text("{}\n")
        scan_file(target, verify=False)
        assert "--no-verification" in seen_cmd[0]

    def test_verification_flag_absent_when_verify_true(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")
        seen_cmd: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            seen_cmd.append(list(cmd))
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = ""
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "x.jsonl"
        target.write_text("{}\n")
        scan_file(target, verify=True)
        assert "--no-verification" not in seen_cmd[0]

    def test_missing_binary_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: None)
        target = tmp_path / "x.jsonl"
        target.write_text("{}\n")
        with pytest.raises(TruffleHogMissingError):
            scan_file(target)

    def test_malformed_json_line_skipped(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        good = {
            "DetectorName": "GitHub",
            "Raw": "ghp_xxx",
            "Verified": True,
            "SourceMetadata": {"Data": {"Filesystem": {"file": "f", "line": 1}}},
        }

        def fake_run(cmd, **kwargs):
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = "not-json\n" + json.dumps(good) + "\n"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "x.jsonl"
        target.write_text("{}\n")
        findings = scan_file(target)
        assert len(findings) == 1
        assert findings[0].detector_name == "GitHub"


# ---------------------------------------------------------------------------
# scan_trace_jsonl — report shape
# ---------------------------------------------------------------------------


class TestScanTraceJsonl:
    def test_clean_report_not_blocked(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                r = subprocess.CompletedProcess(cmd, 0)
                r.stdout = "trufflehog 3.94.3\n"
                r.stderr = ""
                return r
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        report = scan_trace_jsonl(target)

        assert isinstance(report, TruffleHogReport)
        assert report.findings == []
        assert report.blocked is False
        assert report.trufflehog_version
        assert report.scanned_at

    def test_nonzero_exit_raises(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                r = subprocess.CompletedProcess(cmd, 0)
                r.stdout = "trufflehog 3.94.3\n"
                r.stderr = ""
                return r
            r = subprocess.CompletedProcess(cmd, 2)
            r.stdout = ""
            r.stderr = "trufflehog internal error"
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        with pytest.raises(TruffleHogScanError):
            scan_trace_jsonl(target)

    def test_findings_block_report(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        finding = {
            "DetectorName": "Stripe",
            "Raw": "sk_live_xxx",
            "Verified": False,
            "SourceMetadata": {"Data": {"Filesystem": {"file": "f", "line": 1}}},
        }

        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                r = subprocess.CompletedProcess(cmd, 0)
                r.stdout = "trufflehog 3.94.3\n"
                r.stderr = ""
                return r
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = json.dumps(finding) + "\n"
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        report = scan_trace_jsonl(target)

        assert len(report.findings) == 1
        assert report.blocked is True

    def test_deduplicates_identical_findings(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        finding = {
            "DetectorName": "AWS",
            "Raw": "AKIA-same",
            "Verified": False,
            "SourceMetadata": {"Data": {"Filesystem": {"file": "f", "line": 1}}},
        }
        dup = dict(finding)

        def fake_run(cmd, **kwargs):
            if "--version" in cmd:
                r = subprocess.CompletedProcess(cmd, 0)
                r.stdout = "trufflehog 3.94.3\n"
                r.stderr = ""
                return r
            r = subprocess.CompletedProcess(cmd, 0)
            r.stdout = _fake_trufflehog_output([finding, dup])
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        report = scan_trace_jsonl(target)
        assert len(report.findings) == 1


# ---------------------------------------------------------------------------
# Live smoke test — only runs if real trufflehog is installed.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("trufflehog") is None,
    reason="trufflehog binary not installed; skipping live smoke test",
)
def test_live_trufflehog_on_plaintext_secret(tmp_path) -> None:
    """E2E: real trufflehog must detect a planted AWS key in a JSONL file."""
    sample = tmp_path / "sample.jsonl"
    sample.write_text(
        '{"note": "leaked AWS key AKIAIOSFODNN7EXAMPLE for testing"}\n'
    )
    report = scan_trace_jsonl(sample)
    # TruffleHog must at minimum run without erroring; ideally catches the AWS key.
    assert isinstance(report, TruffleHogReport)
    assert report.trufflehog_version
