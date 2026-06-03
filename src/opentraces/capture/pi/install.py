"""Install / verify the OpenTraces Pi package."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .._base import HookInstallError, HookInstallResult

PACKAGE_NAME = "opentraces-pi"
PACKAGE_SPEC = f"npm:{PACKAGE_NAME}"
LOCAL_PACKAGE_DIR = Path(__file__).resolve().parents[4] / "packages" / "opentraces-pi"


def pi_settings_path(*, project: bool = False, cwd: Path | None = None, home: Path | None = None) -> Path:
    if project:
        return (cwd or Path.cwd()) / ".pi" / "settings.json"
    base = Path(home if home is not None else os.environ.get("HOME", "~")).expanduser()
    return base / ".pi" / "agent" / "settings.json"


def _load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HookInstallError("CORRUPT_PI_SETTINGS", f"Cannot parse {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise HookInstallError("CORRUPT_PI_SETTINGS", f"Pi settings root is not an object: {path}")
    return raw


def _package_identity(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        source = entry.get("source")
        return str(source) if source else None
    return None


def _is_opentraces_pi_entry(entry: Any) -> bool:
    source = _package_identity(entry) or ""
    return source in {PACKAGE_SPEC, PACKAGE_NAME} or source.startswith(f"npm:{PACKAGE_NAME}@") or source.endswith("packages/opentraces-pi")


def _target_spec(local: bool = False) -> str:
    if local and LOCAL_PACKAGE_DIR.exists():
        return str(LOCAL_PACKAGE_DIR)
    return PACKAGE_SPEC


def _write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(str(tmp), str(path))


def plan_install(
    *,
    project: bool = False,
    settings_file: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    local: bool = False,
) -> tuple[list[dict[str, Any]], Path]:
    target = settings_file or pi_settings_path(project=project, cwd=cwd, home=home)
    spec = _target_spec(local=local)
    return ([{"event": "pi-package", "source": spec, "dest": str(target)}], target)


def status(
    *,
    project: bool = False,
    settings_file: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    path = settings_file or pi_settings_path(project=project, cwd=cwd, home=home)
    pi_binary = shutil.which("pi")
    try:
        settings = _load_settings(path)
        corrupt = False
    except HookInstallError as exc:
        settings = {}
        corrupt = True
        error = exc.message
    else:
        error = None
    packages = settings.get("packages") if isinstance(settings.get("packages"), list) else []
    installed_entries = [entry for entry in packages if _is_opentraces_pi_entry(entry)]
    return {
        "installer": "pi",
        "installed": bool(installed_entries),
        "package_name": PACKAGE_NAME,
        "package_spec": PACKAGE_SPEC,
        "settings_file": str(path),
        "scope": "project" if project else "global",
        "pi_binary": pi_binary,
        "settings_corrupt": corrupt,
        "error": error,
        "packages": [_package_identity(entry) for entry in installed_entries],
        "local_package_dir": str(LOCAL_PACKAGE_DIR),
        "local_package_present": LOCAL_PACKAGE_DIR.exists(),
    }


def install(
    *,
    project: bool = False,
    settings_file: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
    local: bool = False,
) -> HookInstallResult:
    path = settings_file or pi_settings_path(project=project, cwd=cwd, home=home)
    settings = _load_settings(path)
    packages = settings.setdefault("packages", [])
    if not isinstance(packages, list):
        raise HookInstallError("CORRUPT_PI_SETTINGS", f"{path}: packages must be a list")
    if any(_is_opentraces_pi_entry(entry) for entry in packages):
        return HookInstallResult(ok=True, installed={"package": PACKAGE_SPEC}, config_files=[path])
    spec = _target_spec(local=local)
    packages.append(spec)
    _write_settings(path, settings)
    return HookInstallResult(
        ok=True,
        installed={"package": spec},
        added=["package"],
        config_files=[path],
        notes=["Pi package install only; run /ot-setup or opentraces init --agent pi for project capture."],
    )


def remove(
    *,
    project: bool = False,
    settings_file: Path | None = None,
    cwd: Path | None = None,
    home: Path | None = None,
) -> HookInstallResult:
    path = settings_file or pi_settings_path(project=project, cwd=cwd, home=home)
    settings = _load_settings(path)
    packages = settings.get("packages")
    if not isinstance(packages, list):
        return HookInstallResult(ok=True, config_files=[path])
    kept = [entry for entry in packages if not _is_opentraces_pi_entry(entry)]
    removed = len(kept) != len(packages)
    if removed:
        settings["packages"] = kept
        _write_settings(path, settings)
    return HookInstallResult(ok=True, removed=["package"] if removed else [], config_files=[path])


def setup_plan_json(*, project_dir: Path, project: bool = False) -> dict[str, Any]:
    st = status(project=project, cwd=project_dir)
    project_st = status(project=True, cwd=project_dir) if not project else st
    package_installed = bool(st["installed"] or project_st["installed"])
    bridge = _bridge_status(project_dir)
    return {
        "status": "ok",
        "installer": "pi",
        "package": st,
        "project_package": project_st,
        "bridge": bridge,
        "capture_enabled": bridge.get("project", {}).get("capture_enabled", False),
        "raw_provider_bodies_default": "off",
        "steps": [
            {"name": "pi_package", "state": "ok" if package_installed else "missing", "command": "pi install npm:opentraces-pi"},
            {"name": "opentraces_cli", "state": "ok" if bridge["cli"]["found"] else "missing", "commands": bridge["cli"].get("install_hints", [])},
            {"name": "project_capture", "state": "ok" if bridge.get("project", {}).get("capture_enabled", False) else "missing", "command": "opentraces init --agent pi"},
            {"name": "git_hook", "state": "needs_terminal", "command": "opentraces setup git"},
            {"name": "hf_auth", "state": "needs_terminal", "optional": True, "command": "opentraces setup auth"},
            {"name": "bucket_remote", "state": "needs_terminal", "optional": True, "command": "opentraces setup bucket"},
        ],
    }


def _bridge_status(project_dir: Path) -> dict[str, Any]:
    try:
        from .bridge import bridge_status

        return bridge_status(project_dir)
    except Exception as exc:  # noqa: BLE001
        return {"schema_version": "opentraces.pi.bridge_status.v1", "error": str(exc), "cli": {"found": False}}


@dataclass
class PiHookInstaller:
    """HookInstaller adapter for Pi package settings."""

    installer_name: str = "pi"
    project: bool = False
    settings_file: Path | None = None
    cwd: Path | None = None
    home: Path | None = None
    local: bool = False

    def plan(self) -> list[dict]:
        plan, _target = plan_install(
            project=self.project,
            settings_file=self.settings_file,
            cwd=self.cwd,
            home=self.home,
            local=self.local,
        )
        return plan

    def install(self) -> HookInstallResult:
        return install(
            project=self.project,
            settings_file=self.settings_file,
            cwd=self.cwd,
            home=self.home,
            local=self.local,
        )

    def remove(self) -> HookInstallResult:
        return remove(
            project=self.project,
            settings_file=self.settings_file,
            cwd=self.cwd,
            home=self.home,
        )

    def status(self) -> dict:
        return status(
            project=self.project,
            settings_file=self.settings_file,
            cwd=self.cwd,
            home=self.home,
        )


def pi_version() -> str | None:
    binary = shutil.which("pi")
    if not binary:
        return None
    try:
        result = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
    except Exception:
        return None
    return (result.stdout or result.stderr).strip() or None
