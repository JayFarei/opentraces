"""Shared path constants. No internal imports to avoid circular dependencies."""

from pathlib import Path

OPENTRACES_DIR = Path.home() / ".opentraces"
CONFIG_PATH = OPENTRACES_DIR / "config.json"
CREDENTIALS_PATH = OPENTRACES_DIR / "credentials"
STATE_PATH = OPENTRACES_DIR / "state.json"
STAGING_DIR = OPENTRACES_DIR / "staging"
UPLOADED_DIR = OPENTRACES_DIR / "uploaded"
