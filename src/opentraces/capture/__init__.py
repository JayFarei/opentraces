"""Capture layer: agent session parsers, file importers, and install hooks.

Collapses the former ``agents/``, ``parsers/``, and ``installers/`` trees
into a single place. Cross-agent protocols live in :mod:`._base`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import claude_code, hermes

if TYPE_CHECKING:
    from ._base import FormatImporter, SessionParser  # noqa: F401

REGISTRY = {
    "claude_code": claude_code,
    "hermes": hermes,
}

# Live agent session parsers: agent_name -> parser class
PARSERS: dict[str, type] = {}

# File-based importers: format_name -> importer class
IMPORTERS: dict[str, type] = {}

# Accepted aliases for format names (old_name -> canonical_name)
_IMPORT_ALIASES: dict[str, str] = {}


def _register_defaults() -> None:
    from .claude_code import ClaudeCodeParser
    from .hermes import HermesParser

    PARSERS["claude-code"] = ClaudeCodeParser
    IMPORTERS["hermes"] = HermesParser


_registered = False


def get_parsers() -> dict[str, type]:
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return PARSERS


def get_importers() -> dict[str, type]:
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return IMPORTERS


def resolve_import_format(name: str) -> str | None:
    importers = get_importers()
    if name in importers:
        return name
    if name in _IMPORT_ALIASES:
        return _IMPORT_ALIASES[name]
    return None


__all__ = [
    "REGISTRY",
    "PARSERS",
    "IMPORTERS",
    "claude_code",
    "hermes",
    "get_parsers",
    "get_importers",
    "resolve_import_format",
]
