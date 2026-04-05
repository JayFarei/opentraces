#!/usr/bin/env python3
"""Capture live TUI states via tmux as text, ANSI, and SVG snapshots."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.ansi import AnsiDecoder
from rich.console import Console


@dataclass
class CaptureStep:
    label: str
    keys: list[str]


def _parse_step(raw: str) -> CaptureStep:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"invalid step {raw!r}; expected LABEL=KEY[,KEY...]"
        )
    label, key_spec = raw.split("=", 1)
    label = label.strip()
    keys = [part.strip() for part in key_spec.split(",") if part.strip()]
    if not label or not keys:
        raise argparse.ArgumentTypeError(
            f"invalid step {raw!r}; expected LABEL=KEY[,KEY...]"
        )
    return CaptureStep(label=label, keys=keys)


def _slug(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    return compact.strip("-") or "step"


def _tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "TMUX": ""},
    )


def _capture_pane(session: str, history_lines: int) -> tuple[str, str]:
    target = _pane_target(session)
    ansi = _tmux("capture-pane", "-t", target, "-p", "-e", "-S", f"-{history_lines}").stdout
    text = _tmux("capture-pane", "-t", target, "-p", "-S", f"-{history_lines}").stdout
    return ansi, text


def _ansi_to_svg(ansi_text: str, width: int, title: str) -> str:
    console = Console(record=True, force_terminal=True, width=width)
    decoder = AnsiDecoder()
    for renderable in decoder.decode(ansi_text):
        console.print(renderable)
    return console.export_svg(title=title)


def _write_snapshot(
    output_dir: Path,
    index: int,
    label: str,
    ansi_text: str,
    plain_text: str,
    width: int,
) -> dict[str, str]:
    stem = f"{index:02d}_{_slug(label)}"
    ansi_path = output_dir / f"{stem}.ansi"
    text_path = output_dir / f"{stem}.txt"
    svg_path = output_dir / f"{stem}.svg"

    ansi_path.write_text(ansi_text)
    text_path.write_text(plain_text)
    svg_path.write_text(_ansi_to_svg(ansi_text, width=width, title=label))

    return {
        "label": label,
        "ansi_path": str(ansi_path),
        "text_path": str(text_path),
        "svg_path": str(svg_path),
    }


def _session_exists(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True,
        text=True,
        env={**os.environ, "TMUX": ""},
    )
    return result.returncode == 0


def _pane_target(session: str) -> str:
    result = _tmux("list-panes", "-t", session, "-F", "#{pane_id}")
    pane_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if not pane_ids:
        raise RuntimeError(f"no tmux panes found for session {session!r}")
    return pane_ids[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a TUI command in tmux and capture snapshots after key steps."
    )
    parser.add_argument("--command", required=True, help="Shell command to run inside tmux.")
    parser.add_argument("--output-dir", required=True, help="Directory for capture artifacts.")
    parser.add_argument(
        "--step",
        action="append",
        default=[],
        type=_parse_step,
        help="Interaction step in the form LABEL=KEY[,KEY...].",
    )
    parser.add_argument("--startup-wait", type=float, default=2.0)
    parser.add_argument("--step-wait", type=float, default=1.5)
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=40)
    parser.add_argument("--history-lines", type=int, default=200)
    parser.add_argument("--session-name", default="")
    parser.add_argument("--keep-session", action="store_true")
    args = parser.parse_args()

    if shutil.which("tmux") is None:
        print("tmux is required for tui_capture.py", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = args.session_name or f"tui-capture-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    manifest: dict[str, object] = {
        "command": args.command,
        "output_dir": str(output_dir),
        "session": session,
        "cols": args.cols,
        "rows": args.rows,
        "startup_wait": args.startup_wait,
        "step_wait": args.step_wait,
        "history_lines": args.history_lines,
        "steps": [asdict(step) for step in args.step],
        "snapshots": [],
    }

    try:
        _tmux(
            "new-session",
            "-d",
            "-s",
            session,
            "-x",
            str(args.cols),
            "-y",
            str(args.rows),
            args.command,
        )
        time.sleep(args.startup_wait)
        if not _session_exists(session):
            raise RuntimeError("tmux session exited before initial capture")

        ansi_text, plain_text = _capture_pane(session, history_lines=args.history_lines)
        manifest["snapshots"].append(
            _write_snapshot(output_dir, 0, "initial", ansi_text, plain_text, width=args.cols)
        )

        for index, step in enumerate(args.step, start=1):
            _tmux("send-keys", "-t", _pane_target(session), *step.keys)
            time.sleep(args.step_wait)
            if not _session_exists(session):
                raise RuntimeError(f"tmux session exited before capture step {step.label!r}")
            ansi_text, plain_text = _capture_pane(session, history_lines=args.history_lines)
            manifest["snapshots"].append(
                _write_snapshot(
                    output_dir,
                    index,
                    step.label,
                    ansi_text,
                    plain_text,
                    width=args.cols,
                )
            )
    finally:
        if _session_exists(session) and not args.keep_session:
            subprocess.run(
                ["tmux", "kill-session", "-t", session],
                capture_output=True,
                text=True,
                env={**os.environ, "TMUX": ""},
            )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(str(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
