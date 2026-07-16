"""Run one real Claude child inside the portable A3 capture lifecycle."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import FrameType
from typing import Sequence

from .. import __version__
from ..core.repo_identity import encode_claude_path
from .portable import Capture, CapturePlan


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--result-dir", required=True)
    parser.add_argument("--required-source", action="append", default=[])
    parser.add_argument("--interrupt-source")
    return parser


def _split_argv(argv: Sequence[str]) -> tuple[list[str], list[str]]:
    try:
        separator = argv.index("--")
    except ValueError as exc:
        raise SystemExit("capture wrapper requires '--' before the child command") from exc
    child = list(argv[separator + 1 :])
    if not child:
        raise SystemExit("capture wrapper requires a child command")
    return list(argv[:separator]), child


def _session_transcript(project: Path, session_id: str) -> Path:
    return (
        Path.home()
        / ".claude"
        / "projects"
        / encode_claude_path(project)
        / f"{session_id}.jsonl"
    )


def run(argv: Sequence[str]) -> int:
    wrapper_argv, child_argv = _split_argv(argv)
    args = _parser().parse_args(wrapper_argv)
    project = Path.cwd().resolve()
    expected_result_dir = Path(".opentraces") / "bench-capture" / args.session_id
    supplied_result_dir = Path(args.result_dir)
    if supplied_result_dir.is_absolute() or supplied_result_dir != expected_result_dir:
        raise SystemExit("capture result directory must match the assigned session")
    required_sources = tuple(args.required_source)
    if args.interrupt_source is not None and args.interrupt_source not in required_sources:
        raise SystemExit("interrupted source must also be required")

    capture = Capture.open(
        CapturePlan(
            project=project,
            workspace=project,
            placement="leased",
            requested_sources=required_sources,
            required_sources=required_sources,
            observer_version=__version__,
            product_under_test_version=__version__,
            result_dir=project / expected_result_dir,
            actor="claude-code",
            session_id=args.session_id,
            session_path=_session_transcript(project, args.session_id),
            security_tools=(),
        )
    )
    child_environment = dict(os.environ)
    child_environment.update(capture.bindings.env)
    forwarded = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)
    previous_handlers: dict[signal.Signals, signal.Handlers] = {}
    child: subprocess.Popen[bytes] | None = None

    def _forward(signum: int, _frame: FrameType | None) -> None:
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    try:
        child = subprocess.Popen(child_argv, env=child_environment)
        for signum in forwarded:
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, _forward)
        return child.wait()
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if args.interrupt_source is not None:
            capture.interrupt(args.interrupt_source)
        capture.finish(deadline=time.monotonic() + 60.0)


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
