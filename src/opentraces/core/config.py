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

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from .workflow import (
    DEFAULT_AGENT,
    DEFAULT_PUSH_POLICY,
    DEFAULT_REVIEW_POLICY,
    normalize_agents,
    normalize_push_policy,
    normalize_review_policy,
)


def auth_identity(token: str | None) -> dict | None:
    """Return HF whoami dict for *token*, or None on any failure."""
    if not token:
        return None
    try:
        from huggingface_hub import HfApi

        return HfApi(token=token).whoami()
    except Exception:
        return None


from .paths import (
    CONFIG_PATH,
    CREDENTIALS_PATH,
    MARKER_FILENAME,
    OPENTRACES_DIR,
    PROJECTS_DIR,
)

CONFIG_VERSION = "0.2.0"
MARKER_VERSION = "1"

# Fields that live inside the committable marker file. Anything outside
# this set is treated as machine-local or transient and not written.
_PORTABLE_FIELDS = (
    "excluded",
    "review_policy",
    "push_policy",
    "remote",
    "visibility",
    "agents",
    "post_processors",
)


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


class ReviewLLMConfig(BaseModel):
    """Global settings for the opt-in third-party LLM review step."""

    enabled: bool = False
    provider: Literal["openai", "ollama", "anthropic", "fake"] = "openai"
    base_url: str = "http://localhost:11434/v1"
    model: str = "gemma3n:e4b"
    api_key_env: str = Field("", description="Env var holding the API key")
    timeout: float = 120.0
    prompt_version: str = "1"


class SecurityConfig(BaseModel):
    """Root security-module config tree (Plan 032)."""

    trufflehog: TruffleHogConfig = Field(default_factory=TruffleHogConfig)
    review_llm: ReviewLLMConfig = Field(default_factory=ReviewLLMConfig)


class PostProcessorConfig(BaseModel):
    """One post-processor entry."""

    name: str
    command: str = Field(description="Executable on PATH, or an absolute path")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class ProjectConfig(BaseModel):
    """Per-project portable policy (lives in the repo's .opentraces.json)."""

    excluded: bool = False
    review_policy: str = DEFAULT_REVIEW_POLICY
    push_policy: str = DEFAULT_PUSH_POLICY
    remote: str | None = None
    visibility: str = "private"
    agents: list[str] = Field(default_factory=lambda: [DEFAULT_AGENT])
    post_processors: list[PostProcessorConfig] = Field(default_factory=list)


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
    pricing_file: str | None = None
    projects_path: str | None = Field(
        None,
        description="Override for ~/.claude/projects/ location",
    )
    classifier_sensitivity: str = Field("medium", pattern="^(low|medium|high)$")
    dataset_visibility: str = Field("private", pattern="^(public|private)$")
    security: SecurityConfig = Field(default_factory=SecurityConfig)


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
        save_config(config)
        return config

    try:
        raw = json.loads(CONFIG_PATH.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read config %s: %s; using defaults", CONFIG_PATH, e)
        config = Config()
        save_config(config)
        return config

    stored_version = raw.get("config_version", "0.0.0")

    if stored_version != CONFIG_VERSION:
        raw = _migrate_config(raw, stored_version)

    config = Config.model_validate(raw)

    if config.hf_token is None:
        config.hf_token = _resolve_hf_token()

    return config


def _resolve_hf_token() -> str | None:
    """Resolve HF token from env > opentraces credentials > huggingface-cli."""
    token = os.environ.get("HF_TOKEN")
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
    """Remove stored HF credentials."""
    if CREDENTIALS_PATH.exists():
        CREDENTIALS_PATH.unlink()


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
    """Backfill new project-config keys and normalize values."""
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

    for legacy_key in ("tier", "mode"):
        if legacy_key in data:
            del data[legacy_key]
            modified = True

    return modified


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
        raise RuntimeError(
            f"{project_dir} is not opted in to opentraces "
            f"(no {MARKER_FILENAME} marker). Run `opentraces init` first."
        )
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
    else:
        project_id = marker["project_id"]

    slug = _make_slug(project_dir.name, project_id)
    new_reg = ProjectRegistration(project_id=project_id, slug=slug)

    # Ensure global per-project dirs exist.
    (PROJECTS_DIR / slug / "traces").mkdir(parents=True, exist_ok=True)

    existing = config.projects.get(key)
    if existing == new_reg:
        return False
    config.projects[key] = new_reg
    return True


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

    data = {k: marker[k] for k in _PORTABLE_FIELDS if k in marker}
    changed = _normalize_project_data(data)
    if changed:
        # Persist normalized values back into the marker.
        _write_marker(project_dir, marker["project_id"], data)
    return data


def save_project_config(project_dir: Path, data: dict) -> None:
    """Write project portable policy into the ``.opentraces.json`` marker.

    Preserves an existing project_id if the marker already exists; mints
    a fresh one otherwise.
    """
    existing = _load_marker(project_dir)
    if existing and existing.get("project_id"):
        project_id = existing["project_id"]
    else:
        project_id = uuid.uuid4().hex
    _write_marker(project_dir, project_id, data)
