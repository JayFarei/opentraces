"""Tier 1.5 pipeline integration tests — Plan 032 Part A, follow-up.

Verifies that:
  - process_trace runs TruffleHog when config enables it.
  - skip_trufflehog=True suppresses the subprocess entirely, which is
    the semantic behind ``push --no-trufflehog``.
  - ProcessedTrace.trufflehog_blocked surfaces findings so callers can
    route the trace to TraceStatus.BLOCKED.
"""

from __future__ import annotations

import json
import subprocess as _sp
from pathlib import Path

import pytest

from opentraces.core.config import Config
from opentraces.core.pipeline import process_imported_trace
from opentraces.security.trufflehog import TruffleHogReport


def _make_minimal_trace():
    from opentraces_schema import Agent, TraceRecord
    return TraceRecord(
        trace_id="t-trufflehog-test",
        session_id="s-trufflehog-test",
        agent=Agent(name="claude-code"),
    )


class TestTruffleHogInPipeline:
    def test_disabled_config_runs_no_subprocess(
        self, monkeypatch, tmp_path,
    ) -> None:
        calls: list = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            r = _sp.CompletedProcess(cmd, 0)
            r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        cfg = Config()  # trufflehog disabled by default
        record = _make_minimal_trace()
        result = process_imported_trace(record, cfg)
        assert result.trufflehog_report is None
        assert result.trufflehog_blocked is False

    def test_enabled_config_runs_subprocess(self, monkeypatch, tmp_path) -> None:
        import shutil

        monkeypatch.setattr("shutil.which", lambda _: "/bin/trufflehog")

        def fake_run(cmd, **kwargs):
            r = _sp.CompletedProcess(cmd, 0)
            if "--version" in cmd:
                r.stdout = "trufflehog 3.94.3\n"
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        cfg = Config()
        cfg.security.trufflehog.enabled = True
        record = _make_minimal_trace()
        result = process_imported_trace(record, cfg)

        assert isinstance(result.trufflehog_report, TruffleHogReport)
        assert result.trufflehog_blocked is False  # no findings

    def test_clean_scan_persists_status_marker(self, monkeypatch, tmp_path) -> None:
        """Clean scans write metadata.security.tools.trufflehog = {status: clean, ...}
        so the TUI can distinguish "scanned, no findings" from "not run"."""
        monkeypatch.setattr("shutil.which", lambda _: "/bin/trufflehog")

        def fake_run(cmd, **kwargs):
            r = _sp.CompletedProcess(cmd, 0)
            if "--version" in cmd:
                r.stdout = "trufflehog 3.94.3\n"
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        cfg = Config()
        cfg.security.trufflehog.enabled = True
        record = _make_minimal_trace()
        result = process_imported_trace(record, cfg)

        sec_meta = (result.record.metadata or {}).get("security") or {}
        marker = (sec_meta.get("tools") or {}).get("trufflehog") or {}
        assert marker.get("status") == "clean"
        assert marker.get("findings_count") == 0
        assert "3.94" in (marker.get("version") or "")
        assert marker.get("scanned_at"), "scanned_at should be populated"
        assert not marker.get("findings"), "clean scan has an empty findings list"

    def test_disabled_does_not_write_marker(self, monkeypatch) -> None:
        """When the tier is off, leave metadata.security.tools.trufflehog unset
        so the TUI still shows 'not run (opt-in)'."""
        cfg = Config()  # trufflehog disabled by default
        record = _make_minimal_trace()
        result = process_imported_trace(record, cfg)

        sec_meta = (result.record.metadata or {}).get("security") or {}
        assert "trufflehog" not in (sec_meta.get("tools") or {})

    def test_skip_trufflehog_short_circuits_even_when_enabled(
        self, monkeypatch, tmp_path,
    ) -> None:
        calls: list = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            r = _sp.CompletedProcess(cmd, 0)
            r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        cfg = Config()
        cfg.security.trufflehog.enabled = True

        record = _make_minimal_trace()
        result = process_imported_trace(record, cfg, skip_trufflehog=True)

        assert result.trufflehog_report is None
        # No filesystem subprocess call should have happened.
        for cmd in calls:
            assert "filesystem" not in cmd

    def test_findings_trigger_redaction_and_metadata(self, monkeypatch, tmp_path) -> None:
        """Tier 1.5 findings no longer gate the trace. The pipeline
        redacts the matched substring in place (same mitigation Tier 1
        uses for regex hits) and persists the per-finding detail on
        ``record.metadata.security.tools.trufflehog.findings`` so downstream
        surfaces can show which detectors fired and whether the hit
        was verified."""
        monkeypatch.setattr("shutil.which", lambda _: "/bin/trufflehog")

        raw_secret = "AKIAIOSFODNN7EXAMPLE"
        finding = {
            "DetectorName": "AWS",
            "Raw": raw_secret,
            "Verified": False,
            "SourceMetadata": {"Data": {"Filesystem": {"file": "f", "line": 1}}},
        }

        def fake_run(cmd, **kwargs):
            r = _sp.CompletedProcess(cmd, 0)
            if "--version" in cmd:
                r.stdout = "trufflehog 3.94.3\n"
            else:
                r.stdout = json.dumps(finding) + "\n"
            r.stderr = ""
            return r

        monkeypatch.setattr("subprocess.run", fake_run)

        cfg = Config()
        cfg.security.trufflehog.enabled = True
        record = _make_minimal_trace()
        # Plant the raw secret somewhere the scanner walks so we can
        # prove the redaction path actually ran end-to-end.
        record.task.description = f"check this key {raw_secret} for me"

        result = process_imported_trace(record, cfg)

        # Block flag survives on the report object for back-compat with
        # callers that still read it, but the trace flows as STAGED.
        assert result.trufflehog_blocked is True
        # The raw secret is no longer present anywhere on the record.
        # (For AWS-shaped keys, Tier 1 regex catches it before Tier 1.5
        # sees the already-redacted text — either tier getting there is
        # fine; the guarantee we care about is "not on disk".)
        assert raw_secret not in result.record.task.description
        assert "[REDACTED]" in result.record.task.description
        # Findings persisted for the UI to surface later, regardless of
        # which tier actually performed the redaction.
        sec_meta = (result.record.metadata or {}).get("security") or {}
        marker = (sec_meta.get("tools") or {}).get("trufflehog") or {}
        th_findings = marker.get("findings") or []
        assert any(f.get("detector") == "AWS" for f in th_findings), th_findings
        assert result.record.security.redactions_applied >= 1
        assert marker.get("status") == "findings"
        assert marker.get("findings_count") == len(th_findings)
