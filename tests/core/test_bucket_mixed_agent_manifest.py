from __future__ import annotations

from pathlib import Path

from opentraces_schema import Agent, Step, TraceRecord


def _trace(
    trace_id: str,
    *,
    agent_name: str,
    agent_version: str | None,
    agent_model: str | None,
) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=Agent(name=agent_name, version=agent_version, model=agent_model),
        task={"description": "Patch bucket manifest filtering"},
        steps=[
            Step(step_index=1, role="user", content="Patch bucket manifest filtering"),
            Step(step_index=2, role="agent", content="Updated manifest summary"),
        ],
    )


def test_bucket_manifest_trace_rows_include_agent_metadata(tmp_path: Path) -> None:
    from opentraces.core.bucket_store import bucket_manifest, bucket_verify, project_per_trace_exports

    project_per_trace_exports(
        tmp_path,
        project_slug="demo",
        trace_id="trace-claude",
        record=_trace(
            "trace-claude",
            agent_name="claude-code",
            agent_version="2.1.143",
            agent_model="anthropic/claude-sonnet-4-20250514",
        ),
    )
    project_per_trace_exports(
        tmp_path,
        project_slug="demo",
        trace_id="trace-codex",
        record=_trace(
            "trace-codex",
            agent_name="codex-cli",
            agent_version="0.31.0",
            agent_model="openai/gpt-5-codex",
        ),
    )
    project_per_trace_exports(
        tmp_path,
        project_slug="demo",
        trace_id="trace-pi",
        record=_trace(
            "trace-pi",
            agent_name="pi",
            agent_version="1.0.0",
            agent_model="anthropic/claude-sonnet-4",
        ),
    )

    rows = {
        row["trace_id"]: row
        for row in bucket_manifest(write=False, include_objects=False)["traces"]
    }

    assert rows["trace-claude"]["agent_name"] == "claude-code"
    assert rows["trace-claude"]["agent_version"] == "2.1.143"
    assert rows["trace-claude"]["agent_model"] == "anthropic/claude-sonnet-4-20250514"
    assert rows["trace-codex"]["agent_name"] == "codex-cli"
    assert rows["trace-codex"]["agent_version"] == "0.31.0"
    assert rows["trace-codex"]["agent_model"] == "openai/gpt-5-codex"
    assert rows["trace-pi"]["agent_name"] == "pi"
    assert rows["trace-pi"]["agent_version"] == "1.0.0"
    assert rows["trace-pi"]["agent_model"] == "anthropic/claude-sonnet-4"

    codex_rows = [row for row in rows.values() if row["agent_name"] == "codex-cli"]
    assert [row["trace_id"] for row in codex_rows] == ["trace-codex"]
    pi_rows = [row for row in rows.values() if row["agent_name"] == "pi"]
    assert [row["trace_id"] for row in pi_rows] == ["trace-pi"]

    verify = bucket_verify(full=True)
    assert verify["ok"] is True, verify


def test_bucket_manifest_agent_metadata_allows_missing_optional_fields(
    tmp_path: Path,
) -> None:
    from opentraces.core.bucket_store import bucket_manifest, project_per_trace_exports

    project_per_trace_exports(
        tmp_path,
        project_slug="demo",
        trace_id="trace-name-only",
        record=_trace(
            "trace-name-only",
            agent_name="codex-cli",
            agent_version=None,
            agent_model=None,
        ),
    )

    row = bucket_manifest(write=False, include_objects=False)["traces"][0]

    assert row["agent_name"] == "codex-cli"
    assert row["agent_version"] is None
    assert row["agent_model"] is None
