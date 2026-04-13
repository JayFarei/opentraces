"""Capture layer: agent session parsers, file importers, and install hooks.

Collapses the former ``agents/``, ``parsers/``, and ``installers/`` trees
into a single place. Cross-agent protocols live in :mod:`._base`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import claude_code, hermes

if TYPE_CHECKING:
    from ._base import (  # noqa: F401
        FormatImporter,
        HookInstaller,
        SessionParser,
    )

REGISTRY = {
    "claude_code": claude_code,
    "hermes": hermes,
}

# Live agent session parsers: agent_name -> parser class
PARSERS: dict[str, type] = {}

# File-based importers: format_name -> importer class
IMPORTERS: dict[str, type] = {}

# Hook installers: installer_name -> installer class (HookInstaller protocol)
HOOK_INSTALLERS: dict[str, type] = {}

# Accepted aliases for format names (old_name -> canonical_name)
_IMPORT_ALIASES: dict[str, str] = {}


def _register_defaults() -> None:
    from .claude_code import ClaudeCodeParser
    from .claude_code.install import ClaudeCodeHookInstaller
    from .git.install import GitHookInstaller
    from .hermes import HermesParser

    PARSERS["claude-code"] = ClaudeCodeParser
    IMPORTERS["hermes"] = HermesParser
    HOOK_INSTALLERS["claude-code"] = ClaudeCodeHookInstaller
    HOOK_INSTALLERS["git"] = GitHookInstaller


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


def get_hook_installers() -> dict[str, type]:
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return HOOK_INSTALLERS


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
    "HOOK_INSTALLERS",
    "claude_code",
    "hermes",
    "get_parsers",
    "get_importers",
    "get_hook_installers",
    "resolve_import_format",
]
