"""CLI hooks group: manage Claude Code hook integration."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import click

from opentraces import cli as _cli
from . import main

logger = logging.getLogger("opentraces.cli.hooks")


def emit_json(data):
    return _cli.emit_json(data)


def error_response(*a, **k):
    return _cli.error_response(*a, **k)


def human_echo(*a, **k):
    return _cli.human_echo(*a, **k)


def human_hint(*a, **k):
    return _cli.human_hint(*a, **k)


# ---------------------------------------------------------------------------
# hooks command group
# ---------------------------------------------------------------------------

@main.group()
def hooks() -> None:
    """Manage Claude Code hooks for richer session capture."""


@hooks.command("install")
@click.option(
    "--hooks-dir",
    default=None,
    help="Target directory for hook scripts (default: ~/.claude/hooks/)",
)
@click.option(
    "--settings-file",
    default=None,
    help="Claude Code settings file to update (default: ~/.claude/settings.json)",
)
@click.option("--dry-run", is_flag=True, help="Print what would be done without making changes.")
def hooks_install(hooks_dir: str | None, settings_file: str | None, dry_run: bool) -> None:
    """Install opentraces hooks into ~/.claude/hooks/ and register them in settings.json.

    The Stop hook appends a git-state snapshot to each session transcript.
    The PostCompact hook records explicit compaction events.
    Both are picked up automatically by the opentraces parser.
    """
    import shlex
    import stat

    # Resolve paths
    claude_dir = Path.home() / ".claude"
    target_hooks_dir = Path(hooks_dir) if hooks_dir else claude_dir / "hooks"
    target_settings = Path(settings_file) if settings_file else claude_dir / "settings.json"

    # Source hook scripts are shipped with the package
    src_hooks_dir = Path(__file__).parent.parent / "capture" / "claude_code" / "hooks"
    hook_scripts = {
        "Stop": src_hooks_dir / "on_stop.py",
        "PostCompact": src_hooks_dir / "on_compact.py",
    }

    # Validate source scripts exist before touching anything
    for event, script_path in hook_scripts.items():
        if not script_path.exists():
            emit_json(error_response("MISSING_HOOK_SCRIPT", "install",
                                     f"Hook script not found: {script_path}"))
            sys.exit(5)

    if dry_run:
        plan = []
        for event, script_path in hook_scripts.items():
            dest = target_hooks_dir / f"opentraces_{script_path.name}"
            plan.append({"event": event, "source": str(script_path), "dest": str(dest)})
        human_echo("[dry-run] Would install hooks:")
        for p in plan:
            human_echo(f"  {p['event']}: {p['source']} -> {p['dest']}")
        human_echo(f"[dry-run] Would update: {target_settings}")
        emit_json({
            "status": "ok",
            "dry_run": True,
            "plan": plan,
            "settings_file": str(target_settings),
        })
        return

    # Refuse to clobber an existing settings.json that we cannot parse -
    # silently replacing it with {} would destroy unrelated Claude config.
    settings: dict = {}
    if target_settings.exists():
        try:
            raw = target_settings.read_text()
            settings = json.loads(raw)
            if not isinstance(settings, dict):
                raise ValueError("settings.json root is not a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            emit_json(error_response(
                "CORRUPT_SETTINGS", "install",
                f"Cannot parse {target_settings}: {e}. "
                "Fix or remove the file before running hooks install.",
            ))
            sys.exit(5)
        except OSError as e:
            emit_json(error_response("SETTINGS_READ_ERROR", "install",
                                     f"Cannot read {target_settings}: {e}"))
            sys.exit(5)

    # Create hooks directory
    target_hooks_dir.mkdir(parents=True, exist_ok=True)

    installed: dict[str, str] = {}
    for event, script_path in hook_scripts.items():
        dest = target_hooks_dir / f"opentraces_{script_path.name}"
        dest.write_text(script_path.read_text())
        current_mode = dest.stat().st_mode
        dest.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed[event] = str(dest)
        human_echo(f"Installed: {dest}")

    # Merge hook registrations - path-safe quoting, append only if not already present
    hooks_cfg = settings.setdefault("hooks", {})
    added: list[str] = []
    for event, dest_path in installed.items():
        command = f"python3 {shlex.quote(dest_path)}"
        event_hooks = hooks_cfg.setdefault(event, [])
        already_registered = any(
            h.get("command") == command
            for h in event_hooks
            if isinstance(h, dict)
        )
        if not already_registered:
            event_hooks.append({"type": "command", "command": command})
            added.append(event)

    _tmp = target_settings.with_suffix(".json.tmp")
    _tmp.write_text(json.dumps(settings, indent=2))
    os.replace(str(_tmp), str(target_settings))

    if added:
        human_echo(f"Registered hooks in {target_settings}: {', '.join(added)}")
    else:
        human_echo(f"Hooks already registered in {target_settings}, no changes needed.")

    emit_json({
        "status": "ok",
        "installed": installed,
        "settings_file": str(target_settings),
        "hooks_added": added,
        "next_steps": [
            "Hooks are now active for all future Claude Code sessions.",
            "Re-run 'opentraces push' after sessions to include enriched data.",
        ],
    })


# ---------------------------------------------------------------------------
# Plan 041: git-commit-anchored traces
# ---------------------------------------------------------------------------

