"""CLI coverage for ``opentraces trail verify``."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import EVENT_LOG_REF, TrailEventDraft, append_event_batch


def _git(
    repo: Path,
    *args: str,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=repo,
        input=input,
        text=True,
        env=env,
    ).strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _append_simple(repo: Path, trace_id: str = "tr-verify") -> None:
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_snapshot_created",
                trace_id=trace_id,
                step_index=1,
                capture_method=["doctor_probe"],
                payload={"snapshot_id": f"{trace_id}-snapshot", "limitations": []},
            ),
        ],
        writer="trail-verify-test",
    )


def _tamper_first_event(repo: Path) -> None:
    head = _git(repo, "rev-parse", EVENT_LOG_REF)
    raw = _git(repo, "show", f"{head}:events/000000000001.json")
    event = json.loads(raw)
    event["payload"]["snapshot_id"] = "tampered"
    tampered = json.dumps(event, sort_keys=True, indent=2) + "\n"

    with tempfile.TemporaryDirectory() as td:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(td) / "index")
        _git(repo, "read-tree", head, env=env)
        blob = _git(repo, "hash-object", "-w", "--stdin", input=tampered, env=env)
        _git(
            repo,
            "update-index",
            "--cacheinfo",
            f"100644,{blob},events/000000000001.json",
            env=env,
        )
        tree = _git(repo, "write-tree", env=env)
    commit = _git(repo, "commit-tree", tree, "-m", "tamper event log")
    _git(repo, "update-ref", EVENT_LOG_REF, commit, head)


def test_trail_verify_quick_json_summarizes_without_full_scan(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_simple(tmp_path, "tr1")
    _append_simple(tmp_path, "tr2")

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "verify",
            "--mode",
            "quick",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["state"] == "unverified_large"
    assert payload["mode"] == "quick"
    assert payload["verification_source"] == "bounded_head_summary"
    assert payload["event_count"] == 2
    assert payload["content_hashes_valid"] is None
    assert payload["event_chain_valid"] is None


def test_trail_verify_full_json_progress_stays_on_stderr(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_simple(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "verify",
            "--mode",
            "full",
            "--progress",
            "json",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["state"] == "ok"
    assert payload["mode"] == "full"
    progress = [
        json.loads(line)
        for line in result.stderr.splitlines()
        if line.startswith('{"event":"progress"')
    ]
    assert progress
    assert all(item["command"] == "trail verify" for item in progress)


def test_trail_verify_full_invalid_log_exits_hard(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    _append_simple(tmp_path)
    _tamper_first_event(tmp_path)

    result = CliRunner().invoke(
        main,
        [
            "trail",
            "verify",
            "--mode",
            "full",
            "--json",
            "--project",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 3, result.output
    payload = json.loads(result.stdout)
    assert payload["state"] == "invalid"
    assert payload["content_hashes_valid"] is False
    assert any("content_hash mismatch" in error for error in payload["errors"])
