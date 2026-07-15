from __future__ import annotations

import inspect
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from opentraces.core.arena.emulate.anthropic.runtime import (
    SERVER_SOURCE,
    SCRIPT_SCHEMA,
)
from opentraces.core.arena.engine import VerificationFailed
from tests.arena.scenarios import test_agent_replay_known_wire as replay_scenario


def _post_messages(endpoint: str, body: dict) -> str:
    request = urllib.request.Request(
        f"{endpoint}/v1/messages?beta=true",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        assert response.status == 200
        return response.read().decode()


def test_scripted_anthropic_server_streams_exact_turns_and_ledgers_wire(
    tmp_path: Path,
) -> None:
    script = replay_scenario.known_wire_script()
    script_path = tmp_path / "script.json"
    ledger_path = tmp_path / "ledger.jsonl"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    port = 28419
    environment = {
        **os.environ,
        "PORT": str(port),
        "SCRIPT_PATH": str(script_path),
        "LEDGER_PATH": str(ledger_path),
        "OPENTRACES_ANTHROPIC_LAUNCH_NONCE": "test-launch",
        "OPENTRACES_ANTHROPIC_SOURCE_SHA256": "source-pin",
        "OPENTRACES_ANTHROPIC_SCRIPT_SHA256": "script-pin",
    }
    process = subprocess.Popen(
        [sys.executable, str(SERVER_SOURCE)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    endpoint = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 3
        while True:
            try:
                with urllib.request.urlopen(f"{endpoint}/_emulate/manifest", timeout=0.2) as r:
                    manifest = json.load(r)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        assert manifest["id"] == "anthropic-scripted"
        assert manifest["launch"]["nonce"] == "test-launch"
        assert manifest["script"]["sha256"] == "script-pin"

        first = {
            "model": "claude-opus-test",
            "stream": True,
            "system": [{"type": "text", "text": "You are a Claude agent"}],
            "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
            "messages": [{"role": "user", "content": "Create the deterministic replay proof"}],
        }
        first_stream = _post_messages(endpoint, first)
        assert '"type":"tool_use"' in first_stream
        assert replay_scenario.TOOL_USE_ID in first_stream

        second = {
            **first,
            "messages": [
                *first["messages"],
                {
                    "role": "assistant",
                    "content": script["responses"][0]["content"],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": replay_scenario.TOOL_USE_ID,
                            "content": "",
                        }
                    ],
                },
            ],
        }
        second_stream = _post_messages(endpoint, second)
        assert "OPENTRACES_AGENT_ATTEMPT_COMPLETE" in second_stream
    finally:
        process.terminate()
        process.wait(timeout=3)

    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert [row["request"]["body"] for row in rows] == [first, second]
    assert [row["response"]["body"] for row in rows] == script["responses"]


def test_replay_scenario_freezes_real_harness_world_and_mutation_control() -> None:
    assert SCRIPT_SCHEMA == "opentraces.anthropic-replay-script.v0"
    source = inspect.getsource(replay_scenario.test_real_claude_known_interaction_replay)
    assert 'app_state="agent-ready"' in source
    assert 'execution_mode="agent_replay"' in source
    assert 'harness="claude"' in source
    assert "access=[run.terminal]" in source
    assert "run.verify(known_wire_reached_world, model=model)" in source

    green = replay_scenario.known_wire_script()
    mutated = replay_scenario.known_wire_script(mutate_tool_result=True)
    assert green["schema_version"] == SCRIPT_SCHEMA
    assert green["responses"][1] == mutated["responses"][1]
    assert green["responses"][0] != mutated["responses"][0]
    assert replay_scenario.EXPECTED_PROOF in json.dumps(green["responses"][0])
    assert replay_scenario.EXPECTED_PROOF not in json.dumps(mutated["responses"][0])


def _verifier_inputs(*, world_value: str, script: dict) -> tuple[object, object]:
    first_request = {
        "system": [{"type": "text", "text": "You are a Claude agent"}],
        "tools": [{"name": "Bash"}],
        "messages": [{"role": "user", "content": "Create the deterministic replay proof"}],
    }
    second_request = {
        **first_request,
        "messages": [
            *first_request["messages"],
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": replay_scenario.TOOL_USE_ID,
                        "content": "",
                    }
                ],
            },
        ],
    }
    rows = [
        {
            "request": {"body": request},
            "response": {"body": response},
        }
        for request, response in zip(
            [first_request, second_request], script["responses"], strict=True
        )
    ]
    run = SimpleNamespace(
        terminal=SimpleNamespace(
            exec=lambda *_args: SimpleNamespace(
                returncode=0,
                stdout=world_value + "\n",
                stderr="",
                result_ref="actions/0002/result.json",
            )
        )
    )
    model = SimpleNamespace(
        script=script,
        ledger=SimpleNamespace(
            rows=lambda: rows,
            evidence_ref="ledgers/anthropic.jsonl",
        ),
    )
    return run, model


def test_mutated_tool_line_keeps_exact_wire_but_independent_world_verifier_is_red() -> None:
    green = replay_scenario.known_wire_script()
    run, model = _verifier_inputs(world_value=replay_scenario.EXPECTED_PROOF, script=green)
    assert replay_scenario.known_wire_reached_world(run, model=model) == {
        "evidence_refs": ["actions/0002/result.json", "ledgers/anthropic.jsonl"]
    }

    mutated = replay_scenario.known_wire_script(mutate_tool_result=True)
    run, model = _verifier_inputs(world_value="mutated-wire-value", script=mutated)
    with pytest.raises(VerificationFailed) as failure:
        replay_scenario.known_wire_reached_world(run, model=model)
    assert failure.value.evidence_refs == [
        "actions/0002/result.json",
        "ledgers/anthropic.jsonl",
    ]
