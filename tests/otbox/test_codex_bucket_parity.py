from __future__ import annotations

from pathlib import Path

from opentraces_schema import Agent, Step, TraceRecord

from tests.helpers.codex_parity import (
    assert_bucket_trace_parity,
    normalize_bucket_trace_summary,
)


def _record(
    trace_id: str,
    *,
    agent: Agent,
) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        session_id=f"session-{trace_id}",
        agent=agent,
        task={"description": "Add cache invalidation test"},
        steps=[
            Step(step_index=1, role="user", content="Add cache invalidation test"),
            Step(step_index=2, role="agent", content="Implemented focused test"),
        ],
        context_tree_summary={"capture_methods": ["transcript_reconstruction"]},
    )


def test_normalizer_ignores_expected_agent_identity_differences(
    tmp_path: Path,
) -> None:
    from opentraces.core.bucket_store import bucket_manifest, project_per_trace_exports

    project_per_trace_exports(
        tmp_path,
        project_slug="demo",
        trace_id="claude-golden",
        record=_record(
            "claude-golden",
            agent=Agent(
                name="claude-code",
                version="2.1.143",
                model="anthropic/claude-sonnet-4-20250514",
            ),
        ),
    )
    project_per_trace_exports(
        tmp_path,
        project_slug="demo",
        trace_id="codex-candidate",
        record=_record(
            "codex-candidate",
            agent=Agent(
                name="codex-cli",
                version="0.31.0",
                model="openai/gpt-5-codex",
            ),
        ),
    )

    rows = {
        row["trace_id"]: row
        for row in bucket_manifest(write=False, include_objects=False)["traces"]
    }

    assert rows["claude-golden"]["agent_name"] == "claude-code"
    assert rows["codex-candidate"]["agent_name"] == "codex-cli"
    assert_bucket_trace_parity(rows["claude-golden"], rows["codex-candidate"])


def test_normalizer_keeps_product_contract_differences_visible() -> None:
    base = {
        "title": "Add cache invalidation test",
        "lifecycle": "provisional",
        "summary": {
            "step_count": 2,
            "patch_count": 0,
            "anchored_count": 0,
            "node_count": 0,
            "capture_methods": ["transcript_reconstruction"],
        },
        "files": {"has_trail": False, "has_context": False, "has_sources": False},
        "remote_sync_eligible": False,
        "agent_name": "claude-code",
        "agent_version": "2.1.143",
        "agent_model": "anthropic/claude-sonnet-4-20250514",
    }
    changed = {
        **base,
        "summary": {
            **base["summary"],
            "step_count": 3,
        },
        "agent_name": "codex-cli",
        "agent_version": "0.31.0",
        "agent_model": "openai/gpt-5-codex",
    }
    same_shape_different_method = {
        **base,
        "summary": {
            **base["summary"],
            "capture_methods": ["otel"],
        },
        "agent_name": "codex-cli",
        "agent_version": "0.31.0",
        "agent_model": "openai/gpt-5-codex",
    }

    assert normalize_bucket_trace_summary(base) == normalize_bucket_trace_summary(
        same_shape_different_method
    )
    assert normalize_bucket_trace_summary(base) != normalize_bucket_trace_summary(changed)
