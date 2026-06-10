"""Regenerate the committed B3a envelope-shape snapshot.

Usage:
    .venv/bin/python -m tests.cli.regen_json_envelope_shapes

Runs the shape capture through pytest so the repo conftest's HOME
isolation applies — the sweep executes ~80 CLI commands and must NEVER
run against the developer's real ``~/.opentraces``. Review the resulting
``json_envelope_shapes.json`` diff as a contract change; bump the
affected envelope's ``schema_version`` when it is one of the frozen
``opentraces.*.v1`` consumer contracts.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    env = {**os.environ, "OT_REGEN_ENVELOPE_SHAPES": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/cli/test_json_surface_sweep.py::test_envelope_shapes_match_snapshot",
            "-q",
            "-rs",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
