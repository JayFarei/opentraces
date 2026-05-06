from __future__ import annotations

import pytest
from pydantic import ValidationError

from opentraces_schema import (
    DatasetCandidateQuery,
    DatasetIdentity,
    DatasetManifest,
    DatasetRowIndexEntry,
    DatasetRunRecord,
    DatasetSchedule,
    ExecutorConfig,
    WorkflowRef,
)


def test_dataset_manifest_matches_plan057_local_control_contract():
    manifest = DatasetManifest.model_validate(
        {
            "name": "grill-me-intents",
            "description": "Intent summaries for traces that invoked the grill-me skill.",
            "schema": {
                "path": "schemas/row.schema.json",
                "version": "1.0.0",
                "digest": "sha256:schema",
            },
            "workflow": {
                "skill": "grill-me-intent-curator",
                "digest": "sha256:workflow",
                "instructions": "Emit rows matching the dataset schema.",
            },
            "executor": {
                "default": "claude-code-headless",
                "development": "current-agent",
                "timeout_minutes": 30,
                "budget_usd": 5.0,
            },
            "identity": {
                "mode": "fields",
                "fields": ["source_trace_id", "source_unit_id"],
            },
            "candidate_query": {
                "name": "grill-me-skill",
                "scope": "all-projects",
                "args": {"skill": "grill-me", "latest_generation": True},
                "incremental": {
                    "mode": "since_last_successful_run",
                    "field": "trace_updated_at",
                },
            },
            "schedule": {
                "enabled": False,
                "every": "2h",
                "executor": "claude-code-headless",
            },
            "discoverability": {
                "license": "cc-by-4.0",
                "pretty_name": "Grill-me intent traces",
                "tags": ["opentraces", "agent-traces", "grill-me"],
                "task_categories": ["text-generation"],
                "language": ["en"],
            },
        }
    )

    assert manifest.name == "grill-me-intents"
    assert manifest.schema_ref.path == "schemas/row.schema.json"
    assert manifest.model_dump(mode="json", by_alias=True)["schema"]["path"] == (
        "schemas/row.schema.json"
    )
    assert manifest.workflow.skill == "grill-me-intent-curator"
    assert manifest.identity.mode == "fields"
    assert manifest.identity.fields == ["source_trace_id", "source_unit_id"]
    assert manifest.candidate_query is not None
    assert manifest.candidate_query.args["skill"] == "grill-me"
    assert manifest.schedule is not None
    assert manifest.schedule.executor == "claude-code-headless"
    assert manifest.discoverability.tags == ["opentraces", "agent-traces", "grill-me"]

    restored = DatasetManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest


def test_dataset_manifest_defaults_to_payload_hash_identity_and_current_agent_development():
    manifest = DatasetManifest(
        name="minimal",
        schema={"path": "schemas/row.schema.json", "version": "1.0.0"},
        workflow={"skill": "minimal-curator", "digest": "sha256:workflow"},
    )

    assert manifest.identity == DatasetIdentity(mode="payload_hash")
    assert manifest.executor == ExecutorConfig()
    assert manifest.candidate_query is None
    assert manifest.schedule is None
    assert manifest.discoverability.tags == ["opentraces", "agent-traces"]


def test_dataset_manifest_rejects_local_source_provenance_sidecar_fields():
    with pytest.raises(ValidationError):
        DatasetManifest.model_validate(
            {
                "name": "minimal",
                "schema": {"path": "schemas/row.schema.json", "version": "1.0.0"},
                "workflow": {"skill": "minimal-curator", "digest": "sha256:workflow"},
                "source_provenance": {
                    "schema_version": "opentraces.dataset.source_provenance.v1"
                },
            }
        )


def test_field_identity_requires_declared_fields():
    with pytest.raises(ValidationError):
        DatasetIdentity(mode="fields")


def test_dataset_run_record_and_row_index_entry_are_stable_json_contracts():
    run = DatasetRunRecord(
        run_id="run_abc",
        dataset_name="grill-me-intents",
        dry_run=False,
        executor="claude-code-headless",
        scope={"scope": "all-projects"},
        workflow_digest="sha256:workflow",
        schema_digest="sha256:schema",
        started_at="2026-04-28T12:00:00Z",
        finished_at="2026-04-28T12:01:00Z",
        candidate_count=2,
        emitted_count=2,
        appended_count=1,
        duplicate_count=1,
        validation_error_count=0,
        status="succeeded",
        artefacts={"summary": ".opentraces/runs/run_abc/summary.json"},
    )
    entry = DatasetRowIndexEntry(
        row_id="row_abc",
        identity_hash="sha256:identity",
        payload_hash="sha256:payload",
        schema_digest="sha256:schema",
        data_file="data/train.jsonl",
        line=1,
        run_id=run.run_id,
        appended_at="2026-04-28T12:01:00Z",
    )

    assert run.model_dump(mode="json")["status"] == "succeeded"
    assert run.model_dump(mode="json")["artefacts"]["summary"].endswith("summary.json")
    assert entry.model_dump(mode="json") == {
        "row_id": "row_abc",
        "identity_hash": "sha256:identity",
        "payload_hash": "sha256:payload",
        "schema_digest": "sha256:schema",
        "data_file": "data/train.jsonl",
        "line": 1,
        "run_id": "run_abc",
        "appended_at": "2026-04-28T12:01:00Z",
    }


def test_plan057_literal_boundaries_reject_deferred_or_unknown_modes():
    with pytest.raises(ValidationError):
        ExecutorConfig(default="remote-cloud")

    with pytest.raises(ValidationError):
        DatasetCandidateQuery(name="q", scope="remote")

    with pytest.raises(ValidationError):
        DatasetSchedule(enabled=True, every="2h", executor="remote-cloud")

    with pytest.raises(ValidationError):
        WorkflowRef(skill="missing-digest", digest="")
