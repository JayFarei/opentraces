"""Cluster F D1 — every batch ``trail track`` row carries ``trace_id``.

Joining bursts with survival required a sidecar projection lookup
because the per-row JSON only exposed ``trace_patch_id``. D1 lifts the
trace_id (already present on the ``trace_patch_created`` event) onto
the row so callers can group rows by trace in one ``jq`` expression.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import TrailEventDraft, append_event_batch
from opentraces.core.trails.models import sha256_text


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _seed_patches_with_traces(repo: Path) -> dict[str, str]:
    """Seed three patches across two traces. Returns ``{patch_id: trace_id}``."""
    now = datetime.now(timezone.utc)
    spec: dict[str, str] = {}
    drafts: list[TrailEventDraft] = []
    for index, trace_id in enumerate(["trace-aaa", "trace-aaa", "trace-bbb"]):
        patch_id = f"tracepatch-sha256:row-{index:04d}"
        spec[patch_id] = trace_id
        event_time = (now - timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        drafts.append(
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                step_index=1,
                event_time=event_time,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": patch_id,
                    "trace_id": trace_id,
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": f"    return 'val{index}'\n",
                    "raw_authored_hash": sha256_text(f"val{index}"),
                    "git_clean_hash": sha256_text(f"val{index}"),
                    "limitations": [],
                },
            )
        )
    append_event_batch(repo, drafts, writer="test-fixture")
    return spec


def _run(repo: Path, args: list[str]):
    return CliRunner().invoke(main, args + ["--project", str(repo)])


def test_each_row_has_trace_id(tmp_path: Path) -> None:
    """Every emitted row must carry a non-null ``trace_id``."""
    _init_repo(tmp_path)
    spec = _seed_patches_with_traces(tmp_path)

    result = _run(tmp_path, ["trail", "track", "--since", "12h", "--json"])
    assert result.exit_code == 0, result.output

    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert len(rows) == 3
    for row in rows:
        assert row.get("trace_id"), f"trace_id missing on row: {row}"


def test_trace_id_resolves_via_event_log(tmp_path: Path) -> None:
    """``trace_id`` must come from the trace_patch_created event payload,
    matching the seed data 1:1 across all rows."""
    _init_repo(tmp_path)
    spec = _seed_patches_with_traces(tmp_path)

    result = _run(tmp_path, ["trail", "track", "--since", "12h", "--json"])
    assert result.exit_code == 0, result.output

    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    actual = {row["trace_patch_id"]: row.get("trace_id") for row in rows}
    assert actual == spec, f"trace_id mismatch: actual={actual} spec={spec}"
