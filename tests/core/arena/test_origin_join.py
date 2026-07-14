from __future__ import annotations

import json

import pytest
from opentraces.core.arena.origin import detect_bench_invocations
from opentraces_schema import Agent, Observation, Outcome, Step, TraceRecord


RUN_ID = "run_20260714T190000000000Z_abcdef123456"
CLAIM = "Publishing reaches the configured remote."


def _record(*contents: str, step_content: str | None = None) -> TraceRecord:
    return TraceRecord(
        trace_id="trace-origin-123",
        session_id="session-origin-123",
        agent=Agent(name="test-agent"),
        task={"description": "Run a bench scenario."},
        steps=[
            Step(
                step_index=7,
                role="agent",
                content=step_content,
                observations=[
                    Observation(source_call_id=f"call-{index}", content=content)
                    for index, content in enumerate(contents, start=1)
                ],
            )
        ],
        outcome=Outcome(success=None),
    )


def test_detector_accepts_only_the_two_frozen_captured_output_forms() -> None:
    structured = json.dumps(
        {
            "status": "ok",
            "run_id": RUN_ID,
            "verdict": "pass",
            "claim": CLAIM,
            "result_ref": "/private/run/result.json",
        }
    )
    human = (
        f"bench_run_{RUN_ID} pass {CLAIM}\n"
        f"claim: {CLAIM}\n"
        "verdict: pass\n"
    )

    invocations = detect_bench_invocations(_record(human, structured))

    assert [item.output_format for item in invocations] == ["human", "json"]
    assert [item.run_id for item in invocations] == [RUN_ID, RUN_ID]
    assert [item.verdict for item in invocations] == ["pass", "pass"]
    assert [item.claim for item in invocations] == [CLAIM, CLAIM]
    assert [item.step_index for item in invocations] == [7, 7]
    assert [item.source_call_id for item in invocations] == ["call-1", "call-2"]


@pytest.mark.parametrize(
    "content",
    [
        f"prefix bench_run_{RUN_ID} pass {CLAIM}",
        f"bench_run_run_not-an-id pass {CLAIM}",
        f"bench_run_{RUN_ID} PASS {CLAIM}",
        json.dumps({"run_id": RUN_ID, "verdict": "pass"}),
        json.dumps({"run_id": RUN_ID, "verdict": "pass", "claim": ""}),
        json.dumps([{"run_id": RUN_ID, "verdict": "pass", "claim": CLAIM}]),
        json.dumps({"run_id": RUN_ID, "verdict": "pass", "claim": CLAIM}) + "\nextra",
    ],
)
def test_detector_rejects_near_matches_and_uncaptured_narration(content: str) -> None:
    record = _record(content, step_content=f"bench_run_{RUN_ID} pass {CLAIM}")

    assert detect_bench_invocations(record) == []
