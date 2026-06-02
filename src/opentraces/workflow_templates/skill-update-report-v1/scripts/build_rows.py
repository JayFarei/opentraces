"""Copy a skill-update-report-v1 JSON artifact into JSONL row form."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _env_scope() -> tuple[dict, Path] | None:
    packet = os.environ.get("OT_RUN_PACKET")
    output = os.environ.get("OT_DATASET_OUTPUT")
    if not packet or not output:
        return None
    payload = json.loads(Path(packet).read_text(encoding="utf-8"))
    return dict(payload.get("scope") or {}), Path(output)


def _write_report_row(source: Path, output: Path) -> None:
    row = json.loads(source.read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    env = _env_scope()
    if env is not None:
        scope, output = env
        source = scope.get("report_path") or scope.get("source")
        if not source:
            output.write_text("", encoding="utf-8")
            return 0
        _write_report_row(Path(str(source)), output)
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    _write_report_row(args.source, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
