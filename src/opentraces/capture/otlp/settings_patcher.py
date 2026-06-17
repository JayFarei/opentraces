"""Idempotent patcher for ``~/.claude/settings.json`` (plan 078 R10).

Enables Claude Code's native OTel emission targeting the local OTLP
receiver by setting 12 env keys under the top-level ``env`` block.
Deep-merges so user-set keys outside ``env`` and unrelated keys inside
``env`` are preserved. User-modified values for our 12 keys are NEVER
clobbered; they are reported under ``keys_skipped`` with reason
``user-modified``.

Idempotency contract: re-running ``install_otel_env`` on an already
patched settings file is a no-op; every key already present with the
exact value we would set is reported in ``keys_skipped`` (reason
``already-set``) and ``keys_added`` is empty.

Backup contract: the first install copies settings.json to
``settings.json.opentraces-backup``. Subsequent installs DO NOT overwrite
the backup, so re-running install is safe even if the user has since
modified settings.json post-install. ``uninstall_otel_env`` either
restores the backup atomically (``restore_backup=True``), pops all 12 of
our keys (default), or — for ``ownership_safe=True`` — strips only the
keys opentraces actually wrote (preserving user's other ``env`` entries
and any OTEL key the user owned pre-install).

Atomic write: every settings.json mutation goes via ``<path>.tmp`` +
``os.replace`` after a JSON round-trip validation; ``0644`` perms on the
file, ``0700`` on the parent ``~/.claude/`` directory if we have to
create it (matches Anthropic's convention).

User-modified detection: we compare each of our 12 keys by exact string
equality against what we WOULD set. Any deviation (different scheme,
different path, different value) is treated as user-modified and
preserved. There is no partial-match heuristic.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("opentraces.otlp.settings_patcher")

DEFAULT_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
DEFAULT_BACKUP_SUFFIX = ".opentraces-backup"
DEFAULT_RAW_BODIES_DIR = Path.home() / ".opentraces" / "raw-bodies"
DEFAULT_OTLP_ENDPOINT = "http://127.0.0.1:4318"

# Env keys patched into ~/.claude/settings.json. The three EXPORTER
# selectors are required for the OTel SDK to actually pick OTLP (without
# them, only raw-body files get written; no envelopes reach the receiver).
# The two EXPORT_INTERVAL hints drop the default ~10s flush down to
# ~2s so short interactive sessions actually surface envelopes before
# the user runs `capture-otlp status`. OTEL_LOG_RAW_API_BODIES and
# OTEL_EXPORTER_OTLP_ENDPOINT are computed from the install args.
OTEL_ENV_KEYS: tuple[str, ...] = (
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_LOGS_EXPORTER",
    "OTEL_METRICS_EXPORTER",
    "OTEL_TRACES_EXPORTER",
    "OTEL_LOGS_EXPORT_INTERVAL",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_LOG_USER_PROMPTS",
    "OTEL_LOG_TOOL_DETAILS",
    "OTEL_LOG_TOOL_CONTENT",
    "OTEL_LOG_RAW_API_BODIES",
)


@dataclass
class PatchResult:
    ok: bool
    settings_path: Path
    backup_path: Path | None = None
    keys_added: list[str] = field(default_factory=list)
    keys_skipped: list[str] = field(default_factory=list)
    reason: str | None = None


def _expected_env(otlp_endpoint: str, raw_bodies_dir: Path) -> dict[str, str]:
    raw = raw_bodies_dir.expanduser().resolve()
    return {
        "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
        "OTEL_EXPORTER_OTLP_ENDPOINT": otlp_endpoint,
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
        "OTEL_LOGS_EXPORTER": "otlp",
        "OTEL_METRICS_EXPORTER": "otlp",
        "OTEL_TRACES_EXPORTER": "otlp",
        "OTEL_LOGS_EXPORT_INTERVAL": "2000",
        "OTEL_METRIC_EXPORT_INTERVAL": "2000",
        "OTEL_LOG_USER_PROMPTS": "1",
        "OTEL_LOG_TOOL_DETAILS": "1",
        "OTEL_LOG_TOOL_CONTENT": "1",
        "OTEL_LOG_RAW_API_BODIES": f"file:{raw}",
    }


def _load_settings(path: Path) -> tuple[dict, str | None]:
    if not path.exists():
        return {"env": {}}, None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return {}, f"malformed-existing-settings: {e}"
    if not isinstance(data, dict):
        return {}, "malformed-existing-settings: top-level not an object"
    if "env" not in data or not isinstance(data.get("env"), dict):
        data["env"] = {}
    return data, None


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, sort_keys=False) + "\n"
    # Round-trip validate before write.
    json.loads(serialized)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(serialized)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    os.chmod(path, 0o644)


def install_otel_env(
    settings_path: Path | None = None,
    raw_bodies_dir: Path | None = None,
    otlp_endpoint: str = DEFAULT_OTLP_ENDPOINT,
) -> PatchResult:
    target = Path(settings_path) if settings_path else DEFAULT_SETTINGS_PATH
    raw_dir = Path(raw_bodies_dir) if raw_bodies_dir else DEFAULT_RAW_BODIES_DIR
    backup = target.with_name(target.name + DEFAULT_BACKUP_SUFFIX)
    expected = _expected_env(otlp_endpoint, raw_dir)

    data, err = _load_settings(target)
    if err:
        logger.warning("settings load failed for %s: %s", target, err)
        return PatchResult(ok=False, settings_path=target, reason=err)

    # First-install-only backup.
    backup_recorded: Path | None = backup if backup.exists() else None
    if target.exists() and not backup.exists():
        shutil.copy2(target, backup)
        backup_recorded = backup
        logger.info("backed up %s -> %s", target, backup)

    env = data["env"]
    added: list[str] = []
    skipped: list[str] = []
    for key, want in expected.items():
        current = env.get(key)
        if current is None:
            env[key] = want
            added.append(key)
        elif current == want:
            skipped.append(key)
            logger.debug("env key %s already set (already-set)", key)
        else:
            skipped.append(key)
            logger.info(
                "env key %s preserved (user-modified): kept %r, would have set %r",
                key, current, want,
            )

    raw_dir.expanduser().mkdir(mode=0o700, parents=True, exist_ok=True)
    _atomic_write_json(target, data)
    return PatchResult(
        ok=True,
        settings_path=target,
        backup_path=backup_recorded,
        keys_added=added,
        keys_skipped=skipped,
    )


def _backup_env(backup: Path) -> dict | None:
    """Return the pre-install ``env`` dict from the write-once backup.

    ``None`` means "no usable snapshot" (no backup, or unreadable/malformed)
    — the ownership-safe path then falls back to value-match only.
    """
    if not backup.exists():
        return None
    try:
        data = json.loads(backup.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and isinstance(data.get("env"), dict):
        return data["env"]
    return {}


def uninstall_otel_env(
    settings_path: Path | None = None,
    restore_backup: bool = False,
    ownership_safe: bool = False,
    delete_backup: bool = False,
) -> PatchResult:
    """Reverse :func:`install_otel_env`.

    Three mutually-exclusive modes, in precedence order:

    - ``restore_backup=True``: blind ``os.replace`` of the write-once backup
      over settings.json (the legacy ``setup capture-otlp --uninstall`` /
      plan-078 R10 "restore byte-identical" contract). Unchanged.
    - ``ownership_safe=True``: surgical removal that only strips keys
      opentraces actually *wrote* — a key is removed iff its current value
      equals what we would install AND it was not already present in the
      pre-install backup snapshot. User-set / user-modified OTEL keys are
      preserved. Used by ``opentraces setup uninstall``. With
      ``delete_backup=True`` the leaked write-once backup is removed after a
      clean strip. When no backup snapshot exists, falls back to value-match
      only (and the caller should warn — provenance is unknown).
    - default (``ownership_safe=False``): pop ALL present ``OTEL_ENV_KEYS``
      unconditionally. Unchanged legacy behavior for existing callers.

    Removed keys are reported in ``keys_skipped`` (the same dataclass shape as
    :func:`install_otel_env`; the CLI re-aliases it to ``keys_removed``).
    """
    target = Path(settings_path) if settings_path else DEFAULT_SETTINGS_PATH
    backup = target.with_name(target.name + DEFAULT_BACKUP_SUFFIX)

    if restore_backup and backup.exists():
        os.replace(backup, target)
        os.chmod(target, 0o644)
        logger.info("restored %s from %s", target, backup)
        return PatchResult(ok=True, settings_path=target, backup_path=None,
                           reason="restored-from-backup")

    if not target.exists():
        return PatchResult(ok=True, settings_path=target, reason="no-settings-file")

    data, err = _load_settings(target)
    if err:
        return PatchResult(ok=False, settings_path=target, reason=err)
    env = data.get("env", {})
    removed: list[str] = []

    if ownership_safe:
        expected = _expected_env(DEFAULT_OTLP_ENDPOINT, DEFAULT_RAW_BODIES_DIR)
        backup_env = _backup_env(backup)
        preserved: list[str] = []
        for key in OTEL_ENV_KEYS:
            if key not in env:
                continue
            if env.get(key) != expected.get(key):
                preserved.append(key)  # user-modified value — never ours
                continue
            if backup_env is not None and key in backup_env:
                preserved.append(key)  # user pre-owned the key — preserve
                continue
            env.pop(key)
            removed.append(key)
        _atomic_write_json(target, data)
        if backup_env is None and backup.exists():
            reason = "keys-removed-ownership-safe-backup-unreadable"
        elif not backup.exists():
            reason = "keys-removed-ownership-safe-no-backup"
        else:
            reason = "keys-removed-ownership-safe"
        backup_path = backup if backup.exists() else None
        if delete_backup and backup.exists() and not preserved:
            # Only drop the leaked backup after a *clean* strip — if we left
            # user keys behind, keep the snapshot so a later full restore is
            # still possible.
            try:
                backup.unlink()
                backup_path = None
            except OSError:
                pass
        return PatchResult(
            ok=True,
            settings_path=target,
            backup_path=backup_path,
            keys_added=[],
            keys_skipped=removed,
            reason=reason,
        )

    for key in OTEL_ENV_KEYS:
        if key in env:
            env.pop(key)
            removed.append(key)
    _atomic_write_json(target, data)
    return PatchResult(
        ok=True,
        settings_path=target,
        backup_path=backup if backup.exists() else None,
        keys_added=[],
        keys_skipped=removed,
        reason="keys-removed",
    )


def is_installed(settings_path: Path | None = None) -> bool:
    """True iff all 12 env keys match what install_otel_env would set with default args."""
    target = Path(settings_path) if settings_path else DEFAULT_SETTINGS_PATH
    if not target.exists():
        return False
    data, err = _load_settings(target)
    if err:
        return False
    env = data.get("env", {})
    expected = _expected_env(DEFAULT_OTLP_ENDPOINT, DEFAULT_RAW_BODIES_DIR)
    return all(env.get(k) == v for k, v in expected.items())
