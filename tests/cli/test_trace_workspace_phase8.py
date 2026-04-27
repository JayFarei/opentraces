"""Trace Trails Phase 8 portable workspace and snapshot resume coverage."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import (
    close_step_window_with_snapshot,
    open_step_window,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / ".opentraces.json").write_text(
        json.dumps({"marker_version": "2", "project_id": "portable-phase8"})
    )
    (repo / "app.py").write_text("def value():\n    return 'seed'\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, check=True)


def _write_trace_record(repo: Path, trace_id: str) -> None:
    from opentraces.core.config import get_project_traces_dir
    from opentraces.core.state import StateManager
    from opentraces.core.config import get_project_state_path

    traces_dir = get_project_traces_dir(repo)
    traces_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "schema_version": "0.3.0",
        "trace_id": trace_id,
        "session_id": "ff00aa11-2222-3333-4444-555555555555",
        "agent": {"name": "claude-code", "model": "claude-opus"},
        "task": {"description": "make portable snapshot resume work"},
        "timestamp_start": "2026-04-27T09:00:00Z",
        "timestamp_end": "2026-04-27T09:03:00Z",
        "steps": [
            {
                "step_index": 1,
                "role": "user",
                "content": "inspect the seed project",
                "call_type": "main",
                "parent_step": None,
            },
            {
                "step_index": 2,
                "role": "agent",
                "content": "change app.py to return a portable value",
                "call_type": "main",
                "parent_step": 1,
            },
            {
                "step_index": 3,
                "role": "agent",
                "content": "summarize the change",
                "call_type": "main",
                "parent_step": 2,
            },
        ],
        "metrics": {},
    }
    (traces_dir / f"{trace_id}.jsonl").write_text(json.dumps(record) + "\n")
    state = StateManager(get_project_state_path(repo))
    state._state.setdefault("traces", {})[trace_id] = {
        "trace_id": trace_id,
        "session_id": record["session_id"],
        "status": "parsed",
        "created_at": 0.0,
    }
    state.save()


def _capture_two_step_trace(repo: Path, trace_id: str) -> str:
    open_step_window(
        repo,
        trace_id=trace_id,
        step_index=1,
        agent_step_id="s1",
        tool_call_id="tool-read",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-27T09:00:01Z",
    )
    close_step_window_with_snapshot(
        repo,
        trace_id=trace_id,
        step_index=1,
        agent_step_id="s1",
        tool_call_id="tool-read",
        capture_method=["hook_posttooluse"],
        event_time="2026-04-27T09:00:02Z",
    )
    open_step_window(
        repo,
        trace_id=trace_id,
        step_index=2,
        agent_step_id="s2",
        tool_call_id="tool-edit",
        capture_method=["hook_pretooluse"],
        event_time="2026-04-27T09:01:01Z",
    )
    (repo / "app.py").write_text("def value():\n    return 'portable-phase-8'\n")
    snapshot = close_step_window_with_snapshot(
        repo,
        trace_id=trace_id,
        step_index=2,
        agent_step_id="s2",
        tool_call_id="tool-edit",
        capture_method=["hook_posttooluse"],
        event_time="2026-04-27T09:01:02Z",
    )
    return snapshot.tree_id["hex"]


def _export_workspace(
    tmp_path: Path,
    monkeypatch,
    *,
    trace_id: str = "tr-portable-phase8",
) -> tuple[CliRunner, Path, Path, str]:
    runner = CliRunner()
    source = tmp_path / "source"
    _init_repo(source)
    _write_trace_record(source, trace_id)
    recorded_tree = _capture_two_step_trace(source, trace_id)

    workspace = tmp_path / "trace-workspace"
    monkeypatch.chdir(source)
    export_result = runner.invoke(
        main,
        ["trace", "workspace", "export", trace_id, "--output", str(workspace)],
        catch_exceptions=False,
    )
    assert export_result.exit_code == 0, export_result.output
    monkeypatch.chdir(tmp_path)
    return runner, source, workspace, recorded_tree


def _open_workspace(
    runner: CliRunner,
    workspace: Path,
    project: Path,
) -> dict:
    result = runner.invoke(
        main,
        [
            "trace",
            "workspace",
            "open",
            str(workspace),
            "--project",
            str(project),
            "--json",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_trace_workspace_import_replays_and_resumes_without_source_repo(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-portable-phase8"
    runner, source, workspace, recorded_tree = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    source_unavailable = tmp_path / "source-unavailable"
    source.rename(source_unavailable)

    imported = tmp_path / "blank-import"
    _open_workspace(runner, workspace, imported)

    play_result = runner.invoke(
        main,
        ["trail", "play", trace_id, "--json", "--project", str(imported)],
        catch_exceptions=False,
    )
    assert play_result.exit_code == 0, play_result.output
    play_payload = json.loads(play_result.output)
    assert [item["event_type"] for item in play_payload["timeline"]] == [
        "trace_step_window_opened",
        "trace_snapshot_created",
        "trace_step_window_closed",
        "trace_step_window_opened",
        "trace_snapshot_created",
        "trace_step_window_closed",
    ]
    assert str(source_unavailable) not in play_result.output

    monkeypatch.chdir(imported)
    resume_result = runner.invoke(
        main,
        ["resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )
    assert resume_result.exit_code == 0, resume_result.output
    resume_payload = json.loads(resume_result.output)
    assert resume_payload["resume_mode"] == "snapshot_backed"
    assert resume_payload["target_agent"] == "claude-code"
    assert resume_payload["snapshot"]["tree_id"]["hex"] == recorded_tree
    assert str(source_unavailable) not in resume_result.output

    materialized = Path(resume_payload["materialization"]["path"])
    assert _git(materialized, "write-tree") == recorded_tree


def test_trail_snapshots_lists_rewind_candidates(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-snapshots-list"
    runner, source, _, recorded_tree = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )

    result = runner.invoke(
        main,
        ["trail", "snapshots", "--trace", trace_id, "--json", "--project", str(source)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["snapshot_count"] == 2
    assert payload["snapshots"][0]["step_id"] == "s1"
    assert payload["snapshots"][1]["step_id"] == "s2"
    assert payload["snapshots"][1]["tree_id"]["hex"] == recorded_tree


def test_snapshot_checkout_dry_run_emits_rewind_packet(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-snapshot-checkout"
    runner, source, _, recorded_tree = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    snapshots = runner.invoke(
        main,
        ["trail", "snapshots", "--trace", trace_id, "--json", "--project", str(source)],
        catch_exceptions=False,
    )
    snapshot = json.loads(snapshots.output)["snapshots"][1]

    result = runner.invoke(
        main,
        [
            "trail",
            "snapshot",
            "checkout",
            snapshot["snapshot_ref"]["ref"],
            "--dry-run",
            "--json",
            "--project",
            str(source),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["relation"] == "snapshot_rewind"
    assert payload["snapshot_id"] == snapshot["snapshot_id"]
    assert payload["tree_id"]["hex"] == recorded_tree
    assert payload["materialization"]["materialized"] is False
    assert not Path(payload["materialization"]["path"]).exists()


def test_trace_workspace_export_contains_required_git_objects_and_trail_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-workspace-contents"
    _, source, workspace, _ = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )

    manifest = json.loads((workspace / "manifest.json").read_text())
    assert manifest["trace_id"] == trace_id
    assert manifest["event_count"] == 6
    assert len(manifest["events"]) == 6
    assert len(manifest["snapshots"]) == 2
    assert (workspace / "git.bundle").exists()
    assert (workspace / "traces" / f"{trace_id}.jsonl").exists()
    _git(source, "bundle", "verify", str(workspace / "git.bundle"))


def test_trace_workspace_open_rejects_missing_snapshot_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-missing-tree"
    runner, _, workspace, _ = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    manifest_path = workspace / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["snapshots"][0]["tree_id"]["hex"] = "f" * 40
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    result = runner.invoke(
        main,
        [
            "trace",
            "workspace",
            "open",
            str(workspace),
            "--project",
            str(tmp_path / "blank-bad"),
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 3
    assert "missing_snapshot_tree" in result.output


def test_resume_from_snapshot_packet_is_filtered_and_records_fork_lineage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-resume-packet"
    runner, _, workspace, recorded_tree = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    imported = tmp_path / "resume-import"
    _open_workspace(runner, workspace, imported)
    before_status = _git(imported, "status", "--short")

    monkeypatch.chdir(imported)
    result = runner.invoke(
        main,
        ["resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    after_status = _git(imported, "status", "--short")
    assert after_status == before_status
    payload = json.loads(result.output)
    assert payload["snapshot"]["tree_id"]["hex"] == recorded_tree
    assert [step["step_id"] for step in payload["session_context"]["steps"]] == [
        "s1",
        "s2",
    ]
    assert "summarize the change" not in json.dumps(payload["session_context"])
    assert payload["fork_lineage"]["source_trace_id"] == trace_id
    assert payload["fork_lineage"]["source_step_id"] == "s2"
    assert "external_services_not_captured" in payload["non_portable_dependencies"]
    assert payload["adapter_slots"]["codex"]["implemented"] is False
    assert payload["adapter_slots"]["codex"]["status"] == "schema_ready_unimplemented"


def test_resume_from_snapshot_reports_missing_snapshot_as_unknown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    trace_id = "tr-no-snapshot"
    source = tmp_path / "source-no-snapshot"
    _init_repo(source)
    _write_trace_record(source, trace_id)
    monkeypatch.chdir(source)

    result = runner.invoke(
        main,
        ["resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resume_mode"] == "unknown"
    assert payload["relation"] == "unknown"
    assert payload["limitations"] == ["missing_snapshot:step_2"]
