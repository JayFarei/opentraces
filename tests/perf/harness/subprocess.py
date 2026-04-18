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
                "blame",
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
    if scenario.target == "cli.add_all":
        return CommandPlan(
            cmd=[str(OTD), "add", "--all"],
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    if scenario.target == "cli.assess_local":
        cmd = [str(OTD), "assess", "--dry-run"]
        limit = scenario.params.get("limit")
        if limit is not None:
            cmd.extend(["--limit", str(limit)])
        return CommandPlan(
            cmd=cmd,
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    if scenario.target == "cli.export_agent_trace":
        output_path = base_dir / "opentraces-export.jsonl"
        metadata["output_path"] = str(output_path)
        return CommandPlan(
            cmd=[
                str(OTD),
                "export",
                "--format",
                "agent-trace",
                "--output",
                str(output_path),
            ],
            cwd=fixture.project_dir,
            env=env,
            metadata=metadata,
        )
    raise ValueError(f"unsupported subprocess target {scenario.target!r}")


def _env_from_path() -> dict[str, str]:
    import os

    return {"PATH": os.environ.get("PATH", "")}
