"""Deterministic, non-secret first-run readiness for pinned agent harnesses."""

from __future__ import annotations

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any


PREFERENCES_INVALID_SENTINEL = "opentraces-harness-preferences-invalid"


class HarnessPreferencesInvalid(ValueError):
    """The existing harness preference file cannot be safely amended."""


def _compatible(mapping: dict[str, Any], key: str, expected_type: type[object]) -> None:
    if key in mapping and type(mapping[key]) is not expected_type:
        raise HarnessPreferencesInvalid(f"{key} has an incompatible type")


def _read_preferences(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HarnessPreferencesInvalid("preferences are not readable JSON") from exc
    if not isinstance(payload, dict):
        raise HarnessPreferencesInvalid("preferences root is not an object")
    return payload


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_preferences(*, path: Path, workspace: str, version: str) -> dict[str, Any]:
    coordinate = PurePosixPath(workspace)
    if not coordinate.is_absolute() or str(coordinate) != workspace or ".." in coordinate.parts:
        raise HarnessPreferencesInvalid("workspace is not an absolute canonical coordinate")
    if not version or any(character.isspace() for character in version):
        raise HarnessPreferencesInvalid("harness version is invalid")

    preferences = _read_preferences(path)
    _compatible(preferences, "hasCompletedOnboarding", bool)
    _compatible(preferences, "lastOnboardingVersion", str)
    _compatible(preferences, "theme", str)
    _compatible(preferences, "bypassPermissionsModeAccepted", bool)
    _compatible(preferences, "projects", dict)
    projects = preferences.get("projects", {})
    project = projects.get(workspace, {})
    if not isinstance(project, dict):
        raise HarnessPreferencesInvalid("workspace preferences are not an object")
    _compatible(project, "hasTrustDialogAccepted", bool)
    return preferences


def validate_claude_readiness(*, path: Path, workspace: str, version: str) -> None:
    """Validate existing Claude preferences without materializing readiness."""

    _validated_preferences(path=path, workspace=workspace, version=version)


def ensure_claude_readiness(*, path: Path, workspace: str, version: str) -> bool:
    """Merge only non-secret readiness facts into Claude's global preferences."""

    preferences = _validated_preferences(path=path, workspace=workspace, version=version)
    projects = preferences.setdefault("projects", {})
    project = projects.setdefault(workspace, {})

    preferences.update(
        {
            "bypassPermissionsModeAccepted": True,
            "hasCompletedOnboarding": True,
            "lastOnboardingVersion": version,
            "theme": "dark",
        }
    )
    project["hasTrustDialogAccepted"] = True
    content = (json.dumps(preferences, indent=2, sort_keys=True) + "\n").encode()
    try:
        if path.is_file() and path.read_bytes() == content:
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                os.chmod(path, 0o600)
            return False
    except OSError as exc:
        raise HarnessPreferencesInvalid("preferences cannot be verified") from exc
    try:
        _atomic_write(path, content)
    except OSError as exc:
        raise HarnessPreferencesInvalid("preferences cannot be written atomically") from exc
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        action = validate_claude_readiness if args.check else ensure_claude_readiness
        action(path=Path.home() / ".claude.json", workspace=args.workspace, version=args.version)
    except HarnessPreferencesInvalid:
        print(PREFERENCES_INVALID_SENTINEL)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
