from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentraces.core.cache import AttributionCache
from opentraces.core.config import (
    get_project_state_path,
    get_project_traces_dir,
    save_project_config,
)
from opentraces.core.state import StateManager, TraceStatus


TIER_CONFIG = {
    "small": {
        "trace_count": 24,
        "history_commits": 10,
        "traces_per_commit": 2,
        "steps_per_trace": 8,
        "watcher_sessions": 4,
        "committed_count": 6,
    },
    "medium": {
        "trace_count": 72,
        "history_commits": 24,
        "traces_per_commit": 4,
        "steps_per_trace": 10,
        "watcher_sessions": 12,
        "committed_count": 18,
    },
    "large": {
        "trace_count": 144,
        "history_commits": 48,
        "traces_per_commit": 6,
        "steps_per_trace": 12,
        "watcher_sessions": 24,
        "committed_count": 36,
    },
}


@dataclass(frozen=True)
class PerfFixture:
    home_dir: Path
    project_dir: Path
    staging_dir: Path
    state_path: Path
    trace_ids: list[str]
    commit_shas: list[str]
    head_sha: str | None
    blame_sha: str | None
    primary_trace_id: str | None


def build_fixture(base_dir: Path, home_dir: Path, fixture_name: str, tier: str) -> PerfFixture:
    _configure_opentraces_home(home_dir)
    if fixture_name == "stack":
        return build_stack_fixture(base_dir, home_dir, tier)
    if fixture_name == "watcher_quiet":
        return build_watcher_fixture(base_dir, home_dir, tier, active=False)
    if fixture_name == "watcher_active":
        return build_watcher_fixture(base_dir, home_dir, tier, active=True)
    if fixture_name == "scan_project":
        return build_scan_fixture(base_dir, home_dir, tier)
    if fixture_name == "push_batch":
        return build_push_fixture(base_dir, home_dir, tier)
    raise ValueError(f"unknown fixture {fixture_name!r}")


def build_stack_fixture(base_dir: Path, home_dir: Path, tier: str) -> PerfFixture:
    config = TIER_CONFIG[tier]
    project_dir = base_dir / "proj"
    _init_project(project_dir)
    _init_git_repo(project_dir)
    staging_dir = get_project_traces_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    state_path = get_project_state_path(project_dir)
    state = StateManager(state_path=state_path)

    trace_ids: list[str] = []
    for idx in range(config["trace_count"]):
        trace_id = f"{tier}-trace-{idx:04d}"
        session_id = f"{tier}-sess-{idx:04d}"
        trace = _make_trace(
            trace_id=trace_id,
            session_id=session_id,
            description=f"{tier} trace {idx}",
            step_count=config["steps_per_trace"],
        )
        (staging_dir / f"{trace_id}.jsonl").write_text(json.dumps(trace) + "\n")
        trace_ids.append(trace_id)
        status = TraceStatus.PARSED
        extra: dict[str, Any] = {
            "session_id": session_id,
            "file_path": str(staging_dir / f"{trace_id}.jsonl"),
        }
        if idx < config["committed_count"]:
            status = TraceStatus.COMMITTED
        elif idx % 13 == 0:
            status = TraceStatus.BLOCKED
            extra["block_reason"] = "Blocked by perf fixture"
        elif idx % 9 == 0:
            status = TraceStatus.UPLOADED
            extra["uploaded_to"] = {"origin": "fixture"}
        state.set_trace_status(trace_id, status, **extra)

    commit_shas: list[str] = []
    cache = AttributionCache(project_dir)
    file_buckets = max(3, config["traces_per_commit"])
    for commit_idx in range(config["history_commits"]):
        rel = f"pkg/module_{commit_idx % file_buckets}.py"
        path = project_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        before_lines = _line_count(path)
        added = []
        commit_trace_ids = []
        for trace_offset in range(config["traces_per_commit"]):
            tid = trace_ids[(commit_idx * config["traces_per_commit"] + trace_offset) % len(trace_ids)]
            commit_trace_ids.append(tid)
            added.append(f"value_{commit_idx}_{trace_offset} = {commit_idx + trace_offset}\n")
        with path.open("a") as handle:
            handle.writelines(added)
        _run(["git", "add", rel], cwd=project_dir)
        _run(
            ["git", "-c", "user.email=perf@opentraces.ai", "-c", "user.name=Perf", "commit", "-q", "-m", f"perf {commit_idx:03d}"],
            cwd=project_dir,
        )
        sha = _git_output(["git", "rev-parse", "HEAD"], cwd=project_dir)
        commit_shas.append(sha)

        total_after = _line_count(path)
        first_line = before_lines + 1
        lines = []
        traces = []
        entity_rows = []
        for line_offset, tid in enumerate(commit_trace_ids):
            line_no = first_line + line_offset
            lines.append({"n": line_no, "trace_id": tid, "consistency": "attributed"})
            traces.append(
                {
                    "trace_id": tid,
                    "line_count": 1,
                    "files": [rel],
                    "lifecycle": "final" if commit_idx % 2 else "provisional",
                }
            )
            entity_rows.append(
                {
                    "change_type": "added",
                    "entity_type": "chunk",
                    "entity_name": f"lines {line_no}-{line_no}",
                    "file_path": rel,
                }
            )
        cache.write_attribution(
            sha,
            {
                "commit_sha": sha,
                "project_slug": project_dir.name,
                "generated_at": "2026-04-18T00:00:00Z",
                "traces": traces,
                "files": {
                    rel: {
                        "lines": lines,
                        "total": total_after,
                        "status": "attributed",
                    }
                },
                "coverage": {
                    "attributed": len(lines),
                    "total": total_after,
                    "ratio": len(lines) / max(total_after, 1),
                },
            },
        )
        cache.entity_path(sha).parent.mkdir(parents=True, exist_ok=True)
        cache.entity_path(sha).write_text(json.dumps({"entities": entity_rows}, indent=2))

    head_sha = commit_shas[-1] if commit_shas else None
    blame_sha = commit_shas[len(commit_shas) // 2] if commit_shas else None
    primary_trace_id = trace_ids[0] if trace_ids else None
    return PerfFixture(
        home_dir=home_dir,
        project_dir=project_dir,
        staging_dir=staging_dir,
        state_path=state_path,
        trace_ids=trace_ids,
        commit_shas=commit_shas,
        head_sha=head_sha,
        blame_sha=blame_sha,
        primary_trace_id=primary_trace_id,
    )


def build_push_fixture(base_dir: Path, home_dir: Path, tier: str) -> PerfFixture:
    fixture = build_stack_fixture(base_dir, home_dir, tier)
    state = StateManager(state_path=fixture.state_path)
    for trace_id in fixture.trace_ids[: TIER_CONFIG[tier]["committed_count"]]:
        state.set_trace_status(
            trace_id,
            TraceStatus.COMMITTED,
            session_id=f"{tier}-push-{trace_id}",
            file_path=str(fixture.staging_dir / f"{trace_id}.jsonl"),
        )
    return fixture


def build_scan_fixture(base_dir: Path, home_dir: Path, tier: str) -> PerfFixture:
    config = TIER_CONFIG[tier]
    project_dir = base_dir / "proj"
    _init_project(project_dir)
    _init_git_repo(project_dir)
    state_path = get_project_state_path(project_dir)
    staging_dir = get_project_traces_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    session_ids: list[str] = []
    corpus_dir = _claude_corpus_dir(home_dir, project_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for idx in range(config["watcher_sessions"]):
        session_id = f"{tier}-scan-{idx:03d}"
        session_path = corpus_dir / f"{session_id}.jsonl"
        _write_session(session_path, session_id, turns=max(3, config["steps_per_trace"] // 2))
        session_ids.append(session_id)
    return PerfFixture(
        home_dir=home_dir,
        project_dir=project_dir,
        staging_dir=staging_dir,
        state_path=state_path,
        trace_ids=session_ids,
        commit_shas=[],
        head_sha=None,
        blame_sha=None,
        primary_trace_id=None,
    )


def build_watcher_fixture(base_dir: Path, home_dir: Path, tier: str, *, active: bool) -> PerfFixture:
    project_dir = base_dir / "proj"
    _init_project(project_dir)
    _init_git_repo(project_dir)
    state_path = get_project_state_path(project_dir)
    staging_dir = get_project_traces_dir(project_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    track_file = project_dir / "tracked.py"
    track_file.write_text("print('baseline')\n")
    _run(["git", "add", "tracked.py"], cwd=project_dir)
    _run(
        ["git", "-c", "user.email=perf@opentraces.ai", "-c", "user.name=Perf", "commit", "-q", "-m", "tracked baseline"],
        cwd=project_dir,
    )
    from opentraces.watcher import daemon as _daemon

    if active:
        track_file.write_text("print('baseline')\nprint('active')\n")
        _run(["git", "add", "tracked.py"], cwd=project_dir)
        _run(
            ["git", "-c", "user.email=perf@opentraces.ai", "-c", "user.name=Perf", "commit", "-q", "-m", "active change"],
            cwd=project_dir,
        )
        corpus_dir = _claude_corpus_dir(home_dir, project_dir)
        corpus_dir.mkdir(parents=True, exist_ok=True)
        session_id = f"{tier}-watcher-active-001"
        _write_session(corpus_dir / f"{session_id}.jsonl", session_id, turns=4)
    else:
        first = _daemon.run_once(project_dir)
        assert first.error is None
    head_sha = _git_output(["git", "rev-parse", "HEAD"], cwd=project_dir)
    return PerfFixture(
        home_dir=home_dir,
        project_dir=project_dir,
        staging_dir=staging_dir,
        state_path=state_path,
        trace_ids=[],
        commit_shas=[head_sha],
        head_sha=head_sha,
        blame_sha=head_sha,
        primary_trace_id=None,
    )


def _init_project(project_dir: Path) -> None:
    project_dir.mkdir(parents=True, exist_ok=True)
    save_project_config(
        project_dir,
        {
            "review_policy": "review",
            "push_policy": "manual",
            "remotes": {"origin": {"url": "hf://alice/opentraces", "visibility": "private"}},
            "active_remote": "origin",
            "agents": ["claude-code"],
        },
    )


def _init_git_repo(project_dir: Path) -> None:
    _run(["git", "init", "-q", "-b", "main"], cwd=project_dir)
    _run(["git", "config", "user.email", "perf@opentraces.ai"], cwd=project_dir)
    _run(["git", "config", "user.name", "Perf"], cwd=project_dir)
    (project_dir / "README.md").write_text("# perf\n")
    _run(["git", "add", "."], cwd=project_dir)
    _run(["git", "commit", "-q", "-m", "init"], cwd=project_dir)


def _make_trace(trace_id: str, session_id: str, description: str, step_count: int) -> dict[str, Any]:
    steps = []
    for idx in range(step_count):
        ts = f"2026-04-18T10:{idx // 60:02d}:{idx % 60:02d}Z"
        if idx % 2 == 0:
            steps.append(
                {
                    "step_index": idx + 1,
                    "role": "user",
                    "content": f"user step {idx} for {description}",
                    "timestamp": ts,
                }
            )
            continue
        tool_call_id = f"{trace_id}-call-{idx:04d}"
        steps.append(
            {
                "step_index": idx + 1,
                "role": "agent",
                "content": f"agent step {idx} for {description}",
                "timestamp": ts,
                "tool_calls": [
                    {
                        "tool_call_id": tool_call_id,
                        "tool_name": "Read" if idx % 4 else "Edit",
                        "input": {"path": f"pkg/file_{idx % 5}.py"},
                    }
                ],
                "observations": [
                    {
                        "source_call_id": tool_call_id,
                        "content": f"observation {idx}",
                        "output_summary": "ok",
                    }
                ],
            }
        )
    return {
        "schema_version": "0.5.0",
        "trace_id": trace_id,
        "session_id": session_id,
        "timestamp_start": "2026-04-18T10:00:00Z",
        "timestamp_end": "2026-04-18T10:05:00Z",
        "agent": {"name": "claude-code", "model": "anthropic/claude-opus-4-6"},
        "task": {"description": description},
        "metrics": {
            "total_steps": len(steps),
            "total_input_tokens": len(steps) * 120,
            "total_output_tokens": len(steps) * 60,
            "total_cache_read_tokens": len(steps) * 180,
            "estimated_cost_usd": round(len(steps) * 0.003, 4),
        },
        "steps": steps,
    }


def _write_session(path: Path, session_id: str, turns: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for idx in range(1, turns + 1):
            ts = f"2026-04-18T07:00:{idx:02d}Z"
            user = {
                "type": "user",
                "sessionId": session_id,
                "timestamp": ts,
                "message": {"role": "user", "content": f"prompt {idx}"},
            }
            assistant = {
                "type": "assistant",
                "sessionId": session_id,
                "timestamp": ts,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"tu_{idx}",
                            "name": "Read",
                            "input": {"file_path": "tracked.py"},
                        }
                    ],
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                },
            }
            tool_result = {
                "type": "user",
                "sessionId": session_id,
                "timestamp": ts,
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": f"tu_{idx}", "content": "ok"}
                    ],
                },
            }
            for row in (user, assistant, tool_result):
                handle.write(json.dumps(row) + "\n")


def _claude_corpus_dir(home_dir: Path, project_dir: Path) -> Path:
    encoded = "".join(c if c.isalnum() or c == "-" else "-" for c in str(project_dir.resolve()))
    return home_dir / ".claude" / "projects" / encoded


def _configure_opentraces_home(home_dir: Path) -> None:
    home_dir = home_dir.resolve()
    os.environ["HOME"] = str(home_dir)
    opentraces_dir = home_dir / ".opentraces"
    projects_dir = opentraces_dir / "projects"
    opentraces_dir.mkdir(parents=True, exist_ok=True)
    projects_dir.mkdir(parents=True, exist_ok=True)

    from opentraces.core import config as config_mod
    from opentraces.core import paths as paths_mod

    config_path = opentraces_dir / "config.json"
    credentials_path = opentraces_dir / "credentials"

    paths_mod.OPENTRACES_DIR = opentraces_dir
    paths_mod.CONFIG_PATH = config_path
    paths_mod.CREDENTIALS_PATH = credentials_path
    paths_mod.PROJECTS_DIR = projects_dir

    config_mod.OPENTRACES_DIR = opentraces_dir
    config_mod.CONFIG_PATH = config_path
    config_mod.CREDENTIALS_PATH = credentials_path
    config_mod.PROJECTS_DIR = projects_dir

    try:
        from opentraces.watcher import daemon as daemon_mod
    except Exception:
        daemon_mod = None
    if daemon_mod is not None:
        daemon_mod.OPENTRACES_DIR = opentraces_dir
        daemon_mod.PROJECTS_DIR = projects_dir


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return len(path.read_text().splitlines())


def _run(cmd: list[str], *, cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _git_output(cmd: list[str], *, cwd: Path) -> str:
    return subprocess.check_output(cmd, cwd=str(cwd), text=True).strip()
