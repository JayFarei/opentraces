"""Issue #365 — hot session ingest must not materialize the global Trail mirror."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

from opentraces.watcher.daemon import _tick_in_child


SESSION_ID = "019f365c-7f1f-7ef0-b98d-af58de07709c"


def _write_multimegabyte_codex_rollout(path: Path, project: Path) -> None:
    rows = [
        {
            "timestamp": "2026-07-06T08:37:39Z",
            "type": "session_meta",
            "payload": {
                "id": SESSION_ID,
                "timestamp": "2026-07-06T08:37:39Z",
                "cwd": str(project),
                "originator": "codex-tui",
                "cli_version": "0.132.0",
                "source": "tui",
                "model_provider": "openai",
                "model": "gpt-5.5",
            },
        },
        {
            "timestamp": "2026-07-06T08:37:40Z",
            "type": "turn_context",
            "payload": {
                "turn_id": "turn-365",
                "cwd": str(project),
                "approval_policy": "never",
            },
        },
        {
            "timestamp": "2026-07-06T08:37:41Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Reproduce issue 365."}],
            },
        },
        {
            "timestamp": "2026-07-06T08:37:42Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "exec_command",
                "call_id": "call-365",
                "input": '{"cmd":"produce a large result"}',
            },
        },
        {
            "timestamp": "2026-07-06T08:37:43Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-365",
                "output": "x" * (3 * 1024 * 1024),
            },
        },
        {
            "timestamp": "2026-07-06T08:37:44Z",
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Done."}],
            },
        },
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _write_large_unrelated_events_mirror(home: Path) -> None:
    """Create a highly compressible 512 MiB mirror without retaining it in RAM."""

    events_root = home / ".opentraces" / "bucket" / "events" / "v1"
    batches = events_root / "batches"
    batches.mkdir(parents=True)
    (events_root / "index.json").write_text(
        json.dumps(
            {
                "schema_version": "opentraces.bucket.events.v2",
                "repo_id": "historical-project",
                "event_log_ref": "refs/opentraces/local/events/v1",
                "event_log_head": "0" * 40,
                "batch_count": 1,
                "last_batch_id": "historical-batch",
                "latest_event_sequence": 1,
                "state": "ok",
            }
        ),
        encoding="utf-8",
    )

    event = {
        "event_id": "trailevent-sha256:" + ("0" * 64),
        "event_sequence": 1,
        "event_time": "2026-07-01T00:00:00Z",
        "previous_event_id": None,
        "trace_id": "unrelated-historical-trace",
        "generation_index": 0,
        "step_index": 1,
        "batch_id": "historical-batch",
        "writer": "test-fixture",
        "capture_method": ["test_fixture"],
        "event_type": "trace_patch_created",
        "payload": {"authored_text": "y" * (8 * 1024 * 1024)},
        "content_hash": "sha256:" + ("0" * 64),
        "SCHEMA_VERSION": "0.9.0",
        "SECURITY_VERSION": "0.8.0",
        "ATTRIBUTION_VERSION": "0.1.0",
    }
    line = json.dumps(event, separators=(",", ":")) + "\n"
    batch = batches / "000000000001-historical-batch.jsonl.gz"
    with gzip.open(batch, "wt", encoding="utf-8", compresslevel=1) as handle:
        for _ in range(64):
            handle.write(line)


def test_ingest_session_with_large_record_does_not_expand_global_mirror(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The exact hidden CLI path stays below 800 MiB with a 3 MiB record.

    The project intentionally has no Git repository, matching the reported
    fallback condition. Before #365, per-trace projection called
    ``list(read_events_mirror_batches())`` and expanded this unrelated 512 MiB
    mirror to more than the child RSS ceiling.
    """

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    (project / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": "issue-365-test-project",
                "review_policy": "review",
                "push_policy": "manual",
                "remotes": {},
                "active_remote": None,
                "default_visibility": "private",
                "agents": ["codex-cli"],
            }
        ),
        encoding="utf-8",
    )
    rollout = tmp_path / f"rollout-2026-07-06T08-37-39-{SESSION_ID}.jsonl"
    _write_multimegabyte_codex_rollout(rollout, project)
    _write_large_unrelated_events_mirror(home)
    monkeypatch.setenv("HOME", str(home))

    verdict = _tick_in_child(
        project,
        budget_mb=800,
        timeout_s=60,
        _argv=[
            sys.executable,
            "-m",
            "opentraces",
            "_ingest-session",
            str(rollout),
            "--agent",
            "codex-cli",
            "--project",
            str(project),
        ],
    )

    assert verdict == "ok", (
        "hot ingest expanded the unrelated global events mirror and breached "
        f"the 800 MiB RSS ceiling: {verdict}"
    )
    staged = list((home / ".opentraces" / "projects").glob("*/traces/*.jsonl"))
    assert len(staged) == 1, "bounded completion must still stage the trace"
