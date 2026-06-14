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


def repair_installed_integrations(cwd: Path | None = None) -> dict[str, Any]:
    """Re-render only integrations that are already installed."""
    cwd = Path(cwd or Path.cwd())
    repaired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    try:
        from ..watcher import installer as watcher_installer

        st = watcher_installer.status()
        if st.installed:
            path = watcher_installer.install(interval=st.interval_seconds or 300)
            repaired.append({"name": "watcher", "path": str(path)})
        else:
            skipped.append({"name": "watcher", "reason": "not-installed"})
    except Exception as exc:  # noqa: BLE001 - repair must report all failures.
        errors.append({"name": "watcher", "error": str(exc)})

    try:
        from ..capture import get_hook_installers

        for name, cls in get_hook_installers().items():
            inst = _hook_installer(name, cls, cwd)
            try:
                status = inst.status()
                if not status.get("installed"):
                    skipped.append({"name": name, "reason": "not-installed"})
                    continue
                result = inst.install()
                repaired.append({"name": name, **_result_status(True, result)})
            except Exception as exc:  # noqa: BLE001
                errors.append({"name": name, "error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        errors.append({"name": "hook-registry", "error": str(exc)})

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

    return {
        "status": "ok" if not errors else "warning",
        "repaired": repaired,
        "skipped": skipped,
        "errors": errors,
    }


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
