"""Public models for local dataset and workflow contracts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ``script`` is the automated default executor (deterministic subprocess builder).
# ``claude-code-headless`` was the permanently-stubbed headless executor removed in
# the M1 engine collapse (#185); its value stays READABLE here so schedules and run
# records serialized before the collapse still deserialize. ``current-agent`` is the
# labeled human-in-the-loop lifecycle path.
ExecutorName = Literal["current-agent", "claude-code-headless", "script"]
DatasetScope = Literal["all-projects", "project", "cwd", "trace"]
DatasetIdentityMode = Literal["payload_hash", "fields"]
DatasetRunStatus = Literal["running", "succeeded", "failed", "cancelled"]
DatasetRemoteVisibility = Literal["private", "public"]
DatasetRemoteSchemaPolicy = Literal["refuse_if_newer"]
DatasetPublicationReviewPolicy = Literal["required", "auto"]
DatasetPublicationSecurityPolicy = Literal["required"]
DatasetPublicationLLMReviewPolicy = Literal["optional", "required", "off"]
DatasetPublicationStatus = Literal[
    "needs_review",
    "publishable",
    "rejected",
    "blocked",
    "published",
]


class DatasetSchemaRef(BaseModel):
    """Reference to the dataset-owned JSON Schema for public row shape."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    version: str = Field(min_length=1)
    digest: str | None = None


class WorkflowRef(BaseModel):
    """Workflow skill metadata pinned by digest."""

    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    instructions: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


# Canonical security tool vocabulary (mirrors the runtime tool registry order in
# `opentraces.security.tools._registry`). Kept here so dataset/workflow security
# policy models can validate tool names and emit them in a stable order without
# importing the runtime package. A test asserts this stays in sync with the
# live registry.
SECURITY_TOOL_ORDER: tuple[str, ...] = (
    "regex",
    "entropy",
    "trufflehog",
    "privacy_filter",
    "llm_pii",
    "business_logic",
    "path_anonymizer",
    "capsule_scope",
    "classifier",
)
SecurityToolName = Literal[
    "regex",
    "entropy",
    "trufflehog",
    "privacy_filter",
    "llm_pii",
    "business_logic",
    "path_anonymizer",
    "capsule_scope",
    "classifier",
]
DatasetSecuritySource = Literal["default", "workflow", "manual"]


def _canonical_tool_order(names: "set[str] | frozenset[str]") -> list[str]:
    return [name for name in SECURITY_TOOL_ORDER if name in names]


class WorkflowSecurityContract(BaseModel):
    """Security contract a dataset workflow declares in its front matter.

    A workflow owns the security posture of the rows it projects: which tools
    MUST run (``required_tools``), which MAY be toggled per dataset
    (``optional_tools``), which are on by default (``default_enabled_tools``),
    and which are forbidden (``disallowed_tools``). ``allow_disable_required``
    governs whether a downstream dataset may disable a required tool at all.
    """

    model_config = ConfigDict(extra="forbid")

    required_tools: list[SecurityToolName] = Field(default_factory=list)
    optional_tools: list[SecurityToolName] = Field(default_factory=list)
    default_enabled_tools: list[SecurityToolName] = Field(default_factory=list)
    disallowed_tools: list[SecurityToolName] = Field(default_factory=list)
    allow_disable_required: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> "WorkflowSecurityContract":
        req = set(self.required_tools)
        opt = set(self.optional_tools)
        dis = set(self.disallowed_tools)
        dft = set(self.default_enabled_tools)
        if req & dis:
            raise ValueError(
                f"tools cannot be both required and disallowed: {sorted(req & dis)}"
            )
        if opt & dis:
            raise ValueError(
                f"tools cannot be both optional and disallowed: {sorted(opt & dis)}"
            )
        if req & opt:
            raise ValueError(
                f"tools cannot be both required and optional: {sorted(req & opt)}"
            )
        stray_default = dft - (req | opt)
        if stray_default:
            raise ValueError(
                "default_enabled_tools must be required or optional: "
                f"{sorted(stray_default)}"
            )
        return self

    def resolved_enabled_tools(self) -> list[str]:
        """Tools enabled when a dataset is first seeded from this contract."""

        return _canonical_tool_order(
            set(self.required_tools) | set(self.default_enabled_tools)
        )


class DatasetSecurityOverride(BaseModel):
    """Explicit, recorded unsafe override of a required security tool."""

    model_config = ConfigDict(extra="forbid")

    tool: SecurityToolName
    reason: str | None = None


class DatasetSecurityPolicy(BaseModel):
    """Resolved per-dataset security policy stored in the dataset manifest.

    Seeded from a workflow's :class:`WorkflowSecurityContract` at
    ``dataset new --workflow`` and edited only via ``dataset security <name>``.
    Required tools stay enabled unless an explicit override is recorded.
    """

    model_config = ConfigDict(extra="forbid")

    source: DatasetSecuritySource = "default"
    source_workflow_digest: str | None = None
    required_tools: list[SecurityToolName] = Field(default_factory=list)
    optional_tools: list[SecurityToolName] = Field(default_factory=list)
    enabled_tools: list[SecurityToolName] = Field(default_factory=list)
    disallowed_tools: list[SecurityToolName] = Field(default_factory=list)
    allow_disable_required: bool = False
    overrides: list[DatasetSecurityOverride] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherent(self) -> "DatasetSecurityPolicy":
        req = set(self.required_tools)
        opt = set(self.optional_tools)
        en = set(self.enabled_tools)
        dis = set(self.disallowed_tools)
        overridden = {o.tool for o in self.overrides}
        if req & dis:
            raise ValueError(
                f"tools cannot be both required and disallowed: {sorted(req & dis)}"
            )
        stray_enabled = en - (req | opt)
        if stray_enabled:
            raise ValueError(
                "enabled_tools must be declared required or optional: "
                f"{sorted(stray_enabled)}"
            )
        if en & dis:
            raise ValueError(
                f"disallowed tools cannot be enabled: {sorted(en & dis)}"
            )
        # Required-tools subset invariant: every required tool stays enabled
        # unless an explicit override records the unsafe opt-out.
        missing_required = req - en - overridden
        if missing_required:
            raise ValueError(
                "required tools must be enabled or explicitly overridden: "
                f"{sorted(missing_required)}"
            )
        stray_override = overridden - req
        if stray_override:
            raise ValueError(
                f"overrides may only target required tools: {sorted(stray_override)}"
            )
        if overridden and not self.allow_disable_required:
            raise ValueError(
                "overrides are only allowed when allow_disable_required is true: "
                f"{sorted(overridden)}"
            )
        return self

    @classmethod
    def from_contract(
        cls,
        contract: "WorkflowSecurityContract",
        *,
        source_workflow_digest: str | None = None,
    ) -> "DatasetSecurityPolicy":
        return cls(
            source="workflow",
            source_workflow_digest=source_workflow_digest,
            required_tools=list(contract.required_tools),
            optional_tools=list(contract.optional_tools),
            enabled_tools=contract.resolved_enabled_tools(),
            disallowed_tools=list(contract.disallowed_tools),
            allow_disable_required=contract.allow_disable_required,
        )

    def required_tools_satisfied(self) -> bool:
        """True when every required tool is enabled (no unsafe override gap)."""

        return set(self.required_tools) <= set(self.enabled_tools)

    def missing_required_tools(self) -> list[str]:
        return _canonical_tool_order(set(self.required_tools) - set(self.enabled_tools))


class ExecutorConfig(BaseModel):
    """Default automated and development executors for a local dataset."""

    model_config = ConfigDict(extra="forbid")

    default: ExecutorName = "script"
    development: ExecutorName = "current-agent"
    timeout_minutes: int = Field(30, ge=1)
    budget_usd: float | None = Field(default=None, ge=0)


class DatasetIdentity(BaseModel):
    """Dataset-declared row identity policy."""

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    scope: DatasetScope = "all-projects"
    args: dict[str, Any] = Field(default_factory=dict)
    incremental: dict[str, Any] = Field(default_factory=dict)


class DatasetSchedule(BaseModel):
    """Local schedule state embedded in the dataset manifest."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    every: str | None = None
    executor: ExecutorName = "script"

    @model_validator(mode="after")
    def _enabled_requires_every(self) -> "DatasetSchedule":
        if self.enabled and not (self.every or "").strip():
            raise ValueError("schedule.every is required when schedule.enabled is true")
        return self


class DatasetDiscoverability(BaseModel):
    """HF-style metadata kept on the local dataset card."""

    model_config = ConfigDict(extra="forbid")

    license: str | None = None
    pretty_name: str | None = None
    tags: list[str] = Field(default_factory=lambda: ["opentraces", "agent-traces"])
    task_categories: list[str] = Field(default_factory=list)
    language: list[str] = Field(default_factory=list)


class DatasetRemote(BaseModel):
    """Dataset-scoped HuggingFace remote binding."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1)
    visibility: DatasetRemoteVisibility = "private"


class DatasetPublicationPolicy(BaseModel):
    """Dataset-wide egress policy for publishable public rows."""

    model_config = ConfigDict(extra="forbid")

    review: DatasetPublicationReviewPolicy = "required"
    security: DatasetPublicationSecurityPolicy = "required"
    llm_review: DatasetPublicationLLMReviewPolicy = "optional"


class DatasetManifest(BaseModel):
    """Local control manifest for an executable dataset."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str = Field(min_length=1)
    description: str | None = None
    schema_ref: DatasetSchemaRef = Field(alias="schema")
    workflow: WorkflowRef
    executor: ExecutorConfig = Field(default_factory=ExecutorConfig)
    identity: DatasetIdentity = Field(default_factory=DatasetIdentity)
    candidate_query: DatasetCandidateQuery | None = None
    schedule: DatasetSchedule | None = None
    discoverability: DatasetDiscoverability = Field(default_factory=DatasetDiscoverability)
    remotes: dict[str, DatasetRemote] = Field(default_factory=dict)
    active_remote: str | None = None
    remote_schema: DatasetRemoteSchemaPolicy = "refuse_if_newer"
    publication_policy: DatasetPublicationPolicy = Field(
        default_factory=DatasetPublicationPolicy
    )
    security: DatasetSecurityPolicy = Field(default_factory=DatasetSecurityPolicy)

    @model_validator(mode="after")
    def _active_remote_must_exist(self) -> "DatasetManifest":
        if self.active_remote and self.active_remote not in self.remotes:
            raise ValueError("active_remote must name an entry in remotes")
        return self


class DatasetPublicationStateEntry(BaseModel):
    """Local row-level sidecar preserving publication decisions."""

    model_config = ConfigDict(extra="forbid")

    row_id: str = Field(min_length=1)
    status: DatasetPublicationStatus = "needs_review"
    uploaded_to: dict[str, str] = Field(default_factory=dict)
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    block_reasons: list[str] = Field(default_factory=list)
    security_version: str | None = None
    source_security_version: str | None = None
    privacy_tier: str | None = None
    security_stale: bool = False
    redactions_applied: int = 0
    updated_at: str | None = None


class DatasetPublicationState(BaseModel):
    """On-disk `.opentraces/publication_state.json` contract."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    rows: dict[str, DatasetPublicationStateEntry] = Field(default_factory=dict)


class DatasetRunRecord(BaseModel):
    """Run summary for dry-run and committed local dataset executions."""

    model_config = ConfigDict(extra="forbid")

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

    model_config = ConfigDict(extra="forbid")

    row_id: str = Field(min_length=1)
    identity_hash: str = Field(min_length=1)
    payload_hash: str = Field(min_length=1)
    schema_digest: str = Field(min_length=1)
    data_file: str = Field(min_length=1)
    line: int = Field(ge=1)
    run_id: str = Field(min_length=1)
    appended_at: str = Field(min_length=1)
    source_trace_id: str | None = None
    source_unit_id: str | None = None
    source_slice_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
