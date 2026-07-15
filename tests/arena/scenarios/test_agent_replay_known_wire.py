"""Launch the deterministic real-Claude replay control for bench.v0."""

from __future__ import annotations

import os

import pytest

from opentraces.core.arena.engine import VerificationFailed


pytestmark = pytest.mark.skipif(
    os.environ.get("OT_BENCH_SCENARIOS") != "1",
    reason="bench scenarios run through `opentraces bench run`",
)

PROOF_PATH = "/tmp/opentraces-agent-replay-proof.txt"
EXPECTED_PROOF = "known-wire-v1"
TOOL_USE_ID = "toolu_opentraces_replay_0001"


def known_wire_script(*, mutate_tool_result: bool = False) -> dict:
    """Return the two-turn wire; mutation preserves harness completion."""

    value = "mutated-wire-value" if mutate_tool_result else EXPECTED_PROOF
    return {
        "schema_version": "opentraces.anthropic-replay-script.v0",
        "responses": [
            {
                "id": "msg_opentraces_replay_0001",
                "model": "claude-opentraces-replay-v0",
                "content": [
                    {
                        "type": "tool_use",
                        "id": TOOL_USE_ID,
                        "name": "Bash",
                        "input": {
                            "command": f"printf '%s\\n' '{value}' > {PROOF_PATH}",
                            "description": "Write the deterministic replay proof",
                        },
                    }
                ],
                "stop_reason": "tool_use",
            },
            {
                "id": "msg_opentraces_replay_0002",
                "model": "claude-opentraces-replay-v0",
                "content": [
                    {
                        "type": "text",
                        "text": "OPENTRACES_AGENT_ATTEMPT_COMPLETE",
                    }
                ],
                "stop_reason": "end_turn",
            },
        ],
    }


def known_wire_reached_world(run, *, model):
    """Grade real final state and exact protocol evidence, never narration."""

    observed = run.terminal.exec("cat", PROOF_PATH)
    rows = model.ledger.rows()
    try:
        assert observed.returncode == 0, observed.stderr
        assert observed.stdout.strip() == EXPECTED_PROOF
        assert len(rows) == 2
        assert [row["response"]["body"] for row in rows] == model.script["responses"]

        first_request = rows[0]["request"]["body"]
        system_text = "\n".join(
            block.get("text", "") for block in first_request.get("system", [])
        )
        assert "You are a Claude agent" in system_text
        assert "Bash" in {tool.get("name") for tool in first_request.get("tools", [])}
        assert "Create the deterministic replay proof" in str(
            first_request.get("messages", [])
        )

        second_messages = rows[1]["request"]["body"].get("messages", [])
        assert any(
            block.get("type") == "tool_result"
            and block.get("tool_use_id") == TOOL_USE_ID
            for message in second_messages
            for block in (
                message.get("content", [])
                if isinstance(message.get("content"), list)
                else []
            )
        )
    except AssertionError as exc:
        raise VerificationFailed(
            str(exc) or "scripted model wire did not produce the expected world",
            evidence_refs=[model.ledger.evidence_ref],
        ) from exc
    return {"evidence_refs": [model.ledger.evidence_ref]}


def test_real_claude_known_interaction_replay(bench):
    """The real Claude harness can carry out a known interaction through Bash."""

    mutated = os.environ.get("OT_BENCH_REPLAY_MUTATE_TOOL_RESULT") == "1"
    with bench.run(app_state="install-only", execution_mode="agent_replay") as run:
        model = run.emulate(
            "anthropic",
            script=known_wire_script(mutate_tool_result=mutated),
        )
        attempt = run.agent.attempt(
            harness="claude",
            task="Create the deterministic replay proof through the granted terminal.",
            access=[run.terminal],
            inference=model,
        )
        assert attempt.completed, attempt.failure
        run.verify(known_wire_reached_world, model=model)
