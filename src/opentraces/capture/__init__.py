"""Capture layer: agent session parsers, file importers, and install hooks.

Collapses the former ``agents/``, ``parsers/``, and ``installers/`` trees
into a single place. Cross-agent protocols live in :mod:`._base`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from . import claude_code, codex_cli, hermes

if TYPE_CHECKING:
    from ._base import (  # noqa: F401
        FormatImporter,
        AgentResumer,
        HookInstaller,
        SessionParser,
    )

REGISTRY = {
    "claude_code": claude_code,
    "codex_cli": codex_cli,
    "hermes": hermes,
}

# Live agent session parsers: agent_name -> parser class
PARSERS: dict[str, type] = {}

# File-based importers: format_name -> importer class
IMPORTERS: dict[str, type] = {}

# Hook installers: installer_name -> installer class (HookInstaller protocol)
HOOK_INSTALLERS: dict[str, type] = {}

# Agent resumers: agent_name -> resumer class (AgentResumer protocol)
RESUMERS: dict[str, type] = {}

# Accepted aliases for format names (old_name -> canonical_name)
_IMPORT_ALIASES: dict[str, str] = {}
_AGENT_ALIASES: dict[str, str] = {
    "claude": "claude-code",
    "codex": "codex-cli",
}


def _registry_key(name: str) -> str:
    key = name.strip().replace("_", "-").lower()
    return _AGENT_ALIASES.get(key, key)


def _name_for(cls: type, explicit: str | None, attr: str) -> str:
    name = explicit or getattr(cls, attr, "")
    key = _registry_key(str(name))
    if not key:
        raise ValueError(f"cannot register {cls!r}: missing {attr}")
    return key


def _register_unique(registry: dict[str, type], key: str, cls: type, kind: str) -> type:
    existing = registry.get(key)
    if existing is cls:
        return cls
    if existing is not None:
        raise ValueError(
            f"{kind} {key!r} is already registered to "
            f"{existing.__module__}.{existing.__name__}"
        )
    registry[key] = cls
    return cls


def register_parser(parser_cls: type, name: str | None = None) -> type:
    """Register a live session parser class by ``agent_name``.

    Registration is idempotent for the same class and rejects accidental
    duplicate ownership by a different class.
    """
    return _register_unique(
        PARSERS,
        _name_for(parser_cls, name, "agent_name"),
        parser_cls,
        "parser",
    )


def register_importer(importer_cls: type, name: str | None = None) -> type:
    return _register_unique(
        IMPORTERS,
        _name_for(importer_cls, name, "format_name"),
        importer_cls,
        "importer",
    )


def register_hook_installer(installer_cls: type, name: str | None = None) -> type:
    return _register_unique(
        HOOK_INSTALLERS,
        _name_for(installer_cls, name, "installer_name"),
        installer_cls,
        "hook installer",
    )


def register_resumer(resumer_cls: type, name: str | None = None) -> type:
    return _register_unique(
        RESUMERS,
        _name_for(resumer_cls, name, "agent_name"),
        resumer_cls,
        "resumer",
    )


class _ClaudeCodeResumer:
    agent_name = "claude-code"
    supports_at_step = True

    def resume_session(
        self,
        session_id: str,
        *,
        project_cwd: Path,
        dry_run: bool = False,
    ) -> int:
        from ..core.agent_resume import resume_claude_code

        return resume_claude_code(
            session_id,
            project_cwd=project_cwd,
            dry_run=dry_run,
        )

    def resolve_at_step(
        self,
        trace_id_prefix: str,
        step_id: str,
        staging: Path,
        *,
        project_cwd: Path,
        state: object,
        materialize: bool = True,
    ) -> object:
        from .claude_code.resume import resolve_at_step

        return resolve_at_step(
            trace_id_prefix,
            step_id,
            staging,
            project_cwd=project_cwd,
            state=state,
            materialize=materialize,
        )


def _register_defaults() -> None:
    import importlib

    claude_module = importlib.import_module(".claude_code", __name__)
    claude_parser = getattr(claude_module, "Claude" "CodeParser")
    from .claude_code.install import ClaudeCodeHookInstaller
    from .codex_cli import CodexCliParser, CodexCliResumer
    from .codex_cli.install import CodexCliHookInstaller
    from .git.install import GitHookInstaller
    from .hermes import HermesParser
    from .skill.install import SkillInstaller

    register_parser(claude_parser)
    register_parser(CodexCliParser)
    register_importer(HermesParser)
    register_hook_installer(ClaudeCodeHookInstaller)
    register_hook_installer(CodexCliHookInstaller)
    register_hook_installer(GitHookInstaller)
    register_hook_installer(SkillInstaller)
    register_resumer(_ClaudeCodeResumer)
    register_resumer(CodexCliResumer)


_registered = False


def get_parsers() -> dict[str, type]:
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return PARSERS


def get_parser(agent_name: str) -> type:
    parsers = get_parsers()
    key = _registry_key(agent_name)
    try:
        return parsers[key]
    except KeyError as exc:
        raise KeyError(f"no parser registered for {agent_name!r}") from exc


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


def get_resumers() -> dict[str, type]:
    global _registered
    if not _registered:
        _register_defaults()
        _registered = True
    return RESUMERS


def get_resumer(agent_name: str) -> type | None:
    return get_resumers().get(_registry_key(agent_name))


def session_id_from_path(agent_name: str, session_path: Path) -> str:
    parser = get_parser(agent_name)()
    derive = getattr(parser, "session_id_from_path", None)
    if callable(derive):
        return str(derive(session_path))
    return session_path.stem


def discover_project_sessions(
    project_dir: Path,
    *,
    agent_names: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[str, Path]]:
    """Discover registered parser sessions for a single project.

    Parsers can provide ``discover_project_sessions(project_dir)`` for
    harness-specific storage. Claude keeps its historical root-commit-aware
    discovery until its parser grows that optional method.
    """
    parsers = get_parsers()
    selected = (
        [_registry_key(name) for name in agent_names]
        if agent_names
        else list(parsers)
    )
    out: list[tuple[str, Path]] = []

    for agent_name in selected:
        parser_cls = parsers.get(agent_name)
        if parser_cls is None:
            continue
        parser = parser_cls()
        discover = getattr(parser, "discover_project_sessions", None)
        if callable(discover):
            out.extend(
                (agent_name, Path(path))
                for path in discover(Path(project_dir))
            )
            continue

        if agent_name == "claude-code":
            from ..core.repo_identity import discover_claude_jsonl_corpus

            out.extend(
                ("claude-code", path)
                for path in discover_claude_jsonl_corpus(Path(project_dir))
            )

    return sorted(out, key=lambda item: (item[0], str(item[1])))


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
    "RESUMERS",
    "claude_code",
    "codex_cli",
    "hermes",
    "register_parser",
    "register_importer",
    "register_hook_installer",
    "register_resumer",
    "get_parsers",
    "get_parser",
    "get_importers",
    "get_hook_installers",
    "get_resumers",
    "get_resumer",
    "session_id_from_path",
    "discover_project_sessions",
    "resolve_import_format",
]
