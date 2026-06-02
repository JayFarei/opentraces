"""Build skill-episodes-v1 rows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from opentraces.consumers.skill_intelligence.pipeline import build_episode_rows


def _env_scope() -> tuple[dict, Path] | None:
    packet = os.environ.get("OT_RUN_PACKET")
    output = os.environ.get("OT_DATASET_OUTPUT")
    if not packet or not output:
        return None
    payload = json.loads(Path(packet).read_text(encoding="utf-8"))
    return dict(payload.get("scope") or {}), Path(output)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def main() -> int:
    env = _env_scope()
    if env is not None:
        scope, output = env
        rows = build_episode_rows(
            selected_skill=str(scope.get("selected_skill") or scope.get("skill_id") or "opentraces"),
            project=scope.get("project_slug") or scope.get("project"),
            min_rows=int(scope.get("min_rows") or 0),
        )
        _write(output, rows)
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--skill", default="opentraces")
    parser.add_argument("--project", default=None)
    parser.add_argument("--min-rows", type=int, default=0)
    args = parser.parse_args()
    _write(
        args.output,
        build_episode_rows(
            selected_skill=args.skill,
            project=args.project,
            min_rows=args.min_rows,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
