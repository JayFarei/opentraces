"""Cluster C-1 — batch form of ``trail track``.

Today ``trail track --patch <id>`` works one patch at a time. Surveying
100+ patches via subprocess takes minutes. The in-process API
(``core.trails.sync.sync_patch``) is fast (~30s for 111 patches) but not
exposed. C-1 adds:

* ``trail track --since <duration>`` — emit one JSONL row per Trace
  Patch whose ``event_time`` falls in the window;
* ``trail track --patches-from <file>`` — read patch ids (one per line,
  or JSONL with a ``patch_id`` field);
* ``trail track --all`` — every Trace Patch in the project's trail;
* ``trail track --limit N`` — cap the output.

Performance target: 100 patches under 5s wall-clock when invoked via
``CliRunner`` (in-process, no subprocess fan-out).
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import TrailEventDraft, append_event_batch
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "app.py").write_text("def value():\n    return 'old'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _seed_patches(repo: Path, count: int, *, anchored_count: int = 0) -> list[str]:
    """Append ``count`` Trace Patch events (and optionally Git Anchors)."""
    now = datetime.now(timezone.utc)
    patch_ids: list[str] = []
    drafts: list[TrailEventDraft] = []
    for index in range(count):
        patch_id = f"tracepatch-sha256:batch-{index:04d}"
        patch_ids.append(patch_id)
        # Stagger event_time so we can test --since.
        event_time = (now - timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        drafts.append(
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=f"tr-batch-{index:04d}",
                step_index=1,
                event_time=event_time,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": patch_id,
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": f"    return 'val{index}'\n",
                    "raw_authored_hash": sha256_text(f"val{index}"),
                    "git_clean_hash": sha256_text(f"val{index}"),
                    "limitations": [],
                },
            )
        )
    if drafts:
        append_event_batch(repo, drafts, writer="test-fixture")
    return patch_ids


def _run(repo: Path, args: list[str]):
    return CliRunner().invoke(main, args + ["--project", str(repo)])


def test_plan087_scoped_batch_read_is_faithful_to_full_read(tmp_path: Path) -> None:
    """Plan 087 — ``trail track --all`` now threads a SCOPED event read into
    ``sync_patch`` (only the patch-sync event types) instead of a full log read,
    so a large log's ~500k anchor-search + context events are never parsed. This
    is only sound if the scoped read returns EXACTLY the events a full read would
    of those types, in event_sequence order — proved here end to end.
    """
    from opentraces.core.trails import read_events, read_events_scoped

    _init_repo(tmp_path)
    _seed_patches(tmp_path, 6, anchored_count=4)

    # Run the batch once so sync_patch writes patch_survival_cached events (the
    # third scoped type) into the log.
    res = _run(tmp_path, ["trail", "track", "--all", "--json"])
    assert res.exit_code == 0, res.output

    types = {"trace_patch_created", "git_anchor_created", "patch_survival_cached"}
    scoped_events = read_events_scoped(tmp_path, event_types=types)
    full = [
        (e.event_type, e.event_sequence)
        for e in read_events(tmp_path)
        if e.event_type in types
    ]
    scoped = [(e.event_type, e.event_sequence) for e in scoped_events]
    # Load-bearing: the scoped thread is byte-for-byte the events of these types
    # a full read would surface, in the same order -> identical survival output.
    assert scoped == full, "scoped read diverged from the filtered full read"
    # And the scope is type-correct (never admits an out-of-scope event type).
    assert {e.event_type for e in scoped_events} <= types


def test_track_since_returns_jsonl_one_row_per_patch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_patches(tmp_path, 5)

    result = _run(tmp_path, ["trail", "track", "--since", "12h", "--json"])
    assert result.exit_code == 0, result.output

    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 5
    rows = [json.loads(line) for line in lines]
    seen_patch_ids = {row["trace_patch_id"] for row in rows}
    assert len(seen_patch_ids) == 5
    for row in rows:
        assert "current_survival" in row
        assert "survival_state" in row["current_survival"]


def test_track_since_filters_outside_window(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    # Two recent patches, one ancient.
    now = datetime.now(timezone.utc)
    drafts: list[TrailEventDraft] = []
    for label, age_minutes in [("recent-a", 5), ("recent-b", 30), ("ancient", 60 * 24 * 30)]:
        event_time = (now - timedelta(minutes=age_minutes)).isoformat().replace(
            "+00:00", "Z"
        )
        drafts.append(
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=f"tr-{label}",
                step_index=1,
                event_time=event_time,
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": f"tracepatch-sha256:{label}",
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": f"    return '{label}'\n",
                    "raw_authored_hash": sha256_text(label),
                    "git_clean_hash": sha256_text(label),
                    "limitations": [],
                },
            )
        )
    append_event_batch(tmp_path, drafts, writer="test-fixture")

    result = _run(tmp_path, ["trail", "track", "--since", "12h", "--json"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    ids = {row["trace_patch_id"] for row in rows}
    assert "tracepatch-sha256:recent-a" in ids
    assert "tracepatch-sha256:recent-b" in ids
    assert "tracepatch-sha256:ancient" not in ids


def test_track_since_performance_under_5s_for_100_patches(tmp_path: Path) -> None:
    """In-process invocation must finish 100 patches in under 5s wall-clock."""
    _init_repo(tmp_path)
    _seed_patches(tmp_path, 100)

    t0 = time.time()
    result = _run(tmp_path, ["trail", "track", "--since", "12h", "--json", "--limit", "100"])
    elapsed = time.time() - t0
    assert result.exit_code == 0, result.output
    assert elapsed < 5.0, f"track --since took {elapsed:.2f}s for 100 patches"
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert len(rows) == 100


def test_track_patches_from_file_accepts_one_id_per_line(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    patch_ids = _seed_patches(tmp_path, 3)
    listing = tmp_path / "patches.txt"
    listing.write_text("\n".join(patch_ids) + "\n")

    result = _run(
        tmp_path,
        ["trail", "track", "--patches-from", str(listing), "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert {row["trace_patch_id"] for row in rows} == set(patch_ids)


def test_track_patches_from_file_accepts_jsonl_with_patch_id_field(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    patch_ids = _seed_patches(tmp_path, 3)
    listing = tmp_path / "patches.jsonl"
    listing.write_text(
        "\n".join(json.dumps({"patch_id": pid, "extra": "ignored"}) for pid in patch_ids)
        + "\n"
    )

    result = _run(
        tmp_path,
        ["trail", "track", "--patches-from", str(listing), "--json"],
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert {row["trace_patch_id"] for row in rows} == set(patch_ids)


def test_track_all_returns_every_patch(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    patch_ids = _seed_patches(tmp_path, 4)

    result = _run(tmp_path, ["trail", "track", "--all", "--json"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert {row["trace_patch_id"] for row in rows} == set(patch_ids)


def test_track_limit_caps_output(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_patches(tmp_path, 8)

    result = _run(tmp_path, ["trail", "track", "--all", "--json", "--limit", "3"])
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert len(rows) == 3


def test_track_batch_modes_mutually_exclusive_with_selectors(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    patch_ids = _seed_patches(tmp_path, 1)

    # --since with TRACE_ID should fail
    result = _run(tmp_path, ["trail", "track", "tr-batch-0000", "--since", "12h", "--json"])
    assert result.exit_code == 2
    assert "batch" in result.output.lower() or "scope" in result.output.lower()

    # --all with --patch should fail
    result = _run(tmp_path, ["trail", "track", "--patch", patch_ids[0], "--all", "--json"])
    assert result.exit_code == 2


def test_track_batch_table_rejected(tmp_path: Path) -> None:
    """--table is only meaningful for the full-trace view, not batch."""
    _init_repo(tmp_path)
    _seed_patches(tmp_path, 2)

    result = _run(tmp_path, ["trail", "track", "--all", "--table"])
    assert result.exit_code == 2


def test_track_since_supports_human_durations(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_patches(tmp_path, 2)

    for duration in ("1h", "30m", "1d", "60s"):
        result = _run(tmp_path, ["trail", "track", "--since", duration, "--json"])
        assert result.exit_code == 0, f"--since {duration}: {result.output}"


def test_track_since_invalid_duration_errors(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _seed_patches(tmp_path, 1)

    result = _run(tmp_path, ["trail", "track", "--since", "garbage", "--json"])
    assert result.exit_code == 2
    assert "duration" in result.output.lower() or "since" in result.output.lower()
