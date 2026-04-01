"""Regression tests for security pipeline version stamping.

Ensures that processed traces carry SECURITY_VERSION, not a hardcoded string.
"""

from __future__ import annotations

from opentraces_schema.models import Agent, Outcome, Step, TraceRecord

from opentraces.config import Config
from opentraces.pipeline import process_imported_trace
from opentraces.security import SECURITY_VERSION


def _make_trace() -> TraceRecord:
    return TraceRecord(
        trace_id="version-stamp-test",
        session_id="test-session",
        agent=Agent(name="claude-code", version="2.0"),
        steps=[
            Step(step_index=0, role="user", content="Hello"),
            Step(step_index=1, role="agent", content="Hi there"),
        ],
        outcome=Outcome(),
    )


class TestSecurityVersionStamp:
    """Verify that the security pipeline stamps SECURITY_VERSION on traces."""

    def test_security_version_is_valid_semver(self):
        """SECURITY_VERSION should be a dotted version string."""
        parts = SECURITY_VERSION.split(".")
        assert len(parts) == 3, f"Expected semver, got {SECURITY_VERSION}"
        for part in parts:
            assert part.isdigit(), f"Non-numeric semver part: {part}"

    def test_process_imported_trace_stamps_security_version(self):
        """process_imported_trace() must stamp classifier_version from
        SECURITY_VERSION, not a hardcoded literal."""
        record = _make_trace()
        cfg = Config()
        result = process_imported_trace(record, cfg)
        assert result.record.security.classifier_version == SECURITY_VERSION

    def test_security_version_importable_from_submodule(self):
        """SECURITY_VERSION should be importable from both the package
        and the version submodule."""
        from opentraces.security.version import SECURITY_VERSION as direct

        assert direct == SECURITY_VERSION
