"""Tests for the post-processor runner."""

from __future__ import annotations

import json
import os
import stat
import sys
import textwrap
from pathlib import Path

import pytest

from opentraces_schema import Agent, Task, TraceRecord

from opentraces.core.config import PostProcessorConfig
from opentraces.core.processors import (
    ProcessorError,
    probe_processors,
    run_chain,
    run_processor,
)


# ---------------------------------------------------------------------------
# Stub-binary helpers
# ---------------------------------------------------------------------------


def _make_stub(tmp_path: Path, name: str, body: str) -> Path:
    """Write an executable Python stub and return its path."""
    p = tmp_path / name
    p.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def _trace(**overrides) -> TraceRecord:
    d = dict(
        trace_id="t-1",
        session_id="s-1",
        task=Task(description="do a thing"),
        agent=Agent(name="claude-code", model="m"),
    )
    d.update(overrides)
    return TraceRecord(**d)


_TAG_WRITER = r"""
import json, sys
data = json.loads(sys.stdin.read())
data.setdefault('metadata', {})['tagged_by'] = 'stub'
sys.stdout.write(json.dumps(data))
"""

_IDENTITY = r"""
import sys
sys.stdout.write(sys.stdin.read())
"""

_INVALID_OUTPUT = r"""
import sys
sys.stdout.write('not a trace')
"""

_NONZERO = r"""
import sys
sys.stderr.write('simulated failure\n')
sys.exit(3)
"""

_EMIT_ENV = r"""
import json, os, sys
data = json.loads(sys.stdin.read())
data.setdefault('metadata', {})['seen_env'] = os.environ.get('FOO_VAR', '')
sys.stdout.write(json.dumps(data))
"""


def _spec(path: Path, **kwargs) -> PostProcessorConfig:
    return PostProcessorConfig(
        name=kwargs.pop("name", path.stem),
        command=str(path),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_processor_rewrites_trace(self, tmp_path):
        stub = _make_stub(tmp_path, "tagger.py", _TAG_WRITER)
        trace = _trace()
        new, res = run_processor(trace, _spec(stub))
        assert res.status == "ok"
        assert new.metadata.get("tagged_by") == "stub"

    def test_processor_receives_env_and_args(self, tmp_path):
        stub = _make_stub(tmp_path, "emit_env.py", _EMIT_ENV)
        trace = _trace()
        spec = _spec(stub, args=["--flag"], env={"FOO_VAR": "bar"})
        new, res = run_processor(trace, spec)
        assert res.status == "ok"
        assert new.metadata["seen_env"] == "bar"

    def test_byte_identical_output_is_noop_not_failure(self, tmp_path):
        stub = _make_stub(tmp_path, "identity.py", _IDENTITY)
        trace = _trace()
        new, res = run_processor(trace, _spec(stub))
        assert res.status == "noop"
        # Record returned is the original (unchanged)
        assert new is trace


class TestFailureModes:
    def test_missing_binary_is_non_fatal_by_default(self):
        spec = PostProcessorConfig(name="ghost", command="/does/not/exist")
        trace = _trace()
        new, res = run_processor(trace, spec)
        assert res.status == "skipped_missing"
        assert new is trace

    def test_missing_binary_strict_raises(self):
        spec = PostProcessorConfig(name="ghost", command="/does/not/exist")
        with pytest.raises(ProcessorError):
            run_processor(_trace(), spec, strict=True)

    def test_nonzero_exit_non_fatal(self, tmp_path):
        stub = _make_stub(tmp_path, "nonzero.py", _NONZERO)
        new, res = run_processor(_trace(), _spec(stub))
        assert res.status == "nonzero_exit"
        assert res.exit_code == 3
        # Trace preserved on failure
        assert new.metadata == {}

    def test_nonzero_exit_strict_raises(self, tmp_path):
        stub = _make_stub(tmp_path, "nonzero.py", _NONZERO)
        with pytest.raises(ProcessorError):
            run_processor(_trace(), _spec(stub), strict=True)

    def test_invalid_output_preserves_trace(self, tmp_path):
        stub = _make_stub(tmp_path, "bad.py", _INVALID_OUTPUT)
        new, res = run_processor(_trace(), _spec(stub))
        assert res.status == "invalid_output"
        assert new.metadata == {}

    def test_invalid_output_strict_raises(self, tmp_path):
        stub = _make_stub(tmp_path, "bad.py", _INVALID_OUTPUT)
        with pytest.raises(ProcessorError):
            run_processor(_trace(), _spec(stub), strict=True)


class TestChaining:
    def test_ordered_pipeline(self, tmp_path):
        first = _make_stub(tmp_path, "first.py", _TAG_WRITER)
        # Second reads the first's output and appends another tag.
        second_body = r"""
import json, sys
data = json.loads(sys.stdin.read())
data['metadata']['tagged_by'] = data['metadata'].get('tagged_by', '') + '+second'
sys.stdout.write(json.dumps(data))
"""
        second = _make_stub(tmp_path, "second.py", second_body)
        trace = _trace()
        result = run_chain(trace, [_spec(first), _spec(second)])
        assert [r.status for r in result.results] == ["ok", "ok"]
        assert result.record.metadata["tagged_by"] == "stub+second"

    def test_one_failure_continues_non_fatal(self, tmp_path):
        nonzero = _make_stub(tmp_path, "nonzero.py", _NONZERO)
        writer = _make_stub(tmp_path, "tagger.py", _TAG_WRITER)
        result = run_chain(_trace(), [_spec(nonzero), _spec(writer)])
        statuses = [r.status for r in result.results]
        assert statuses == ["nonzero_exit", "ok"]
        assert result.failed is True
        assert result.record.metadata.get("tagged_by") == "stub"  # second still ran


class TestRedactionOrderingInvariant:
    """If a processor is in the chain, the caller must have already run
    the security pipeline. This test proves that when we hand the runner
    a post-redaction trace, the secret does not reach the processor."""

    def test_secret_absent_from_processor_stdin(self, tmp_path):
        from opentraces.core.config import Config
        from opentraces.core.pipeline import process_imported_trace
        from opentraces_schema import Step

        SECRET = "ghp_RealSecretThatShouldNeverReachProcessorStdin0"
        trace = TraceRecord(
            trace_id="t-s",
            session_id="s-s",
            task=Task(description=f"Deploy {SECRET}"),
            agent=Agent(name="claude-code", model="m"),
            steps=[Step(step_index=0, role="user", content=f"Token {SECRET}")],
        )
        cfg = Config()
        cfg.security.regex.enabled = True
        processed = process_imported_trace(trace, cfg)
        assert processed.redaction_count > 0

        # Stub writes its received stdin to a file; we then verify the
        # secret is absent from that capture.
        capture = tmp_path / "captured_stdin.json"
        stub_body = f"""
import sys
data = sys.stdin.read()
open({str(capture)!r}, 'w').write(data)
sys.stdout.write(data)
"""
        stub = _make_stub(tmp_path, "capture.py", stub_body)
        run_processor(processed.record, _spec(stub))

        seen = capture.read_text()
        assert SECRET not in seen


class TestProbe:
    def test_probe_reports_detected_and_missing(self, tmp_path):
        real = _make_stub(tmp_path, "present.py", _IDENTITY)
        specs = [
            _spec(real),
            PostProcessorConfig(name="gone", command="/no/such/binary"),
        ]
        probed = probe_processors(specs)
        assert probed[0][1] == str(real)
        assert probed[1][1] is None
