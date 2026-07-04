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
    DatasetSecurityOverride,
    DatasetSecurityPolicy,
    ExecutorConfig,
    WorkflowRef,
    WorkflowSecurityContract,
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
        "source_trace_id": None,
        "source_unit_id": None,
        "source_slice_id": None,
        "provenance": {},
    }


def test_row_index_entry_carries_lineage_ref_inside_free_provenance_dict():
    """#191 (M2): the first-class span ref rides inside the free-form
    ``provenance`` dict — the frozen ``DatasetRowIndexEntry`` schema gains NO new
    top-level field (the train's one MINOR bump is reserved for M3)."""
    entry = DatasetRowIndexEntry(
        row_id="row_ref",
        identity_hash="sha256:identity",
        payload_hash="sha256:payload",
        schema_digest="sha256:schema",
        data_file="data/train.jsonl",
        line=1,
        run_id="run_ref",
        appended_at="2026-07-02T12:00:00Z",
        source_trace_id="9f8e7d6c-1234-4abc-8def-0123456789ab",
        provenance={
            "schema_version": "opentraces.dataset.row_provenance.v2",
            "ref": "9f8e7d6c-1234-4abc-8def-0123456789ab:3-9",
            "source_refs": {
                "trace_id": "9f8e7d6c-1234-4abc-8def-0123456789ab",
                "step_range": [3, 9],
                "ref": "9f8e7d6c-1234-4abc-8def-0123456789ab:3-9",
                "ref_valid": True,
            },
        },
    )
    # No top-level ``ref`` / ``span_ref`` field exists on the model.
    dumped = entry.model_dump(mode="json")
    assert "ref" not in dumped
    assert "span_ref" not in dumped
    assert dumped["provenance"]["ref"] == "9f8e7d6c-1234-4abc-8def-0123456789ab:3-9"
    restored = DatasetRowIndexEntry.model_validate_json(entry.model_dump_json())
    assert restored == entry


def test_plan057_literal_boundaries_reject_deferred_or_unknown_modes():
    with pytest.raises(ValidationError):
        ExecutorConfig(default="remote-cloud")

    with pytest.raises(ValidationError):
        DatasetCandidateQuery(name="q", scope="remote")

    with pytest.raises(ValidationError):
        DatasetSchedule(enabled=True, every="2h", executor="remote-cloud")

    with pytest.raises(ValidationError):
        WorkflowRef(skill="missing-digest", digest="")


# --- M1 engine collapse (#185): script default + headless back-compat --------


def test_script_is_the_automated_default_executor():
    """The engine collapse makes ``script`` the automated default executor
    (``current-agent`` remains the development/human-in-the-loop path)."""
    config = ExecutorConfig()
    assert config.default == "script"
    assert config.development == "current-agent"
    # ``script`` is an accepted executor value for both surfaces.
    assert ExecutorConfig(default="script").default == "script"
    assert DatasetSchedule(enabled=True, every="2h", executor="script").executor == "script"


def test_legacy_headless_executor_value_still_deserializes():
    """The permanently-dead ``claude-code-headless`` executor was removed from
    the engine, but the value stays READABLE on ``ExecutorName`` so schedules,
    executor configs, and run records serialized before the collapse still load
    (and round-trip) instead of failing validation."""
    config = ExecutorConfig(default="claude-code-headless")
    assert config.default == "claude-code-headless"

    schedule = DatasetSchedule.model_validate(
        {"enabled": True, "every": "2h", "executor": "claude-code-headless"}
    )
    assert schedule.executor == "claude-code-headless"
    restored = DatasetSchedule.model_validate_json(schedule.model_dump_json())
    assert restored == schedule

    run = DatasetRunRecord(
        run_id="run_legacy",
        dataset_name="legacy",
        dry_run=False,
        executor="claude-code-headless",
        scope={"scope": "all-projects"},
        workflow_digest="sha256:workflow",
        schema_digest="sha256:schema",
        started_at="2026-04-28T12:00:00Z",
    )
    assert run.model_dump(mode="json")["executor"] == "claude-code-headless"


# --- Plan 092 Track 2: dataset security policy schema ------------------------


def test_dataset_manifest_security_defaults_to_empty_policy():
    manifest = DatasetManifest(
        name="minimal",
        schema={"path": "schemas/row.schema.json", "version": "1.0.0"},
        workflow={"skill": "minimal-curator", "digest": "sha256:workflow"},
    )
    assert manifest.security == DatasetSecurityPolicy()
    assert manifest.security.source == "default"
    assert manifest.security.enabled_tools == []
    # Round-trips through JSON unchanged (additive field is reader-compatible).
    restored = DatasetManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest


def test_workflow_security_contract_rejects_unknown_tool():
    with pytest.raises(ValidationError):
        WorkflowSecurityContract(required_tools=["not_a_real_tool"])


def test_workflow_security_contract_rejects_incoherent_sets():
    with pytest.raises(ValidationError):
        WorkflowSecurityContract(required_tools=["regex"], disallowed_tools=["regex"])
    with pytest.raises(ValidationError):
        # default_enabled must be inside required ∪ optional
        WorkflowSecurityContract(
            required_tools=["regex"], default_enabled_tools=["entropy"]
        )


def test_policy_from_contract_seeds_required_plus_default_in_canonical_order():
    contract = WorkflowSecurityContract(
        required_tools=["entropy", "regex"],
        optional_tools=["classifier", "business_logic"],
        default_enabled_tools=["business_logic"],
        disallowed_tools=["trufflehog"],
        allow_disable_required=False,
    )
    policy = DatasetSecurityPolicy.from_contract(
        contract, source_workflow_digest="sha256:wf"
    )
    assert policy.source == "workflow"
    assert policy.source_workflow_digest == "sha256:wf"
    # required (regex, entropy) + default_enabled (business_logic), canonical order.
    assert policy.enabled_tools == ["regex", "entropy", "business_logic"]
    assert policy.required_tools_satisfied() is True
    assert policy.missing_required_tools() == []


def test_policy_rejects_enabled_tool_outside_contract():
    with pytest.raises(ValidationError):
        DatasetSecurityPolicy(
            required_tools=["regex"],
            optional_tools=["entropy"],
            enabled_tools=["regex", "classifier"],  # classifier not in contract
        )


def test_policy_rejects_enabling_disallowed_tool():
    with pytest.raises(ValidationError):
        DatasetSecurityPolicy(
            optional_tools=["entropy"],
            enabled_tools=["entropy"],
            disallowed_tools=["entropy"],
        )


def test_policy_required_tool_must_be_enabled_or_overridden():
    # Disabling a required tool without an override is invalid.
    with pytest.raises(ValidationError):
        DatasetSecurityPolicy(required_tools=["regex"], enabled_tools=[])
    # With an explicit override it is allowed and recorded.
    policy = DatasetSecurityPolicy(
        required_tools=["regex"],
        enabled_tools=[],
        allow_disable_required=True,
        overrides=[DatasetSecurityOverride(tool="regex", reason="legacy import")],
    )
    assert policy.required_tools_satisfied() is False
    assert policy.missing_required_tools() == ["regex"]


def test_policy_override_must_target_required_tool():
    with pytest.raises(ValidationError):
        DatasetSecurityPolicy(
            optional_tools=["entropy"],
            enabled_tools=["entropy"],
            overrides=[DatasetSecurityOverride(tool="entropy")],
        )


def test_policy_rejects_override_when_required_disable_not_allowed():
    # A hand-edited / migrated manifest cannot smuggle in an override unless the
    # contract permits disabling required tools (the CLI already blocks this).
    with pytest.raises(ValidationError):
        DatasetSecurityPolicy(
            required_tools=["regex"],
            enabled_tools=[],
            allow_disable_required=False,
            overrides=[DatasetSecurityOverride(tool="regex")],
        )
