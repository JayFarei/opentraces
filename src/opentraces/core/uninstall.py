"""Reverse-of-install orchestrator for ``opentraces setup uninstall``.

The inverse of :func:`core.integration_repair.repair_installed_integrations`,
and it borrows that function's **two-class skip taxonomy**: a surface that is
not installed is reported ``skipped(reason="not-installed")`` and does NOT fail
the aggregate; a ``platform-unsupported`` skip or an ``error`` DOES. Every
surface runs in its own ``try/except`` so one failure never aborts the rest of
the teardown.

Data safety
-----------
The default (``integrations``) tier reverses install-time *patches* and
*daemons* but PRESERVES every byte of captured data — ``bucket/``,
``projects/``, ``staging/`` (the Plan-58 default-inbox ``*.jsonl`` records),
``datasets/``, ``workflows/``, ``pruned_projects/``, ``raw-bodies/``, and ALL
``refs/opentraces/*`` + ``refs/notes/opentraces`` Git refs. The ``purge`` tier
additionally deletes that captured corpus and the Git refs; the CLI gates it
behind a typed confirmation.

Ordering (load-bearing)
-----------------------
Phase 0 quiesces *before* touching anything: it (1) writes ``excluded=True``
into every target repo's ``.opentraces.json`` marker FIRST — the only gate that
stops ``auto_enroll_if_global`` from re-registering an already-marked repo mid
teardown (resetting ``tracking_mode`` alone does NOT, it only closes the
settings.json re-patch arm); (2) resets ``tracking_mode`` to ``manual``; and
(3) stops the watcher + OTLP daemons. OTLP teardown is unload-unit → stop-daemon
→ strip-env so a ``KeepAlive``/``Restart=always`` supervisor cannot respawn the
receiver after we strip its env.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

SCHEMA_VERSION = "opentraces.setup_uninstall.v1"

# A skip with one of these reasons is benign (does not fail the aggregate).
# Everything else (``platform-unsupported``) plus any ``error`` flips ok=False.
_FATAL_SKIP_REASONS = {"platform-unsupported"}


@dataclass
class Surface:
    """One teardown surface result."""

    name: str
    action: str  # removed | skipped | error | purged | excluded | reset | preserved
    reason: str | None = None
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        out: dict[str, Any] = {"name": self.name, "action": self.action}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.detail:
            out["detail"] = self.detail
        return out


# ---------------------------------------------------------------------------
# Git ref purge — path-parameterized helper (extracted from the cwd-bound
# ``opentraces remove --all`` loop so both that command and ``setup
# uninstall --purge`` can purge any repo's refs). Returns structured data
# and NEVER prints; callers render.
# ---------------------------------------------------------------------------
def purge_git_refs(repo: Path) -> dict:
    """Delete every ``refs/opentraces/*`` + ``refs/notes/opentraces`` in ``repo``.

    Best-effort and idempotent: missing refs are a no-op. Returns
    ``{"purged_refs": [...], "failed_refs": [...], "git_not_found": bool}``.
    """
    purged: list[str] = []
    failed: list[str] = []
    try:
        audit = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/opentraces/"],
            capture_output=True, text=True, check=False, cwd=str(repo),
        ).stdout.splitlines()
        notes = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/notes/opentraces"],
            capture_output=True, text=True, check=False, cwd=str(repo),
        ).stdout.splitlines()
    except FileNotFoundError:
        return {"purged_refs": [], "failed_refs": [], "git_not_found": True}

    for ref in (r.strip() for r in audit + notes):
        if not ref:
            continue
        rc = subprocess.run(
            ["git", "update-ref", "-d", ref],
            capture_output=True, text=True, check=False, cwd=str(repo),
        ).returncode
        (purged if rc == 0 else failed).append(ref)
    return {"purged_refs": purged, "failed_refs": failed, "git_not_found": False}


def list_opentraces_refs(repo: Path) -> list[str]:
    """List (without deleting) the repo's opentraces + notes refs, for preview."""
    try:
        audit = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/opentraces/"],
            capture_output=True, text=True, check=False, cwd=str(repo),
        ).stdout.splitlines()
        notes = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/notes/opentraces"],
            capture_output=True, text=True, check=False, cwd=str(repo),
        ).stdout.splitlines()
    except FileNotFoundError:
        return []
    return [r.strip() for r in audit + notes if r.strip()]


# ---------------------------------------------------------------------------
# Repo discovery
# ---------------------------------------------------------------------------
def _target_repos(config, project: Path | None) -> list[Path]:
    if project is not None:
        return [Path(project).resolve()]
    repos: list[Path] = []
    seen: set[str] = set()
    for path_str in config.projects.keys():
        p = Path(path_str)
        rp = str(p.resolve()) if p.exists() else str(p)
        if rp in seen:
            continue
        seen.add(rp)
        repos.append(p)
    cwd = Path.cwd()
    if str(cwd.resolve()) not in seen and (cwd / paths.MARKER_FILENAME).exists():
        repos.append(cwd)
    return repos


def _rmtree(path: Path) -> bool:
    """Race-tolerant rmtree. Returns True if anything was there to remove."""
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def _unlink(path: Path) -> bool:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
            return True
    except OSError:
        pass
    return False


# ---------------------------------------------------------------------------
# Phase 0 — quiesce
# ---------------------------------------------------------------------------
def _phase0(config, repos: list[Path], *, purge: bool, dry_run: bool,
            surfaces: list[Surface], flags: dict, residue: list[str]) -> None:
    from .config import load_project_config, save_config, save_project_config

    # (a) excluded markers FIRST — the load-bearing re-enroll gate. Even under
    #     --purge (where the marker is deleted later) this is harmless and
    #     closes the race during the teardown window.
    excluded: list[str] = []
    for repo in repos:
        marker = repo / paths.MARKER_FILENAME
        if not marker.exists():
            continue
        try:
            if not dry_run:
                data = load_project_config(repo)
                data["excluded"] = True
                save_project_config(repo, data)
            excluded.append(str(repo))
        except Exception as exc:  # noqa: BLE001
            residue.append(f"could not set excluded marker for {repo}: {exc}")
    surfaces.append(Surface(
        "project-opt-out",
        "excluded" if excluded else "skipped",
        None if excluded else "no-projects",
        {"repos": excluded},
    ))

    # (b) tracking_mode -> manual (closes the settings.json re-patch arm).
    try:
        if config.capture.tracking_mode != "manual":
            if not dry_run:
                config.capture.tracking_mode = "manual"
                save_config(config)
            flags["tracking_mode_reset"] = True
            surfaces.append(Surface("tracking-mode", "reset", detail={"to": "manual"}))
        else:
            surfaces.append(Surface("tracking-mode", "skipped", "already-manual"))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("tracking-mode", "error", str(exc)))

    # (c) stop daemons: watcher first (stop + remove unit in one call), then OTLP.
    _watcher_surface(dry_run=dry_run, surfaces=surfaces, flags=flags)
    _otlp_daemon_surfaces(dry_run=dry_run, surfaces=surfaces, flags=flags)


def _watcher_surface(*, dry_run: bool, surfaces: list[Surface], flags: dict) -> None:
    from ..watcher import installer as winst
    try:
        try:
            st = winst.status()
        except RuntimeError as exc:
            surfaces.append(Surface("watcher", "skipped", "platform-unsupported",
                                    {"error": str(exc)}))
            return
        if not st.installed:
            # The unit is gone; still sweep the shim/logs in the cache step.
            surfaces.append(Surface("watcher", "skipped", "not-installed"))
            return
        if dry_run:
            surfaces.append(Surface("watcher", "removed", detail={"dry_run": True}))
            flags["unit_unloaded"] = True
            if st.running:
                flags["daemon_stopped"] = True
            return
        was_running = st.running
        winst.uninstall()  # unloads + removes the unit
        flags["unit_unloaded"] = True
        if was_running:
            flags["daemon_stopped"] = True
        surfaces.append(Surface("watcher", "removed", detail={"was_running": was_running}))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("watcher", "error", str(exc)))


def _otlp_daemon_surfaces(*, dry_run: bool, surfaces: list[Surface], flags: dict) -> None:
    from ..capture.otlp import lifecycle

    # 1. Unload the supervised unit FIRST (KeepAlive/Restart respawn otherwise).
    try:
        if not lifecycle.is_installed():
            surfaces.append(Surface("otlp-autostart", "skipped", "not-installed"))
        elif dry_run:
            surfaces.append(Surface("otlp-autostart", "removed", detail={"dry_run": True}))
            flags["unit_unloaded"] = True
        else:
            res = lifecycle.uninstall_autostart()
            if res.ok:
                flags["unit_unloaded"] = True
                surfaces.append(Surface("otlp-autostart", "removed",
                                        detail={"platform": res.platform}))
            else:
                surfaces.append(Surface("otlp-autostart", "error", res.reason,
                                        {"details": res.details}))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("otlp-autostart", "error", str(exc)))

    # 2. Reap any *bare* foreground PID the unit never managed (is_running()
    #    checks the PID file even when no unit is installed).
    try:
        running, pid = lifecycle.is_running()
        if not running or not pid:
            surfaces.append(Surface("otlp-daemon", "skipped", "not-running"))
        elif dry_run:
            surfaces.append(Surface("otlp-daemon", "removed", detail={"pid": pid, "dry_run": True}))
            flags["daemon_stopped"] = True
        else:
            _kill_pid(pid)
            flags["daemon_stopped"] = True
            surfaces.append(Surface("otlp-daemon", "removed", detail={"pid": pid}))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("otlp-daemon", "error", str(exc)))


def _kill_pid(pid: int) -> None:
    from ..capture.otlp import lifecycle
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    for _ in range(20):
        if not lifecycle._alive(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Phase 1 — integrations
# ---------------------------------------------------------------------------
def _otlp_env_surface(*, dry_run: bool, surfaces: list[Surface], keys_removed: list[str],
                      residue: list[str]) -> None:
    from ..capture.otlp.settings_patcher import (
        DEFAULT_SETTINGS_PATH,
        OTEL_ENV_KEYS,
        _load_settings,
        uninstall_otel_env,
    )
    try:
        target = DEFAULT_SETTINGS_PATH
        present: list[str] = []
        if target.exists():
            data, err = _load_settings(target)
            if not err:
                env = data.get("env", {})
                present = [k for k in OTEL_ENV_KEYS if k in env]
        if not present:
            surfaces.append(Surface("otlp-settings", "skipped", "not-installed"))
            return
        if dry_run:
            surfaces.append(Surface("otlp-settings", "removed",
                                    detail={"present": present, "dry_run": True}))
            return
        res = uninstall_otel_env(ownership_safe=True, delete_backup=True)
        removed = list(res.keys_skipped or [])
        keys_removed.extend(removed)
        if "no-backup" in (res.reason or "") or "unreadable" in (res.reason or ""):
            residue.append(
                "OTLP env keys stripped without a pre-install backup snapshot; "
                "value-matched keys removed, user-owned keys preserved by value."
            )
        preserved = [k for k in present if k not in removed]
        if preserved:
            residue.append(f"preserved user-owned OTEL keys: {', '.join(preserved)}")
        surfaces.append(Surface(
            "otlp-settings",
            "removed" if removed else "skipped",
            None if removed else "user-owned",
            {"keys_removed": removed, "reason": res.reason},
        ))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("otlp-settings", "error", str(exc)))


def _hook_surface(name: str, make_installer, *, dry_run: bool, surfaces: list[Surface],
                  detail: dict | None = None) -> None:
    """Status-gated remove for one HookInstaller. removed-vs-skipped is derived
    ONLY from the pre-call ``status()['installed']`` gate — never from the
    reverser return value (git/skill/pi all return ok=True with empty work)."""
    base = dict(detail or {})
    try:
        inst = make_installer()
        st = inst.status()
        if not st.get("installed"):
            surfaces.append(Surface(name, "skipped", "not-installed", base))
            return
        if dry_run:
            surfaces.append(Surface(name, "removed", detail={**base, "dry_run": True}))
            return
        res = inst.remove()
        if res.ok:
            surfaces.append(Surface(name, "removed", detail={**base, "removed": list(res.removed)}))
        else:
            surfaces.append(Surface(name, "error", "remove-failed", base))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface(name, "error", str(exc), base))


def _completions_surface(*, dry_run: bool, surfaces: list[Surface]) -> None:
    from ..cli.completions import SUPPORTED_SHELLS, _script_path, uninstall_completions
    try:
        removed_any: list[str] = []
        for shell in SUPPORTED_SHELLS:
            if dry_run:
                if _script_path(shell).exists():
                    removed_any.append(shell)
                continue
            res = uninstall_completions(shell)
            if res["removed"]:
                removed_any.append(shell)
        if removed_any:
            surfaces.append(Surface("completions", "removed", detail={"shells": removed_any}))
        else:
            surfaces.append(Surface("completions", "skipped", "not-installed"))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("completions", "error", str(exc)))


def _security_flags_surface(config, *, dry_run: bool, surfaces: list[Surface]) -> None:
    try:
        changed: list[str] = []
        for tool in ("trufflehog", "privacy_filter", "llm_review"):
            sec = getattr(config.security, tool, None)
            if sec is not None and getattr(sec, "enabled", False):
                changed.append(tool)
                if not dry_run:
                    sec.enabled = False
        if changed:
            if not dry_run:
                from .config import save_config
                save_config(config)
            surfaces.append(Surface("security-flags", "removed",
                                    detail={"disabled": changed, "binaries": "left-in-place"}))
        else:
            surfaces.append(Surface("security-flags", "skipped", "already-disabled"))
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("security-flags", "error", str(exc)))


def _cache_surface(*, dry_run: bool, surfaces: list[Surface]) -> None:
    """Delete pure-derived / transient runtime files. Race-tolerant: a straggler
    daemon tick recreating a dir must not fail the teardown."""
    from ..capture.otlp import lifecycle as _otlp_life
    from ..watcher import installer as _winst

    targets: list[Path] = [
        paths.OPENTRACES_DIR / "index",            # legacy + search index (rebuildable)
        paths.otlp_receiver_log_dir(),             # logs/
        paths.OPENTRACES_DIR / "version-check.json",
        paths.OPENTRACES_DIR / "watcher.status.json",
        paths.otlp_receiver_pid_path(),
        paths.otlp_receiver_status_path(),
        _otlp_life.SHIM_PATH,                       # bin/ot-otlp-receiver
    ]
    try:
        targets.append(_winst._shim_path())         # bin/ot-watcher
    except Exception:  # noqa: BLE001
        pass

    removed: list[str] = []
    try:
        for sem in (paths.OPENTRACES_DIR / "bin").glob("sem-*"):
            targets.append(sem)
    except Exception:  # noqa: BLE001
        pass

    for t in targets:
        if t.exists() or t.is_symlink():
            if not dry_run:
                if t.is_dir() and not t.is_symlink():
                    _rmtree(t)
                else:
                    _unlink(t)
            removed.append(str(t))
    if removed:
        surfaces.append(Surface("cache", "removed", detail={"paths": removed}))
    else:
        surfaces.append(Surface("cache", "skipped", "nothing-to-clean"))


def _credentials_surface(*, dry_run: bool, surfaces: list[Surface], residue: list[str]) -> None:
    """Delete ONLY ``~/.opentraces/credentials`` — never HF logout, never touch
    ``~/.cache/huggingface/token`` (that is what ``config.clear_credentials``
    does, which would log the user out of HF everywhere)."""
    try:
        if paths.CREDENTIALS_PATH.exists():
            if not dry_run:
                _unlink(paths.CREDENTIALS_PATH)
            surfaces.append(Surface("credentials", "removed",
                                    detail={"path": str(paths.CREDENTIALS_PATH)}))
        else:
            surfaces.append(Surface("credentials", "skipped", "not-present"))
        residue.append("~/.cache/huggingface/token left untouched (HF login preserved)")
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("credentials", "error", str(exc)))


# ---------------------------------------------------------------------------
# Phase 2 — purge (destructive)
# ---------------------------------------------------------------------------
def _purge_user_data(*, dry_run: bool, surfaces: list[Surface]) -> None:
    targets = [
        ("bucket", paths.bucket_dir()),
        ("projects", paths.PROJECTS_DIR),
        ("staging", paths.STAGING_DIR),  # canonical Plan-58 *.jsonl records — NOT cache
        ("datasets", paths.OPENTRACES_DIR / "datasets"),
        ("workflows", paths.OPENTRACES_DIR / "workflows"),
        ("pruned_projects", paths.OPENTRACES_DIR / "pruned_projects"),
        ("raw-bodies", paths.raw_bodies_dir()),
    ]
    removed: list[str] = []
    for label, path in targets:
        if path.exists():
            if not dry_run:
                _rmtree(path)
            removed.append(label)
    surfaces.append(Surface(
        "user-data", "purged" if removed else "skipped",
        None if removed else "nothing-to-purge", {"removed": removed},
    ))


def _purge_git_refs_surface(repos: list[Path], *, dry_run: bool, surfaces: list[Surface],
                            refs_purged: list[str]) -> None:
    for repo in repos:
        try:
            if dry_run:
                refs = list_opentraces_refs(repo)
                refs_purged.extend(refs)
                surfaces.append(Surface("git-refs", "purged" if refs else "skipped",
                                        None if refs else "no-refs",
                                        {"repo": str(repo), "refs": refs, "dry_run": True}))
                continue
            result = purge_git_refs(repo)
            refs_purged.extend(result["purged_refs"])
            # Delete the in-repo marker under purge.
            _unlink(repo / paths.MARKER_FILENAME)
            action = "purged" if result["purged_refs"] else "skipped"
            reason = None if result["purged_refs"] else (
                "git-not-found" if result["git_not_found"] else "no-refs"
            )
            surfaces.append(Surface("git-refs", action, reason, {
                "repo": str(repo),
                "purged": result["purged_refs"],
                "failed": result["failed_refs"],
            }))
        except Exception as exc:  # noqa: BLE001
            surfaces.append(Surface("git-refs", "error", str(exc), {"repo": str(repo)}))


def _purge_config_surface(*, dry_run: bool, surfaces: list[Surface], residue: list[str]) -> None:
    try:
        removed: list[str] = []
        for p in (paths.CONFIG_PATH, paths.CREDENTIALS_PATH):
            if p.exists():
                if not dry_run:
                    _unlink(p)
                removed.append(str(p))
        # Offer to remove the whole dir only if it is now empty of our content.
        surfaces.append(Surface("config", "purged" if removed else "skipped",
                                None if removed else "not-present", {"removed": removed}))
        if paths.OPENTRACES_DIR.exists():
            residue.append(f"{paths.OPENTRACES_DIR} may still exist; remove manually if desired")
    except Exception as exc:  # noqa: BLE001
        surfaces.append(Surface("config", "error", str(exc)))


# ---------------------------------------------------------------------------
# Confirmation preview (the CLI renders this before a --purge)
# ---------------------------------------------------------------------------
def summarize_purge_targets(config, repos: list[Path]) -> dict:
    def _dir_bytes(path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except OSError:
                    pass
        return total

    bucket_bytes = _dir_bytes(paths.bucket_dir())
    datasets = paths.OPENTRACES_DIR / "datasets"
    dataset_count = sum(1 for _ in datasets.iterdir()) if datasets.exists() else 0
    repos_with_refs = sum(1 for r in repos if list_opentraces_refs(r))
    remote_enabled = False
    remote_url = None
    try:
        remote_enabled = bool(config.bucket.remote.enabled)
        remote_url = getattr(config.bucket.remote, "repo_id", None) or getattr(
            config.bucket.remote, "url", None)
    except Exception:  # noqa: BLE001
        pass
    return {
        "bucket_bytes": bucket_bytes,
        "dataset_count": dataset_count,
        "registered_projects": len(repos),
        "repos_with_refs": repos_with_refs,
        "remote_bucket_enabled": remote_enabled,
        "remote_bucket_url": remote_url,
    }


# ---------------------------------------------------------------------------
# Package self-uninstall command (printed, never executed)
# ---------------------------------------------------------------------------
def package_uninstall_command() -> str:
    try:
        from ..cli import _detect_install_method
        method = _detect_install_method()
    except Exception:  # noqa: BLE001
        method = "pip"
    if method == "brew":
        return "brew uninstall jayfarei/opentraces/opentraces"
    if method == "pipx":
        return "pipx uninstall opentraces"
    if method == "source":
        return "pip uninstall opentraces  # (run in your editable-checkout venv)"
    return "pip uninstall opentraces"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_uninstall(*, tier: str = "integrations", project: Path | None = None,
                  prune_unflushed: bool = False, dry_run: bool = False) -> dict:
    """Reverse the opentraces install. See module docstring for the contract."""
    from .config import load_config

    purge = tier == "purge"
    config = load_config()
    repos = _target_repos(config, project)

    surfaces: list[Surface] = []
    flags = {"daemon_stopped": False, "unit_unloaded": False, "tracking_mode_reset": False}
    keys_removed: list[str] = []
    refs_purged: list[str] = []
    residue: list[str] = []

    # Phase 0 — quiesce (markers, tracking-mode, daemons).
    _phase0(config, repos, purge=purge, dry_run=dry_run,
            surfaces=surfaces, flags=flags, residue=residue)

    # Phase 1 — integrations.
    _otlp_env_surface(dry_run=dry_run, surfaces=surfaces,
                      keys_removed=keys_removed, residue=residue)

    from ..capture.claude_code.install import ClaudeCodeHookInstaller
    from ..capture.codex_cli.install import CodexCliHookInstaller
    from ..capture.pi.install import PiHookInstaller
    from ..capture.skill.install import SkillInstaller

    _hook_surface("claude-code", ClaudeCodeHookInstaller, dry_run=dry_run, surfaces=surfaces)
    _hook_surface("codex-cli", CodexCliHookInstaller, dry_run=dry_run, surfaces=surfaces)
    _hook_surface("pi", PiHookInstaller, dry_run=dry_run, surfaces=surfaces)  # global
    _hook_surface("skill", SkillInstaller, dry_run=dry_run, surfaces=surfaces)

    _completions_surface(dry_run=dry_run, surfaces=surfaces)

    # Per-repo: git hook + (additive) per-project pi settings.
    from ..capture.git.install import GitHookInstaller
    for repo in repos:
        _hook_surface("git", lambda r=repo: GitHookInstaller(repo=r),
                      dry_run=dry_run, surfaces=surfaces, detail={"repo": str(repo)})
        _hook_surface("pi-project", lambda r=repo: PiHookInstaller(project=True, cwd=r),
                      dry_run=dry_run, surfaces=surfaces, detail={"repo": str(repo)})

    _security_flags_surface(config, dry_run=dry_run, surfaces=surfaces)
    _cache_surface(dry_run=dry_run, surfaces=surfaces)
    _credentials_surface(dry_run=dry_run, surfaces=surfaces, residue=residue)

    # Optional opt-in prune of un-flushed captured content (default tier).
    if prune_unflushed and not purge:
        pruned: list[str] = []
        for label, path in (("raw-bodies", paths.raw_bodies_dir()),
                            ("staging/otel", paths.otel_staging_dir())):
            if path.exists():
                if not dry_run:
                    _rmtree(path)
                pruned.append(label)
        surfaces.append(Surface("prune-unflushed", "purged" if pruned else "skipped",
                                None if pruned else "nothing-to-prune", {"removed": pruned}))

    # Phase 2 — purge (destructive).
    if purge:
        _purge_user_data(dry_run=dry_run, surfaces=surfaces)
        _purge_git_refs_surface(repos, dry_run=dry_run, surfaces=surfaces, refs_purged=refs_purged)
        _purge_config_surface(dry_run=dry_run, surfaces=surfaces, residue=residue)

    # Refs preserved (default tier): the full opentraces ref set we did NOT touch.
    refs_preserved: list[str] = []
    if not purge:
        for repo in repos:
            refs_preserved.extend(list_opentraces_refs(repo))

    # Remote-bucket honesty.
    try:
        if config.bucket.remote.enabled:
            url = getattr(config.bucket.remote, "url", None) or "<configured remote>"
            residue.append(f"remote bucket at {url} is NOT deleted (local-only teardown)")
    except Exception:  # noqa: BLE001
        pass

    residue.append(f"package not self-uninstalled; run: {package_uninstall_command()}")

    # Aggregate ok (two-class taxonomy).
    ok = not any(
        s.action == "error" or (s.action == "skipped" and s.reason in _FATAL_SKIP_REASONS)
        for s in surfaces
    )

    # Only genuine removals/purges count as "removed" — ``reset``
    # (tracking-mode) and ``excluded`` (marker opt-out) are idempotent state
    # normalizations that recur on every run, so counting them would break the
    # "second run removes nothing" idempotency contract.
    removed_names = sorted({s.name for s in surfaces if s.action in ("removed", "purged")})
    skipped_names = sorted({s.name for s in surfaces if s.action == "skipped"})
    error_names = sorted({s.name for s in surfaces if s.action == "error"})

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "tier": tier,
        "dry_run": dry_run,
        "prune_unflushed": prune_unflushed,
        "surfaces": [s.as_dict() for s in surfaces],
        "removed_names": removed_names,
        "skipped_names": skipped_names,
        "error_names": error_names,
        "keys_removed": keys_removed,
        "refs_preserved": sorted(set(refs_preserved)),
        "refs_purged": sorted(set(refs_purged)),
        "daemon_stopped": flags["daemon_stopped"],
        "unit_unloaded": flags["unit_unloaded"],
        "tracking_mode_reset": flags["tracking_mode_reset"],
        "package_uninstall_command": package_uninstall_command(),
        "residue": residue,
        "repos": [str(r) for r in repos],
    }
