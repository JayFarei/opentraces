"""Host-side conversion of util-linux ``script`` output to asciicast v2."""

from __future__ import annotations

import json
from pathlib import Path


class RecordingConversionError(ValueError):
    """The raw terminal recording cannot be converted without inventing bytes."""


def convert_script_cast(typescript_path: Path, timing_path: Path) -> bytes:
    """Convert one exact typescript/timing pair into a deterministic v2 cast."""

    payload = typescript_path.read_bytes()
    if payload.startswith(b"Script started on "):
        _, separator, payload = payload.partition(b"\n")
        if not separator:
            raise RecordingConversionError("typescript has an unterminated Script started header")
        payload, separator, _ = payload.rpartition(b"\nScript done on ")
        if not separator:
            raise RecordingConversionError("typescript has no matching Script done footer")
    cursor = 0
    elapsed = 0.0
    events: list[list[object]] = []
    for line_number, raw_line in enumerate(
        timing_path.read_text(encoding="utf-8", errors="strict").splitlines(), start=1
    ):
        parts = raw_line.split()
        if not parts:
            continue
        if parts[0] == "H":
            continue
        if parts[0] in {"O", "I"}:
            if parts[0] != "O":
                continue
            fields = parts[1:]
        else:
            fields = parts
        if len(fields) < 2:
            raise RecordingConversionError(f"invalid timing record on line {line_number}")
        try:
            delay = float(fields[0])
            count = int(fields[1])
        except ValueError as exc:
            raise RecordingConversionError(f"invalid timing record on line {line_number}") from exc
        if delay < 0 or count < 0 or cursor + count > len(payload):
            raise RecordingConversionError(f"timing record exceeds typescript on line {line_number}")
        elapsed += delay
        chunk = payload[cursor : cursor + count]
        cursor += count
        events.append([round(elapsed, 6), "o", chunk.decode("utf-8", errors="replace")])
    if cursor != len(payload):
        raise RecordingConversionError(
            f"timing records account for {cursor} of {len(payload)} typescript bytes"
        )
    header = {
        "version": 2,
        "width": 120,
        "height": 36,
        "timestamp": 0,
        "env": {"SHELL": "/bin/sh", "TERM": "xterm-256color"},
    }
    lines = [json.dumps(header, ensure_ascii=False, separators=(",", ":"))]
    lines.extend(json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in events)
    return ("\n".join(lines) + "\n").encode("utf-8")
