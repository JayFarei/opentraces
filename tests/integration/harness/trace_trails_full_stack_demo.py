"""Deterministic full-stack Trace Trails demo harness.

The scenario is intentionally close to the Plan 54 failure mode:

1. A Claude-style agent step starts, opening a firm hook boundary.
2. A Bash/codegen step mutates the worktree, so only the filesystem watcher
   sees the concrete file transition.
3. Git commit lands before session ingest has emitted Trace Patch events.
4. The post-commit hook runs too early and cannot anchor anything yet.
5. Session ingest emits step windows and patches, reconciles the watcher
   observation, and ``trail mature`` later anchors the already-existing commit.

The harness is reusable from pytest and directly from a shell:

    python tests/integration/harness/trace_trails_full_stack_demo.py --keep --verbose
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from click.testing import CliRunner


DISTINCTIVE_LINE = 'FULL_STACK_VALUE = "trace-trails-full-stack-demo-001"\n'
SESSION_ID = "sess-trace-trails-full-stack-demo"
TOOL_USE_ID = "toolu_trace_trails_full_stack_bash"


@dataclass(frozen=True)
class DemoPaths:
    root: Path
    repo: Path
    home: Path
    workspace: Path
    opened_workspace: Path
    transcript: Path


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def _write_jsonl_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _isolate_opentraces_home(home: Path) -> None:
    """Redirect process-local OpenTraces state into ``home``.

    Tests already patch these constants, but the standalone harness must not
    touch the developer's real ``~/.opentraces`` tree. Keep imports inside this
    function so direct script execution sets ``HOME`` first.
    """
    os.environ["HOME"] = str(home)
    opentraces_dir = home / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)

    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    for mod in (paths_mod, config_mod):
        mod.OPENTRACES_DIR = opentraces_dir
        mod.CONFIG_PATH = opentraces_dir / "config.json"
        mod.CREDENTIALS_PATH = opentraces_dir / "credentials"
        mod.PROJECTS_DIR = projects_dir


@contextlib.contextmanager
def _temporary_opentraces_home(home: Path) -> Iterator[None]:
    """Temporarily redirect process-global OpenTraces paths."""
    old_home = os.environ.get("HOME")

    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    names = ("OPENTRACES_DIR", "CONFIG_PATH", "CREDENTIALS_PATH", "PROJECTS_DIR")
    saved = {
        mod: {name: getattr(mod, name) for name in names}
        for mod in (paths_mod, config_mod)
    }
    _isolate_opentraces_home(home)
    try:
        yield
    finally:
        if old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = old_home
        for mod, values in saved.items():
            for name, value in values.items():
                setattr(mod, name, value)


def _init_demo_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "opentraces-demo@example.invalid")
    _git(repo, "config", "user.name", "OpenTraces Demo")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / ".gitignore").write_text(
        ".agent-trace/\n.claude_projects_fake/\n", encoding="utf-8"
    )
    (repo / ".opentraces.json").write_text(
        json.dumps(
            {
                "marker_version": "2",
                "project_id": "trace-trails-full-stack-demo-0000",
                "review_policy": "review",
                "push_policy": "manual",
                "remotes": {
                    "origin": {
                        "url": "hf://example/trace-trails-full-stack-demo",
                        "visibility": "private",
                    }
                },
                "active_remote": "origin",
                "agents": ["claude-code"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text(
        "def existing_value():\n    return 'seed'\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed demo project")


def _trail_state(repo: Path) -> dict[str, Any]:
    from opentraces.capture.claude_code.hooks._trails import trail_state

    return trail_state(str(repo))


def _hook_payload(paths: DemoPaths, *, tool_response: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": SESSION_ID,
        "transcript_path": str(paths.transcript),
        "cwd": str(paths.repo),
        "tool_name": "Bash",
        "tool_use_id": TOOL_USE_ID,
        "tool_input": {
            "command": "python scripts/generate.py",
            "description": "Generate deterministic Trace Trails demo output",
        },
    }
    if tool_response is not None:
        payload["tool_response"] = tool_response
    return payload


def _invoke_hook(main_func: Any, payload: dict[str, Any]) -> None:
    old_stdin = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(payload))
        main_func()
    finally:
        sys.stdin = old_stdin


def _write_session_prefix(paths: DemoPaths) -> None:
    _write_jsonl_line(
        paths.transcript,
        {
            "type": "system",
            "subtype": "init",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "cwd": str(paths.repo),
            "model": "claude-sonnet-4-5",
            "claude_code_version": "trace-trails-demo",
            "tools": [
                {
                    "name": "Bash",
                    "description": "Run shell commands in the project.",
                }
            ],
            "permissionMode": "default",
            "gitBranch": "master",
        },
    )
    _write_jsonl_line(
        paths.transcript,
        {
            "type": "user",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": "user-trace-trails-demo-1",
            "message": {
                "role": "user",
                "content": "Generate a deterministic code artifact for the Trace Trails demo.",
            },
        },
    )
    _write_jsonl_line(
        paths.transcript,
        {
            "type": "assistant",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": "assistant-trace-trails-demo-1",
            "requestId": "req-trace-trails-demo-1",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": [
                    {
                        "type": "text",
                        "text": "I will generate the project artifact now.",
                    },
                    {
                        "type": "tool_use",
                        "id": TOOL_USE_ID,
                        "name": "Bash",
                        "input": {
                            "command": "python scripts/generate.py",
                            "description": "Generate deterministic Trace Trails demo output",
                        },
                    },
                ],
                "usage": {"input_tokens": 40, "output_tokens": 18},
            },
        },
    )


def _write_session_suffix(paths: DemoPaths) -> None:
    _write_jsonl_line(
        paths.transcript,
        {
            "type": "user",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": "user-trace-trails-demo-2",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": TOOL_USE_ID,
                        "content": "generated src/generated.py",
                    }
                ],
            },
            "toolUseResult": {
                "exitCode": 0,
                "durationMs": 12,
            },
        },
    )
    _write_jsonl_line(
        paths.transcript,
        {
            "type": "assistant",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "uuid": "assistant-trace-trails-demo-2",
            "requestId": "req-trace-trails-demo-2",
            "message": {
                "role": "assistant",
                "model": "claude-sonnet-4-5",
                "content": "Generated the artifact.",
                "usage": {"input_tokens": 18, "output_tokens": 8},
            },
        },
    )
    _write_jsonl_line(
        paths.transcript,
        {
            "type": "opentraces_hook",
            "event": "Stop",
            "timestamp": _now(),
            "data": {
                "session_id": SESSION_ID,
                "agent_type": "main",
                "permission_mode": "default",
                "stop_hook_active": True,
                "git": {
                    "sha": _git(paths.repo, "rev-parse", "HEAD"),
                    "dirty": bool(_git(paths.repo, "status", "--porcelain", check=False)),
                    "changed_paths": ["src/generated.py"],
                },
                "trail": _trail_state(paths.repo),
            },
        },
    )
    _write_jsonl_line(
        paths.transcript,
        {
            "type": "result",
            "subtype": "success",
            "sessionId": SESSION_ID,
            "timestamp": _now(),
            "duration_ms": 1234,
            "num_turns": 1,
            "stop_reason": "stop",
            "total_cost_usd": 0.0,
        },
    )


def _simulate_agent_step(paths: DemoPaths) -> None:
    from opentraces.capture.claude_code.hooks import on_pre_tool_use, on_tool_use

    _write_session_prefix(paths)
    _invoke_hook(on_pre_tool_use.main, _hook_payload(paths))

    generated = paths.repo / "src" / "generated.py"
    generated.write_text(
        "# generated by the Trace Trails full stack demo\n" + DISTINCTIVE_LINE,
        encoding="utf-8",
    )
    time.sleep(0.01)

    _invoke_hook(
        on_tool_use.main,
        _hook_payload(
            paths,
            tool_response={
                "stdout": "generated src/generated.py",
                "stderr": "",
                "exit_code": 0,
            },
        ),
    )
    _write_session_suffix(paths)


@contextlib.contextmanager
def _chdir(path: Path | None) -> Iterator[None]:
    if path is None:
        yield
        return
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _run_cli(args: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    from opentraces.cli import main

    cli_args = [str(arg) for arg in args]
    runner = CliRunner()
    with _chdir(cwd):
        result = runner.invoke(main, cli_args)
    if result.exit_code != 0:
        raise AssertionError(
            "opentraces "
            + " ".join(cli_args)
            + f" failed with exit {result.exit_code}:\n{result.output}"
        )
    output = result.output.strip()
    sentinel = "---OPENTRACES_JSON---"
    if sentinel in output:
        output = output.split(sentinel, 1)[1].strip()
    try:
        value, _ = json.JSONDecoder().raw_decode(output)
        return value
    except json.JSONDecodeError as exc:
        raise AssertionError(
            "opentraces " + " ".join(cli_args) + f" did not emit JSON:\n{result.output}"
        ) from exc


def _events_by_type(repo: Path) -> dict[str, list[Any]]:
    from opentraces.core.trails import read_events

    out: dict[str, list[Any]] = {}
    for event in read_events(repo):
        out.setdefault(event.event_type, []).append(event)
    return out


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _resource_ref(value: Any) -> str:
    if isinstance(value, dict) and isinstance(value.get("ref"), str):
        return value["ref"]
    if isinstance(value, str):
        return value
    raise AssertionError(f"expected resource ref string or object ref, got {value!r}")


def _create_paths(root: Path) -> DemoPaths:
    return DemoPaths(
        root=root,
        repo=root / "mock-project",
        home=root / "home",
        workspace=root / "exported.trace-workspace",
        opened_workspace=root / "opened-workspace",
        transcript=root
        / "mock-project"
        / ".claude_projects_fake"
        / f"{SESSION_ID}.jsonl",
    )


def run_demo(root: Path, *, verbose: bool = False) -> dict[str, Any]:
    paths = _create_paths(root.resolve())
    if paths.root.exists():
        shutil.rmtree(paths.root)
    paths.root.mkdir(parents=True)
    with _temporary_opentraces_home(paths.home):
        return _run_demo_with_isolated_home(paths, verbose=verbose)


def _run_demo_with_isolated_home(paths: DemoPaths, *, verbose: bool) -> dict[str, Any]:
    _init_demo_repo(paths.repo)
    _simulate_agent_step(paths)

    _git(paths.repo, "add", "src/generated.py")
    _git(paths.repo, "commit", "-q", "-m", "commit before Trace Trail ingest")
    commit_sha = _git(paths.repo, "rev-parse", "HEAD")

    from opentraces.capture.git.post_commit import run_for_repo

    run_for_repo(paths.repo)
    before_ingest_events = _events_by_type(paths.repo)
    _assert(
        not before_ingest_events.get("git_anchor_created"),
        "post-commit hook unexpectedly created a Git Anchor before Trace Patch ingest",
    )

    from opentraces.core.ingest import ingest_one_session

    ingest_result = ingest_one_session(paths.transcript, paths.repo)
    _assert(
        ingest_result.action in {"new", "refreshed", "new_generation"},
        f"unexpected ingest action: {ingest_result}",
    )
    _assert(ingest_result.trace_id is not None, "ingest did not produce a trace_id")
    trace_id = ingest_result.trace_id

    events_after_ingest = _events_by_type(paths.repo)
    observations = events_after_ingest.get("filesystem_mutation_observed", [])
    _assert(observations, "filesystem watcher emitted no mutation observations")
    _assert(
        any(event.payload.get("path") == "src/generated.py" for event in observations),
        "watcher did not observe src/generated.py",
    )

    attribution_events = events_after_ingest.get("watcher_observation_attributed", [])
    attributed = [
        event
        for event in attribution_events
        if event.payload.get("path") == "src/generated.py"
        and event.payload.get("result") == "attributed"
    ]
    _assert(attributed, "watcher observation was not attributed to a firm step window")

    trace_patches = [
        event
        for event in events_after_ingest.get("trace_patch_created", [])
        if event.trace_id == trace_id
        and event.payload.get("file_path") == "src/generated.py"
    ]
    _assert(trace_patches, "ingest did not create a Trace Patch for src/generated.py")
    watcher_patches = [
        event for event in trace_patches if "watcher_backstop" in event.capture_method
    ]
    _assert(
        watcher_patches,
        "Trace Patch was not corroborated or created by watcher_backstop",
    )
    trace_patch = max(watcher_patches, key=lambda event: event.event_sequence)
    trace_patch_id = trace_patch.payload["trace_patch_id"]
    step_index = trace_patch.step_index
    _assert(step_index is not None, "Trace Patch has no step_index")

    mature_payload = _run_cli(
        [
            "trail",
            "mature",
            "--commit",
            commit_sha,
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    _assert(mature_payload["commits_considered"] == 1, "mature considered wrong commit count")
    _assert(mature_payload["searches_completed"] >= 1, "mature recorded no searches")
    _assert(mature_payload["anchors_created"] >= 1, "mature created no Git Anchors")

    events_after_mature = _events_by_type(paths.repo)
    search_events = events_after_mature.get("git_anchor_search_completed", [])
    _assert(
        any(
            event.payload.get("trace_patch_id") == trace_patch_id
            and event.payload.get("result") == "anchored"
            for event in search_events
        ),
        "mature did not record an anchored search for the Trace Patch",
    )
    anchors = [
        event
        for event in events_after_mature.get("git_anchor_created", [])
        if event.payload.get("trace_patch_id") == trace_patch_id
        and (event.payload.get("commit_id") or {}).get("hex") == commit_sha
    ]
    _assert(anchors, "mature did not create the expected Git Anchor")
    _assert(
        len(anchors) == 1,
        "mature created duplicate Git Anchors for the same Trace Patch and commit",
    )
    anchor = max(anchors, key=lambda event: event.event_sequence)
    git_anchor_id = anchor.payload["git_anchor_id"]
    _assert(
        anchor.payload.get("evidence_tier") == "exact_range_hash",
        "Git Anchor was not an exact-range match",
    )

    explain_commit = _run_cli(
        [
            "trail",
            "explain",
            "--commit",
            commit_sha,
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    explain_step = _run_cli(
        [
            "trail",
            "explain",
            "--trace",
            trace_id,
            "--step",
            str(step_index),
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    explain_line = _run_cli(
        [
            "trail",
            "explain",
            "src/generated.py:2",
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    search_commit = _run_cli(
        [
            "trail",
            "search",
            "--commit",
            commit_sha,
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    search_trace = _run_cli(
        [
            "trail",
            "search",
            "--trace",
            trace_id,
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    search_path = _run_cli(
        [
            "trail",
            "search",
            "--path",
            "src/generated.py",
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    resolve_patch = _run_cli(
        [
            "trail",
            "resolve",
            _resource_ref(trace_patch.payload["trace_patch_ref"]),
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    blame_commit = _run_cli(
        ["trail", "blame", commit_sha, "--json", "--project", str(paths.repo)]
    )
    graph_commit = _run_cli(
        ["trail", "graph", "--json", "--project", str(paths.repo)]
    )
    play = _run_cli(
        ["trail", "timeline", trace_id, "--json", "--project", str(paths.repo)]
    )
    snapshots = _run_cli(
        [
            "trail",
            "snapshots",
            "--trace",
            trace_id,
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    _assert(snapshots["snapshot_count"] >= 1, "no trace snapshots were available")
    snapshot_ref = _resource_ref(snapshots["snapshots"][-1]["snapshot_ref"])
    checkout = _run_cli(
        [
            "trail",
            "snapshot",
            "checkout",
            snapshot_ref,
            "--dry-run",
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    export_payload = _run_cli(
        [
            "trail",
            "teleport",
            "export",
            trace_id,
            "--output",
            str(paths.workspace),
            "--json",
        ],
        cwd=paths.repo,
    )
    open_payload = _run_cli(
        [
            "trail",
            "teleport",
            "open",
            str(paths.workspace),
            "--project",
            str(paths.opened_workspace),
            "--json",
        ]
    )

    from opentraces.core.config import get_project_dir

    source_state_dir = get_project_dir(paths.repo)
    source_repo_unavailable = paths.root / "source-repo-unavailable"
    source_state_unavailable = source_state_dir.with_name(
        source_state_dir.name + "-unavailable"
    )
    paths.repo.rename(source_repo_unavailable)
    if source_state_dir.exists():
        source_state_dir.rename(source_state_unavailable)

    opened_play = _run_cli(
        [
            "trail",
            "timeline",
            trace_id,
            "--json",
            "--project",
            str(paths.opened_workspace),
        ]
    )
    opened_play_json = json.dumps(opened_play, sort_keys=True)

    _assert(explain_step["relation"] == "anchored_in_git", "step explanation is not anchored")
    _assert(explain_step["trace_patch_id"] == trace_patch_id, "step explanation lost patch identity")
    _assert(explain_step["git_anchor_id"] == git_anchor_id, "step explanation lost anchor identity")
    _assert(
        explain_line["trace_patch"]["trace_patch_id"] == trace_patch_id,
        "line explanation did not resolve the Trace Patch",
    )
    _assert(search_commit["result_count"] >= 1, "commit search found no anchors")
    _assert(search_trace["result_count"] >= 1, "trace search found no patches")
    _assert(search_path["result_count"] >= 1, "path search found no patches")
    _assert(
        resolve_patch["resource_type"] == "trace_patch_trail",
        "resolve did not return a Trace Patch trail",
    )
    _assert(blame_commit.get("trailEvidence"), "blame did not expose Trail evidence")
    _assert(
        any(
            trace.get("trace_id") == trace_id
            for commit in graph_commit.get("commits", [])
            for trace in commit.get("traces", [])
        ),
        "graph did not expose the Trace Trail-backed trace",
    )
    _assert(play["event_count"] >= 1, "trail timeline has no timeline events")
    _assert(checkout["relation"] == "snapshot_rewind", "snapshot checkout dry-run failed")
    _assert(export_payload["event_count"] >= 1, "workspace export carried no events")
    _assert(open_payload["status"] == "ok", "workspace open failed")
    _assert(
        opened_play["workspace_source"]["type"] == "trace_workspace",
        "opened workspace did not preserve trace workspace source metadata",
    )
    _assert(
        str(source_repo_unavailable) not in opened_play_json,
        "opened workspace play referenced the unavailable source repo",
    )
    _assert(
        str(source_state_unavailable) not in opened_play_json,
        "opened workspace play referenced the unavailable source trace store",
    )

    command_results = {
        "trail_mature": mature_payload,
        "trail_explain_commit": explain_commit,
        "trail_explain_step": explain_step,
        "trail_explain_line": explain_line,
        "trail_search_commit": search_commit,
        "trail_search_trace": search_trace,
        "trail_search_path": search_path,
        "trail_resolve_patch": resolve_patch,
        "blame_commit": blame_commit,
        "graph": graph_commit,
        "trail_play": play,
        "trail_snapshots": snapshots,
        "trail_snapshot_checkout": checkout,
        "trace_workspace_export": export_payload,
        "trace_workspace_open": open_payload,
        "opened_trail_play": opened_play,
    }

    summary: dict[str, Any] = {
        "status": "ok",
        "repo": str(paths.repo),
        "opentraces_home": str(paths.home / ".opentraces"),
        "transcript": str(paths.transcript),
        "workspace": str(paths.workspace),
        "opened_workspace": str(paths.opened_workspace),
        "source_repo_unavailable": str(source_repo_unavailable),
        "source_state_unavailable": str(source_state_unavailable),
        "commit_sha": commit_sha,
        "trace_id": trace_id,
        "step_index": step_index,
        "trace_patch_id": trace_patch_id,
        "git_anchor_id": git_anchor_id,
        "snapshot_ref": snapshot_ref,
        "post_commit_before_ingest_anchors": len(
            before_ingest_events.get("git_anchor_created", [])
        ),
        "filesystem_observation_event_id": next(
            event.event_id
            for event in observations
            if event.payload.get("path") == "src/generated.py"
        ),
        "watcher_attribution_event_id": attributed[-1].event_id,
        "event_counts": {
            key: len(value) for key, value in sorted(events_after_mature.items())
        },
    }
    if verbose:
        summary["commands"] = command_results
    return summary


def _runtime_transcript_path(paths: DemoPaths) -> Path:
    from opentraces.core.repo_identity import encode_claude_path

    return (
        paths.home
        / ".claude"
        / "projects"
        / encode_claude_path(paths.repo)
        / f"{SESSION_ID}.jsonl"
    )


def _read_last_hook_log(repo: Path) -> dict[str, Any]:
    hook_log = repo / ".git" / "opentraces-hook.log"
    if not hook_log.exists():
        raise AssertionError(f"missing post-commit hook log: {hook_log}")
    lines = [line for line in hook_log.read_text().splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"empty post-commit hook log: {hook_log}")
    return json.loads(lines[-1])


def run_installed_runtime_demo(root: Path, *, verbose: bool = False) -> dict[str, Any]:
    """Run the production-path Trace Trails UAT.

    This scenario uses command/install surfaces rather than direct core calls:

    * ``opentraces --json setup git`` installs the Git post-commit hook.
    * ``git commit`` executes that hook before any Trace Patch exists.
    * The installed Claude hook boundary observer records the concrete
      filesystem transition without direct attribution.
    * ``opentraces watcher tick --project ... --json`` performs session ingest,
      watcher reconciliation, and Trail maturation.

    The only white-box part is the synthetic Claude transcript itself. That is
    deliberate: this UAT is about the OpenTraces runtime surfaces, not launching
    a real Claude Code REPL.
    """
    paths = _create_paths(root.resolve())
    if paths.root.exists():
        shutil.rmtree(paths.root)
    paths.root.mkdir(parents=True)
    with _temporary_opentraces_home(paths.home):
        runtime_paths = replace(paths, transcript=_runtime_transcript_path(paths))
        return _run_installed_runtime_demo_with_isolated_home(
            runtime_paths,
            verbose=verbose,
        )


def _run_installed_runtime_demo_with_isolated_home(
    paths: DemoPaths,
    *,
    verbose: bool,
) -> dict[str, Any]:
    _init_demo_repo(paths.repo)

    watcher_status = _run_cli(["watcher", "status", "--json"])
    setup_git = _run_cli(["--json", "setup", "git"], cwd=paths.repo)
    _assert(setup_git["status"] == "ok", "setup git did not report ok")
    _assert(
        setup_git["state"]["installed"] is True,
        "setup git did not install the post-commit hook",
    )

    _simulate_agent_step(paths)

    _git(paths.repo, "add", "src/generated.py")
    _git(paths.repo, "commit", "-q", "-m", "installed runtime commit before ingest")
    commit_sha = _git(paths.repo, "rev-parse", "HEAD")
    hook_log = _read_last_hook_log(paths.repo)
    _assert(hook_log["sha"] == commit_sha, "post-commit hook log did not record HEAD")
    _assert(
        hook_log.get("trail_anchors_created") == 0,
        "post-commit hook created anchors before watcher/session ingest",
    )
    events_before_tick = _events_by_type(paths.repo)
    _assert(
        events_before_tick.get("filesystem_mutation_observed"),
        "hook boundary watcher emitted no pre-ingest filesystem observation",
    )
    _assert(
        not events_before_tick.get("watcher_observation_attributed"),
        "watcher observation was reconciled before watcher tick/session ingest",
    )
    _assert(
        not events_before_tick.get("git_anchor_created"),
        "Git Anchor existed before watcher tick/session ingest",
    )

    tick = _run_cli(
        ["watcher", "tick", "--project", str(paths.repo), "--json"],
    )
    _assert(isinstance(tick, list) and len(tick) == 1, "watcher tick did not return one report")
    tick_report = tick[0]
    _assert(tick_report["error"] is None, f"watcher tick failed: {tick_report['error']}")
    _assert(
        tick_report["fs_observations"] == 0,
        "watcher tick re-observed a mutation already captured at the tool boundary",
    )
    _assert(tick_report["sessions_created"] >= 1, "watcher tick did not ingest a session")
    _assert(
        tick_report["trail_maturation_searches"] >= 1,
        "watcher tick did not mature any Trace Patch searches",
    )
    _assert(
        tick_report["trail_maturation_anchors"] >= 1,
        "watcher tick did not create any Git Anchors",
    )

    from opentraces.core.trails import read_events

    events = read_events(paths.repo)
    observations = [
        event
        for event in events
        if event.event_type == "filesystem_mutation_observed"
        and event.payload.get("path") == "src/generated.py"
    ]
    _assert(observations, "no filesystem observation event for src/generated.py")
    _assert(
        any(event.writer == "hook-fs-watcher" for event in observations),
        "filesystem observation was not emitted by the hook boundary watcher",
    )
    attributed = [
        event
        for event in events
        if event.event_type == "watcher_observation_attributed"
        and event.payload.get("path") == "src/generated.py"
        and event.payload.get("result") == "attributed"
    ]
    _assert(attributed, "watcher observation was not attributed")
    watcher_patches = [
        event
        for event in events
        if event.event_type == "trace_patch_created"
        and event.payload.get("file_path") == "src/generated.py"
        and "watcher_backstop" in event.capture_method
    ]
    _assert(watcher_patches, "Trace Patch was not corroborated by watcher_backstop")
    anchors = [
        event
        for event in events
        if event.event_type == "git_anchor_created"
        and (event.payload.get("commit_id") or {}).get("hex") == commit_sha
    ]
    _assert(len(anchors) == 1, "watcher maturation did not create exactly one Git Anchor")
    anchor = anchors[0]
    trace_id = anchor.trace_id
    step_index = anchor.step_index
    trace_patch_id = anchor.payload["trace_patch_id"]
    git_anchor_id = anchor.payload["git_anchor_id"]
    _assert(trace_id is not None, "Git Anchor has no trace id")
    _assert(step_index is not None, "Git Anchor has no step index")

    explain_step = _run_cli(
        [
            "trail",
            "explain",
            "--trace",
            trace_id,
            "--step",
            str(step_index),
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    blame_commit = _run_cli(
        ["trail", "blame", commit_sha, "--json", "--project", str(paths.repo)]
    )
    graph_commit = _run_cli(["trail", "graph", "--json", "--project", str(paths.repo)])
    search_commit = _run_cli(
        [
            "trail",
            "search",
            "--commit",
            commit_sha,
            "--json",
            "--project",
            str(paths.repo),
        ]
    )
    _assert(explain_step["relation"] == "anchored_in_git", "step explanation is not anchored")
    _assert(blame_commit.get("trailEvidence"), "blame did not expose Trail evidence")
    _assert(search_commit["result_count"] >= 1, "trail search did not expose the anchor")
    _assert(
        any(
            trace.get("trace_id") == trace_id
            for commit in graph_commit.get("commits", [])
            for trace in commit.get("traces", [])
        ),
        "graph did not expose the Trace Trail-backed trace",
    )

    summary: dict[str, Any] = {
        "status": "ok",
        "scenario": "installed-runtime",
        "repo": str(paths.repo),
        "opentraces_home": str(paths.home / ".opentraces"),
        "transcript": str(paths.transcript),
        "commit_sha": commit_sha,
        "trace_id": trace_id,
        "step_index": step_index,
        "trace_patch_id": trace_patch_id,
        "git_anchor_id": git_anchor_id,
        "watcher_status": watcher_status,
        "setup_git_installed": setup_git["state"]["installed"],
        "post_commit_hook_log": hook_log,
        "watcher_tick": tick_report,
        "pre_tick_event_counts": {
            event_type: len(values)
            for event_type, values in sorted(events_before_tick.items())
        },
        "filesystem_observation_event_id": observations[-1].event_id,
        "watcher_attribution_event_id": attributed[-1].event_id,
        "event_counts": {
            event_type: len([event for event in events if event.event_type == event_type])
            for event_type in sorted({event.event_type for event in events})
        },
    }
    if verbose:
        summary["commands"] = {
            "trail_explain_step": explain_step,
            "blame_commit": blame_commit,
            "graph": graph_commit,
            "trail_search_commit": search_commit,
        }
    return summary


def _run_in_temp(*, keep: bool, verbose: bool, workdir: Path | None) -> dict[str, Any]:
    if workdir is not None:
        return run_demo(workdir, verbose=verbose)
    if keep:
        root = Path(tempfile.gettempdir()) / f"opentraces-trace-trails-demo-{int(time.time())}"
        return run_demo(root, verbose=verbose)
    with tempfile.TemporaryDirectory(prefix="opentraces-trace-trails-demo-") as tmp:
        return run_demo(Path(tmp), verbose=verbose)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep the disposable demo directory.")
    parser.add_argument("--verbose", action="store_true", help="Include raw command JSON outputs.")
    parser.add_argument(
        "--scenario",
        choices=["hook-boundary", "installed-runtime"],
        default="hook-boundary",
        help="Full-stack scenario to run.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Directory to use for the disposable demo. Removed and recreated.",
    )
    args = parser.parse_args(argv)
    if args.scenario == "installed-runtime":
        if args.workdir is not None:
            summary = run_installed_runtime_demo(args.workdir, verbose=args.verbose)
        elif args.keep:
            root = (
                Path(tempfile.gettempdir())
                / f"opentraces-trace-trails-installed-runtime-{int(time.time())}"
            )
            summary = run_installed_runtime_demo(root, verbose=args.verbose)
        else:
            with tempfile.TemporaryDirectory(
                prefix="opentraces-trace-trails-installed-runtime-"
            ) as tmp:
                summary = run_installed_runtime_demo(Path(tmp), verbose=args.verbose)
    else:
        summary = _run_in_temp(keep=args.keep, verbose=args.verbose, workdir=args.workdir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
