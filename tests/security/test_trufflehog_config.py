"""TruffleHog config defaults: opt-in via security.trufflehog.enabled.

A default install never invokes TruffleHog. Enable/disable behaviour and
the binary-missing path are covered end-to-end by test_pipeline_trufflehog.py.
"""

from __future__ import annotations

from opentraces.core.config import Config, TruffleHogConfig


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
