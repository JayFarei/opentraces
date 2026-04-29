"""Deterministic Trace Trails portrayal/UAT packet coverage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.integration.harness.trace_trails_full_stack_demo import (
    run_demo,
    run_installed_runtime_demo,
)


REPORT_PATH = (
    Path(__file__).parent
    / "trail_scenarios"
    / "reports"
    / "trace_trails_portrayal_uat.md"
)

PACKET_KEYS = {
    "packet_version",
    "purpose",
    "fixture_mode",
    "live_claude_required",
    "scenarios",
}
SCENARIO_KEYS = {
    "id",
    "title",
    "source_helper",
    "acceptance_target",
    "identities",
    "review_commands",
    "judgement_points",
}
IDENTITY_KEYS = {
    "commit_sha",
    "trace_id",
    "step_index",
    "trace_patch_id",
    "git_anchor_id",
}
COMMAND_KEYS = {"label", "command", "payload_key"}
JUDGEMENT_KEYS = {"id", "question", "judgement", "evidence"}


def _point(
    point_id: str,
    question: str,
    passed: bool,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": point_id,
        "question": question,
        "judgement": "pass" if passed else "fail",
        "evidence": evidence,
    }


def _identity(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "commit_sha": summary["commit_sha"],
        "trace_id": summary["trace_id"],
        "step_index": summary["step_index"],
        "trace_patch_id": summary["trace_patch_id"],
        "git_anchor_id": summary["git_anchor_id"],
    }


def _command(label: str, command: str, payload_key: str) -> dict[str, str]:
    return {
        "label": label,
        "command": command,
        "payload_key": payload_key,
    }


def _commands(summary: dict[str, Any]) -> dict[str, Any]:
    commands = summary.get("commands")
    if not isinstance(commands, dict):
        raise AssertionError("demo summary did not include verbose command payloads")
    return commands


def _graph_has_trace(graph_payload: dict[str, Any], trace_id: str) -> bool:
    return any(
        trace.get("trace_id") == trace_id
        for commit in graph_payload.get("commits", [])
        for trace in commit.get("traces", [])
    )


def _full_stack_review_surfaces_pass(
    summary: dict[str, Any],
    commands: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    explain_step = commands["trail_explain_step"]
    evidence = {
        "explain_relation": explain_step.get("relation"),
        "explain_trace_patch_matches": explain_step.get("trace_patch_id")
        == summary["trace_patch_id"],
        "explain_git_anchor_matches": explain_step.get("git_anchor_id")
        == summary["git_anchor_id"],
        "blame_has_trail_evidence": bool(commands["blame_commit"].get("trailEvidence")),
        "graph_has_trace": _graph_has_trace(commands["graph"], summary["trace_id"]),
        "search_commit_result_count": commands["trail_search_commit"].get(
            "result_count"
        ),
        "trail_play_event_count": commands["trail_play"].get("event_count"),
        "opened_trail_play_event_count": commands["opened_trail_play"].get(
            "event_count"
        ),
    }
    passed = (
        evidence["explain_relation"] == "anchored_in_git"
        and evidence["explain_trace_patch_matches"]
        and evidence["explain_git_anchor_matches"]
        and evidence["blame_has_trail_evidence"]
        and evidence["graph_has_trace"]
        and evidence["search_commit_result_count"] >= 1
        and evidence["trail_play_event_count"] >= 1
        and evidence["opened_trail_play_event_count"] >= 1
    )
    return passed, evidence


def _watcher_review_surfaces_pass(
    summary: dict[str, Any],
    commands: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    explain_step = commands["trail_explain_step"]
    evidence = {
        "explain_relation": explain_step.get("relation"),
        "blame_has_trail_evidence": bool(commands["blame_commit"].get("trailEvidence")),
        "graph_has_trace": _graph_has_trace(commands["graph"], summary["trace_id"]),
        "search_commit_result_count": commands["trail_search_commit"].get(
            "result_count"
        ),
    }
    passed = (
        evidence["explain_relation"] == "anchored_in_git"
        and evidence["blame_has_trail_evidence"]
        and evidence["graph_has_trace"]
        and evidence["search_commit_result_count"] >= 1
    )
    return passed, evidence


def _full_stack_scenario(summary: dict[str, Any]) -> dict[str, Any]:
    commands = _commands(summary)
    review_passed, review_evidence = _full_stack_review_surfaces_pass(
        summary,
        commands,
    )
    event_counts = summary["event_counts"]
    return {
        "id": "hook_boundary_full_stack_demo",
        "title": "Hook-boundary full-stack demo",
        "source_helper": "run_demo(verbose=True)",
        "acceptance_target": (
            "A synthetic Claude hook boundary observes a mutation, ingest "
            "turns it into Trace Patch evidence, and reviewer surfaces agree "
            "after maturation."
        ),
        "identities": _identity(summary),
        "review_commands": [
            _command(
                "Explain commit",
                "opentraces trail explain --commit <commit_sha> --project <scratch_repo> --json",
                "commands.trail_explain_commit",
            ),
            _command(
                "Explain step",
                "opentraces trail explain --trace <trace_id> --step <step_index> --project <scratch_repo> --json",
                "commands.trail_explain_step",
            ),
            _command(
                "Blame commit",
                "opentraces trail blame <commit_sha> --project <scratch_repo> --json",
                "commands.blame_commit",
            ),
            _command(
                "Graph",
                "opentraces trail graph --project <scratch_repo> --json",
                "commands.graph",
            ),
            _command(
                "Search commit",
                "opentraces trail search --commit <commit_sha> --project <scratch_repo> --json",
                "commands.trail_search_commit",
            ),
            _command(
                "Play trace",
                "opentraces trail timeline <trace_id> --project <opened_workspace> --json",
                "commands.opened_trail_play",
            ),
        ],
        "judgement_points": [
            _point(
                "post_commit_before_ingest_is_unanchored",
                "Does the post-commit path avoid inventing anchors before session ingest?",
                summary["post_commit_before_ingest_anchors"] == 0,
                {
                    "post_commit_before_ingest_anchors": summary[
                        "post_commit_before_ingest_anchors"
                    ],
                },
            ),
            _point(
                "hook_boundary_observes_mutation",
                "Did the hook-boundary watcher observe the generated file change?",
                event_counts.get("filesystem_mutation_observed", 0) >= 1,
                {
                    "filesystem_mutation_observed": event_counts.get(
                        "filesystem_mutation_observed",
                        0,
                    ),
                    "filesystem_observation_event_id": summary[
                        "filesystem_observation_event_id"
                    ],
                },
            ),
            _point(
                "watcher_backstop_attributes_step",
                "Was the observation attributed to the firm step window?",
                event_counts.get("watcher_observation_attributed", 0) >= 1,
                {
                    "watcher_observation_attributed": event_counts.get(
                        "watcher_observation_attributed",
                        0,
                    ),
                    "watcher_attribution_event_id": summary[
                        "watcher_attribution_event_id"
                    ],
                },
            ),
            _point(
                "mature_creates_exact_anchor",
                "Did maturation produce exactly one reviewer-grade Git Anchor?",
                event_counts.get("git_anchor_created", 0) == 1,
                {
                    "git_anchor_created": event_counts.get("git_anchor_created", 0),
                    "trace_patch_id": summary["trace_patch_id"],
                    "git_anchor_id": summary["git_anchor_id"],
                },
            ),
            _point(
                "review_surfaces_are_coherent",
                "Do explain, blame, graph, search, and play expose the same trail?",
                review_passed,
                review_evidence,
            ),
        ],
    }


def _installed_runtime_scenario(summary: dict[str, Any]) -> dict[str, Any]:
    commands = _commands(summary)
    review_passed, review_evidence = _watcher_review_surfaces_pass(summary, commands)
    pre_tick_counts = summary["pre_tick_event_counts"]
    tick = summary["watcher_tick"]
    event_counts = summary["event_counts"]
    return {
        "id": "installed_runtime_watcher_tick",
        "title": "Installed-runtime watcher tick",
        "source_helper": "run_installed_runtime_demo(verbose=True)",
        "acceptance_target": (
            "Installed setup/git and watcher tick surfaces reconcile the "
            "synthetic session without launching live Claude."
        ),
        "identities": _identity(summary),
        "review_commands": [
            _command(
                "Setup Git hook",
                "opentraces --json setup git",
                "setup_git_installed",
            ),
            _command(
                "Watcher tick",
                "opentraces watcher tick --project <scratch_repo> --json",
                "watcher_tick",
            ),
            _command(
                "Explain step",
                "opentraces trail explain --trace <trace_id> --step <step_index> --project <scratch_repo> --json",
                "commands.trail_explain_step",
            ),
            _command(
                "Blame commit",
                "opentraces trail blame <commit_sha> --project <scratch_repo> --json",
                "commands.blame_commit",
            ),
            _command(
                "Graph",
                "opentraces trail graph --project <scratch_repo> --json",
                "commands.graph",
            ),
            _command(
                "Search commit",
                "opentraces trail search --commit <commit_sha> --project <scratch_repo> --json",
                "commands.trail_search_commit",
            ),
        ],
        "judgement_points": [
            _point(
                "git_hook_installed",
                "Did the installed setup path report an active Git post-commit hook?",
                summary["setup_git_installed"] is True,
                {"setup_git_installed": summary["setup_git_installed"]},
            ),
            _point(
                "post_commit_hook_defers_anchor_creation",
                "Did the real post-commit hook run without creating premature anchors?",
                summary["post_commit_hook_log"].get("trail_anchors_created") == 0,
                {
                    "hook_log_sha_matches_head": summary[
                        "post_commit_hook_log"
                    ].get("sha")
                    == summary["commit_sha"],
                    "trail_anchors_created": summary["post_commit_hook_log"].get(
                        "trail_anchors_created"
                    ),
                },
            ),
            _point(
                "pre_tick_state_is_observed_not_reconciled",
                "Before watcher tick, is the mutation observed but not attributed or anchored?",
                pre_tick_counts.get("filesystem_mutation_observed", 0) >= 1
                and "watcher_observation_attributed" not in pre_tick_counts
                and "git_anchor_created" not in pre_tick_counts,
                {
                    "pre_tick_filesystem_mutation_observed": pre_tick_counts.get(
                        "filesystem_mutation_observed",
                        0,
                    ),
                    "pre_tick_has_attribution": "watcher_observation_attributed"
                    in pre_tick_counts,
                    "pre_tick_has_git_anchor": "git_anchor_created" in pre_tick_counts,
                },
            ),
            _point(
                "watcher_tick_ingests_and_matures",
                "Did watcher tick ingest the session and mature one Git Anchor?",
                tick.get("error") is None
                and tick.get("sessions_created", 0) >= 1
                and tick.get("trail_maturation_searches", 0) >= 1
                and tick.get("trail_maturation_anchors") == 1,
                {
                    "error": tick.get("error"),
                    "sessions_created": tick.get("sessions_created"),
                    "trail_maturation_searches": tick.get(
                        "trail_maturation_searches"
                    ),
                    "trail_maturation_anchors": tick.get(
                        "trail_maturation_anchors"
                    ),
                },
            ),
            _point(
                "final_event_log_has_reviewer_evidence",
                "Does the final event log contain observation, attribution, and one anchor?",
                event_counts.get("filesystem_mutation_observed", 0) >= 1
                and event_counts.get("watcher_observation_attributed", 0) >= 1
                and event_counts.get("git_anchor_created") == 1,
                {
                    "filesystem_mutation_observed": event_counts.get(
                        "filesystem_mutation_observed",
                        0,
                    ),
                    "watcher_observation_attributed": event_counts.get(
                        "watcher_observation_attributed",
                        0,
                    ),
                    "git_anchor_created": event_counts.get("git_anchor_created", 0),
                },
            ),
            _point(
                "review_surfaces_are_coherent",
                "Do explain, blame, graph, and search expose the anchored trail?",
                review_passed,
                review_evidence,
            ),
        ],
    }


def build_portrayal_packet(
    *,
    full_stack_summary: dict[str, Any],
    installed_runtime_summary: dict[str, Any],
) -> dict[str, Any]:
    """Return the concise reviewer packet asserted by this UAT layer."""

    return {
        "packet_version": 1,
        "purpose": "trace_trails_portrayal_uat",
        "fixture_mode": "synthetic_claude_transcript",
        "live_claude_required": False,
        "scenarios": [
            _full_stack_scenario(full_stack_summary),
            _installed_runtime_scenario(installed_runtime_summary),
        ],
    }


def _assert_packet_shape(packet: dict[str, Any]) -> None:
    assert set(packet) == PACKET_KEYS
    assert packet["packet_version"] == 1
    assert packet["purpose"] == "trace_trails_portrayal_uat"
    assert packet["fixture_mode"] == "synthetic_claude_transcript"
    assert packet["live_claude_required"] is False
    assert [scenario["id"] for scenario in packet["scenarios"]] == [
        "hook_boundary_full_stack_demo",
        "installed_runtime_watcher_tick",
    ]

    for scenario in packet["scenarios"]:
        assert set(scenario) == SCENARIO_KEYS
        assert set(scenario["identities"]) == IDENTITY_KEYS
        assert scenario["review_commands"]
        assert scenario["judgement_points"]
        for command in scenario["review_commands"]:
            assert set(command) == COMMAND_KEYS
            assert command["label"]
            assert command["command"].startswith("opentraces ")
            assert command["payload_key"]
        for judgement in scenario["judgement_points"]:
            assert set(judgement) == JUDGEMENT_KEYS
            assert judgement["id"]
            assert judgement["question"].endswith("?")
            assert judgement["judgement"] in {"pass", "fail"}
            assert judgement["evidence"]


def test_trace_trails_portrayal_packet_covers_demo_and_watcher_tick(
    tmp_path: Path,
) -> None:
    full_stack_summary = run_demo(tmp_path / "hook-boundary", verbose=True)
    installed_runtime_summary = run_installed_runtime_demo(
        tmp_path / "installed-runtime",
        verbose=True,
    )

    packet = build_portrayal_packet(
        full_stack_summary=full_stack_summary,
        installed_runtime_summary=installed_runtime_summary,
    )

    _assert_packet_shape(packet)
    for scenario in packet["scenarios"]:
        assert all(
            judgement["judgement"] == "pass"
            for judgement in scenario["judgement_points"]
        ), scenario


def test_trace_trails_portrayal_report_documents_track_a_scope() -> None:
    report = REPORT_PATH.read_text(encoding="utf-8")
    for required in [
        "Trace Trails Portrayal UAT",
        "hook_boundary_full_stack_demo",
        "installed_runtime_watcher_tick",
        "synthetic Claude transcript",
        "live Claude is not required",
        "pytest tests/integration/test_trace_trails_portrayal.py -q",
        "OT_REAL_REPL=1 OT_TRAIL_REAL_REPL=1",
    ]:
        assert required in report
