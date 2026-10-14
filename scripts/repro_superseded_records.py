#!/usr/bin/env python3
"""Reproduce superseded TraceRecord growth when a growing session is re-captured.

Grows a synthetic session one turn at a time inside a throwaway
``OT_OPENTRACES_DIR``, writing the TraceRecord after every turn exactly like
the capture path does (``write_trace_record``), then prints the record-object
count and bytes under ``objects/traces/v1``. It then runs ``bucket pack`` (if
this build has it) and prints the same numbers again plus a byte-exact check
of every earlier version. Deterministic: fixed turn size, no randomness.

Without packing the object count equals the capture count and bytes grow
O(N^2); with it one loose current record remains plus one small pack.

Usage:

    PYTHONPATH=src:packages/opentraces-schema/src \\
      python3 scripts/repro_superseded_records.py [--captures N] [--turn-bytes B]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


def _tree(root: Path) -> tuple[int, int, dict[str, str]]:
    objects = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*.json")
        if p.name != "current.json"
    }
    total = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    return len(objects), total, objects


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--captures", type=int, default=20)
    parser.add_argument("--turn-bytes", type=int, default=10_000)
    args = parser.parse_args()

    home = Path(tempfile.mkdtemp(prefix="ot-repro-"))
    os.environ["OT_OPENTRACES_DIR"] = str(home / ".opentraces")

    from click.testing import CliRunner
    from opentraces_schema import Agent, Step, TraceRecord

    from opentraces import __version__
    from opentraces.cli import main as cli
    from opentraces.core.bucket_layout import trace_records_root
    from opentraces.core.bucket_trace_records import write_trace_record

    print(f"opentraces {__version__}: {args.captures} captures, {args.turn_bytes} B/turn")
    old = time.time() - 2 * 3600
    for n in range(1, args.captures + 1):
        steps = [
            Step(step_index=i, role="user" if i % 2 else "agent", content=f"turn {i} ".ljust(args.turn_bytes, "x"))
            for i in range(1, n + 1)
        ]
        record = TraceRecord(
            trace_id="repro",
            session_id="session-repro",
            agent=Agent(name="claude-code", model="anthropic/claude-opus-4-6"),
            task={"description": "growing session"},
            steps=steps,
        )
        obj = write_trace_record(record, project_slug="repro", source_layer="bucket")
        os.utime(obj.path, (old + n, old + n))  # older than the 1 h pack floor
    root = trace_records_root()
    count, total, before = _tree(root)
    current = max(p.stat().st_size for p in root.rglob("*.json") if p.name != "current.json")
    print(f"before pack: {count} record objects, {total} bytes (current record {current} bytes)")

    result = CliRunner().invoke(cli, ["bucket", "pack", "--json"])
    if result.exit_code != 0:
        print(f"bucket pack: unavailable (exit {result.exit_code}): {result.output.strip().splitlines()[-1]}")
        return 0
    report = json.loads(result.output)
    count, total, _ = _tree(root)
    print(f"after pack:  {count} loose record object(s), {total} bytes; records_packed={report['records_packed']}")

    from opentraces.core.bucket_trace_pack import iter_trace_record_versions

    trace_dir = root / "repro" / "repro"
    after = {
        f"repro/repro/{name}": hashlib.sha256(data).hexdigest()
        for name, data, _ in iter_trace_record_versions(trace_dir)
    }
    print(f"byte-exact versions recoverable: {sum(after.get(k) == v for k, v in before.items())}/{len(before)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
