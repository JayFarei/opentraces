"""Process entry point for a leased, inline OTLP receiver."""

from __future__ import annotations

import argparse
import signal
import threading
from pathlib import Path

from .otlp import start_receiver
from .otlp.emitter import OTLPCaptureBuffer, write_snapshot
from .otlp.raw_body_watcher import RawBodyWatcher


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--raw-bodies-dir", required=True, type=Path)
    args = parser.parse_args()

    buffer = OTLPCaptureBuffer()
    receiver = start_receiver(
        port=args.port,
        bind="127.0.0.1",
        raw_bodies_dir=args.raw_bodies_dir,
    )
    receiver.set_callback(buffer.handle_envelope)
    watcher = RawBodyWatcher(args.raw_bodies_dir, buffer.handle_body_pair)
    watcher.start()
    stopped = threading.Event()

    def _stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    try:
        while not stopped.wait(0.25):
            for session_id in buffer.session_ids():
                snapshot = buffer.snapshot_session(session_id)
                if snapshot is not None:
                    write_snapshot(buffer, session_id, _snapshot_path(session_id))
    finally:
        watcher.shutdown()
        receiver.shutdown()
    return 0


def _snapshot_path(session_id: str) -> Path:
    from ..core.paths import otel_staging_dir

    return otel_staging_dir() / f"{session_id}.json"


if __name__ == "__main__":
    raise SystemExit(main())
