"""Configuration management for opentraces.

Storage layout (see paths.py):

* Global, machine-local: ``~/.opentraces/``
    - ``config.json``       — global settings + project registry
    - ``credentials``       — HF token (0600)
    - ``projects/<slug>/``  — per-project runtime state
        * ``traces/*.jsonl`` — captured traces
        * ``state.json``     — runtime bookkeeping (statuses, offsets, commits)
        * ``.lock``          — per-project upload lock

* Per-project, committable: ``<repo>/.opentraces.json``
    - ``project_id``        — stable UUID, source of truth for identity
    - portable policy fields (review_policy, push_policy, agents,
      post_processors, remote, visibility) — meant to travel with the repo

The two trees are linked by ``project_id``. Cloning a repo on a new
machine and running ``opentraces init`` re-registers the same id under a
fresh local ``~/.opentraces/projects/<slug>/`` dir.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal

import click
from pydantic import BaseModel, Field

from .paths import (
    CONFIG_PATH,
    CREDENTIALS_PATH,
    MARKER_FILENAME,
    OPENTRACES_DIR,
    PROJECTS_DIR,
)
from .trace_stage import (
    DEFAULT_AGENT,
    DEFAULT_PUSH_POLICY,
    DEFAULT_REVIEW_POLICY,
    normalize_agents,
    normalize_push_policy,
    normalize_review_policy,
)

logger = logging.getLogger(__name__)


class NotOptedInError(click.ClickException):
    """Raised when a command is run from a directory that has not run
    ``opentraces init``. Click renders ``message`` to stderr and exits 2.
    """

    exit_code = 2

    def __init__(self, project_dir: Path, action: str = "review") -> None:
        self.project_dir = project_dir
        self.action = action
        super().__init__(
            f"opentraces: this project has not opted in to {action}.\n"
            "Run 'opentraces init' here first, only initialized "
            "projects appear in the UI or get pushed upstream."
        )

    def format_message(self) -> str:
        return self.message

    def show(self, file: Any = None) -> None:
        click.echo(self.format_message(), err=True)


def auth_identity(token: str | None) -> dict | None:
    """Return HF whoami dict for *token*, or None on any failure."""
    if not token:
        return None
    try:
        from huggingface_hub import HfApi

        return HfApi(token=token).whoami()
    except Exception:
        return None


CONFIG_VERSION = "0.2.0"
MARKER_VERSION = "2"

# Fields that live inside the committable marker file. Anything outside
# this set is treated as machine-local or transient and not written.
#
# Legacy single-remote fields (`remote`, `visibility`) are not in this list
# anymore; load_project_config() migrates them to `remotes` + `active_remote`
# on read, and save_project_config() never writes them back.
_PORTABLE_FIELDS = (
    "excluded",
    "review_policy",
    "push_policy",
    "remotes",
    "active_remote",
    "default_visibility",
    "agents",
    "post_processors",
    # Plan-043 phase 6: committable repo-identity + first-run decision.
    # `root_commit_sha` is populated by `ot init`; it's the SHA of the
    # first commit and survives `git mv`/relocation. `first_run_backfill_decision`
    # records the user's Y/n/never answer from the init prompt so we
    # don't re-nag every time they re-init.
    "root_commit_sha",
    "first_run_backfill_decision",
)

# Valid values for first_run_backfill_decision. None = not asked yet.
BACKFILL_DECISIONS = ("Y", "declined", "never")


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
    push_policy: str = DEFAULT_PUSH_POLICY
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
    classifier_sensitivity: str = Field("medium", pattern="^(low|medium|high)$")
    dataset_visibility: str = Field("private", pattern="^(public|private)$")
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    bucket: BucketConfig = Field(default_factory=BucketConfig)
    capture: CaptureConfig = Field(default_factory=CaptureConfig)

    model_config = {"extra": "ignore"}  # silently drop dead keys (e.g. legacy pricing_file)


def ensure_dirs() -> None:
    """Create global opentraces directories."""
    for d in [OPENTRACES_DIR, PROJECTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _secure_write(path: Path, data: str) -> None:
    """Write file with 0600 permissions (owner read/write only)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)


def load_config() -> Config:
    """Load config from disk, migrating if version mismatches."""
    ensure_dirs()

    if not CONFIG_PATH.exists():
        config = Config()
        config.hf_token = _resolve_hf_token()
        save_config(config)
        return config

    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read config %s: %s; using defaults", CONFIG_PATH, e)
        config = Config()
        config.hf_token = _resolve_hf_token()
        save_config(config)
        return config

    stored_version = raw.get("config_version", "0.0.0")

    if stored_version != CONFIG_VERSION:
        raw = _migrate_config(raw, stored_version)

    # In-place renames:
    #   security.review_llm -> security.llm_review
    #   review_llm.provider -> review_llm.api_format ("openai" -> "openai-compat")
    # Dead key pricing_file is dropped by Config (extra="ignore") on validate.
    sec = raw.get("security") if isinstance(raw.get("security"), dict) else None
    if isinstance(sec, dict) and "review_llm" in sec and "llm_review" not in sec:
        sec["llm_review"] = sec.pop("review_llm")
    rl = sec.get("llm_review") if isinstance(sec, dict) else None
    if isinstance(rl, dict) and "provider" in rl and "api_format" not in rl:
        legacy = rl.pop("provider")
        rl["api_format"] = "openai-compat" if legacy == "openai" else legacy

    config = Config.model_validate(raw)

    if config.hf_token is None:
        config.hf_token = _resolve_hf_token()

    return config


def _resolve_hf_token() -> str | None:
    """Resolve HF token from env > opentraces credentials > hf CLI cache."""
    token = os.environ.get("HF_TOKEN", "").strip()
    if token:
        return token

    token = os.environ.get("HUGGINGFACE_TOKEN", "").strip()
    if token:
        return token

    if CREDENTIALS_PATH.exists():
        try:
            text = CREDENTIALS_PATH.read_text().strip()
            if text.startswith("hf_"):
                return text
        except OSError as e:
            logger.debug("Could not read credentials file: %s", e)

    hf_cache_token = Path.home() / ".cache" / "huggingface" / "token"
    if hf_cache_token.exists():
        try:
            text = hf_cache_token.read_text().strip()
            if text.startswith("hf_"):
                return text
        except OSError as e:
            logger.debug("Could not read HF cache token: %s", e)

    return None


def save_credentials(token: str) -> None:
    """Save HF token to ~/.opentraces/credentials with 0600 permissions."""
    ensure_dirs()
    _secure_write(CREDENTIALS_PATH, token)


def clear_credentials() -> None:
    """Remove stored HF credentials from both opentraces and huggingface_hub caches.

    The token may live in either location: device-flow login writes to
    ``~/.opentraces/credentials`` via ``save_credentials``, while tokens that
    arrived through ``huggingface-cli login`` or older paths sit in the
    huggingface_hub cache. Logout must clear both or it is a silent no-op.
    """
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()

    try:
        from huggingface_hub import logout as _hf_logout
        _hf_logout()
    except Exception:
        # Fall back to direct file removal if the hf_hub helper is absent or
        # misbehaves — we still want logout to clear the cache.
        try:
            from huggingface_hub.constants import HF_TOKEN_PATH
            hf_token_path = Path(HF_TOKEN_PATH)
        except Exception:
            hf_token_path = Path.home() / ".cache" / "huggingface" / "token"
        for p in (hf_token_path, hf_token_path.parent / "stored_tokens"):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass


def save_config(config: Config) -> None:
    """Save config to disk with secure permissions.

    Never persists hf_token to disk; it should stay in env vars.
    """
    ensure_dirs()
    data = config.model_dump(exclude={"hf_token"})
    _secure_write(CONFIG_PATH, json.dumps(data, indent=2))


def _migrate_config(raw: dict[str, Any], from_version: str) -> dict[str, Any]:
    """Migrate config across versions. One-way, versioned migrations."""
    if from_version < "0.2.0":
        # 0.1.x -> 0.2.0: projects went from {path: ProjectConfig} to
        # {path: ProjectRegistration}. The portable policy fields move
        # into each project's .opentraces.json marker.
        old_projects = raw.get("projects") or {}
        new_projects: dict[str, dict] = {}
        for path_str, old_cfg in old_projects.items():
            project_dir = Path(path_str)
            legacy_policy = old_cfg if isinstance(old_cfg, dict) else {}
            try:
                project_id = _migrate_legacy_to_marker(
                    project_dir, fallback_policy=legacy_policy
                )
            except Exception as e:
                logger.warning(
                    "opentraces: could not migrate %s: %s; minting fresh id",
                    project_dir,
                    e,
                )
                project_id = uuid.uuid4().hex
            new_projects[path_str] = {
                "project_id": project_id,
                "slug": _make_slug(project_dir.name, project_id),
            }
        raw["projects"] = new_projects

    raw["config_version"] = CONFIG_VERSION
    return raw


def get_projects_path(config: Config) -> Path:
    """Get the path to Claude Code projects directory."""
    if config.projects_path:
        return Path(config.projects_path)
    return Path.home() / ".claude" / "projects"


def is_project_excluded(config: Config, project_path: str) -> bool:
    """Check if a project is excluded from trace collection."""
    if project_path in config.excluded_projects:
        return True
    # Excluded flag now lives in the marker file.
    marker = _load_marker_raw(Path(project_path))
    return bool(marker and marker.get("excluded"))


# ---------------------------------------------------------------------------
# Slug + marker helpers
# ---------------------------------------------------------------------------


def _make_slug(basename: str, project_id: str) -> str:
    """Compute a directory-safe slug from a project basename + uuid."""
    base = re.sub(r"[^a-z0-9]+", "-", basename.lower()).strip("-")[:48]
    if not base:
        base = "project"
    return f"{base}-{project_id[:8]}"


def _marker_path(project_dir: Path) -> Path:
    return project_dir / MARKER_FILENAME


def _legacy_local_dir(project_dir: Path) -> Path:
    return project_dir / ".opentraces"


def _load_marker_raw(project_dir: Path) -> dict | None:
    """Read the marker file as a raw dict, or None if missing/invalid.

    Does NOT trigger migration. Use ``_load_marker`` for the migrating
    variant.
    """
    path = _marker_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.debug("Could not read marker %s: %s", path, e)
        return None


def _load_marker(project_dir: Path) -> dict | None:
    """Read marker, migrating from legacy ``.opentraces/`` layout if needed."""
    raw = _load_marker_raw(project_dir)
    if raw is not None:
        return raw
    # No marker — try legacy migration.
    if (_legacy_local_dir(project_dir) / "config.json").exists() or (
        _legacy_local_dir(project_dir) / "config.yml"
    ).exists():
        try:
            _migrate_legacy_to_marker(project_dir)
        except Exception as e:
            logger.warning("opentraces: legacy migration failed for %s: %s", project_dir, e)
            return None
        return _load_marker_raw(project_dir)
    return None


def _write_marker(project_dir: Path, project_id: str, policy: dict) -> None:
    """Write the marker file atomically. ``policy`` carries portable fields."""
    marker = _marker_path(project_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "marker_version": MARKER_VERSION,
        "project_id": project_id,
    }
    for key in _PORTABLE_FIELDS:
        if key in policy:
            payload[key] = policy[key]
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(str(tmp), str(marker))


def _parse_yaml_config(text: str) -> dict:
    """Hand-parse a simple key: value YAML file into a dict."""
    result: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if key == "tier":
                tier = int(value)
                result["review_policy"] = "auto" if tier == 1 else "review"
            elif key == "remote":
                result["remote"] = value
            else:
                result[key] = value
    return result


def _normalize_project_data(data: dict) -> bool:
    """Backfill new project-config keys and normalize values.

    Also migrates legacy single-remote fields (``remote``, ``visibility``)
    into the new ``remotes`` / ``active_remote`` shape. The legacy keys
    are dropped from ``data`` after migration so the saved marker has
    only the new schema.
    """
    modified = False

    legacy_mode = data.get("mode")
    fallback = "auto" if legacy_mode == "auto" else DEFAULT_REVIEW_POLICY
    review_policy = normalize_review_policy(data.get("review_policy") or fallback)
    if data.get("review_policy") != review_policy:
        data["review_policy"] = review_policy
        modified = True

    push_policy = normalize_push_policy(data.get("push_policy"))
    if data.get("push_policy") != push_policy:
        data["push_policy"] = push_policy
        modified = True

    agents = normalize_agents(data.get("agents"))
    if data.get("agents") != agents:
        data["agents"] = agents
        modified = True

    privacy_tier = data.get("privacy_tier")
    if privacy_tier is not None and privacy_tier not in {"off", "low", "medium", "high"}:
        data["privacy_tier"] = "medium"
        modified = True

    for legacy_key in ("tier", "mode"):
        if legacy_key in data:
            del data[legacy_key]
            modified = True

    # Legacy single-remote -> remotes dict migration. Both legacy keys
    # (``remote`` and ``visibility``) are dropped from ``data`` after
    # being absorbed into the new shape.
    legacy_remote = data.pop("remote", None) if "remote" in data else None
    legacy_vis = data.pop("visibility", None) if "visibility" in data else None
    if legacy_remote or legacy_vis:
        modified = True
    if legacy_remote and not data.get("remotes"):
        data["remotes"] = {
            "origin": {
                "url": legacy_remote,
                "visibility": legacy_vis or "private",
            }
        }
        if not data.get("active_remote"):
            data["active_remote"] = "origin"
    elif "remotes" not in data:
        data["remotes"] = {}
    if "active_remote" not in data:
        data["active_remote"] = None
    # Project-level visibility default (used by ``ot remote add`` when
    # no --public/--private is given). Legacy projects with
    # ``visibility`` but no ``remote`` set this so the next remote
    # inherits the user's expressed intent.
    if legacy_vis and "default_visibility" not in data:
        data["default_visibility"] = legacy_vis

    return modified


def _synthesize_legacy_remote_keys(data: dict) -> None:
    """Populate ``data["remote"]`` / ``data["visibility"]`` from the active remote.

    Backward compatibility for callers (cli/__init__.py, cli/publish.py,
    cli/inspect.py, clients/web_server.py, etc.) that still read the
    legacy keys. These synthesized keys are NOT persisted by
    ``save_project_config()`` — they exist only in the in-memory dict
    returned to callers during the transition to the new schema. Step 4
    of the CLI restructure migrates each caller; this shim disappears
    when the last one is updated.
    """
    active = data.get("active_remote")
    remotes = data.get("remotes") or {}
    if active and active in remotes:
        cfg = remotes[active]
        # cfg may be a dict (loaded from JSON) or a RemoteConfig model.
        if isinstance(cfg, dict):
            data["remote"] = cfg.get("url")
            data["visibility"] = cfg.get("visibility", data.get("default_visibility", "private"))
        else:
            data["remote"] = cfg.url
            data["visibility"] = cfg.visibility
    else:
        # No active remote — expose the project-level default so callers
        # that read data["visibility"] still see the expressed intent.
        data["visibility"] = data.get("default_visibility", "private")


def _migrate_legacy_to_marker(
    project_dir: Path, fallback_policy: dict | None = None
) -> str:
    """Move legacy ``<proj>/.opentraces/`` content into the new layout.

    Idempotent. Returns the project_id (existing or newly minted).
    """
    legacy_dir = _legacy_local_dir(project_dir)
    marker = _marker_path(project_dir)

    # If marker already exists, just return its id.
    if marker.exists():
        existing = _load_marker_raw(project_dir) or {}
        pid = existing.get("project_id")
        if pid:
            return pid

    # Read legacy policy (json preferred, then yaml, then fallback).
    legacy_policy: dict = {}
    legacy_json = legacy_dir / "config.json"
    legacy_yaml = legacy_dir / "config.yml"
    if legacy_json.exists():
        try:
            legacy_policy = json.loads(legacy_json.read_text())
        except (json.JSONDecodeError, OSError):
            legacy_policy = {}
    elif legacy_yaml.exists():
        try:
            legacy_policy = _parse_yaml_config(legacy_yaml.read_text())
        except OSError:
            legacy_policy = {}

    if not legacy_policy and fallback_policy:
        legacy_policy = dict(fallback_policy)

    _normalize_project_data(legacy_policy)

    project_id = legacy_policy.pop("project_id", None) or uuid.uuid4().hex
    slug = _make_slug(project_dir.name, project_id)

    # Write the new marker.
    _write_marker(project_dir, project_id, legacy_policy)

    # Move runtime state into the per-project global dir.
    target_dir = PROJECTS_DIR / slug
    target_traces = target_dir / "traces"
    target_traces.mkdir(parents=True, exist_ok=True)

    legacy_state = legacy_dir / "state.json"
    target_state = target_dir / "state.json"
    if legacy_state.exists() and not target_state.exists():
        try:
            shutil.move(str(legacy_state), str(target_state))
        except OSError as e:
            logger.warning("opentraces: could not move %s: %s", legacy_state, e)

    legacy_staging = legacy_dir / "staging"
    if legacy_staging.exists():
        for f in legacy_staging.iterdir():
            if not f.is_file():
                continue
            dest = target_traces / f.name
            if dest.exists():
                continue
            try:
                shutil.move(str(f), str(dest))
            except OSError as e:
                logger.warning("opentraces: could not move %s: %s", f, e)

    # Drop a breadcrumb so the user can clean up the old dir.
    if legacy_dir.exists():
        try:
            (legacy_dir / "MIGRATED.txt").write_text(
                f"opentraces state migrated to {target_dir}\n"
                f"this directory is safe to delete.\n"
            )
        except OSError:
            pass

    logger.info(
        "opentraces: migrated %s -> %s (project_id=%s)",
        legacy_dir,
        target_dir,
        project_id[:8],
    )
    return project_id


# ---------------------------------------------------------------------------
# Public per-project API
# ---------------------------------------------------------------------------


def project_is_opted_in(project_dir: Path) -> bool:
    """Single source of truth: has this directory run ``opentraces init``?

    The presence of the ``.opentraces.json`` marker file is the ground
    truth. Triggers a one-time migration from the legacy
    ``<proj>/.opentraces/config.json`` layout if present.
    """
    if _marker_path(project_dir).is_file():
        return True
    # Try legacy migration.
    return _load_marker(project_dir) is not None


def _project_id_for(project_dir: Path) -> str:
    """Return the project_id from the marker. Raises if not opted in."""
    marker = _load_marker(project_dir)
    if not marker or not marker.get("project_id"):
        raise NotOptedInError(project_dir)
    return marker["project_id"]


def _project_slug_for(project_dir: Path) -> str:
    return _make_slug(project_dir.name, _project_id_for(project_dir))


def get_project_dir(project_dir: Path) -> Path:
    """Return ``~/.opentraces/projects/<slug>/`` for this project."""
    return PROJECTS_DIR / _project_slug_for(project_dir)


def get_project_traces_dir(project_dir: Path) -> Path:
    """Return the per-project traces directory (creates parents on use)."""
    return get_project_dir(project_dir) / "traces"


def get_project_state_path(project_dir: Path) -> Path:
    """Return the per-project state.json path."""
    return get_project_dir(project_dir) / "state.json"


def register_project(config: Config, project_dir: Path) -> bool:
    """Add ``project_dir`` to the global opted-in registry.

    Generates a project_id and writes the marker if missing. Idempotent.
    Caller is responsible for ``save_config()``. Returns True if the
    config object changed.
    """
    key = str(project_dir.resolve())

    marker = _load_marker(project_dir)
    if marker is None:
        # Mint a fresh marker with default policy.
        project_id = uuid.uuid4().hex
        _write_marker(project_dir, project_id, {})
    elif marker.get("project_id"):
        project_id = marker["project_id"]
    else:
        # Marker exists but carries no project_id (legal for a bare
        # ``{"excluded": true}`` opt-out). Registration is an explicit
        # enrollment act, so mint an id here — but preserve the existing
        # portable policy fields rather than resetting them.
        project_id = uuid.uuid4().hex
        _write_marker(project_dir, project_id, marker)

    slug = _make_slug(project_dir.name, project_id)
    new_reg = ProjectRegistration(project_id=project_id, slug=slug)

    # Ensure global per-project dirs exist and carry the project identity
    # sidecar used by watcher/project discovery. This is part of enrollment,
    # not only explicit ``opentraces init``: global auto-enroll needs the same
    # manifest so the project can be swept and backfilled later.
    slug_dir = PROJECTS_DIR / slug
    (slug_dir / "traces").mkdir(parents=True, exist_ok=True)
    try:
        from .repo_identity import root_commit_sha, write_project_identity

        write_project_identity(
            slug_dir,
            project_dir=project_dir,
            root_sha=root_commit_sha(project_dir),
        )
    except Exception:
        logger.debug(
            "opentraces: could not write project identity for %s",
            project_dir,
            exc_info=True,
        )

    existing = config.projects.get(key)
    if existing == new_reg:
        return False
    config.projects[key] = new_reg
    return True


def _global_capture_agents() -> list[str]:
    """Agents enrolled by default under global tracking mode.

    Pi is included so global tracking (the default) auto-enrolls Pi the same way
    it does Claude/Codex, the first time a Pi capture event fires in a project.
    Capture stays opt-out: ``tracking_mode = manual`` or a per-project
    ``excluded`` marker turns it off, and raw provider bodies remain default-off
    (the ``OPENTRACES_PI_RETAIN_RAW_PROVIDER_BODIES`` opt-in is separate).
    """
    return normalize_agents([DEFAULT_AGENT, "codex-cli", "pi"])


def _merge_agent_lists(*agent_lists: list[str]) -> list[str]:
    merged: list[str] = []
    for agent in normalize_agents([
        agent
        for agents in agent_lists
        for agent in agents
    ]):
        if agent not in merged:
            merged.append(agent)
    return merged or [DEFAULT_AGENT]


def _ensure_global_capture_agents(project_dir: Path) -> bool:
    """Backfill legacy global markers so scans see every built-in agent."""
    data = load_project_config(project_dir)
    current = normalize_agents(data.get("agents"))
    desired = _global_capture_agents()
    merged = _merge_agent_lists(current, desired)
    if merged == current:
        return False
    data["agents"] = merged
    save_project_config(project_dir, data)
    return True


def auto_enroll_if_global(project_dir: Path) -> bool:
    """Enroll ``project_dir`` if global tracking mode is active (plan 081).

    Also repairs marker/registry drift for projects that already have a
    ``.opentraces.json`` marker. That repair is safe in manual mode because it
    does not enroll an unmarked project; it restores the machine-local registry
    and watcher manifest for a project that has already opted in.

    No-op (returns False) when the unmarked project is in manual mode or on any
    unexpected error. Enrollment goes through ``register_project``, so new
    projects inherit the standard private + review-required marker policy.
    Returns True when registry/manifest state changed.

    Best-effort by contract: this runs on the capture-hook hot path and
    must never raise into the agent, so all failures are swallowed.
    """
    try:
        config = load_config()
        # Per-project opt-out is absolute: an excluded project is never
        # enrolled and its marker is never mutated from the capture hot
        # path (no agent backfill, no project_id minting, no registry
        # write). ``is_project_excluded`` reads the marker raw, so the
        # check itself cannot trigger a migrating write.
        if is_project_excluded(config, str(project_dir.resolve())):
            return False
        if project_is_opted_in(project_dir):
            changed = False
            if config.capture.tracking_mode == "global":
                changed = _ensure_global_capture_agents(project_dir)
            if register_project(config, project_dir):
                save_config(config)
                changed = True
            return changed
        if config.capture.tracking_mode != "global":
            return False
        changed = _ensure_global_capture_agents(project_dir)
        if register_project(config, project_dir):
            save_config(config)
            changed = True
        return changed
    except Exception:
        logger.debug(
            "auto_enroll_if_global failed for %s", project_dir, exc_info=True
        )
        return False


def unregister_project(config: Config, project_dir: Path) -> bool:
    """Remove ``project_dir`` from the registry. Returns True if changed.

    Does NOT delete the local marker file or the global per-project
    state directory; callers can choose what to clean up.
    """
    key = str(project_dir.resolve())
    if key not in config.projects:
        return False
    del config.projects[key]
    return True


def opted_in_projects(config: Config) -> list[str]:
    """Return absolute paths of all registered projects, sorted."""
    return sorted(config.projects.keys())


def load_project_config(project_dir: Path) -> dict:
    """Read project portable policy as a dict.

    Returns at least the policy defaults if no marker is present (matches
    legacy contract — callers expect a dict, not None).
    """
    marker = _load_marker(project_dir)
    if marker is None:
        return {
            "review_policy": DEFAULT_REVIEW_POLICY,
            "push_policy": DEFAULT_PUSH_POLICY,
            "agents": [DEFAULT_AGENT],
        }

    # Pull both new and legacy fields out of the on-disk marker so
    # _normalize_project_data can migrate them.
    data = {k: marker[k] for k in _PORTABLE_FIELDS if k in marker}
    if "remote" in marker:
        data["remote"] = marker["remote"]
    if "visibility" in marker:
        data["visibility"] = marker["visibility"]

    changed = _normalize_project_data(data)
    if changed and marker.get("project_id"):
        # Persist normalized values back into the marker. A marker without
        # a project_id (e.g. a bare committed ``{"excluded": true}``
        # opt-out) is legal: normalize in memory only — persisting would
        # mean minting an id, and reads must never mutate such a marker.
        _write_marker(project_dir, marker["project_id"], data)

    # Synthesize legacy ``remote``/``visibility`` keys for back-compat
    # with callers that haven't been migrated yet (step 4).
    _synthesize_legacy_remote_keys(data)
    return data


def save_project_config(project_dir: Path, data: dict) -> None:
    """Write project portable policy into the ``.opentraces.json`` marker.

    Preserves an existing project_id if the marker already exists; mints
    a fresh one otherwise. Accepts either the new shape (``remotes`` /
    ``active_remote``) or the legacy shape (``remote`` / ``visibility``);
    legacy input is migrated before write so the on-disk marker has only
    the new keys.
    """
    existing = _load_marker(project_dir)
    if existing and existing.get("project_id"):
        project_id = existing["project_id"]
    else:
        project_id = uuid.uuid4().hex

    # Defensive copy so we don't mutate the caller's dict.
    payload = dict(data)
    _normalize_project_data(payload)
    _write_marker(project_dir, project_id, payload)


# ---------------------------------------------------------------------------
# Plan-043 phase 6: root_commit_sha + first_run_backfill_decision helpers
# ---------------------------------------------------------------------------


def get_root_commit_sha(project_dir: Path) -> str | None:
    """Return the marker's recorded ``root_commit_sha`` or None."""
    marker = _load_marker(project_dir)
    if not marker:
        return None
    v = marker.get("root_commit_sha")
    return v if isinstance(v, str) and v else None


def set_root_commit_sha(project_dir: Path, sha: str | None) -> None:
    """Persist ``root_commit_sha`` into the marker, preserving other fields."""
    marker = _load_marker_raw(project_dir) or {}
    project_id = marker.get("project_id") or uuid.uuid4().hex
    # Build policy from existing marker's portable fields.
    policy = {k: marker[k] for k in _PORTABLE_FIELDS if k in marker}
    if sha:
        policy["root_commit_sha"] = sha
    else:
        policy.pop("root_commit_sha", None)
    _write_marker(project_dir, project_id, policy)


def get_first_run_backfill_decision(project_dir: Path) -> str | None:
    """Return ``"Y" | "declined" | "never" | None``."""
    marker = _load_marker(project_dir)
    if not marker:
        return None
    v = marker.get("first_run_backfill_decision")
    return v if v in BACKFILL_DECISIONS else None


def set_first_run_backfill_decision(project_dir: Path, decision: str | None) -> None:
    """Persist the backfill-prompt decision. ``None`` clears the field
    (will re-prompt next init). Non-standard values raise ``ValueError``."""
    if decision is not None and decision not in BACKFILL_DECISIONS:
        raise ValueError(
            f"invalid backfill decision: {decision!r} "
            f"(expected one of {BACKFILL_DECISIONS} or None)"
        )
    marker = _load_marker_raw(project_dir) or {}
    project_id = marker.get("project_id") or uuid.uuid4().hex
    policy = {k: marker[k] for k in _PORTABLE_FIELDS if k in marker}
    if decision:
        policy["first_run_backfill_decision"] = decision
    else:
        policy.pop("first_run_backfill_decision", None)
    _write_marker(project_dir, project_id, policy)


# ---------------------------------------------------------------------------
# Plan-080 Phase C: OTLP raw-body retention accessor
# ---------------------------------------------------------------------------


def get_capture_otlp_raw_body_retention(cfg: Config) -> str:
    """Return the raw-body retention policy literal.

    One of ``"delete"`` (default; remove the JSON file after the
    emitter has built a layer + appended events), ``"keep_N_days"``
    (e.g. ``"keep_7_days"`` — sweep on age), or ``"keep_forever"``
    (no-op sweep; bodies retained indefinitely).

    Per plan 080 §5: the default eliminates raw-body disk footprint
    while preserving the opt-in TTL for replay / debugging use cases.
    """
    raw = (cfg.capture.otlp.raw_body_retention or "delete").strip()
    if not raw:
        return "delete"
    return raw


def parse_raw_body_retention_days(retention: str) -> int | None:
    """Parse ``keep_N_days`` -> N. Returns None for ``delete`` / ``keep_forever``.

    Raises ``ValueError`` if the literal is malformed (e.g. ``keep_abc_days``
    or ``keep_0_days``). Whitespace around the value is tolerated by the
    accessor; this helper expects a normalized literal.
    """
    if retention in ("delete", "keep_forever"):
        return None
    if retention.startswith("keep_") and retention.endswith("_days"):
        middle = retention[len("keep_"): -len("_days")]
        try:
            n = int(middle)
        except ValueError as exc:
            raise ValueError(
                f"invalid raw_body_retention {retention!r}: "
                "expected 'keep_N_days' with integer N"
            ) from exc
        if n < 1:
            raise ValueError(
                f"invalid raw_body_retention {retention!r}: N must be >= 1"
            )
        return n
    raise ValueError(
        f"unknown raw_body_retention {retention!r}: "
        "expected 'delete' | 'keep_N_days' | 'keep_forever'"
    )
