"""Install / plan opentraces hooks into Claude Code's settings.json.

Pure adapter logic, no CLI/IO surface. Mirrors the pattern in
`capture/git/install.py`. The CLI command `opentraces setup claude-code`
(in `cli/installers.py`) is a thin wrapper that delegates here, and
`ClaudeCodeHookInstaller` below satisfies the `HookInstaller` protocol
from `_base.py` so doctor and the setup wizard can treat every adapter
uniformly.

Claude Code's settings.json hook schema uses a matcher envelope:

    "hooks": {
      "Stop": [
        {"matcher"?: "<tool>", "hooks": [{"type": "command", "command": "..."}]}
      ]
    }

Writing a bare `{"type": "command", "command": "..."}` at the event-array
level produces `hooks: Expected array, but received undefined` and causes
Claude Code to skip the entire settings.json — so the envelope is load-bearing.
"""
from __future__ import annotations

import json
import os
import shlex
import stat
from dataclasses import dataclass, field
from pathlib import Path

from ...core.integration_versions import (
    current_cli_version,
    read_version_stamp,
    stamp_script,
    version_drift,
)
from .._base import HookInstallError, HookInstallResult, HookInstaller  # noqa: F401
from .._interpreter import stable_interpreter

HOOK_SCRIPTS_DIR = Path(__file__).parent / "hooks"

EVENT_SCRIPTS: dict[str, str] = {
    "PreToolUse": "on_pre_tool_use.py",
    "PostToolUse": "on_tool_use.py",
    "Stop": "on_stop.py",
    "PostCompact": "on_compact.py",
}


# Alias kept for backwards-compatible imports; use HookInstallError in new code.
InstallError = HookInstallError


@dataclass
class InstallPlan:
    event: str
    source: Path
    dest: Path


@dataclass
class InstallResult:
    installed: dict[str, str] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    settings_file: Path | None = None
    plan: list[InstallPlan] = field(default_factory=list)


def _resolve_paths(
    hooks_dir: Path | None, settings_file: Path | None
) -> tuple[Path, Path]:
    claude_dir = Path.home() / ".claude"
    th = Path(hooks_dir) if hooks_dir else claude_dir / "hooks"
    ts = Path(settings_file) if settings_file else claude_dir / "settings.json"
    return th, ts


def _build_plan(target_hooks_dir: Path) -> list[InstallPlan]:
    plan: list[InstallPlan] = []
    for event, name in EVENT_SCRIPTS.items():
        src = HOOK_SCRIPTS_DIR / name
        if not src.exists():
            raise InstallError(
                "MISSING_HOOK_SCRIPT",
                f"Hook script not found: {src}",
            )
        dest = target_hooks_dir / f"opentraces_{name}"
        plan.append(InstallPlan(event=event, source=src, dest=dest))
    return plan


def _load_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = path.read_text()
    except OSError as e:
        raise InstallError(
            "SETTINGS_READ_ERROR", f"Cannot read {path}: {e}"
        ) from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise InstallError(
            "CORRUPT_SETTINGS",
            f"Cannot parse {path}: {e}. "
            "Fix or remove the file before running 'opentraces setup claude-code'.",
        ) from e
    if not isinstance(data, dict):
        raise InstallError(
            "CORRUPT_SETTINGS",
            f"Cannot parse {path}: settings.json root is not a JSON object. "
            "Fix or remove the file before running 'opentraces setup claude-code'.",
        )
    return data


def _already_registered(event_hooks: list, command: str) -> bool:
    """True if `command` is registered under this event in any supported shape."""
    for entry in event_hooks:
        if not isinstance(entry, dict):
            continue
        inner = entry.get("hooks")
        if isinstance(inner, list):
            if any(
                isinstance(h, dict) and h.get("command") == command
                for h in inner
            ):
                return True
        elif entry.get("command") == command:
            # Legacy bare-command entry written by older installers.
            return True
    return False


def _hook_command(script: str) -> str:
    py = stable_interpreter()
    return f"{shlex.quote(py)} {shlex.quote(script)}"


def _is_stale_opentraces_command(cmd: object, command: str) -> bool:
    """True iff ``cmd`` is a DIFFERENT opentraces hook command to supersede.

    Matches both the script-path form this installer writes (``opentraces_<event>``)
    AND the module form (``-m opentraces...`` / ``opentraces.capture...``) that an
    older install — or a different runtime — may have left, so re-rendering to a
    selected runtime (issue #99) supersedes the prior interpreter's hook rather
    than stacking a second one for the same event.
    """
    if not isinstance(cmd, str) or cmd == command:
        return False
    return (
        "opentraces_" in cmd
        or "-m opentraces" in cmd
        or "opentraces.capture" in cmd
        or "/opentraces/" in cmd
    )


def _prune_stale_opentraces_hooks(event_hooks: list, command: str) -> list:
    """Drop older opentraces hook commands for this event while preserving others."""
    kept = []
    for entry in event_hooks:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        inner = entry.get("hooks")
        if isinstance(inner, list):
            kept_inner = []
            for hook in inner:
                hook_command = hook.get("command") if isinstance(hook, dict) else None
                if _is_stale_opentraces_command(hook_command, command):
                    continue
                kept_inner.append(hook)
            if kept_inner:
                kept.append({**entry, "hooks": kept_inner})
            continue
        entry_command = entry.get("command")
        if _is_stale_opentraces_command(entry_command, command):
            continue
        kept.append(entry)
    return kept


def plan_install(
    hooks_dir: Path | None = None,
    settings_file: Path | None = None,
) -> tuple[list[InstallPlan], Path]:
    """Return the install plan without writing anything."""
    th, ts = _resolve_paths(hooks_dir, settings_file)
    return _build_plan(th), ts


def install(
    hooks_dir: Path | None = None,
    settings_file: Path | None = None,
) -> InstallResult:
    """Copy hook scripts and register them in settings.json. Idempotent."""
    th, ts = _resolve_paths(hooks_dir, settings_file)
    plan = _build_plan(th)
    # Load + validate BEFORE touching the fs, so a corrupt settings.json
    # aborts without leaving hook scripts half-installed.
    settings = _load_settings(ts)

    th.mkdir(parents=True, exist_ok=True)

    result = InstallResult(settings_file=ts, plan=plan)
    for p in plan:
        p.dest.write_text(stamp_script(p.source.read_text()))
        mode = p.dest.stat().st_mode
        p.dest.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        result.installed[p.event] = str(p.dest)

    hooks_cfg = settings.setdefault("hooks", {})
    for p in plan:
        command = _hook_command(result.installed[p.event])
        event_hooks = _prune_stale_opentraces_hooks(
            hooks_cfg.setdefault(p.event, []),
            command,
        )
        hooks_cfg[p.event] = event_hooks
        if _already_registered(event_hooks, command):
            continue
        event_hooks.append(
            {"hooks": [{"type": "command", "command": command}]}
        )
        result.added.append(p.event)

    tmp = ts.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2))
    os.replace(str(tmp), str(ts))

    return result


def remove(
    hooks_dir: Path | None = None,
    settings_file: Path | None = None,
) -> HookInstallResult:
    """Unregister opentraces hooks from settings.json and delete scripts."""
    th, ts = _resolve_paths(hooks_dir, settings_file)
    result = HookInstallResult(config_files=[ts])

    for event, name in EVENT_SCRIPTS.items():
        dest = th / f"opentraces_{name}"
        if dest.exists():
            dest.unlink()
            result.removed.append(event)

    if not ts.exists():
        return result
    try:
        settings = _load_settings(ts)
    except HookInstallError:
        result.ok = False
        return result
    hooks_cfg = settings.get("hooks") or {}
    changed = False
    for event in EVENT_SCRIPTS:
        entries = hooks_cfg.get(event) or []
        kept = []
        for entry in entries:
            inner = entry.get("hooks") if isinstance(entry, dict) else None
            if isinstance(inner, list):
                kept_inner = [
                    h for h in inner
                    if not (isinstance(h, dict)
                            and "opentraces_" in (h.get("command") or ""))
                ]
                if kept_inner:
                    kept.append({**entry, "hooks": kept_inner})
                else:
                    changed = True
            elif isinstance(entry, dict) and "opentraces_" in (entry.get("command") or ""):
                changed = True
            else:
                kept.append(entry)
        if kept != entries:
            changed = True
            hooks_cfg[event] = kept
    if changed:
        tmp = ts.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings, indent=2))
        os.replace(str(tmp), str(ts))
    return result


def status(
    hooks_dir: Path | None = None,
    settings_file: Path | None = None,
) -> dict:
    """Report current install state for `opentraces doctor`."""
    th, ts = _resolve_paths(hooks_dir, settings_file)
    scripts_present: dict[str, bool] = {}
    script_versions: dict[str, str | None] = {}
    for event, name in EVENT_SCRIPTS.items():
        dest = th / f"opentraces_{name}"
        scripts_present[event] = dest.exists()
        script_versions[event] = read_version_stamp(dest) if dest.exists() else None

    registered: dict[str, bool] = {e: False for e in EVENT_SCRIPTS}
    if ts.exists():
        try:
            settings = _load_settings(ts)
        except HookInstallError:
            settings = {}
        hooks_cfg = settings.get("hooks") or {}
        for event in EVENT_SCRIPTS:
            for entry in hooks_cfg.get(event) or []:
                inner = entry.get("hooks") if isinstance(entry, dict) else None
                cmds: list[str] = []
                if isinstance(inner, list):
                    cmds = [h.get("command", "") for h in inner if isinstance(h, dict)]
                elif isinstance(entry, dict):
                    cmds = [entry.get("command", "")]
                if any("opentraces_" in c for c in cmds):
                    registered[event] = True
                    break

    installed = all(scripts_present.values()) and all(registered.values())
    deployed_versions = sorted({v for v in script_versions.values() if v})
    deployed_version = deployed_versions[0] if len(deployed_versions) == 1 else None
    drift: list[str] = []
    if installed:
        if any(script_versions.get(event) is None for event in EVENT_SCRIPTS):
            drift.append("version-missing")
        if any(version_drift(version) == ["version-drift"] for version in script_versions.values() if version):
            drift.append("version-drift")
    return {
        "installer": "claude-code",
        "installed": installed,
        "scripts_present": scripts_present,
        "script_versions": script_versions,
        "deployed_version": deployed_version,
        "cli_version": current_cli_version(),
        "drift": drift,
        "registered": registered,
        "hooks_dir": str(th),
        "settings_file": str(ts),
    }


@dataclass
class ClaudeCodeHookInstaller:
    """HookInstaller protocol adapter for Claude Code.

    Binds to (hooks_dir, settings_file) at construction. Defaults resolve
    to ~/.claude/hooks and ~/.claude/settings.json.
    """

    installer_name: str = "claude-code"
    hooks_dir: Path | None = None
    settings_file: Path | None = None

    def plan(self) -> list[dict]:
        plan, _ = plan_install(self.hooks_dir, self.settings_file)
        return [
            {"event": p.event, "source": str(p.source), "dest": str(p.dest)}
            for p in plan
        ]

    def install(self) -> HookInstallResult:
        r = install(self.hooks_dir, self.settings_file)
        return HookInstallResult(
            ok=True,
            installed=r.installed,
            added=r.added,
            config_files=[r.settings_file] if r.settings_file else [],
        )

    def remove(self) -> HookInstallResult:
        return remove(self.hooks_dir, self.settings_file)

    def status(self) -> dict:
        return status(self.hooks_dir, self.settings_file)
