#!/usr/bin/env python3
"""Deterministic pytest file buckets for CI lanes.

The helper deliberately shards by file, not by individual test item. That keeps
module-level fixtures and git-heavy scenario setup together while still giving
GitHub Actions independent buckets to run in parallel.
"""

from __future__ import annotations

import argparse
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

LANE_PREFIXES: dict[str, tuple[str, ...]] = {
    "premerge": ("tests/",),
    "integration": ("tests/integration/", "tests/e2e/"),
}

LANE_EXCLUDES: dict[str, tuple[str, ...]] = {
    "premerge": (
        "tests/e2e/",
        "tests/integration/",
        "tests/otbox/",
        "tests/perf/",
    ),
    "integration": (),
}


def _as_repo_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _matches_any(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path.startswith(prefix) for prefix in prefixes)


def collect_files(lane: str) -> list[Path]:
    includes = LANE_PREFIXES[lane]
    excludes = LANE_EXCLUDES[lane]
    files: list[Path] = []
    for path in (REPO_ROOT / "tests").rglob("test*.py"):
        repo_path = _as_repo_path(path)
        if not _matches_any(repo_path, includes):
            continue
        if _matches_any(repo_path, excludes):
            continue
        files.append(path)
    return sorted(files, key=_as_repo_path)


def _weight(path: Path) -> int:
    try:
        return max(1, path.read_text(encoding="utf-8").count("\n"))
    except UnicodeDecodeError:
        return 1


def bucket_files(files: list[Path], *, shard_index: int, shard_total: int) -> list[Path]:
    if shard_total < 1:
        raise ValueError("--shard-total must be >= 1")
    if shard_index < 0 or shard_index >= shard_total:
        raise ValueError("--shard-index must satisfy 0 <= index < shard-total")

    buckets: list[list[Path]] = [[] for _ in range(shard_total)]
    weights = [0 for _ in range(shard_total)]
    for path in sorted(files, key=lambda p: (-_weight(p), _as_repo_path(p))):
        bucket_index = min(range(shard_total), key=lambda idx: (weights[idx], idx))
        buckets[bucket_index].append(path)
        weights[bucket_index] += _weight(path)
    return sorted(buckets[shard_index], key=_as_repo_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", choices=sorted(LANE_PREFIXES), required=True)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-total", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = bucket_files(
        collect_files(args.lane),
        shard_index=args.shard_index,
        shard_total=args.shard_total,
    )
    for path in files:
        print(_as_repo_path(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
