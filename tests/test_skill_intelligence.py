from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from opentraces_schema import Agent, Step, ToolCall, TraceRecord


def _trace(
    trace_id: str,
    *,
    agent_name: str,
    skill_name: str,
    source: str,
    success: bool | None = False,
) -> TraceRecord:
    metadata = {
        "skill_invocations": [
            {
                "skill_name": skill_name,
                "name": skill_name,
                "source": source,
                "command_name": f"/{skill_name}",
                "args": "run the skill intelligence seed task",
                "step_index": 2,
            }
        ]
    }
    steps = [
        Step(step_index=1, role="user", content="Use a skill to update the project."),
    ]
    if source == "codex_cli_tool_call":
        steps.append(
            Step(
                step_index=2,
                role="agent",
                tool_calls=[
                    ToolCall(
                        tool_call_id=f"call-{trace_id}",
                        tool_name="skill_invoke",
                        input={"skill_name": skill_name},
                    )
                ],
            )
        )
        metadata["skill_invocations"][0]["tool_call_id"] = f"call-{trace_id}"
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name=agent_name),
        task={"description": "Use a skill to update the project."},
        steps=steps,
        metadata=metadata,
        outcome={"success": success, "committed": success is True},
    )


def _seed_skill_bucket() -> None:
    from opentraces.core.bucket_store import write_trace_record

    traces = [
        _trace(
            "trace-claude-opentraces",
            agent_name="claude-code",
            skill_name="opentraces",
            source="claude_slash_command",
            success=True,
        ),
        _trace(
            "trace-codex-opentraces",
            agent_name="codex-cli",
            skill_name="opentraces",
            source="codex_cli_tool_call",
            success=False,
        ),
        _trace(
            "trace-claude-handoff",
            agent_name="claude-code",
            skill_name="handoff",
            source="claude_slash_command",
            success=False,
        ),
    ]
    for record in traces:
        write_trace_record(
            record,
            project_slug="demo",
            source_layer="canonical",
            legacy_mirror=False,
        )


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_corpus_audit_aggregates_trace_index_skill_invocations() -> None:
    from opentraces.consumers.skill_intelligence import audit_skill_invocations

    _seed_skill_bucket()

    audit = audit_skill_invocations(project="demo", min_usable_episodes=2)

    assert audit["selected_skill"] == "opentraces"
    assert audit["seeded_ci_corpus_required"] is False
    opentraces_counts = audit["counts_by_skill"]["opentraces"]
    assert opentraces_counts["usable_episodes"] == 2
    assert opentraces_counts["agents"] == {"claude-code": 1, "codex-cli": 1}
    assert opentraces_counts["sources"] == {
        "claude_slash_command": 1,
        "codex_cli_tool_call": 1,
    }


def test_audit_and_episode_rows_prefer_bounded_snapshot_over_whole_corpus_scan(
    monkeypatch,
) -> None:
    # #208 perf-core round 2: a "known scope in hand" (project, and for
    # episode rows, the selected skill) must never fall through to a
    # whole-corpus TraceRecord scan while a schema-valid search snapshot
    # (even a dirty/stale one) can serve the same skill-invocation units
    # boundedly. Prove it by making the whole-corpus fallback explode if
    # called at all.
    from opentraces.consumers.skill_intelligence import audit_skill_invocations
    from opentraces.consumers.skill_intelligence.pipeline import build_episode_rows
    from opentraces.core import trace_index
    from opentraces.core import trace_search_snapshot as snapshot_module
    from opentraces.core.trace_search_snapshot import build_trace_search_snapshot
    from opentraces.core.trace_search_state import mark_search_snapshot_dirty

    _seed_skill_bucket()
    build_trace_search_snapshot()
    # Simulate an actively-capturing box: the persisted snapshot exists and
    # is schema-valid but dirty (a new trace landed since the last build).
    mark_search_snapshot_dirty("active-capture", trace_id="trace-claude-opentraces")

    def _explode(*_args, **_kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError(
            "whole-corpus record scan must not run while the snapshot can serve"
        )

    monkeypatch.setattr(
        trace_index, "list_skill_invocation_units_from_records", _explode
    )
    # Force the "too large to self-heal rebuild inline" branch regardless of
    # this test's tiny corpus, so the assertions exercise the same
    # cold_build_too_large / stale-serve routing a mature bucket hits.
    monkeypatch.setattr(snapshot_module, "_cold_build_exceeds", lambda _path: True)

    audit = audit_skill_invocations(project="demo", min_usable_episodes=2)
    assert audit["selected_skill"] == "opentraces"
    assert audit["counts_by_skill"]["opentraces"]["usable_episodes"] == 2

    rows = build_episode_rows(selected_skill="opentraces", project="demo", min_rows=0)
    assert {row["source_trace_id"] for row in rows} == {
        "trace-claude-opentraces",
        "trace-codex-opentraces",
    }


def test_skill_intelligence_workflow_chain_and_schemas(tmp_path) -> None:
    from opentraces.consumers.skill_intelligence.pipeline import _initial_skill_for
    from opentraces.core.datasets import validate_row
    from opentraces.core.workflow_runner import execute_workflow
    from opentraces.core.workflows import create_workflow, list_workflow_templates

    _seed_skill_bucket()
    for name in (
        "skill-episodes-v1",
        "skill-eval-tasks-v1",
        "skill-rollouts-v1",
        "skill-update-report-v1",
    ):
        assert name in list_workflow_templates()
        create_workflow(name, template=name, replace=True)

    episodes = tmp_path / "episodes.jsonl"
    result = execute_workflow(
        "skill-episodes-v1",
        scope={"selected_skill": "opentraces", "project_slug": "demo", "min_rows": 6},
        output_path=episodes,
    )
    assert result.row_count == 6
    episode_rows = _load_jsonl(episodes)
    assert {
        row["schema_version"] for row in episode_rows
    } == {"opentraces.skill_episodes.v1"}

    eval_tasks = tmp_path / "eval-tasks.jsonl"
    result = execute_workflow(
        "skill-eval-tasks-v1",
        scope={"episodes_path": str(episodes), "seed": "test-seed"},
        output_path=eval_tasks,
    )
    assert result.row_count == 6
    task_rows = _load_jsonl(eval_tasks)
    assert {
        row["schema_version"] for row in task_rows
    } == {"opentraces.skill_eval_tasks.v1"}
    assert {row["split"] for row in task_rows} == {"Dtrain", "Dsel", "Dtest"}
    assert all(row["verifier"] and row["reward_spec_json"] for row in task_rows)
    split_by_leakage_key = {}
    for row in task_rows:
        previous = split_by_leakage_key.setdefault(row["leakage_key"], row["split"])
        assert previous == row["split"]

    skill_path = tmp_path / "baseline.md"
    skill_path.write_text(_initial_skill_for("opentraces"), encoding="utf-8")
    rollouts = tmp_path / "rollouts.jsonl"
    result = execute_workflow(
        "skill-rollouts-v1",
        scope={"eval_tasks_path": str(eval_tasks), "skill_path": str(skill_path)},
        output_path=rollouts,
    )
    assert result.row_count == 6
    rollout_rows = _load_jsonl(rollouts)
    assert {
        row["schema_version"] for row in rollout_rows
    } == {"opentraces.skill_rollouts.v1"}
    assert all(row["harness"] == "synthetic-otbox" for row in rollout_rows)
    assert all((rollouts.parent / row["transcript_ref"]).exists() for row in rollout_rows)

    for workflow_name, rows in (
        ("skill-episodes-v1", episode_rows),
        ("skill-eval-tasks-v1", task_rows),
        ("skill-rollouts-v1", rollout_rows),
    ):
        schema = json.loads(
            (
                Path("src/opentraces/workflow_templates")
                / workflow_name
                / "schemas"
                / "row.schema.json"
            ).read_text(encoding="utf-8")
        )
        assert validate_row(rows[0], schema) == []


def test_dataset_new_from_skill_runs_skill_episode_workflow() -> None:
    from opentraces.cli import main
    from opentraces.core.datasets import read_source_provenance

    _seed_skill_bucket()
    runner = CliRunner()

    created = runner.invoke(
        main,
        ["dataset", "new", "opentraces-episodes", "--from-skill", "opentraces", "--json"],
    )

    assert created.exit_code == 0, created.output
    created_payload = json.loads(created.output)
    manifest = created_payload["dataset"]["manifest"]
    assert manifest["workflow"]["skill"] == "skill-episodes-v1"
    assert manifest["candidate_query"]["args"]["skill"] == "opentraces"
    assert manifest["candidate_query"]["args"]["candidate_kind"] == "skill_invocation"
    schema = json.loads(
        (
            Path(created_payload["dataset"]["path"])
            / manifest["schema"]["path"]
        ).read_text(encoding="utf-8")
    )
    assert "episode_id" in schema["required"]
    assert created_payload["next_command"] == (
        "opentraces dataset run opentraces-episodes --executor script --json"
    )
    assert created_payload["telemetry"]["duration_ms"] >= 0
    provenance = read_source_provenance(Path(created_payload["dataset"]["path"]))
    assert provenance is not None
    assert provenance["schema_version"] == "opentraces.dataset.source_provenance.v1"
    assert provenance["bucket_snapshot"]["capture_mode"] == "deferred"
    assert provenance["bucket_manifest"]["capture_mode"] == "deferred"
    assert provenance["query_fingerprint"]

    run = runner.invoke(
        main,
        ["dataset", "run", "opentraces-episodes", "--executor", "script", "--json"],
    )

    assert run.exit_code == 0, run.output
    run_payload = json.loads(run.output)
    assert run_payload["status"] == "ok"
    assert run_payload["run"]["executor"] == "script"
    assert run_payload["run"]["emitted_count"] == 2
    assert run_payload["run"]["appended_count"] == 2
    assert run_payload["telemetry"]["duration_ms"] >= 0

    status = runner.invoke(main, ["dataset", "status", "opentraces-episodes", "--json"])
    assert status.exit_code == 0, status.output
    assert json.loads(status.output)["row_count"] == 2


def test_workflow_skill_intelligence_dry_run_writes_report_and_case_study(tmp_path) -> None:
    from opentraces.cli import main
    from opentraces.core.datasets import validate_row

    _seed_skill_bucket()
    out_dir = tmp_path / "skill-intelligence"
    result = CliRunner().invoke(
        main,
        [
            "workflow",
            "skill-intelligence",
            "--dry-run",
            "--project",
            "demo",
            "--min-episodes",
            "6",
            "--out",
            str(out_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["selected_skill"] == "opentraces"
    assert payload["report_path"].endswith("skill-update-report-v1.json")
    assert payload["dtest_score"] == 1.0
    assert payload["accepted"] is True

    report = json.loads(Path(payload["report_path"]).read_text(encoding="utf-8"))
    report_schema = json.loads(
        (
            Path("src/opentraces/workflow_templates")
            / "skill-update-report-v1"
            / "schemas"
            / "row.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert validate_row(report, report_schema) == []
    assert report["schema_version"] == "opentraces.skill_update_report.v1"
    assert report["baseline_digest"] != report["candidate_digest"]
    assert report["dsel_after"] > report["dsel_before"]
    assert report["dtest_score"] == 1.0
    assert report["approval_state"] == "manual_required_default_off"
    assert report["automatic_promotion"] is False
    case_study_dir = Path(payload["case_study_dir"])
    readme = (case_study_dir / "README.md").read_text(encoding="utf-8")
    markdown_report = (
        case_study_dir / "skill-update-report-v1.md"
    ).read_text(encoding="utf-8")
    assert "Rollout refs:" in readme
    assert "transcripts/rollout-" in readme
    assert "Promotion state: `default_off`" in readme
    assert "Automatic promotion: false" in readme
    assert "Baseline rollouts:" in markdown_report
    assert "Candidate rollouts:" in markdown_report
    assert (out_dir / "corpus-audit.json").exists()
