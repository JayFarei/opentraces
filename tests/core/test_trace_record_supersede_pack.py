"""Regression: re-captures of a growing session leave one full record each.

Every capture of a still-growing session writes a new full TraceRecord object
and repoints ``current.json``; the superseded objects stay on disk forever, so
bytes grow O(N^2) in the number of captures. ``bucket pack`` must fold them
into one compressed per-trace archive without losing a single byte.
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import pytest
from click.testing import CliRunner
from opentraces_schema import Agent, Step, TraceRecord

from tests.core._bucket_isolation import bucket_isolation_guard  # noqa: F401

CAPTURES = 8


def _grown(turns: int) -> TraceRecord:
    steps = [
        Step(step_index=i, role="user" if i % 2 else "agent", content=f"turn {i} " + "lorem ipsum " * 400)
        for i in range(1, turns + 1)
    ]
    return TraceRecord(
        trace_id="grow-1",
        session_id="session-grow-1",
        agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
        task={"description": "growing session"},
        steps=steps,
    )


def _no_codec_installed() -> bool:
    try:
        from opentraces.core.bucket_pack_codecs import available_codecs
    except ImportError:  # pre-pack build: run, and fail on the missing verb
        return False
    return not available_codecs()


@pytest.mark.skipif(_no_codec_installed(), reason="needs a pack codec (opentraces[pack])")
def test_recaptured_growing_session_packs_losslessly() -> None:
    from opentraces.cli import main
    from opentraces.core.bucket_layout import trace_records_root
    from opentraces.core.bucket_trace_records import write_trace_record

    for turns in range(1, CAPTURES + 1):
        write_trace_record(_grown(turns), project_slug="proj", source_layer="bucket")
    trace_dir = trace_records_root() / "proj" / "grow-1"
    objects = sorted(p for p in trace_dir.glob("*.json") if p.name != "current.json")
    assert len(objects) == CAPTURES
    old = time.time() - 2 * 3600
    for path in objects:
        os.utime(path, (old, old))
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in objects}
    bytes_before = sum(p.stat().st_size for p in trace_dir.iterdir())

    result = CliRunner().invoke(main, ["bucket", "pack", "--json"])
    assert result.exit_code == 0, result.output
    report = json.loads(result.output)
    assert report["records_packed"] == CAPTURES - 1
    assert report["bytes_before"] == bytes_before

    loose = [p for p in trace_dir.glob("*.json") if p.name != "current.json"]
    assert len(loose) == 1  # only the current record stays loose
    bytes_after = sum(p.stat().st_size for p in trace_dir.iterdir())
    current_size = loose[0].stat().st_size
    # Superseded history costs a fraction of one extra current record.
    assert bytes_after < 1.5 * current_size + 4096, (bytes_before, bytes_after, current_size)

    from opentraces.core.bucket_trace_pack import iter_trace_record_versions

    after = {name: hashlib.sha256(data).hexdigest() for name, data, _ in iter_trace_record_versions(trace_dir)}
    assert after == before
