"""Install / remove / verify OpenTraces hooks for Codex CLI."""

from __future__ import annotations

import json
import os
import shlex
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .._base import HookInstallError, HookInstallResult
from .sessions import codex_home

HOOK_SCRIPTS_DIR = Path(__file__).parent / "hooks"

EVENT_SCRIPTS: dict[str, str] = {
    "SessionStart": "on_session_start.py",
    "UserPromptSubmit": "on_user_prompt_submit.py",
    "PreToolUse": "on_pre_tool_use.py",
    "PermissionRequest": "on_permission_request.py",
    "PostToolUse": "on_post_tool_use.py",
    "PreCompact": "on_pre_compact.py",
    "PostCompact": "on_post_compact.py",
    "Stop": "on_stop.py",
}


@dataclass
class InstallPlan:
    event: str
    module: str
    source: Path
    dest: Path


@dataclass
class InstallResult:
    installed: dict[str, str] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    hooks_file: Path | None = None
    plan: list[InstallPlan] = field(default_factory=list)


def _resolve_paths(
    hooks_dir: Path | None = None,
    hooks_file: Path | None = None,
    home: Path | None = None,
) -> tuple[Path, Path]:
    base = codex_home(home)
    target_hooks_dir = Path(hooks_dir) if hooks_dir else base / "hooks" / "opentraces"
    target_hooks_file = Path(hooks_file) if hooks_file else base / "hooks.json"
    return target_hooks_dir, target_hooks_file


def _build_plan(target_hooks_dir: Path) -> list[InstallPlan]:
    plan: list[InstallPlan] = []
    for event, name in EVENT_SCRIPTS.items():
        source = HOOK_SCRIPTS_DIR / name
        if not source.exists():
            raise HookInstallError(
                "MISSING_HOOK_SCRIPT",
                f"Hook script not found: {source}",
            )
        plan.append(
            InstallPlan(
                event=event,
                module=Path(name).stem,
                source=source,
                dest=target_hooks_dir / name,
            )
        )
    return plan


def _load_hooks_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text()
    except OSError as exc:
        raise HookInstallError("HOOKS_READ_ERROR", f"Cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HookInstallError(
            "CORRUPT_HOOKS",
            f"Cannot parse {path}: {exc}. Fix or remove the file before running setup.",
        ) from exc
    if not isinstance(data, dict):
        raise HookInstallError(
            "CORRUPT_HOOKS",
            f"Cannot parse {path}: hooks.json root is not a JSON object.",
        )
    return data


def _hook_command(module_name: str) -> str:
    module = f"opentraces.capture.codex_cli.hooks.{module_name}"
    return f"{shlex.quote(sys.executable)} -m {shlex.quote(module)}"


def _is_opentraces_command(command: object) -> bool:
    return isinstance(command, str) and (
        "/opentraces/" in command
        or "\\opentraces\\" in command
        or "codex_cli/hooks/" in command
        or "codex_cli/hooks\\" in command
        or "opentraces.capture.codex_cli.hooks." in command
    )


def _already_registered(event_hooks: list, command: str) -> bool:
    for entry in event_hooks:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            continue
        for hook in inner:
            if isinstance(hook, dict) and hook.get("command") == command:
                return True
    return False


def _module_registered(event_hooks: list, module_name: str) -> bool:
    """Interpreter-agnostic registration check for status reporting.

    ``_already_registered`` requires a byte-exact match including the full
    interpreter path, so a hook written by one venv reads as missing when a
    different ``opentraces`` (e.g. pipx vs an editable ``.venv``) runs the
    check. For *status* we only care that an opentraces hook for this event
    module is registered, regardless of which interpreter launches it.
    """
    module = f"opentraces.capture.codex_cli.hooks.{module_name}"
    for entry in event_hooks:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            continue
        for hook in inner:
            if not isinstance(hook, dict):
                continue
            command = hook.get("command")
            if _is_opentraces_command(command) and module in command:
                return True
    return False


def _prune_stale_opentraces_hooks(event_hooks: list, command: str) -> list:
    kept = []
    for entry in event_hooks:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        inner = entry.get("hooks")
        if not isinstance(inner, list):
            kept.append(entry)
            continue
        kept_inner = []
        for hook in inner:
            hook_command = hook.get("command") if isinstance(hook, dict) else None
            if _is_opentraces_command(hook_command) and hook_command != command:
                continue
            kept_inner.append(hook)
        if kept_inner:
            kept.append({**entry, "hooks": kept_inner})
    return kept


def plan_install(
    hooks_dir: Path | None = None,
    hooks_file: Path | None = None,
    home: Path | None = None,
) -> tuple[list[InstallPlan], Path]:
    target_hooks_dir, target_hooks_file = _resolve_paths(hooks_dir, hooks_file, home)
    return _build_plan(target_hooks_dir), target_hooks_file


def install(
    hooks_dir: Path | None = None,
    hooks_file: Path | None = None,
    home: Path | None = None,
) -> InstallResult:
    target_hooks_dir, target_hooks_file = _resolve_paths(hooks_dir, hooks_file, home)
    plan = _build_plan(target_hooks_dir)
    config = _load_hooks_file(target_hooks_file)

    target_hooks_dir.mkdir(parents=True, exist_ok=True)
    target_hooks_file.parent.mkdir(parents=True, exist_ok=True)

    result = InstallResult(hooks_file=target_hooks_file, plan=plan)
    for item in plan:
        item.dest.write_text(item.source.read_text())
        mode = item.dest.stat().st_mode
        item.dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        result.installed[item.event] = str(item.dest)

    hooks_cfg = config.setdefault("hooks", {})
    if not isinstance(hooks_cfg, dict):
        raise HookInstallError(
            "CORRUPT_HOOKS",
            f"Cannot parse {target_hooks_file}: hooks field is not an object.",
        )

    for item in plan:
        command = _hook_command(item.module)
        event_hooks = hooks_cfg.setdefault(item.event, [])
        if not isinstance(event_hooks, list):
            raise HookInstallError(
                "CORRUPT_HOOKS",
                f"Cannot parse {target_hooks_file}: {item.event} hooks are not a list.",
            )
        event_hooks = _prune_stale_opentraces_hooks(event_hooks, command)
        hooks_cfg[item.event] = event_hooks
        if _already_registered(event_hooks, command):
            continue
        event_hooks.append(
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": command,
                        "timeout": 15,
                        "statusMessage": "OpenTraces capture",
                    }
                ],
            }
        )
        result.added.append(item.event)

    tmp = target_hooks_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    os.replace(str(tmp), str(target_hooks_file))
    return result


def remove(
    hooks_dir: Path | None = None,
    hooks_file: Path | None = None,
    home: Path | None = None,
) -> HookInstallResult:
    target_hooks_dir, target_hooks_file = _resolve_paths(hooks_dir, hooks_file, home)
    result = HookInstallResult(config_files=[target_hooks_file])

    for name in EVENT_SCRIPTS.values():
        target = target_hooks_dir / name
        if target.exists():
            target.unlink()

    if not target_hooks_file.exists():
        return result

    try:
        config = _load_hooks_file(target_hooks_file)
    except HookInstallError:
        result.ok = False
        return result
    hooks_cfg = config.get("hooks")
    if not isinstance(hooks_cfg, dict):
        return result

    for event in EVENT_SCRIPTS:
        entries = hooks_cfg.get(event)
        if not isinstance(entries, list):
            continue
        kept = []
        for entry in entries:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            if not isinstance(inner, list):
                kept.append(entry)
                continue
            kept_inner = [
                hook
                for hook in inner
                if not _is_opentraces_command(
                    hook.get("command") if isinstance(hook, dict) else None
                )
            ]
            if kept_inner:
                kept.append({**entry, "hooks": kept_inner})
            else:
                result.removed.append(event)
        if kept:
            hooks_cfg[event] = kept
        else:
            hooks_cfg.pop(event, None)

    tmp = target_hooks_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n")
    os.replace(str(tmp), str(target_hooks_file))
    return result


def status(
    hooks_dir: Path | None = None,
    hooks_file: Path | None = None,
    home: Path | None = None,
) -> dict[str, object]:
    target_hooks_dir, target_hooks_file = _resolve_paths(hooks_dir, hooks_file, home)
    scripts_present = {
        event: (target_hooks_dir / name).exists()
        for event, name in EVENT_SCRIPTS.items()
    }
    registered: dict[str, bool] = {}
    try:
        config = _load_hooks_file(target_hooks_file)
    except HookInstallError:
        config = {}
    hooks_cfg = config.get("hooks") if isinstance(config, dict) else {}
    if not isinstance(hooks_cfg, dict):
        hooks_cfg = {}
    for event, name in EVENT_SCRIPTS.items():
        entries = hooks_cfg.get(event) or []
        registered[event] = isinstance(entries, list) and _module_registered(entries, Path(name).stem)
    installed = all(scripts_present.values()) and all(registered.values())
    return {
        "installer": "codex-cli",
        "installed": installed,
        "scripts_present": scripts_present,
        "registered": registered,
        "hooks_dir": str(target_hooks_dir),
        "hooks_file": str(target_hooks_file),
    }


@dataclass
class CodexCliHookInstaller:
    """HookInstaller protocol adapter for Codex CLI."""

    installer_name: str = "codex-cli"
    hooks_dir: Path | None = None
    hooks_file: Path | None = None
    home: Path | None = None

    def plan(self) -> list[dict]:
        plan, _ = plan_install(self.hooks_dir, self.hooks_file, self.home)
        return [
            {"event": item.event, "source": str(item.source), "dest": str(item.dest)}
            for item in plan
        ]

    def install(self) -> HookInstallResult:
        result = install(self.hooks_dir, self.hooks_file, self.home)
        return HookInstallResult(
            ok=True,
            installed=result.installed,
            added=result.added,
            config_files=[result.hooks_file] if result.hooks_file else [],
        )

    def remove(self) -> HookInstallResult:
        return remove(self.hooks_dir, self.hooks_file, self.home)

    def status(self) -> dict:
        return status(self.hooks_dir, self.hooks_file, self.home)
