"""Regression coverage for the bundled skill-command-trajectory-eval-v1 template.

The flagship (only bundled) workflow template's build_rows.py hardcodes a
default upstream source path
(~/.opentraces/datasets/skill-command-trajectories/data/train.jsonl) and used
to crash with an unhandled FileNotFoundError when that upstream dataset had
not been produced yet -- i.e. out of the box on a fresh bucket. It must
degrade gracefully instead: emit zero rows and explain why on stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BUILD_ROWS = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "opentraces"
    / "workflow_templates"
    / "skill-command-trajectory-eval-v1"
    / "scripts"
    / "build_rows.py"
)

RAW_ROW = {
    "row_id": "raw-1",
    "project_slug": "demo",
    "session_id": "sess-1",
    "command": {
        "source": "claude_slash_command",
        "skill_name": "demo-skill",
        "command_name": "/demo-skill",
        "args": "do the thing",
    },
    "attribution": {
        "start_step_index": 1,
        "end_step_index": 3,
        "confidence": "high",
    },
    "trajectory": {
        "summary": {
            "files_modified": ["a.py"],
            "tools": ["Edit"],
            "write_operation_count": 1,
        }
    },
    "outcome": {"success": True, "committed": True, "commit_sha": "abc123"},
    "quality": {"limitations": []},
    "patches": [{"file_path": "a.py"}],
}


def _run(env: dict[str, str], *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(BUILD_ROWS), *extra_args],
        env=env,
        capture_output=True,
        text=True,
    )


def test_missing_default_upstream_degrades_gracefully(tmp_path):
    # Fresh bucket: OT_OPENTRACES_DIR points somewhere with no
    # datasets/skill-command-trajectories/data/train.jsonl at all.
    fresh_bucket = tmp_path / "opentraces-home"
    fresh_bucket.mkdir()
    output = tmp_path / "out.jsonl"

    import os

    env = dict(os.environ)
    env["OT_OPENTRACES_DIR"] = str(fresh_bucket)
    env["OT_DATASET_OUTPUT"] = str(output)
    env.pop("OT_RUN_PACKET", None)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert "FileNotFoundError" not in result.stderr
    assert "not been produced" in result.stderr
    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_source_present_still_works(tmp_path):
    fresh_bucket = tmp_path / "opentraces-home"
    source = fresh_bucket / "datasets" / "skill-command-trajectories" / "data" / "train.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(RAW_ROW) + "\n", encoding="utf-8")
    output = tmp_path / "out.jsonl"

    import os

    env = dict(os.environ)
    env["OT_OPENTRACES_DIR"] = str(fresh_bucket)
    env["OT_DATASET_OUTPUT"] = str(output)
    env.pop("OT_RUN_PACKET", None)

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert output.exists()
    lines = [line for line in output.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["source_raw_row_id"] == "raw-row-000001"


def test_explicit_source_missing_still_raises(tmp_path):
    # --source is an explicit, deliberate request -- a missing explicit
    # source should still fail loudly, not silently emit zero rows.
    missing = tmp_path / "does-not-exist.jsonl"
    output = tmp_path / "out.jsonl"

    import os

    env = dict(os.environ)
    env.pop("OT_OPENTRACES_DIR", None)
    env.pop("OT_RUN_PACKET", None)

    result = _run(env, "--source", str(missing), "--output", str(output))

    assert result.returncode != 0
    assert "FileNotFoundError" in result.stderr
