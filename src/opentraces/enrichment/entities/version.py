"""Pinned version + manifest path for the entity-diff binary."""
from __future__ import annotations

from pathlib import Path

ENTITY_BINARY_VERSION = "0.3.19"
MANIFEST_PATH = Path(__file__).parent / "manifest.json"
