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

from opentraces.config import Config
from opentraces.pipeline import process_imported_trace
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

    def test_findings_set_blocked_flag(self, monkeypatch, tmp_path) -> None:
        import shutil

        monkeypatch.setattr("shutil.which", lambda _: "/bin/trufflehog")

        finding = {
            "DetectorName": "AWS",
            "Raw": "AKIAIOSFODNN7EXAMPLE",
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
        result = process_imported_trace(record, cfg)

        assert result.trufflehog_blocked is True
        assert result.needs_review is True
