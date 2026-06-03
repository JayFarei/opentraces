"""Public API contract for :func:`opentraces.security.sanitize_*`.

Two resolution paths feed every entry point:

  1. ``tools=[...]`` explicit list  → runs exactly those tools.
  2. ``cfg=<Config>``               → runs registry tools whose ``enabled(cfg)``
                                        returns True.

Callers MUST pass one; omitting both raises ValueError. ``tools_applied`` is
stamped on the record metadata after every run, and
``metadata.security.tools.<name>`` carries the per-tool patch.
"""

from __future__ import annotations

import pytest

from opentraces_schema import TraceRecord
from opentraces_schema.models import Agent, Step

from opentraces.core.config import Config
from opentraces.security import (
    PipelineReport,
    sanitize_dict,
    sanitize_record,
    sanitize_text,
)


def _trace(description: str = "", step_content: str = "") -> TraceRecord:
    rec = TraceRecord(
        trace_id="t-pipeline-api",
        session_id="s-pipeline-api",
        agent=Agent(name="test"),
    )
    if description:
        rec.task.description = description
    if step_content:
        rec.steps = [Step(step_index=0, role="agent", content=step_content)]
    return rec


# ---------------------------------------------------------------------------
# sanitize_text
# ---------------------------------------------------------------------------


class TestSanitizeText:
    def test_explicit_tools_overrides_cfg(self) -> None:
        cfg = Config()
        cfg.security.trufflehog.enabled = True  # would be enabled by cfg
        # Explicit tools=[regex] should NOT pull trufflehog in.
        clean, findings = sanitize_text(
            "AKIAIOSFODNN7EXAMPLE leaked",
            tools=["regex"],
            cfg=cfg,
        )
        assert "[REDACTED]" in clean
        assert all(f.tool == "regex" for f in findings)

    def test_no_tools_no_cfg_raises(self) -> None:
        with pytest.raises(ValueError, match="tools="):
            sanitize_text("sk-ant-abcdefghij1234567890")

    def test_cfg_only_runs_no_tools_by_default(self) -> None:
        cfg = Config()
        clean, findings = sanitize_text("AKIAIOSFODNN7EXAMPLE", cfg=cfg)
        assert clean == "AKIAIOSFODNN7EXAMPLE"
        assert findings == []

    def test_cfg_only_runs_explicitly_enabled_tools(self) -> None:
        cfg = Config()
        cfg.security.regex.enabled = True
        clean, findings = sanitize_text("AKIAIOSFODNN7EXAMPLE", cfg=cfg)
        assert clean == "[REDACTED]"
        assert [f.tool for f in findings] == ["regex"]


# ---------------------------------------------------------------------------
# sanitize_dict
# ---------------------------------------------------------------------------


class TestSanitizeDict:
    def test_walks_nested_dict(self) -> None:
        row = {
            "a": "AKIAIOSFODNN7EXAMPLE",
            "b": {"c": "sk-ant-abc1234567890def12345", "d": [1, 2]},
            "n": 99,
        }
        clean, report = sanitize_dict(row, tools=["regex"])
        assert clean["a"] == "[REDACTED]"
        assert clean["b"]["c"] == "[REDACTED]"
        assert clean["b"]["d"] == [1, 2]
        assert clean["n"] == 99
        assert report.redactions_applied >= 2

    def test_field_path_attached(self) -> None:
        row = {"outer": {"inner": "AKIAIOSFODNN7EXAMPLE"}}
        _, report = sanitize_dict(row, tools=["regex"])
        assert any("outer.inner" in f.field_path for f in report.findings)

    def test_report_lists_tools_applied(self) -> None:
        clean, report = sanitize_dict({"x": "hi"}, tools=["regex", "entropy"])
        assert report.tools_applied == ["regex", "entropy"]


# ---------------------------------------------------------------------------
# sanitize_record
# ---------------------------------------------------------------------------


class TestSanitizeRecord:
    def test_stamps_tools_applied(self) -> None:
        rec = _trace(description="hi AKIAIOSFODNN7EXAMPLE there")
        rec, report = sanitize_record(rec, tools=["regex", "entropy"])
        sec = rec.metadata["security"]
        assert sec["tools_applied"] == ["regex", "entropy"]
        assert "regex" in sec["tools"]
        assert "entropy" in sec["tools"]
        assert sec["tools"]["regex"]["findings_count"] >= 1
        assert isinstance(report, PipelineReport)
        assert report.redactions_applied >= 1

    def test_no_args_raises(self) -> None:
        rec = _trace(description="hi AKIAIOSFODNN7EXAMPLE")
        with pytest.raises(ValueError, match="tools="):
            sanitize_record(rec)

    def test_cfg_runs_no_tools_by_default(self) -> None:
        rec = _trace(description="hi AKIAIOSFODNN7EXAMPLE")
        rec.metadata["security"] = {
            "tools_applied": ["regex"],
            "tools": {"regex": {"findings_count": 1}},
        }
        cfg = Config()
        rec, report = sanitize_record(rec, cfg=cfg)
        assert report.tools_applied == []
        assert rec.metadata["security"]["tools_applied"] == []
        assert rec.metadata["security"]["tools"] == {}

    def test_cfg_runs_explicitly_enabled_tools(self) -> None:
        rec = _trace(description="hi AKIAIOSFODNN7EXAMPLE")
        cfg = Config()
        cfg.security.regex.enabled = True
        cfg.security.entropy.enabled = True
        cfg.security.path_anonymizer.enabled = True
        cfg.security.classifier.enabled = True
        rec, report = sanitize_record(rec, cfg=cfg)
        assert report.tools_applied == [
            "regex",
            "entropy",
            "path_anonymizer",
            "classifier",
        ]

    def test_explicit_tools_ignores_cfg(self) -> None:
        rec = _trace(description="hi AKIAIOSFODNN7EXAMPLE")
        cfg = Config()
        cfg.security.trufflehog.enabled = True
        rec, report = sanitize_record(rec, tools=["regex"], cfg=cfg)
        assert report.tools_applied == ["regex"]

    def test_tools_canonical_order(self) -> None:
        """``tools=`` is normalised to the canonical registry order so detectors
        run before transformers and judges no matter how the caller spelled
        them."""
        rec = _trace(description="x")
        rec, report = sanitize_record(rec, tools=["classifier", "regex"])
        assert report.tools_applied == ["regex", "classifier"]

    def test_pi_provider_context_metadata_is_redacted(self) -> None:
        rec = _trace(description="safe")
        rec.metadata["pi"] = {
            "provider_contexts": [
                {"messages": [{"role": "user", "content": "leak AKIAIOSFODNN7EXAMPLE"}]}
            ]
        }

        rec, report = sanitize_record(rec, tools=["regex"])

        assert report.redactions_applied >= 1
        content = rec.metadata["pi"]["provider_contexts"][0]["messages"][0]["content"]
        assert content == "leak [REDACTED]"
