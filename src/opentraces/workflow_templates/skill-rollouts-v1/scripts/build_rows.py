"""Build skill-rollouts-v1 rows from skill-eval-tasks-v1 JSONL."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from opentraces.consumers.skill_intelligence.pipeline import build_rollouts_from_file


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
        source = scope.get("eval_tasks_path") or scope.get("source")
        if not source:
            output.write_text("", encoding="utf-8")
            return 0
        skill_path = scope.get("skill_path")
        build_rollouts_from_file(
            Path(str(source)),
            output,
            skill_path=Path(str(skill_path)) if skill_path else None,
        )
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--skill-path", type=Path, default=None)
    args = parser.parse_args()
    build_rollouts_from_file(args.source, args.output, skill_path=args.skill_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
