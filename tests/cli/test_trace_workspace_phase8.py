"""Trace Trails Phase 8 portable workspace and snapshot resume coverage."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from opentraces.cli import main
from opentraces.core.trails import (
    TrailEventDraft,
    append_event_batch,
    close_step_window_with_snapshot,
    open_step_window,
)
from opentraces.core.trails.ids import git_anchor_ref, trace_patch_ref
from opentraces.core.trails.models import sha256_text


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def _commit(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)
    return _git(repo, "rev-parse", "HEAD")


def _run_cli_subprocess(
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[2]
    subprocess_env = os.environ.copy()
    pythonpath = os.pathsep.join([
        str(repo_root / "src"),
        str(repo_root / "packages" / "opentraces-schema" / "src"),
        subprocess_env.get("PYTHONPATH", ""),
    ])
    subprocess_env["PYTHONPATH"] = pythonpath
    if env:
        subprocess_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", "from opentraces.cli import main; main()", *args],
        cwd=cwd,
        env=subprocess_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            "CLI subprocess failed "
            f"exit={proc.returncode} args={args}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


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


def _write_trace_record(
    repo: Path,
    trace_id: str,
    *,
    snapshot_resume_contract: bool = True,
) -> None:
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
    if snapshot_resume_contract:
        record["metadata"] = {
            "opentraces_version": "0.4.0",
            "trace_trails": {
                "schema_version": "opentraces.trace_trails.v1",
                "snapshot_resume_contract": "opentraces.snapshot_resume.v1",
            },
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


def _append_step2_patch_and_anchor(
    repo: Path,
    trace_id: str,
    commit_sha: str,
) -> dict[str, str]:
    authored = "    return 'portable-phase-8'\n"
    trace_patch_id = sha256_text(f"{trace_id}:step2:patch").split(":", 1)[1]
    git_anchor_id = sha256_text(f"{trace_id}:step2:anchor").split(":", 1)[1]
    append_event_batch(
        repo,
        [
            TrailEventDraft(
                event_type="trace_patch_created",
                trace_id=trace_id,
                step_index=2,
                event_time="2026-04-27T09:01:03Z",
                capture_method=["hook_posttooluse"],
                payload={
                    "trace_patch_id": trace_patch_id,
                    "trace_patch_ref": trace_patch_ref(trace_patch_id),
                    "file_path": "app.py",
                    "affected_range": {"start_line": 2, "end_line": 2},
                    "authored_text": authored,
                    "raw_authored_hash": sha256_text(authored),
                    "git_clean_hash": sha256_text(" ".join(authored.split())),
                    "limitations": ["external_services_not_captured"],
                },
            ),
            TrailEventDraft(
                event_type="git_anchor_created",
                trace_id=trace_id,
                step_index=2,
                event_time="2026-04-27T09:01:04Z",
                capture_method=["post_commit_correlator"],
                payload={
                    "git_anchor_id": git_anchor_id,
                    "git_anchor_ref": git_anchor_ref(git_anchor_id),
                    "trace_patch_id": trace_patch_id,
                    "trace_patch_ref": trace_patch_ref(trace_patch_id),
                    "commit_id": {"algo": "sha1", "hex": commit_sha},
                    "path": "app.py",
                    "range": {"start_line": 2, "end_line": 2},
                    "relation": "anchored_in_git",
                    "evidence_tier": "exact_range_hash",
                    "evidence_firmness": "firm",
                    "limitations": [],
                },
            ),
        ],
        writer="test-fixture",
    )
    return {
        "trace_patch_id": trace_patch_id,
        "git_anchor_id": git_anchor_id,
    }


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
        ["trail", "teleport", "export", trace_id, "--output", str(workspace)],
        catch_exceptions=False,
    )
    assert export_result.exit_code == 0, export_result.output
    monkeypatch.chdir(tmp_path)
    return runner, source, workspace, recorded_tree


def _export_workspace_with_patch_anchor(
    tmp_path: Path,
    monkeypatch,
    *,
    trace_id: str,
) -> tuple[CliRunner, Path, Path, str, str, dict[str, str]]:
    runner = CliRunner()
    source = tmp_path / "source"
    _init_repo(source)
    _write_trace_record(source, trace_id)
    recorded_tree = _capture_two_step_trace(source, trace_id)
    commit_sha = _commit(source, "agent change")
    ids = _append_step2_patch_and_anchor(source, trace_id, commit_sha)

    workspace = tmp_path / "trace-workspace"
    monkeypatch.chdir(source)
    export_result = runner.invoke(
        main,
        ["trail", "teleport", "export", trace_id, "--output", str(workspace)],
        catch_exceptions=False,
    )
    assert export_result.exit_code == 0, export_result.output
    monkeypatch.chdir(tmp_path)
    return runner, source, workspace, recorded_tree, commit_sha, ids


def _open_workspace(
    runner: CliRunner,
    workspace: Path,
    project: Path,
) -> dict:
    result = runner.invoke(
        main,
        [
            "trail",
            "teleport",
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


def test_trail_play_default_renders_human_graph(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-play-human"
    runner, source, workspace, recorded_tree, commit_sha, ids = (
        _export_workspace_with_patch_anchor(
            tmp_path,
            monkeypatch,
            trace_id=trace_id,
        )
    )
    source_unavailable = tmp_path / "source-unavailable"
    source.rename(source_unavailable)

    imported = tmp_path / "play-human-import"
    _open_workspace(runner, workspace, imported)

    result = runner.invoke(
        main,
        ["trail", "timeline", trace_id, "--project", str(imported)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert result.output.startswith(f"Trace timeline for t:{trace_id[:8]}\n")
    assert "Workspace: trace-workspace-" in result.output
    assert "Source repo: unavailable" in result.output
    assert "2 snapshots · 1 Trace Patch anchored in Git" in result.output
    assert f"╭◆ t:{trace_id[:8]}" in result.output
    assert "├◆ step:s1  inspect the seed project" in result.output
    assert "╰◆ step:s2  change app.py to return a portable value" in result.output
    assert "◇ snap:" in result.output
    assert f"tree:{recorded_tree[:8]}" in result.output
    assert "checkout: opentraces trail snapshot checkout " in result.output
    assert f"resume: opentraces trail resume {trace_id} --at-step s2" in result.output
    assert f"◇ tp:{ids['trace_patch_id'][:8]}  app.py  landed_exact" in result.output
    assert f"● c:{commit_sha[:8]}  ga:{ids['git_anchor_id'][:8]}" in result.output
    assert "evidence: exact range match · firm" in result.output
    assert "Limitations: external_services_not_captured" in result.output
    assert "{" not in result.output
    assert str(source_unavailable) not in result.output


def test_trail_play_table_renders_scan_friendly_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-play-table"
    runner, source, workspace, _, commit_sha, ids = (
        _export_workspace_with_patch_anchor(
            tmp_path,
            monkeypatch,
            trace_id=trace_id,
        )
    )
    source_unavailable = tmp_path / "source-unavailable"
    source.rename(source_unavailable)

    imported = tmp_path / "play-table-import"
    _open_workspace(runner, workspace, imported)

    result = runner.invoke(
        main,
        ["trail", "timeline", trace_id, "--table", "--project", str(imported)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == (
        "SEQ  STEP  KIND          OBJECT        FILE/COMMIT  EVIDENCE             LIMITATIONS"
    )
    assert "s1    snapshot" in result.output
    assert "s2    snapshot" in result.output
    assert (
        f"s2    trace_patch   tp:{ids['trace_patch_id'][:8]}  app.py"
    ) in result.output
    assert (
        f"tp:{ids['trace_patch_id'][:8]}  app.py       -                    "
        "external_services_not_captured"
    ) in result.output
    assert (
        f"s2    git_anchor    ga:{ids['git_anchor_id'][:8]}  c:{commit_sha[:8]}   "
        "exact range match · firm"
    ) in result.output
    assert "{" not in result.output
    assert str(source_unavailable) not in result.output


def test_trail_play_json_uses_imported_events_not_original_repository(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-play-json-imported"
    runner, source, workspace, _, _, _ = _export_workspace_with_patch_anchor(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    source_unavailable = tmp_path / "source-unavailable"
    source.rename(source_unavailable)

    imported = tmp_path / "play-json-import"
    _open_workspace(runner, workspace, imported)

    result = runner.invoke(
        main,
        ["trail", "timeline", trace_id, "--json", "--project", str(imported)],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["event_count"] == 8
    assert [item["event_type"] for item in payload["timeline"]] == [
        "trace_step_window_opened",
        "trace_snapshot_created",
        "trace_step_window_closed",
        "trace_step_window_opened",
        "trace_snapshot_created",
        "trace_step_window_closed",
        "trace_patch_created",
        "git_anchor_created",
    ]
    assert payload["workspace_source"]["type"] == "trace_workspace"
    assert str(source_unavailable) not in result.output


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
        ["trail", "timeline", trace_id, "--json", "--project", str(imported)],
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
        ["trail", "resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )
    assert resume_result.exit_code == 0, resume_result.output
    resume_payload = json.loads(resume_result.output)
    assert resume_payload["resume_mode"] == "snapshot_backed"
    assert resume_payload["target_agent"] == "claude-code"
    assert resume_payload["snapshot"]["tree_id"]["hex"] == recorded_tree
    assert str(source_unavailable) not in resume_result.output

    materialized = Path(resume_payload["materialization"]["path"])
    assert resume_payload["materialization"]["materialized"] is False
    assert not materialized.exists()


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


def test_snapshot_checkout_materializes_isolated_worktree_and_renders_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-snapshot-checkout-materialize"
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
    target = tmp_path / "snapshot-worktree"

    result = runner.invoke(
        main,
        [
            "trail",
            "snapshot",
            "checkout",
            snapshot["snapshot_ref"]["ref"],
            "--target",
            str(target),
            "--project",
            str(source),
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    assert "Snapshot:" in result.output
    assert "Tree:" in result.output
    assert str(target) in result.output
    assert target != source
    assert _git(target, "write-tree") == recorded_tree


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


def test_trace_workspace_export_unknown_trace_returns_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    source = tmp_path / "source-unknown"
    _init_repo(source)
    monkeypatch.chdir(source)

    result = runner.invoke(
        main,
        [
            "trail",
            "teleport",
            "export",
            "tr-unknown",
            "--output",
            str(tmp_path / "unknown-workspace"),
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 3
    assert "no TrailEvents found for trace tr-unknown" in result.output


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

    target = tmp_path / "blank-bad"
    result = runner.invoke(
        main,
        [
            "trail",
            "teleport",
            "open",
            str(workspace),
            "--project",
            str(target),
            "--json",
        ],
        catch_exceptions=False,
    )

    assert result.exit_code == 3
    assert "missing_snapshot_tree" in result.output
    assert not target.exists() or not any(target.iterdir())


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
        ["trail", "resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    after_status = _git(imported, "status", "--short")
    assert after_status == before_status
    payload = json.loads(result.output)
    assert payload["materialization"]["materialized"] is False
    assert not Path(payload["materialization"]["path"]).exists()
    assert payload["snapshot"]["tree_id"]["hex"] == recorded_tree
    assert payload["resume_contract"] == {
        "metadata_key": "trace_trails.snapshot_resume_contract",
        "schema_version": "opentraces.snapshot_resume.v1",
        "status": "satisfied",
    }
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
    session_path = Path(payload["session"]["new_session_path"])
    assert not session_path.exists()

    from opentraces.core.config import get_project_state_path
    from opentraces.core.state import StateManager

    state = StateManager(get_project_state_path(imported))
    assert state.get_session(payload["session"]["new_session_id"]) is None


def test_resume_from_snapshot_execution_hands_off_to_claude_in_materialized_worktree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-resume-execution"
    runner, _, workspace, recorded_tree = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    imported = tmp_path / "execution-import"
    _open_workspace(runner, workspace, imported)

    monkeypatch.chdir(imported)
    with patch("shutil.which", return_value="/usr/local/bin/claude"), \
         patch("os.execvp") as exec_mock, \
         patch("os.chdir") as chdir_mock:
        result = runner.invoke(
            main,
            ["trail", "resume", trace_id, "--at-step", "s2"],
            catch_exceptions=False,
        )

    assert result.exit_code == 1
    materialized = Path(chdir_mock.call_args.args[0])
    assert materialized != imported
    assert _git(materialized, "write-tree") == recorded_tree

    executable, argv = exec_mock.call_args.args
    assert executable == "/usr/local/bin/claude"
    assert argv[0] == "/usr/local/bin/claude"
    assert argv[1] == "--resume"
    resumed_session_id = argv[2]
    assert resumed_session_id

    from opentraces.core.config import get_project_state_path
    from opentraces.core.state import StateManager

    state = StateManager(get_project_state_path(imported))
    forked = state.get_session(resumed_session_id)
    assert forked is not None
    assert forked.parent_session_id == "ff00aa11-2222-3333-4444-555555555555"
    assert forked.parent_step_id == "s2"
    assert forked.source_path and Path(forked.source_path).exists()
    session_lines = [
        json.loads(line)
        for line in Path(forked.source_path).read_text().splitlines()
    ]
    preamble = session_lines[0]
    assert preamble["type"] == "opentraces_resume_packet"
    assert preamble["schema_version"] == "opentraces.snapshot_resume.v1"
    assert "parentSessionId" not in preamble
    assert "parentStepId" not in preamble
    assert preamble["opentraces"]["resume_mode"] == "snapshot_backed"
    assert preamble["opentraces"]["source_trace_id"] == trace_id
    assert preamble["opentraces"]["source_session_id"] == (
        "ff00aa11-2222-3333-4444-555555555555"
    )
    assert preamble["opentraces"]["source_step_id"] == "s2"
    assert preamble["opentraces"]["source_snapshot_id"]
    assert preamble["opentraces"]["materialized_worktree"] == str(materialized)


def test_trace_workspace_resume_executes_claude_subprocess_end_to_end(
    tmp_path: Path,
) -> None:
    trace_id = "tr-resume-subprocess-e2e"
    source = tmp_path / "source-subprocess"
    _init_repo(source)
    _write_trace_record(source, trace_id)
    recorded_tree = _capture_two_step_trace(source, trace_id)
    workspace = tmp_path / "trace-workspace"

    export_result = _run_cli_subprocess(
        ["trail", "teleport", "export", trace_id, "--output", str(workspace), "--json"],
        cwd=source,
    )
    export_payload = json.loads(export_result.stdout)
    assert export_payload["event_count"] == 6
    assert export_payload["snapshot_count"] == 2

    source_unavailable = tmp_path / "source-unavailable"
    source.rename(source_unavailable)
    imported = tmp_path / "blank-import-subprocess"
    _run_cli_subprocess(
        [
            "trail",
            "teleport",
            "open",
            str(workspace),
            "--project",
            str(imported),
            "--json",
        ],
        cwd=tmp_path,
    )

    play_result = _run_cli_subprocess(
        ["trail", "timeline", trace_id, "--json", "--project", str(imported)],
        cwd=tmp_path,
    )
    play_payload = json.loads(play_result.stdout)
    assert play_payload["event_count"] == 6
    assert str(source_unavailable) not in play_result.stdout

    dry_run = _run_cli_subprocess(
        ["trail", "resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        cwd=imported,
    )
    dry_payload = json.loads(dry_run.stdout)
    assert dry_payload["resume_mode"] == "snapshot_backed"
    assert dry_payload["snapshot"]["tree_id"]["hex"] == recorded_tree
    assert dry_payload["materialization"]["materialized"] is False
    assert not Path(dry_payload["materialization"]["path"]).exists()

    shim_log = tmp_path / "claude-shim.json"
    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    shim = shim_dir / "claude"
    shim.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['CLAUDE_SHIM_LOG']).write_text("
        "json.dumps({'argv': sys.argv, 'cwd': os.getcwd()}) + '\\n')\n"
        "sys.exit(42)\n"
    )
    shim.chmod(0o755)

    exec_result = _run_cli_subprocess(
        ["trail", "resume", trace_id, "--at-step", "s2"],
        cwd=imported,
        env={
            "PATH": f"{shim_dir}{os.pathsep}{os.environ.get('PATH', '')}",
            "CLAUDE_SHIM_LOG": str(shim_log),
        },
        check=False,
    )
    assert exec_result.returncode == 42
    shim_payload = json.loads(shim_log.read_text())
    assert shim_payload["argv"][1] == "--resume"
    resumed_session_id = shim_payload["argv"][2]
    assert resumed_session_id

    materialized = Path(shim_payload["cwd"])
    assert materialized != imported
    assert _git(materialized, "write-tree") == recorded_tree

    from opentraces.core.config import get_project_state_path
    from opentraces.core.state import StateManager

    state = StateManager(get_project_state_path(imported))
    forked = state.get_session(resumed_session_id)
    assert forked is not None
    assert forked.parent_session_id == "ff00aa11-2222-3333-4444-555555555555"
    assert forked.parent_step_id == "s2"
    assert forked.source_path and Path(forked.source_path).exists()
    preamble = json.loads(Path(forked.source_path).read_text().splitlines()[0])
    assert preamble["type"] == "opentraces_resume_packet"
    assert preamble["opentraces"]["source_trace_id"] == trace_id
    assert preamble["opentraces"]["source_step_id"] == "s2"
    assert preamble["opentraces"]["materialized_worktree"] == str(materialized)


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
        ["trail", "resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resume_mode"] == "unknown"
    assert payload["relation"] == "unknown"
    assert payload["limitations"] == ["missing_snapshot:step_2"]
    assert payload["legacy_fallback"]["mode"] == "claude_code_at_step"
    assert payload["legacy_fallback"]["available_when"] == "non_json_resume"


def test_resume_from_pre_04_trace_requires_snapshot_resume_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = CliRunner()
    trace_id = "tr-pre-04-contract"
    source = tmp_path / "source-pre-04"
    _init_repo(source)
    _write_trace_record(source, trace_id, snapshot_resume_contract=False)
    _capture_two_step_trace(source, trace_id)
    monkeypatch.chdir(source)

    result = runner.invoke(
        main,
        ["trail", "resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["resume_mode"] == "unsupported"
    assert payload["relation"] == "unknown"
    assert payload["limitations"] == [
        "missing_snapshot_resume_contract:opentraces.snapshot_resume.v1"
    ]
    assert payload["required_capability"] == {
        "metadata_key": "trace_trails.snapshot_resume_contract",
        "schema_version": "opentraces.snapshot_resume.v1",
        "min_opentraces_version": "0.4.0",
    }
    assert payload["legacy_fallback"]["mode"] == "claude_code_at_step"
    assert "session" not in payload
    assert "materialization" not in payload


def test_resume_from_snapshot_rejects_malformed_step_without_traceback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-bad-step"
    runner, _, workspace, _ = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    imported = tmp_path / "bad-step-import"
    _open_workspace(runner, workspace, imported)
    monkeypatch.chdir(imported)

    result = runner.invoke(
        main,
        ["trail", "resume", trace_id, "--at-step", "not-a-step", "--dry-run", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "INVALID_STEP"
    assert "invalid step id" in payload["error"]["message"]
    assert "Traceback" not in result.output


def test_resume_at_step_rejects_non_claude_agent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    trace_id = "tr-non-claude-step"
    runner, _, workspace, _ = _export_workspace(
        tmp_path,
        monkeypatch,
        trace_id=trace_id,
    )
    imported = tmp_path / "non-claude-import"
    _open_workspace(runner, workspace, imported)

    from opentraces.core.config import get_project_traces_dir

    traces_dir = get_project_traces_dir(imported)
    trace_file = traces_dir / f"{trace_id}.jsonl"
    record = json.loads(trace_file.read_text().splitlines()[0])
    record["agent"]["name"] = "hermes"
    trace_file.write_text(json.dumps(record) + "\n")
    monkeypatch.chdir(imported)

    result = runner.invoke(
        main,
        ["trail", "resume", trace_id, "--at-step", "s2", "--dry-run", "--json"],
        catch_exceptions=False,
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "UNSUPPORTED_AT_STEP_AGENT"
    assert "claude-code" in payload["error"]["message"]
