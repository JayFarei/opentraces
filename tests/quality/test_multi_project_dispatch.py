from __future__ import annotations

from pathlib import Path
from typing import Iterator

from opentraces import capture
from opentraces.quality import engine
from opentraces.quality.engine import PersonaScore, ProjectInfo, TraceAssessment
from opentraces_schema.models import Agent, Metrics, Step, TraceRecord
from opentraces_schema.version import SCHEMA_VERSION


def _trace(agent_name: str, session_id: str) -> TraceRecord:
    return TraceRecord(
        schema_version=SCHEMA_VERSION,
        trace_id=f"trace-{session_id}",
        session_id=session_id,
        agent=Agent(name=agent_name),
        steps=[
            Step(step_index=0, role="user", content="please inspect this"),
            Step(step_index=1, role="agent", content="done"),
        ],
        metrics=Metrics(total_steps=2),
    )


def test_multi_project_assessment_uses_project_agent_parser(
    tmp_path,
    monkeypatch,
):
    saved_parsers = dict(capture.PARSERS)
    saved_registered = capture._registered
    parsed: list[Path] = []

    class OtherAgentStubParser:
        agent_name = "other-cli"

        def discover_sessions(self, projects_path: Path) -> Iterator[Path]:
            return iter(())

        def parse_session(self, session_path: Path, byte_offset: int = 0):
            parsed.append(session_path)
            return _trace("other-cli", session_path.stem)

    def fake_assess_trace(record, **_kwargs):
        return TraceAssessment(
            trace_id=record.trace_id,
            session_id=record.session_id,
            task_description="stub",
            persona_scores={
                "conformance": PersonaScore(
                    persona_name="conformance",
                    total_score=100.0,
                    pass_rate=100.0,
                )
            },
        )

    try:
        capture.register_parser(OtherAgentStubParser)
        monkeypatch.setattr(engine, "assess_trace", fake_assess_trace)

        project_dir = tmp_path / "codex-project"
        project_dir.mkdir()
        session_file = project_dir / "rollout-2026.jsonl"
        session_file.write_text("{}\n")

        result = engine.assess_multi_project(
            [
                ProjectInfo(
                    name="codex-project",
                    path=project_dir,
                    session_count=1,
                    agent_name="other-cli",
                )
            ],
            max_per_project=1,
            max_total=1,
        )

        assert parsed == [session_file]
        assert result.total_traces_parsed == 1
        assert result.cohorts[0].project.agent_name == "other-cli"
        assert result.cohorts[0].batch.persona_averages["conformance"] == 100.0
    finally:
        capture.PARSERS.clear()
        capture.PARSERS.update(saved_parsers)
        capture._registered = saved_registered
