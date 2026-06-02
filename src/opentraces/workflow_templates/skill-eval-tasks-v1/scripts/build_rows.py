"""Build skill-eval-tasks-v1 rows from skill-episodes-v1 JSONL."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from opentraces.consumers.skill_intelligence.pipeline import build_eval_tasks_from_file


def _env_scope() -> tuple[dict, Path] | None:
    packet = os.environ.get("OT_RUN_PACKET")
    output = os.environ.get("OT_DATASET_OUTPUT")
    if not packet or not output:
        return None
    payload = json.loads(Path(packet).read_text(encoding="utf-8"))
    return dict(payload.get("scope") or {}), Path(output)


def main() -> int:
    env = _env_scope()
    if env is not None:
        scope, output = env
        source = scope.get("episodes_path") or scope.get("source")
        if not source:
            output.write_text("", encoding="utf-8")
            return 0
        build_eval_tasks_from_file(Path(str(source)), output, seed=str(scope.get("seed") or "skill-intelligence"))
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", default="skill-intelligence")
    args = parser.parse_args()
    build_eval_tasks_from_file(args.source, args.output, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
