from __future__ import annotations

import sys
from pathlib import Path

import opentraces


def test_prefer_workspace_schema_path_moves_repo_schema_src_to_front(monkeypatch):
    repo_root = Path(opentraces.__file__).resolve().parents[2]
    schema_src = str(repo_root / "packages" / "opentraces-schema" / "src")

    shifted_path = [p for p in sys.path if p != schema_src]
    shifted_path.append(schema_src)
    monkeypatch.setattr(sys, "path", shifted_path[:])

    assert sys.path[0] != schema_src

    opentraces._prefer_workspace_schema_path()

    assert sys.path[0] == schema_src
    assert sys.path.count(schema_src) == 1
