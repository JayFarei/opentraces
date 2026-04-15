"""Watcher daemon — per-project polling worker.

Public API:
    run_once(project_cwd)              -> TickReport  (single tick, one project)
    run_forever(interval, projects)    -> never returns (service loop)
    discover_enlisted_projects()       -> list[Path]  (from ~/.opentraces/projects/)

A tick does the minimum work to decide whether backfill is needed:

    1. Read ``.opentraces.json`` and per-project state.
    2. Count ``git rev-list <last_backfilled_commit>..HEAD``.
    3. mtime-probe the Claude Code JSONL dir for the project.
    4. If nothing changed since ``last_watcher_run_at`` -> early exit (<50ms).
    5. Otherwise call ``core.backfill.run_incremental(project_cwd)``.
    6. Stamp ``last_watcher_run_at`` regardless (probe happened).

The worker catches and logs all exceptions; a crashing tick MUST NOT crash
the service loop. ``TickReport.error`` carries the failure string.

Idempotency: ``core.backfill`` writes each commit's cache atomically
(tmpfile + rename) and only advances ``last_backfilled_commit`` after a
successful commit write. A tick interrupted mid-pass resumes cleanly on the
next tick.
"""

from __future__ import annotations

import datetime
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..core.config import PROJECTS_DIR, get_project_state_path
from ..core.ingest import scan_project
from ..core.paths import OPENTRACES_DIR
from ..core.state import StateManager

logger = logging.getLogger("opentraces.watcher")

DEFAULT_INTERVAL = 300
QUIET_TICK_BUDGET_MS = 50  # target only; we don't enforce

# Test hook: monkeypatched by the crash-recovery scenario harness step.
# When set, run_once raises RuntimeError(_CRASH_AFTER_PROBE) at the named
# checkpoint. Valid values: "after_probe", "after_first_commit".
_CRASH_AFTER_PROBE: str | None = None


# --- public types ----------------------------------------------------------

@dataclass
class TickReport:
    project_cwd: Path
    started_at: datetime.datetime
    duration_ms: float = 0.0
    new_commits: int = 0
    jsonl_activity: bool = False
    backfill_invoked: bool = False
    coverage_ratio: float | None = None
    error: str | None = None
    # Extra fields (kept non-default so dataclass stays append-friendly)
    commits_processed: int = 0
    # Session-ingestion sweep (Phase 1 of live-session ingestion). These
    # are zero on quiet ticks (no sweep) and populated on active ticks
    # from ``ScanReport``. Always present so callers don't need version
    # guards.
    sessions_created: int = 0
    sessions_refreshed: int = 0
    sessions_new_generations: int = 0
    sessions_noops: int = 0
    sessions_errored: int = 0


# --- helpers ---------------------------------------------------------------

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _encode_project_path(project_cwd: Path) -> str:
    """Claude Code's JSONL-dir naming: non-alnum chars -> '-'."""
    s = str(project_cwd.resolve())
    return "".join(c if c.isalnum() or c == "-" else "-" for c in s)


def _home() -> Path:
    # Honour HOME override (used by tests to sandbox away from the real home).
    return Path(os.environ.get("HOME") or Path.home())


def _claude_jsonl_dir(project_cwd: Path) -> Path:
    return _home() / ".claude" / "projects" / _encode_project_path(project_cwd)


def _read_marker(project_cwd: Path) -> dict:
    marker = project_cwd / ".opentraces.json"
    if not marker.is_file():
        return {}
    try:
        return json.loads(marker.read_text()) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def _watcher_interval_for(project_cwd: Path, default: int = DEFAULT_INTERVAL) -> int:
    cfg = _read_marker(project_cwd)
    watcher = (cfg.get("watcher") or {}) if isinstance(cfg, dict) else {}
    try:
        iv = int(watcher.get("interval_seconds") or default)
    except (TypeError, ValueError):
        iv = default
    return max(30, iv)  # sanity floor — don't let pathological configs spin


def _count_new_commits(project_cwd: Path, last_sha: str | None) -> int:
    """``git rev-list --count <last>..HEAD``, 0 on any failure."""
    if not last_sha:
        # No bookmark yet — a full history walk would be surprising here;
        # treat as "everything new" by returning a large sentinel so the
        # caller invokes backfill, which respects max_commits.
        try:
            out = subprocess.check_output(
                ["git", "-C", str(project_cwd), "rev-list", "--count", "HEAD"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
            return int(out or "0")
        except (subprocess.CalledProcessError, ValueError, OSError):
            return 0
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_cwd), "rev-list", "--count",
             f"{last_sha}..HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return int(out or "0")
    except (subprocess.CalledProcessError, ValueError, OSError):
        # last_sha might not be reachable (rebase/force-push); treat as new.
        return 1


def _jsonl_activity_since(project_cwd: Path, threshold_iso: str | None) -> bool:
    """True if any ``*.jsonl`` under the Claude corpus has mtime > threshold."""
    d = _claude_jsonl_dir(project_cwd)
    if not d.is_dir():
        return False
    if threshold_iso is None:
        # First probe ever; any file is "new".
        return any(d.glob("*.jsonl"))
    try:
        threshold = datetime.datetime.fromisoformat(
            threshold_iso.replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, TypeError):
        return True
    for p in d.glob("*.jsonl"):
        try:
            if p.stat().st_mtime > threshold:
                return True
        except OSError:
            continue
    return False


def _logs_dir() -> Path:
    return OPENTRACES_DIR / "logs"


_LOG_CONFIGURED = False

def _configure_logging() -> None:
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    try:
        _logs_dir().mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            _logs_dir() / "watcher.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=4,
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    except OSError:
        pass
    _LOG_CONFIGURED = True


# --- public API ------------------------------------------------------------

def run_once(project_cwd: Path, *, verbose: bool = False) -> TickReport:
    """One tick against one project. Never raises; errors go on the report."""
    _configure_logging()
    project_cwd = Path(project_cwd).resolve()
    started = _utcnow()
    t0 = time.monotonic()
    report = TickReport(project_cwd=project_cwd, started_at=started)

    try:
        # Even the 'quiet' case needs the state handle to stamp the bookmark.
        state_path = _state_path_for(project_cwd)
        if state_path is None:
            # Project not enlisted — nothing to do.
            report.duration_ms = (time.monotonic() - t0) * 1000.0
            return report
        state = StateManager(state_path=state_path)

        last_sha = state.get_last_backfilled_commit()
        last_run = state.get_last_watcher_run_at()

        new_commits = _count_new_commits(project_cwd, last_sha)
        jsonl_active = _jsonl_activity_since(project_cwd, last_run)

        report.new_commits = int(new_commits)
        report.jsonl_activity = bool(jsonl_active)

        if _CRASH_AFTER_PROBE == "after_probe":
            raise RuntimeError("simulated crash: after_probe")

        if new_commits == 0 and not jsonl_active:
            # Quiet tick — still record that we probed.
            state.set_last_watcher_run_at()
            report.duration_ms = (time.monotonic() - t0) * 1000.0
            logger.info("quiet tick %s (%.1fms)", project_cwd, report.duration_ms)
            return report

        # Active tick — invoke incremental backfill.
        from ..core import backfill as _bf
        report.backfill_invoked = True
        br = _bf.run_incremental(project_cwd, verbose=verbose)
        report.commits_processed = int(getattr(br, "commits_processed", 0) or 0)
        report.coverage_ratio = float(getattr(br, "coverage_ratio", 0.0) or 0.0)

        if _CRASH_AFTER_PROBE == "after_first_commit":
            raise RuntimeError("simulated crash: after_first_commit")

        # Re-load state — backfill wrote to its own StateManager instance,
        # so our in-memory copy is stale. Reloading avoids clobbering the
        # ``last_backfilled_commit`` advance when we stamp the watcher
        # run-at timestamp.
        state = StateManager(state_path=state_path)
        state.set_last_watcher_run_at()

        # Session sweep (Phase 1 of live-session ingestion). Active ticks
        # only — quiet ticks are short-circuited above. Best-effort:
        # anything that goes wrong here is logged but does NOT fail the
        # commit-attribution tick. The daemon looks up ``scan_project``
        # via module-level attribute so tests can monkeypatch it.
        try:
            sr = scan_project(project_cwd)
            report.sessions_created = int(getattr(sr, "created", 0) or 0)
            report.sessions_refreshed = int(getattr(sr, "refreshed", 0) or 0)
            report.sessions_new_generations = int(
                getattr(sr, "new_generations", 0) or 0
            )
            report.sessions_noops = int(getattr(sr, "noops", 0) or 0)
            report.sessions_errored = int(getattr(sr, "errored", 0) or 0)
        except Exception as sweep_err:  # noqa: BLE001
            logger.exception("session sweep failed for %s", project_cwd)

        report.duration_ms = (time.monotonic() - t0) * 1000.0
        logger.info(
            "tick %s processed=%d coverage=%.2f sessions=+%d/*%d/g%d (%.1fms)",
            project_cwd, report.commits_processed,
            report.coverage_ratio or 0.0,
            report.sessions_created, report.sessions_refreshed,
            report.sessions_new_generations, report.duration_ms,
        )
    except Exception as e:  # noqa: BLE001
        report.error = f"{type(e).__name__}: {e}"
        report.duration_ms = (time.monotonic() - t0) * 1000.0
        logger.exception("tick failed for %s", project_cwd)
    return report


def _state_path_for(project_cwd: Path) -> Path | None:
    """Return the project state.json path, or None if project isn't enlisted."""
    try:
        return get_project_state_path(project_cwd)
    except Exception:  # noqa: BLE001
        return None


def discover_enlisted_projects() -> list[Path]:
    """Scan ``~/.opentraces/projects/*/project.json`` for enlisted projects.

    Returns a list of project_cwd Paths. Projects whose source dir no
    longer exists are filtered out.
    """
    out: list[Path] = []
    if not PROJECTS_DIR.is_dir():
        return out
    for slug_dir in sorted(PROJECTS_DIR.iterdir()):
        manifest = slug_dir / "project.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text()) or {}
        except (OSError, json.JSONDecodeError):
            continue
        src = data.get("project_dir") or data.get("path")
        if not src:
            continue
        p = Path(src)
        if p.is_dir():
            out.append(p)
    return out


def run_forever(interval: int = DEFAULT_INTERVAL, *,
                projects: list[Path] | None = None) -> None:
    """Service loop. Ticks every `interval` seconds across enlisted projects."""
    _configure_logging()
    logger.info("watcher service starting (interval=%ds)", interval)
    while True:
        targets = projects if projects is not None else discover_enlisted_projects()
        for p in targets:
            try:
                run_once(p)
            except Exception:  # noqa: BLE001
                # run_once swallows its own errors; this is pure belt-and-braces.
                logger.exception("run_once escaped for %s", p)
        time.sleep(max(1, int(interval)))


# --- module entrypoint -----------------------------------------------------

def _cli_entry(argv: list[str]) -> int:
    """Minimal argv dispatcher used by the installed shim.

    Usage:
        python -m opentraces.watcher.daemon run-forever [--interval 300]
        python -m opentraces.watcher.daemon tick <project>
    """
    if not argv or argv[0] in ("-h", "--help"):
        print(_cli_entry.__doc__ or "")
        return 0
    if argv[0] == "run-forever":
        interval = DEFAULT_INTERVAL
        i = 1
        while i < len(argv):
            if argv[i] == "--interval" and i + 1 < len(argv):
                try:
                    interval = int(argv[i + 1])
                except ValueError:
                    pass
                i += 2
            else:
                i += 1
        try:
            run_forever(interval=interval)
        except KeyboardInterrupt:
            return 0
        return 0
    if argv[0] == "tick":
        if len(argv) < 2:
            print("usage: tick <project_dir>", file=sys.stderr)
            return 2
        r = run_once(Path(argv[1]))
        print(json.dumps({
            "project_cwd": str(r.project_cwd),
            "duration_ms": round(r.duration_ms, 2),
            "new_commits": r.new_commits,
            "jsonl_activity": r.jsonl_activity,
            "backfill_invoked": r.backfill_invoked,
            "coverage_ratio": r.coverage_ratio,
            "commits_processed": r.commits_processed,
            "error": r.error,
        }, indent=2))
        return 1 if r.error else 0
    print(f"unknown subcommand: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli_entry(sys.argv[1:]))
