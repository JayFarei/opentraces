"""Public models for local dataset and workflow contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ExecutorName = Literal["current-agent", "claude-code-headless"]
DatasetScope = Literal["all-projects", "project", "cwd", "trace"]
DatasetIdentityMode = Literal["payload_hash", "fields"]
DatasetRunStatus = Literal["running", "succeeded", "failed", "cancelled"]


class DatasetSchemaRef(BaseModel):
    """Reference to the dataset-owned JSON Schema for public row shape."""

    path: str = Field(min_length=1)
    version: str = Field(min_length=1)
    digest: str | None = None


class WorkflowRef(BaseModel):
    """Workflow skill metadata pinned by digest."""

    skill: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    instructions: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ExecutorConfig(BaseModel):
    """Default automated and development executors for a local dataset."""

    default: ExecutorName = "claude-code-headless"
    development: ExecutorName = "current-agent"
    timeout_minutes: int = Field(30, ge=1)
    budget_usd: float | None = Field(default=None, ge=0)


class DatasetIdentity(BaseModel):
    """Dataset-declared row identity policy."""

    mode: DatasetIdentityMode = "payload_hash"
    fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fields_mode_requires_fields(self) -> "DatasetIdentity":
        if self.mode == "fields" and not self.fields:
            raise ValueError("identity.fields is required when identity.mode is 'fields'")
        return self

    @field_validator("fields")
    @classmethod
    def _fields_must_be_non_empty_names(cls, value: list[str]) -> list[str]:
        if any(not field for field in value):
            raise ValueError("identity.fields entries must be non-empty")
        return value


class DatasetCandidateQuery(BaseModel):
    """Remembered trace query scope for dataset runs."""

    name: str = Field(min_length=1)
    scope: DatasetScope = "all-projects"
    args: dict[str, Any] = Field(default_factory=dict)
    incremental: dict[str, Any] = Field(default_factory=dict)


class DatasetSchedule(BaseModel):
    """Local schedule state embedded in the dataset manifest."""

    enabled: bool = False
    every: str | None = None
    executor: ExecutorName = "claude-code-headless"


class DatasetDiscoverability(BaseModel):
    """HF-style metadata kept on the local dataset card."""

    license: str | None = None
    pretty_name: str | None = None
    tags: list[str] = Field(default_factory=lambda: ["opentraces", "agent-traces"])
    task_categories: list[str] = Field(default_factory=list)
    language: list[str] = Field(default_factory=list)


class DatasetManifest(BaseModel):
    """Local control manifest for an executable dataset."""

    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1)
    description: str | None = None
    schema_ref: DatasetSchemaRef = Field(alias="schema")
    workflow: WorkflowRef
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    identity: DatasetIdentity = Field(default_factory=DatasetIdentity)
    candidate_query: DatasetCandidateQuery | None = None
    schedule: DatasetSchedule | None = None
    discoverability: DatasetDiscoverability = Field(default_factory=DatasetDiscoverability)


class DatasetRunRecord(BaseModel):
    """Run summary for dry-run and committed local dataset executions."""

    run_id: str = Field(min_length=1)
    dataset_name: str = Field(min_length=1)
    dry_run: bool
    executor: str = Field(min_length=1)
    scope: dict[str, Any]
    workflow_digest: str = Field(min_length=1)
    schema_digest: str = Field(min_length=1)
    started_at: str = Field(min_length=1)
    finished_at: str | None = None
    candidate_count: int = Field(0, ge=0)
    emitted_count: int = Field(0, ge=0)
    appended_count: int = Field(0, ge=0)
    duplicate_count: int = Field(0, ge=0)
    validation_error_count: int = Field(0, ge=0)
    status: DatasetRunStatus = "running"
    artefacts: dict[str, str] = Field(default_factory=dict)


class DatasetRowIndexEntry(BaseModel):
    """Private, rebuildable row index entry for local dataset dedupe."""

    row_id: str = Field(min_length=1)
    identity_hash: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)
    schema_digest: str = Field(min_length=1)
    data_file: str = Field(min_length=1)
    line: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    appended_at: str = Field(min_length=1)
