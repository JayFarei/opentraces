"""Agent session parsers and file-based importers.

Registries map names to classes (not instances) for lazy instantiation.
The CLI imports these registries inside command functions to avoid
eager loading of parser dependencies at module scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import FormatImporter, SessionParser

# Live agent session parsers: agent_name -> parser class
# Instantiate on access: PARSERS["claude-code"]()
PARSERS: dict[str, type] = {}

# File-based importers: format_name -> importer class
IMPORTERS: dict[str, type] = {}

# Accepted aliases for format names (old_name -> canonical_name)
_IMPORT_ALIASES: dict[str, str] = {}


def _register_defaults() -> None:
    """Register built-in parsers and importers.

    Called lazily on first access to avoid importing concrete modules
    at package import time.
    """
    from .claude_code import ClaudeCodeParser
    from .hermes import HermesParser

    PARSERS["claude-code"] = ClaudeCodeParser
    IMPORTERS["hermes"] = HermesParser


_registered = False


def get_parsers() -> dict[str, type]:
    """Get the PARSERS registry, registering defaults on first call."""
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return PARSERS


def get_importers() -> dict[str, type]:
    """Get the IMPORTERS registry, registering defaults on first call."""
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return IMPORTERS


def resolve_import_format(name: str) -> str | None:
    """Resolve a format name, handling aliases. Returns canonical name or None."""
    importers = get_importers()
    if name in importers:
        return name
    aliases = _IMPORT_ALIASES
    if name in aliases:
        return aliases[name]
    return None
