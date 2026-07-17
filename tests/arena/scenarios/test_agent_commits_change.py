"""Launch scenario 4: one live agent commit with its required capture footprint."""

from __future__ import annotations

import json
import os

import pytest

from opentraces.core.arena.engine import VerificationFailed
from opentraces.core.arena.stored_judge import SCENARIO_4_CONTENT, SCENARIO_4_PATH


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)


def _contains_value(value: object, expected: str) -> bool:
    if value == expected:
        return True
    if isinstance(value, dict):
        return any(_contains_value(child, expected) for child in value.values())
    if isinstance(value, list):
        return any(_contains_value(child, expected) for child in value)
    return False


def scenario_4_world_state(run, *, attempt):
    """Grade the committed world and public Trace/Context/Trail/storage reads."""

    evidence_refs: list[str] = []
    try:
        commit = run.terminal.exec("git", "rev-parse", "HEAD")
        evidence_refs.append(commit.result_ref)
        assert commit.returncode == 0, commit.stderr
        commit_sha = commit.stdout.strip()
        assert len(commit_sha) == 40

        content = run.terminal.exec(
            "git", "show", f"{commit_sha}:{SCENARIO_4_PATH}"
        )
        evidence_refs.append(content.result_ref)
        assert content.returncode == 0, content.stderr
        assert content.stdout.strip() == SCENARIO_4_CONTENT

        assert attempt.capture_session_id
        capture_dir = f".opentraces/bench-capture/{attempt.capture_session_id}"
        capture_result = run.terminal.exec("cat", f"{capture_dir}/capture_result.json")
        evidence_refs.append(capture_result.result_ref)
        assert capture_result.returncode == 0, capture_result.stderr
        capture = capture_result.json

        # The suppression control isolates the founder-ratified product law:
        # the commit may exist, while the engine must name the missing capture
        # footprint. It deliberately does not ask a second verifier to fail first.
        if os.environ.get("OT_BENCH_CAPTURE_INTERRUPT_SOURCE"):
            return {"evidence_refs": evidence_refs}

        assert capture["completeness"] == "complete"
        trace_refs = capture.get("trace_refs") or []
        assert len(trace_refs) == 1
        trace_id = str(trace_refs[0])
        capture_env = {"OT_OPENTRACES_DIR": f"{capture_dir}/runtime"}

        blame = run.terminal.exec(
            "opentraces",
            "trail",
            "blame",
            "commit",
            commit_sha,
            "--project",
            ".",
            "--json",
            env=capture_env,
            timeout=120,
        )
        evidence_refs.append(blame.result_ref)
        assert blame.returncode == 0, blame.stderr
        trail_rows = blame.json.get("trailEvidence") or []
        assert any(row.get("trace_id") == trace_id for row in trail_rows)

        trace = run.terminal.exec(
            "opentraces", "trace", "get", trace_id, "--json", env=capture_env
        )
        evidence_refs.append(trace.result_ref)
        assert trace.returncode == 0, trace.stderr
        assert _contains_value(trace.json, trace_id)

        context = run.terminal.exec(
            "opentraces", "ctx", trace_id, "--json", env=capture_env
        )
        evidence_refs.append(context.result_ref)
        assert context.returncode == 0, context.stderr
        assert _contains_value(context.json, trace_id)

        bucket = run.terminal.exec(
            "opentraces", "bucket", "verify", "--json", env=capture_env
        )
        evidence_refs.append(bucket.result_ref)
        assert bucket.returncode == 0, bucket.stderr
        assert bucket.json.get("ok") is True
    except (AssertionError, json.JSONDecodeError) as exc:
        raise VerificationFailed(
            str(exc) or "scenario-4 world state is not publicly observable",
            evidence_refs=evidence_refs,
        ) from exc
    return {"evidence_refs": evidence_refs}


def test_agent_commits_change(bench):
    """The agent makes a small change and commits it."""

    with bench.run(
        app_state="agent-ready",
        execution_mode="agent_live",
        capture_required=["trace", "context", "trail", "storage"],
    ) as run:
        attempt = run.agent.attempt(
            harness="claude",
            task=(
                f"Create {SCENARIO_4_PATH} containing exactly {SCENARIO_4_CONTENT!r} "
                "followed by one newline. Commit only that file with commit message "
                "'bench: scenario 4 agent change', and leave the worktree clean."
            ),
            access=[run.terminal],
            inference="live",
        )
        assert attempt.completed, attempt.failure
        run.verify(scenario_4_world_state, attempt=attempt)
