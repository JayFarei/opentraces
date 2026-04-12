"""Part A config tests: TruffleHog is opt-in via security.trufflehog.enabled.

Default install never invokes TruffleHog. Contributors enable it
explicitly; once enabled, a missing binary is a hard error so
regressions surface immediately.
"""

from __future__ import annotations

import pytest

from opentraces.config import Config, TruffleHogConfig
from opentraces.security.scanner_trufflehog import (
    maybe_run_trufflehog,
)
from opentraces.security.trufflehog import (
    TruffleHogMissingError,
    TruffleHogReport,
)


class TestTruffleHogConfigDefaults:
    def test_default_disabled(self) -> None:
        c = Config()
        assert c.security.trufflehog.enabled is False

    def test_default_no_verify(self) -> None:
        c = Config()
        assert c.security.trufflehog.verify_secrets is False

    def test_enable_via_update(self) -> None:
        cfg = TruffleHogConfig(enabled=True)
        assert cfg.enabled is True
        assert cfg.verify_secrets is False


class TestMaybeRunTruffleHog:
    def test_disabled_returns_none(self, monkeypatch, tmp_path) -> None:
        cfg = TruffleHogConfig(enabled=False)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        report = maybe_run_trufflehog(target, cfg)
        assert report is None

    def test_enabled_but_missing_raises(self, monkeypatch, tmp_path) -> None:
        import shutil
        monkeypatch.setattr(shutil, "which", lambda _: None)
        cfg = TruffleHogConfig(enabled=True)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        with pytest.raises(TruffleHogMissingError):
            maybe_run_trufflehog(target, cfg)

    def test_enabled_and_present_returns_report(self, monkeypatch, tmp_path) -> None:
        import shutil
        import subprocess

        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")

        def fake_run(cmd, **kwargs):
            r = subprocess.CompletedProcess(cmd, 0)
            if "--version" in cmd:
                r.stdout = "trufflehog 3.94.3\n"
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        cfg = TruffleHogConfig(enabled=True)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        report = maybe_run_trufflehog(target, cfg)
        assert isinstance(report, TruffleHogReport)
        assert report.blocked is False

    def test_verify_secrets_threaded_through(self, monkeypatch, tmp_path) -> None:
        import shutil
        import subprocess

        monkeypatch.setattr(shutil, "which", lambda _: "/bin/trufflehog")
        captured: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            captured.append(list(cmd))
            r = subprocess.CompletedProcess(cmd, 0)
            if "--version" in cmd:
                r.stdout = "trufflehog 3.94.3\n"
            else:
                r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)
        cfg = TruffleHogConfig(enabled=True, verify_secrets=True)
        target = tmp_path / "trace.jsonl"
        target.write_text("{}\n")
        maybe_run_trufflehog(target, cfg)
        # Scan command (not the --version probe) must not carry --no-verification.
        scan_cmd = next(c for c in captured if "filesystem" in c)
        assert "--no-verification" not in scan_cmd
