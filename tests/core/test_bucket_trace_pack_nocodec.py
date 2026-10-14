"""``bucket pack`` without any codec: refuse clearly, keep reads and verify working.

Runs on every interpreter (codecs are an optional extra); codecs are also
masked explicitly so the test holds where one happens to be installed.
"""

from __future__ import annotations

import json
import os
import time

from click.testing import CliRunner
from opentraces_schema import Agent, Step, TraceRecord

from opentraces.core import bucket_pack_codecs as codecs
from opentraces.core import bucket_trace_pack as btp
from opentraces.core.bucket_layout import trace_records_root
from opentraces.core.bucket_trace_records import (
    read_trace_record_object,
    trace_record_path,
    write_trace_record,
)


def _capture(turns: int) -> None:
    old = time.time() - 3 * 3600
    for n in range(1, turns + 1):
        record = TraceRecord(
            trace_id="t1",
            session_id="session-t1",
            agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
            task={"description": "growing"},
            steps=[Step(step_index=i, role="user", content=f"step {i}") for i in range(1, n + 1)],
        )
        obj = write_trace_record(record, project_slug="proj", source_layer="bucket")
        os.utime(obj.path, (old + n, old + n))


def test_no_codec_pack_refuses_but_reads_and_verify_work(monkeypatch) -> None:
    from opentraces.cli import main
    from opentraces.core.bucket_store import bucket_verify

    monkeypatch.setattr(codecs, "_zstd_backend", lambda: None)
    monkeypatch.setattr(codecs, "_xz_available", lambda: False)
    _capture(3)
    trace_dir = trace_records_root() / "proj" / "t1"
    # A pack from a codec this install does not have (or a future one).
    header = {"codec": "future-codec", "format": "tar", "schema": codecs.PACK_SCHEMA}
    btp.pack_path(trace_dir).write_bytes(codecs.MAGIC + json.dumps(header).encode() + b"\n\x00junk")

    result = CliRunner().invoke(main, ["bucket", "pack", "--json"])
    assert result.exit_code != 0
    assert "opentraces[pack]" in result.output

    dry = CliRunner().invoke(main, ["bucket", "pack", "--dry-run", "--json"])
    assert dry.exit_code == 0, dry.output
    assert json.loads(dry.output)["records_packable"] == 2

    current = read_trace_record_object(trace_record_path("proj", "t1"))
    assert current is not None and len(current.record.steps) == 3

    verify = bucket_verify(full=True)
    assert verify["ok"], verify["errors"]
    assert verify["packs_checked"] == 0
    assert verify["packs_skipped_no_codec"] == [btp._rel(btp.pack_path(trace_dir))]
