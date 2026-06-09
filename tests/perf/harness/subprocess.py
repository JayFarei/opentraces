from __future__ import annotations

from pathlib import Path

from .fixtures import build_fixture
from .measure import CommandPlan
from .models import PerfScenario


REPO_ROOT = Path(__file__).resolve().parents[3]
OTD = REPO_ROOT / "otd"


def build_command_plan(base_dir: Path, home_dir: Path, scenario: PerfScenario) -> CommandPlan:
    fixture = build_fixture(base_dir, home_dir, scenario.fixture, scenario.tier)
    env = {"HOME": str(home_dir), **_env_from_path()}
    metadata = {
        "scenario": scenario.name,
        "project_dir": str(fixture.project_dir),
        "trace_count": len(fixture.trace_ids),
        "commit_count": len(fixture.commit_shas),
    }
    if scenario.target == "cli.graph":
        return CommandPlan(
            cmd=[
                str(OTD),
                "trail",
                "graph",
                "--project",
                str(fixture.project_dir),
                "--limit",
                str(scenario.params.get("limit", 20)),
                "--no-color",
            ],
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    if scenario.target == "cli.blame":
        sha = fixture.blame_sha
        assert sha is not None
        metadata["sha"] = sha
        return CommandPlan(
            cmd=[
                str(OTD),
                "trail",
                "blame",
                "commit",
                sha,
                "--project",
                str(fixture.project_dir),
                "--no-color",
            ],
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    if scenario.target == "cli.status":
        return CommandPlan(
            cmd=[
                str(OTD),
                "status",
                "--limit",
                str(scenario.params.get("limit", 10)),
            ],
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    if scenario.target == "cli.trace_query":
        from opentraces.core.trace_search_snapshot import build_trace_search_snapshot

        summary = build_trace_search_snapshot()
        metadata["search_snapshot_trace_count"] = summary.trace_count
        metadata["search_snapshot_size_bytes"] = summary.path.stat().st_size
        return CommandPlan(
            cmd=[
                str(OTD),
                "trace",
                "query",
                "--lex",
                str(scenario.params.get("lex", "trace")),
                "--limit",
                str(scenario.params.get("limit", 20)),
                "--json",
            ],
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    if scenario.target == "cli.bucket_status":
        return CommandPlan(
            cmd=[str(OTD), "bucket", "status", "--json"],
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    raise ValueError(f"unsupported subprocess target {scenario.target!r}")


def _env_from_path() -> dict[str, str]:
    import os

    return {"PATH": os.environ.get("PATH", "")}
