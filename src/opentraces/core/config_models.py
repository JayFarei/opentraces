"""opentraces configuration data models (the base config layer).

The Pydantic models for global + per-project config (``Config`` and its nested
security / capture / bucket / remote / project models) plus ``CONFIG_VERSION``.
Extracted from ``config`` (god-module decomposition) as the dependency-free base
layer: imports only pydantic + typing + the ``trace_stage`` vocab — NOTHING from
``config`` — so it can be imported anywhere without a cycle. ``config`` re-exports
every model here, so ``from ...core.config import Config`` / ``ProjectConfig``
call sites are unchanged.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .trace_stage import (
    DEFAULT_AGENT,
    DEFAULT_REVIEW_POLICY,
)


CONFIG_VERSION = "0.2.0"


class RegexConfig(BaseModel):
    """Settings for the opt-in built-in regex detector."""

    enabled: bool = False


class EntropyConfig(BaseModel):
    """Settings for the opt-in high-entropy detector."""

    enabled: bool = False


class TruffleHogConfig(BaseModel):
    """Tier 1.5 TruffleHog secret-scanning settings (Plan 032 Part A)."""

    enabled: bool = False
    verify_secrets: bool = Field(
        False,
        description=(
            "Live API probing. Off = offline scan, no outbound calls. "
            "Default off: we never assume consent for vendor-detector "
            "network activity on every scan."
        ),
    )


class LLMReviewConfig(BaseModel):
    """Global settings for the opt-in third-party LLM review step.

    ``api_format`` selects the wire protocol the local client speaks, not
    the vendor — ``openai-compat`` covers vLLM, LM Studio, llama.cpp and
    Ollama-via-/v1, in addition to OpenAI proper.
    """

    enabled: bool = False
    api_format: Literal["openai-compat", "ollama", "anthropic", "fake"] = "openai-compat"
    base_url: str = "http://localhost:11434/v1"
    model: str = "gemma3n:e4b"
    api_key_env: str = Field("", description="Env var holding the API key")
    timeout: float = 120.0
    prompt_version: str = "1"


class LLMPIIConfig(BaseModel):
    """Settings for the opt-in per-field LLM PII detector.

    Same provider configuration model as :class:`LLMReviewConfig` — a separate
    config block so users can plug different models (or providers) into the
    inline PII detector versus the on-demand session reviewer.
    """

    enabled: bool = False
    api_format: Literal["openai-compat", "ollama", "anthropic", "fake"] = "openai-compat"
    base_url: str = "http://localhost:11434/v1"
    model: str = "gemma3n:e4b"
    api_key_env: str = Field("", description="Env var holding the API key")
    timeout: float = 120.0


class BusinessLogicConfig(BaseModel):
    """Opt-in detector for internal infrastructure/business signals."""

    enabled: bool = False


class PathAnonymizerConfig(BaseModel):
    """Opt-in transformer that rewrites local usernames in paths."""

    enabled: bool = False


class CapsuleScopeConfig(BaseModel):
    """Opt-in field-exclusion transformer for prompt-bearing fields."""

    enabled: bool = False
    exclude: list[str] = Field(
        default_factory=lambda: [
            "context_resume_packet.system_layer",
            "slice.steps.*.reasoning_content",
        ],
    )


class ClassifierConfig(BaseModel):
    """Opt-in heuristic content classifier."""

    enabled: bool = False
    sensitivity: Literal["low", "medium", "high"] = "medium"


class PrivacyFilterConfig(BaseModel):
    """``openai/privacy-filter`` BERT-NER detector (opt-in).

    Requires the ``transformers`` and ``torch`` packages, installed by
    ``opentraces setup privacy-filter``. The model is downloaded from
    HuggingFace on first use (~500MB).
    """

    enabled: bool = False
    model_name: str = "openai/privacy-filter"
    score_threshold: float = 0.7


class SecurityConfig(BaseModel):
    """Root security-module config.

    No top-level ``privacy_tier`` field — the set of tools that run is the
    sum of the per-tool ``enabled`` flags below. Installer-backed tools are
    opted in via ``opentraces setup <tool>``; lightweight local tools can also
    be invoked directly with ``opentraces security sanitize --tools``.
    """

    regex: RegexConfig = Field(default_factory=RegexConfig)
    entropy: EntropyConfig = Field(default_factory=EntropyConfig)
    trufflehog: TruffleHogConfig = Field(default_factory=TruffleHogConfig)
    llm_review: LLMReviewConfig = Field(default_factory=LLMReviewConfig)
    llm_pii: LLMPIIConfig = Field(default_factory=LLMPIIConfig)
    privacy_filter: PrivacyFilterConfig = Field(default_factory=PrivacyFilterConfig)
    business_logic: BusinessLogicConfig = Field(default_factory=BusinessLogicConfig)
    path_anonymizer: PathAnonymizerConfig = Field(default_factory=PathAnonymizerConfig)
    capsule_scope: CapsuleScopeConfig = Field(default_factory=CapsuleScopeConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)

    model_config = {"extra": "ignore"}  # silently drop legacy ``privacy_tier`` from on-disk configs


class CaptureOTLPConfig(BaseModel):
    """OTLP receiver capture-source settings (plan 078 + plan 080 §5).

    ``raw_body_retention`` controls what happens to the raw Anthropic
    Messages API JSON files Claude Code drops under
    ``OTEL_LOG_RAW_API_BODIES=file:<dir>`` after the emitter has
    successfully built ContextLayers + appended events. Default
    ``"delete"`` keeps zero raw-body disk footprint per plan 080 §5
    (the storage diet). ``"keep_N_days"`` retains bodies for N days
    (N parsed from the literal, e.g. ``"keep_7_days"``);
    ``"keep_forever"`` retains them indefinitely (debugging / replay).
    """

    raw_body_retention: str = Field(
        "delete",
        description=(
            "One of 'delete' (default), 'keep_N_days' (e.g. "
            "'keep_7_days'), or 'keep_forever'. See plan 080 §5."
        ),
    )

    model_config = {"extra": "ignore"}


class CaptureConfig(BaseModel):
    """Capture-side settings: per-capture-source configuration.

    ``tracking_mode`` controls project enrollment (plan 081). ``"global"``
    (default) auto-enrolls any project an agent session touches — git or
    not — the first time a capture hook fires there, seeding the project
    with the standard private + review-required policy. ``"manual"``
    preserves the explicit per-project ``opentraces init`` opt-in: the
    hook path performs no auto-enrollment.
    """

    otlp: CaptureOTLPConfig = Field(default_factory=CaptureOTLPConfig)
    tracking_mode: Literal["global", "manual"] = "global"

    model_config = {"extra": "ignore"}


class BucketRemoteConfig(BaseModel):
    """Private remote bucket sync target.

    This is workspace infrastructure, not dataset publication. Datasets bind
    their own remotes separately.
    """

    enabled: bool = False
    provider: Literal["huggingface", "fake"] = "huggingface"
    url: str | None = None
    visibility: Literal["private"] = "private"
    sync_policy: Literal["daemon", "manual"] = "daemon"


class BucketContextsConfig(BaseModel):
    """Context Tree bucket projection policy (plan 079).

    The Context Tree substrate ships a content-addressed layer blob
    namespace. Per the adversarial review's Condition 1, dedup across
    projects is intentionally absent by default to avoid a cross-tenant
    correlation channel; opt-in via ``layer_blob_scope="global"``
    writes blobs to a shared ``_shared/`` namespace instead.
    """

    layer_blob_scope: Literal["project", "global"] = "project"


class BucketConfig(BaseModel):
    """Global private bucket storage policy."""

    storage: Literal["local", "remote"] = "local"
    local_cache: bool = True
    remote: BucketRemoteConfig = Field(default_factory=BucketRemoteConfig)
    contexts: BucketContextsConfig = Field(default_factory=BucketContextsConfig)


class PostProcessorConfig(BaseModel):
    """One post-processor entry."""

    name: str
    command: str = Field(description="Executable on PATH, or an absolute path")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class RemoteConfig(BaseModel):
    """One named remote in ``ProjectConfig.remotes``.

    The ``url`` carries the full backend-qualified URL (e.g.
    ``hf://user/dataset``). Short forms like ``user/dataset`` are
    expanded to ``hf://user/dataset`` by the CLI layer before they reach
    this model.
    """

    url: str
    visibility: str = Field("private", pattern="^(public|private)$")


class ProjectConfig(BaseModel):
    """Per-project portable policy (lives in the repo's .opentraces.json).

    The remote model supports multiple named remotes (``remotes``) plus
    a pointer to the active one (``active_remote``). Legacy single
    ``remote`` / ``visibility`` fields from marker_version=1 are
    migrated by ``load_project_config()`` and never written back.
    """

    excluded: bool = False
    review_policy: str = DEFAULT_REVIEW_POLICY
    remotes: dict[str, RemoteConfig] = Field(default_factory=dict)
    active_remote: str | None = None
    default_visibility: str = Field("private", pattern="^(public|private)$")
    agents: list[str] = Field(default_factory=lambda: [DEFAULT_AGENT])
    post_processors: list[PostProcessorConfig] = Field(default_factory=list)

    model_config = {"extra": "ignore"}  # silently drop legacy ``privacy_tier`` from on-disk markers


class ProjectRegistration(BaseModel):
    """One entry in the global registry: maps a path to a stable identity.

    The path key in ``Config.projects`` is the last-known project
    directory. ``project_id`` is the stable identity carried in the
    repo's marker file; ``slug`` is the directory name under
    ``~/.opentraces/projects/``.
    """

    project_id: str
    slug: str


class Config(BaseModel):
    """Root configuration model."""

    config_version: str = CONFIG_VERSION
    hf_token: str | None = None
    projects: dict[str, ProjectRegistration] = Field(default_factory=dict)
    excluded_projects: list[str] = Field(default_factory=list)
    custom_redact_strings: list[str] = Field(default_factory=list)
    projects_path: str | None = Field(
        None,
        description="Override for ~/.claude/projects/ location",
    )
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    bucket: BucketConfig = Field(default_factory=BucketConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)

    model_config = {"extra": "ignore"}  # silently drop dead keys (e.g. legacy pricing_file)
