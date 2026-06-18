"""Inventory and repair installed opentraces integration glue."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .integration_versions import current_cli_version, read_version_stamp, version_drift


def _result_status(ok: bool, result: object | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": ok}
    if result is None:
        return payload
    for attr in ("installed", "added", "removed", "config_files", "notes"):
        value = getattr(result, attr, None)
        if value:
            if attr == "config_files":
                value = [str(path) for path in value]
            payload[attr] = value
    return payload


def _hook_installer(name: str, cls: type, cwd: Path):
    if name == "git":
        return cls(repo=cwd)
    if name == "pi":
        return cls(cwd=cwd)
    return cls()


def repair_installed_integrations(
    cwd: Path | None = None,
    *,
    interpreter: str | None = None,
    dry_run: bool = False,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """Re-render installed integration glue. Idempotent, never touches data.

    Default call (``interpreter=None, dry_run=False, only=None``) is the legacy
    ``setup upgrade --integrations-only`` behaviour, byte-unchanged: re-render
    every integration whose ``status().installed`` is true, OTLP included.

    Issue #99 adds three keyword-only knobs for the ``setup runtime`` path:

    * ``interpreter`` — when set, the chosen interpreter is threaded EXPLICITLY
      into every hook/watcher render path via ``selected_interpreter`` (a
      contextvar consulted inside ``stable_interpreter`` with precedence over
      the renderer's local default — adversarial BLOCKER 1). OTLP is EXCLUDED
      from runtime selection in v1 (a long-lived supervised daemon whose
      interpreter is orthogonal to the hook/watcher glue) and recorded as a
      ``skipped`` row with ``reason == "excluded-from-runtime-selection"``.
    * ``dry_run`` — return the PLAN (one ``{name, action, target_interpreter,
      config_files}`` row per integration) WITHOUT calling any ``install()``.
    * ``only`` — restrict re-render to this set of integration names (registry
      keys plus the literal ``"watcher"``). When given, the named integrations
      are re-rendered REGARDLESS of ``status().installed`` (the runtime-switch
      re-renders exactly the runners doctor DISCOVERED, which on a genuinely
      mixed machine may be configured-but-partially-installed). The watcher
      re-render is SHIM-ONLY (no launchd/systemd mutation; see
      ``watcher.installer.rerender_shim``).
    """
    from ..capture._interpreter import selected_interpreter, stable_interpreter

    cwd = Path(cwd or Path.cwd())
    repaired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    plan: list[dict[str, Any]] = []

    def _wanted(name: str) -> bool:
        return only is None or name in only

    runtime_switch = interpreter is not None

    # --- watcher ----------------------------------------------------------
    try:
        from ..watcher import installer as watcher_installer

        st = watcher_installer.status()
        watcher_present = st.installed or (
            runtime_switch and watcher_installer.shim_exists()
        )
        if not _wanted("watcher"):
            pass
        elif not watcher_present:
            skipped.append({"name": "watcher", "reason": "not-installed"})
        elif dry_run:
            target = stable_interpreter(interpreter) if runtime_switch else None
            plan.append({
                "name": "watcher",
                "action": "re-render-shim",
                "target_interpreter": target,
                "config_files": [str(watcher_installer.shim_path())],
            })
        elif runtime_switch:
            # Runtime switch: re-render the shim ONLY (no launchctl/systemctl,
            # no plist/unit rewrite). #65 made the watcher one-shot-per-sweep,
            # so the supervisor picks up the new shim on its next interval.
            with selected_interpreter(interpreter):
                path = watcher_installer.rerender_shim()
            repaired.append({"name": "watcher", "path": str(path),
                             "action": "re-render-shim"})
        else:
            path = watcher_installer.install(interval=st.interval_seconds or 300)
            repaired.append({"name": "watcher", "path": str(path)})
    except Exception as exc:  # noqa: BLE001 - repair must report all failures.
        errors.append({"name": "watcher", "error": str(exc)})

    # --- codex / claude / git / pi / skill hooks --------------------------
    try:
        from ..capture import get_hook_installers

        for name, cls in get_hook_installers().items():
            if not _wanted(name):
                continue
            inst = _hook_installer(name, cls, cwd)
            try:
                status = inst.status()
                # Runtime switch (only=...) re-renders the DISCOVERED runner
                # even when it is not fully installed; the legacy path keeps the
                # status gate. For git specifically the discovered runner is the
                # owned post-commit shim FILE, which can exist even when the dir
                # is not a fully-initialised git repo (the installer's
                # ``install()`` would bail) — re-render that file in place.
                git_owned = None
                if name == "git" and runtime_switch:
                    from ..capture.git.install import owned_hook_path

                    git_owned = owned_hook_path(cwd)
                render = (
                    bool(status.get("installed"))
                    or (runtime_switch and only is not None and name in only
                        and name != "git")
                    or (name == "git" and git_owned is not None)
                )
                if not render:
                    skipped.append({"name": name, "reason": "not-installed"})
                    continue
                if dry_run:
                    target = stable_interpreter(interpreter) if runtime_switch else None
                    if name == "git" and git_owned is not None:
                        config_files = [str(git_owned)]
                    else:
                        rows = inst.plan() if hasattr(inst, "plan") else []
                        config_files = sorted({r.get("dest") for r in rows if r.get("dest")})
                    plan.append({
                        "name": name,
                        "action": "re-render",
                        "target_interpreter": target,
                        "config_files": config_files,
                    })
                    continue
                if name == "git" and runtime_switch:
                    from ..capture.git.install import rerender_owned_hook

                    with selected_interpreter(interpreter):
                        path = rerender_owned_hook(cwd)
                    repaired.append({"name": name, "ok": path is not None,
                                     "config_files": [str(path)] if path else [],
                                     "action": "re-render"})
                elif runtime_switch:
                    with selected_interpreter(interpreter):
                        result = inst.install()
                    repaired.append({"name": name, **_result_status(True, result)})
                else:
                    result = inst.install()
                    repaired.append({"name": name, **_result_status(True, result)})
            except Exception as exc:  # noqa: BLE001
                errors.append({"name": name, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        errors.append({"name": "hook-registry", "error": str(exc)})

    # --- OTLP -------------------------------------------------------------
    if runtime_switch:
        # Excluded from runtime selection in v1 (documented honest gap).
        if _wanted("capture-otlp") or only is None:
            skipped.append({
                "name": "capture-otlp",
                "action": "skipped",
                "reason": "excluded-from-runtime-selection",
            })
            if dry_run:
                plan.append({
                    "name": "capture-otlp",
                    "action": "skipped",
                    "target_interpreter": None,
                    "reason": "excluded-from-runtime-selection",
                    "config_files": [],
                })
    else:
        try:
            from ..capture.otlp import lifecycle, settings_patcher

            settings_installed = settings_patcher.is_installed()
            autostart_installed = lifecycle.is_installed()
            if settings_installed:
                result = settings_patcher.install_otel_env()
                repaired.append({
                    "name": "capture-otlp-settings",
                    "ok": result.ok,
                    "settings_path": str(result.settings_path),
                    "keys_added": result.keys_added,
                    "keys_skipped": result.keys_skipped,
                    "reason": result.reason,
                })
            else:
                skipped.append({"name": "capture-otlp-settings", "reason": "not-installed"})
            if autostart_installed:
                result = lifecycle.install_autostart()
                repaired.append({
                    "name": "capture-otlp-autostart",
                    "ok": result.ok,
                    "platform": result.platform,
                    "path": str(result.path) if result.path else None,
                    "reason": result.reason,
                })
            else:
                skipped.append({"name": "capture-otlp-autostart", "reason": "not-installed"})
        except Exception as exc:  # noqa: BLE001
            errors.append({"name": "capture-otlp", "error": str(exc)})

    out: dict[str, Any] = {
        "status": "ok" if not errors else "warning",
        "repaired": repaired,
        "skipped": skipped,
        "errors": errors,
    }
    if dry_run:
        out["dry_run"] = True
        out["plan"] = plan
    return out


def integration_version_report(
    *,
    watcher: dict[str, Any] | None = None,
    hooks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a compact doctor summary of deployed-vs-running CLI versions."""
    cli_version = current_cli_version()
    items: list[dict[str, Any]] = []

    watcher = watcher or {}
    watcher_prov = watcher.get("provenance") or {}
    if watcher.get("installed") or watcher_prov.get("shim_exists"):
        shim_version = watcher_prov.get("shim_version")
        drift = list(watcher_prov.get("drift") or [])
        items.append({
            "name": "watcher",
            "installed": bool(watcher.get("installed")),
            "deployed_version": shim_version,
            "cli_version": cli_version,
            "drift": drift,
        })

    for hook in hooks or []:
        name = str(hook.get("installer") or "?")
        if not hook.get("installed") and name != "skill":
            continue
        if name == "skill":
            deployed_version = hook.get("installed_version")
            drift: list[str] = []
            if hook.get("installed"):
                if hook.get("drift"):
                    drift.append("version-drift")
                if hook.get("broken_harnesses"):
                    drift.append("broken-harness")
        else:
            deployed_version = hook.get("deployed_version")
            drift = list(hook.get("drift") or [])
        items.append({
            "name": name,
            "installed": bool(hook.get("installed")),
            "deployed_version": deployed_version,
            "cli_version": hook.get("cli_version") or cli_version,
            "drift": drift,
        })

    try:
        from ..capture.otlp import lifecycle

        if lifecycle.is_installed():
            path = lifecycle.autostart_path()
            version = read_version_stamp(path) if path else None
            items.append({
                "name": "capture-otlp-autostart",
                "installed": True,
                "deployed_version": version,
                "cli_version": cli_version,
                "drift": version_drift(version, cli_version),
            })
    except Exception:
        pass

    return {
        "cli_version": cli_version,
        "items": items,
        "drift": [item for item in items if item.get("drift")],
    }
