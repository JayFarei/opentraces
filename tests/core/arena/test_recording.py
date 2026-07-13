from __future__ import annotations

import json
from pathlib import Path

from opentraces.core.arena.recording import convert_script_cast


def test_util_linux_timing_is_converted_to_asciicast_v2(tmp_path: Path) -> None:
    typescript = tmp_path / "terminal.typescript"
    timing = tmp_path / "terminal.timing"
    typescript.write_bytes(b"hello\r\nworld\r\n")
    timing.write_text("0.100 7\n0.250 7\n", encoding="utf-8")

    cast = convert_script_cast(typescript, timing)

    lines = cast.decode("utf-8").splitlines()
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert json.loads(lines[1]) == [0.1, "o", "hello\r\n"]
    assert json.loads(lines[2]) == [0.35, "o", "world\r\n"]


def test_advanced_timing_output_records_are_supported(tmp_path: Path) -> None:
    typescript = tmp_path / "terminal.typescript"
    timing = tmp_path / "terminal.timing"
    typescript.write_bytes(b"ok\r\n")
    timing.write_text("H 0.000 START_TIME 1\nO 0.020 4\n", encoding="utf-8")

    cast = convert_script_cast(typescript, timing)

    assert json.loads(cast.decode().splitlines()[1]) == [0.02, "o", "ok\r\n"]
