"""Configuration management for opentraces.

State persisted to ~/.opentraces/config.json with chmod 0600.
Supports config version migration between releases.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

CONFIG_VERSION = "0.1.0"
OPENTRACES_DIR = Path.home() / ".opentraces"
CONFIG_PATH = OPENTRACES_DIR / "config.json"
STATE_PATH = OPENTRACES_DIR / "state.json"
STAGING_DIR = OPENTRACES_DIR / "staging"
UPLOADED_DIR = OPENTRACES_DIR / "uploaded"


class ProjectConfig(BaseModel):
    """Per-project configuration override."""

    tier: int = 3
    excluded: bool = False


class Config(BaseModel):
    """Root configuration model."""

    config_version: str = CONFIG_VERSION
    hf_token: str | None = None
    default_tier: int = Field(3, ge=1, le=3)
    projects: dict[str, ProjectConfig] = Field(default_factory=dict)
    excluded_projects: list[str] = Field(default_factory=list)
    custom_redact_strings: list[str] = Field(default_factory=list)
    pricing_file: str | None = None
    dataset_name_template: str = "{username}/opentraces-claude-code"
    projects_path: str | None = Field(
        None,
        description="Override for ~/.claude/projects/ location",
    )
    classifier_sensitivity: str = Field("medium", pattern="^(low|medium|high)$")


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

    raw = json.loads(CONFIG_PATH.read_text())
    stored_version = raw.get("config_version", "0.0.0")

    if stored_version != CONFIG_VERSION:
        raw = _migrate_config(raw, stored_version)

    config = Config.model_validate(raw)

    # Pick up HF token from env if not in config
    if config.hf_token is None:
        config.hf_token = os.environ.get("HF_TOKEN")

    return config


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


def get_tier_for_project(config: Config, project_path: str) -> int:
    """Get the security tier for a specific project."""
    if project_path in config.excluded_projects:
        return -1  # Excluded

    proj = config.projects.get(project_path)
    if proj and proj.excluded:
        return -1

    if proj:
        return proj.tier

    return config.default_tier


def get_dataset_name(config: Config, username: str) -> str:
    """Get the HF dataset repo name for a user."""
    return config.dataset_name_template.replace("{username}", username)


def load_project_config(project_dir: Path) -> dict:
    """Read .opentraces/config.yml from a project directory and return a dict.

    Returns at least a 'tier' key (defaults to 3 if file missing or unparseable).
    """
    config_file = project_dir / ".opentraces" / "config.yml"
    result: dict = {"tier": 3}
    if not config_file.exists():
        return result
    try:
        text = config_file.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#") or not line:
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key == "tier":
                    result["tier"] = int(value)
                elif key == "remote":
                    result["remote"] = value
                else:
                    result[key] = value
    except Exception:
        pass
    return result


def save_project_config(project_dir: Path, data: dict) -> None:
    """Write .opentraces/config.yml with the given dict values."""
    config_file = project_dir / ".opentraces" / "config.yml"
    lines = [
        "# opentraces configuration",
        "# https://opentraces.ai/docs/security-tiers",
    ]
    for key, value in data.items():
        lines.append(f"{key}: {value}")
    config_file.write_text("\n".join(lines) + "\n")


def get_project_staging_dir(project_dir: Path) -> Path:
    """Return the project-local staging directory."""
    return project_dir / ".opentraces" / "staging"


def get_project_state_path(project_dir: Path) -> Path:
    """Return the project-local state.json path."""
    return project_dir / ".opentraces" / "state.json"
