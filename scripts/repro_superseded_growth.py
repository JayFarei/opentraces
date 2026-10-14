#!/usr/bin/env python3
"""Reproduce raw-blob growth when a still-growing session is re-captured.

Grows a synthetic session one turn at a time inside a throwaway
``OT_OPENTRACES_DIR``, capturing after every turn exactly like the ingest
path does (``write_raw_source_artifact``), and prints the raw blob count and
total bytes after each capture. Deterministic: fixed turn size, no clock or
randomness in the content.

Without the superseded-blob fix the blob count equals the capture count and
bytes grow O(N^2); with it there is one blob the size of the transcript.

Usage:

    PYTHONPATH=src:packages/opentraces-schema/src \\
      python3 scripts/repro_superseded_growth.py [--captures N] [--turn-bytes B]
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--captures", type=int, default=10)
    parser.add_argument("--turn-bytes", type=int, default=10_000)
    args = parser.parse_args()

    home = Path(tempfile.mkdtemp(prefix="ot-repro-"))
    os.environ["OT_OPENTRACES_DIR"] = str(home / ".opentraces")

    from opentraces import __version__
    from opentraces.core.bucket_layout import raw_sources_root
    from opentraces.core.bucket_raw_sources import write_raw_source_artifact

    session = home / "session.jsonl"
    print(f"opentraces {__version__}")
    print("capture  transcript_bytes  raw_blobs  raw_blob_bytes")
    with session.open("w", encoding="utf-8") as handle:
        for turn in range(1, args.captures + 1):
            handle.write('{"turn": %d, "text": "%s"}\n' % (turn, "x" * args.turn_bytes))
            handle.flush()
            write_raw_source_artifact(
                session,
                trace_id="growing-session",
                project_slug="repro",
                source_kind="claude-code-session-jsonl",
                parser="claude-code",
            )
            blobs = [p for p in (raw_sources_root() / "blobs").glob("*/*") if p.is_file()]
            total = sum(p.stat().st_size for p in blobs)
            print(f"{turn:>7}  {session.stat().st_size:>16}  {len(blobs):>9}  {total:>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
