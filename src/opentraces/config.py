"""Configuration management for opentraces.

State persisted to ~/.opentraces/config.json with chmod 0600.
Supports config version migration between releases.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

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
    OPENTRACES_DIR,
    STAGING_DIR,
    STATE_PATH,
    UPLOADED_DIR,
)

CONFIG_VERSION = "0.1.0"


class ProjectConfig(BaseModel):
    """Per-project configuration override."""

    excluded: bool = False
    review_policy: str = DEFAULT_REVIEW_POLICY
    push_policy: str = DEFAULT_PUSH_POLICY
    remote: str | None = None
    visibility: str = "private"
    agents: list[str] = Field(default_factory=lambda: [DEFAULT_AGENT])


class Config(BaseModel):
    """Root configuration model."""

    config_version: str = CONFIG_VERSION
    hf_token: str | None = None
    projects: dict[str, ProjectConfig] = Field(default_factory=dict)
    excluded_projects: list[str] = Field(default_factory=list)
    custom_redact_strings: list[str] = Field(default_factory=list)
    pricing_file: str | None = None
    projects_path: str | None = Field(
        None,
        description="Override for ~/.claude/projects/ location",
    )
    classifier_sensitivity: str = Field("medium", pattern="^(low|medium|high)$")
    dataset_visibility: str = Field("private", pattern="^(public|private)$")


def ensure_dirs() -> None:
    """Create opentraces directories with appropriate permissions."""
    for d in [OPENTRACES_DIR, STAGING_DIR, UPLOADED_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _secure_write(path: Path, data: str) -> None:
    """Write file with 0600 permissions (owner read/write only).

    Uses os.open with O_CREAT to avoid TOCTOU race where the file is
    briefly world-readable between creation and chmod.
    """
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

    # Token resolution chain: env var > opentraces credentials > huggingface-cli token
    if config.hf_token is None:
        config.hf_token = _resolve_hf_token()

    return config



def _resolve_hf_token() -> str | None:
    """Resolve HF token from multiple sources in priority order."""
    # 1. Environment variable (highest priority, CI/CD)
    token = os.environ.get("HF_TOKEN")
    if token:
        return token

    # 2. opentraces-managed credentials file
    if CREDENTIALS_PATH.exists():
        try:
            text = CREDENTIALS_PATH.read_text().strip()
            if text.startswith("hf_"):
                return text
        except OSError as e:
            logger.debug("Could not read credentials file: %s", e)

    # 3. huggingface-cli login token (fallback)
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

    Never persists hf_token to disk, it should stay in env vars.
    """
    ensure_dirs()
    data = config.model_dump(exclude={"hf_token"})
    _secure_write(CONFIG_PATH, json.dumps(data, indent=2))


def _migrate_config(raw: dict[str, Any], from_version: str) -> dict[str, Any]:
    """Migrate config from older versions. One-way, versioned migrations."""
    # v0.0.0 -> v0.1.0: initial version, no migration needed
    # Future migrations go here as elif chains
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
    proj = config.projects.get(project_path)
    return bool(proj and proj.excluded)


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
            # Legacy tier -> review_policy migration
            if key == "tier":
                tier = int(value)
                result["review_policy"] = "auto" if tier == 1 else "review"
            elif key == "remote":
                result["remote"] = value
            else:
                result[key] = value
    return result


def _normalize_project_data(data: dict) -> bool:
    """Backfill new project config keys and normalize values."""
    modified = False

    # Legacy mode -> review_policy migration
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

    # Strip legacy keys
    for legacy_key in ("tier", "mode"):
        if legacy_key in data:
            del data[legacy_key]
            modified = True

    return modified


def load_project_config(project_dir: Path) -> dict:
    """Read project config from .opentraces/config.json (or migrate from .yml).

    Returns at least a 'mode' key (defaults to "review" if no config found).
    """
    config_dir = project_dir / ".opentraces"
    json_file = config_dir / "config.json"
    yml_file = config_dir / "config.yml"

    data: dict | None = None

    # Prefer JSON
    if json_file.exists():
        try:
            data = json.loads(json_file.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Could not read project config %s: %s", json_file, e)
            data = None

    # Fall back to YAML, migrate if found
    if data is None and yml_file.exists():
        try:
            text = yml_file.read_text()
            data = _parse_yaml_config(text)
            # Migrate: write JSON, rename YAML to .bak
            config_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = json_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2))
            os.replace(str(tmp_path), str(json_file))
            os.replace(str(yml_file), str(yml_file) + ".bak")
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("YAML migration failed for %s: %s", yml_file, e)

    if data is None:
        return {
            "review_policy": DEFAULT_REVIEW_POLICY,
            "push_policy": DEFAULT_PUSH_POLICY,
            "agents": [DEFAULT_AGENT],
        }

    # Migrate legacy keys and normalize values
    changed = _normalize_project_data(data)

    if changed:
        try:
            tmp_path = json_file.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(data, indent=2))
            os.replace(str(tmp_path), str(json_file))
        except OSError as e:
            logger.debug("Could not save normalized config %s: %s", json_file, e)

    return data


def save_project_config(project_dir: Path, data: dict) -> None:
    """Write project config as .opentraces/config.json (atomic write)."""
    config_dir = project_dir / ".opentraces"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.json"
    tmp_path = config_file.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2))
    os.replace(str(tmp_path), str(config_file))


def get_project_staging_dir(project_dir: Path) -> Path:
    """Return the project-local staging directory."""
    return project_dir / ".opentraces" / "staging"


def get_project_state_path(project_dir: Path) -> Path:
    """Return the project-local state.json path."""
    return project_dir / ".opentraces" / "state.json"
