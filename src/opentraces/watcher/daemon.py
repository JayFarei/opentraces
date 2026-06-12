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
from dataclasses import dataclass
from pathlib import Path

from ..capture import discover_project_sessions
from ..core import config as _config
from ..core import paths as _paths
from ..core.config import get_project_state_path
from ..core.ingest import scan_project
from ..core.state import StateManager

logger = logging.getLogger("opentraces.watcher")

DEFAULT_INTERVAL = 300
QUIET_TICK_BUDGET_MS = 50  # target only; we don't enforce

# #65: per-tick wall-clock budget for trail maturation. 0 disables the bound.
DEFAULT_MATURATION_BUDGET_S = 120.0


def _maturation_budget_s() -> float:
    raw = os.environ.get("OT_MATURATION_TICK_BUDGET_S")
    if raw is None or raw == "":
        return DEFAULT_MATURATION_BUDGET_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_MATURATION_BUDGET_S
    return max(0.0, value)

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
    fs_observations: int = 0
    fs_reconciled: int = 0
    fs_patches_created: int = 0
    fs_patches_upgraded: int = 0
    trail_maturation_searches: int = 0
    trail_maturation_anchors: int = 0
    bucket_sync_state: str | None = None
    bucket_sync_digest: str | None = None
    bucket_sync_error: str | None = None


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


def _read_head_sha(project_cwd: Path) -> str | None:
    """Resolve HEAD to a sha by reading ``.git`` directly — no subprocess fork.

    Returns None when HEAD can't be resolved from loose refs / packed-refs
    (``.git`` is a worktree/submodule file, detached-HEAD edge cases, unusual
    layouts); callers fall back to ``git rev-list`` in that case.
    """
    git_dir = project_cwd / ".git"
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return None
    if not head.startswith("ref:"):
        return head or None  # detached HEAD — already a sha
    ref = head[4:].strip()
    try:
        return (git_dir / ref).read_text().strip() or None
    except OSError:
        pass
    try:
        for line in (git_dir / "packed-refs").read_text().splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "^")):
                continue
            sha, _, name = line.partition(" ")
            if name == ref:
                return sha or None
    except OSError:
        pass
    return None


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
    """True if any registered parser session has mtime > threshold."""
    session_paths = [path for _agent, path in discover_project_sessions(project_cwd)]
    claude_dir = _claude_jsonl_dir(project_cwd)
    if claude_dir.is_dir():
        # Keep the watcher backstop sensitive to nested Claude subagent JSONLs
        # even though the main ingest path only parses root session files.
        session_paths.extend(claude_dir.rglob("*.jsonl"))
    if not session_paths:
        return False
    if threshold_iso is None:
        # First probe ever; any file is "new".
        return True
    try:
        threshold = datetime.datetime.fromisoformat(
            threshold_iso.replace("Z", "+00:00")
        ).timestamp()
    except (ValueError, TypeError):
        return True
    for p in session_paths:
        try:
            if p.stat().st_mtime > threshold:
                return True
        except OSError:
            continue
    return False


def _logs_dir() -> Path:
    return _paths.OPENTRACES_DIR / "logs"


def _status_file_path() -> Path:
    return _paths.OPENTRACES_DIR / "watcher.status.json"


def _write_status_file(verb: str) -> None:
    """Declare which code this daemon actually runs (#65 provenance).

    The pipx→brew migration left a shim pointing at a deleted interpreter
    while a dev-venv daemon ran unfixed code — and nothing could tell. The
    daemon now self-declares on every start; ``opentraces doctor`` cross-checks
    this against the installed CLI and flags drift. Best-effort: never fails
    the sweep.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            pkg_version = version("opentraces")
        except PackageNotFoundError:
            pkg_version = None
        payload = {
            "schema_version": "opentraces.watcher_status.v1",
            "version": pkg_version,
            "executable": sys.executable,
            "daemon_file": __file__,
            "pid": os.getpid(),
            "verb": verb,
            "started_at": _utcnow().isoformat(),
        }
        path = _status_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(path)
    except Exception:  # noqa: BLE001
        logger.debug("failed to write watcher status file", exc_info=True)


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


def _run_trace_trails_runtime(
    project_cwd: Path,
    report: TickReport,
    *,
    force_maturation: bool = False,
) -> bool:
    """Best-effort Plan 54 runtime loop for one watcher tick.

    Returns True when the tick actually changed local trail state (fs
    observations, reconciled patches, or matured anchors) or maturation was
    forced. Callers use this to skip the HF remote reconcile on idle ticks.
    """
    try:
        from ..capture.fs_watcher.runtime import poll_project_once
        from ..core.trails import reconcile_watcher_observations
        from ..core.trails.maturation import has_unsearched_recent_patches, mature_trails

        poll = poll_project_once(project_cwd)
        report.fs_observations = len(poll.observations)
        # #45 (optional gate): the reconciler's inputs only change between ticks
        # when this poll yielded new filesystem observations OR an active tick
        # appended new step windows via the session sweep (force_maturation is
        # True on active ticks, False on quiet ones; quiet ticks never append
        # windows — scan_project is active-only). On a truly idle quiet tick
        # there is nothing new to attribute, so skip even the scoped read. This
        # does NOT gate maturation, which has its own watermark short-circuit.
        if force_maturation or poll.observations:
            reconcile_summary = reconcile_watcher_observations(project_cwd)
            report.fs_reconciled = int(
                reconcile_summary.get("observations_processed", 0) or 0
            )
            report.fs_patches_created = int(
                reconcile_summary.get("patches_created", 0) or 0
            )
            report.fs_patches_upgraded = int(
                reconcile_summary.get("patches_upgraded", 0) or 0
            )
        should_mature = force_maturation or bool(poll.observations) or bool(
            report.fs_patches_created or report.fs_patches_upgraded
        )
        if not should_mature:
            should_mature = has_unsearched_recent_patches(project_cwd)
        if should_mature:
            # #65: bound one tick's maturation by wall-clock. The per-patch
            # deadline machinery (#44 Phase 2) is durable + idempotent, so a
            # truncated sweep resumes next tick (watermark NOT stamped). A
            # cold backlog amortises across ticks instead of doing 710K
            # searches / 14GB peak in one.
            budget_s = _maturation_budget_s()
            summary = mature_trails(
                project_cwd,
                deadline=(time.monotonic() + budget_s) if budget_s else None,
            )
            report.trail_maturation_searches = int(summary.searches_completed)
            report.trail_maturation_anchors = int(summary.anchors_created)
            if summary.truncated:
                logger.info(
                    "trail maturation truncated at %.0fs budget for %s; "
                    "resuming next tick", budget_s, project_cwd,
                )
            if summary.errors:
                logger.warning(
                    "trail maturation completed with errors for %s: %s",
                    project_cwd,
                    "; ".join(summary.errors[:3]),
                )
        # Only export the trail event log and re-project the Context Tree to
        # the bucket when this tick actually changed something. These two
        # operations are unbounded in cost (full git ref export + whole
        # context-tree projection) and were the dominant per-tick cost across
        # hundreds of idle projects; gating them on real change is what keeps
        # an idle sweep cheap.
        changed = bool(
            force_maturation
            or report.fs_observations
            or report.fs_patches_created
            or report.fs_patches_upgraded
            or report.trail_maturation_searches
            or report.trail_maturation_anchors
        )
        if changed:
            from ..core.config import get_project_dir
            project_slug = get_project_dir(project_cwd).name
            try:
                from ..core.bucket_store import sync_trail_events_from_repo

                sync_trail_events_from_repo(project_cwd, repo_id=project_slug)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "bucket TrailEvent export refresh failed for %s",
                    project_cwd,
                    exc_info=True,
                )
            # Plan 079: first-class Context Tree bucket projection. Stage 2
            # is additive; the trail-piggyback above remains.
            #
            # #65: bounded + watermark-gated. The unconditional call here
            # previously did TWO full read_events walks per changed tick
            # (~872K pydantic events + the 2GB snapshot pickle — the 12GB
            # spike in the live capture). The daemon now (a) skips the
            # projection entirely when no context_* events landed since the
            # last projected head, and (b) feeds the projection from a
            # SCOPED context-type read plus the all-type suffix, so its peak
            # is bounded by context density, not log size. Repair/status
            # verbs keep the legacy full-fidelity path.
            try:
                _project_context_tree_bounded(project_cwd, project_slug)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "bucket Context Tree projection failed for %s",
                    project_cwd,
                    exc_info=True,
                )
        return changed
    except Exception:  # noqa: BLE001
        logger.exception("Trace Trails runtime failed for %s", project_cwd)
        return False


def _ctx_projection_watermark_path(project_cwd: Path) -> Path | None:
    proc = subprocess.run(
        ["git", "-C", str(project_cwd), "rev-parse", "--git-dir"],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        return None
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (project_cwd / git_dir).resolve()
    return git_dir / "opentraces" / "context_projection_watermark.json"


def _project_context_tree_bounded(project_cwd: Path, project_slug: str) -> None:
    """Watermark-gated, scoped-read Context Tree bucket projection (#65)."""
    from ..core.bucket_store import project_context_tree_to_bucket
    from ..core.context_tree.contract import CONTEXT_EVENT_TYPES
    from ..core.trails.event_log import (
        _ref_head,
        read_events_scoped,
        read_events_since,
    )

    wm_path = _ctx_projection_watermark_path(project_cwd)
    watermark: str | None = None
    if wm_path is not None and wm_path.is_file():
        try:
            watermark = (json.loads(wm_path.read_text()) or {}).get("head")
        except (OSError, json.JSONDecodeError):
            watermark = None

    head_now = _ref_head(project_cwd)
    if head_now is None:
        return  # no event log — nothing to project

    suffix: list | None = None
    if watermark:
        head_now, suffix = read_events_since(project_cwd, watermark)
        if head_now is None:
            return
        if suffix is not None and not any(
            ev.event_type in CONTEXT_EVENT_TYPES for ev in suffix
        ):
            # No context activity since the last projection: heads are
            # current for context purposes; just advance the watermark.
            _stamp_ctx_watermark(wm_path, head_now)
            return

    # Bootstrap (no/stale watermark) or context activity present: project
    # from the scoped context slice. Bounded by context density by
    # construction — the daemon never takes the full-read path.
    ctx_events = read_events_scoped(
        project_cwd, event_types=set(CONTEXT_EVENT_TYPES)
    )
    changed_tids: set[str] = set()
    source = suffix if suffix is not None else ctx_events
    for ev in source:
        if ev.event_type not in CONTEXT_EVENT_TYPES:
            continue
        tid = ev.trace_id or (
            ev.payload.get("trace_id") if isinstance(ev.payload, dict) else None
        )
        if tid:
            changed_tids.add(str(tid))
    for tid in sorted(changed_tids):
        project_context_tree_to_bucket(
            project_cwd,
            project_slug=project_slug,
            trace_id=tid,
            events=ctx_events,
            seq_suffix=suffix or [],
        )
    _stamp_ctx_watermark(wm_path, head_now)


def _stamp_ctx_watermark(wm_path: Path | None, head: str | None) -> None:
    if wm_path is None or head is None:
        return
    try:
        wm_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = wm_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"head": head}))
        tmp.replace(wm_path)
    except OSError:
        logger.debug("failed to stamp context projection watermark", exc_info=True)


def _bucket_reconcile_once(*, reason: str) -> dict:
    from ..core.bucket_remote import reconcile_once

    return reconcile_once(reason=reason)


def _run_bucket_remote_sync(report: TickReport) -> None:
    try:
        result = _bucket_reconcile_once(reason="watcher")
        report.bucket_sync_state = str(result.get("state") or "")
        digest = result.get("digest") or result.get("remote_digest") or result.get("local_digest")
        report.bucket_sync_digest = str(digest) if digest else None
        error = result.get("error")
        report.bucket_sync_error = str(error) if error else None
    except Exception as exc:  # noqa: BLE001
        report.bucket_sync_state = "error"
        report.bucket_sync_error = f"{type(exc).__name__}: {exc}"
        logger.exception("bucket remote sync failed")


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

        # Fork-free fast path: if HEAD still points at the commit we last
        # backfilled there are no new commits, so skip the ``git rev-list``
        # subprocess. Across hundreds of enlisted projects this removes a
        # fork+exec per project on every sweep.
        if last_sha and _read_head_sha(project_cwd) == last_sha:
            new_commits = 0
        else:
            new_commits = _count_new_commits(project_cwd, last_sha)
        jsonl_active = _jsonl_activity_since(project_cwd, last_run)

        report.new_commits = int(new_commits)
        report.jsonl_activity = bool(jsonl_active)

        if _CRASH_AFTER_PROBE == "after_probe":
            raise RuntimeError("simulated crash: after_probe")

        if new_commits == 0 and not jsonl_active:
            # Quiet tick: still poll + reconcile + mature (anchors mature over
            # time, independent of new commits/JSONL), but only reconcile the
            # HF remote when that runtime actually changed local state. An idle
            # project must not pay a network reconcile on every sweep.
            changed = _run_trace_trails_runtime(
                project_cwd, report, force_maturation=False
            )
            if changed:
                _run_bucket_remote_sync(report)
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
            sr = scan_project(project_cwd, reconcile_watcher=True)
            report.sessions_created = int(getattr(sr, "created", 0) or 0)
            report.sessions_refreshed = int(getattr(sr, "refreshed", 0) or 0)
            report.sessions_new_generations = int(
                getattr(sr, "new_generations", 0) or 0
            )
            report.sessions_noops = int(getattr(sr, "noops", 0) or 0)
            report.sessions_errored = int(getattr(sr, "errored", 0) or 0)
        except Exception:  # noqa: BLE001
            logger.exception("session sweep failed for %s", project_cwd)

        _run_trace_trails_runtime(project_cwd, report, force_maturation=True)
        _run_bucket_remote_sync(report)

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
    finally:
        # #45: drop this project's parsed-event memo at the end of every tick so
        # the daemon's RSS floor is not the largest repo's full parsed log held
        # alive across ticks. The cache exists to dedupe reads WITHIN one tick;
        # the next tick re-reads (incrementally, from the on-disk snapshot) and
        # rebuilds only what it needs. Best-effort: never let teardown raise.
        try:
            from ..core.trails.event_log import invalidate_read_events_cache

            invalidate_read_events_cache(project_cwd)
        except Exception:  # noqa: BLE001
            pass
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
    if not _config.PROJECTS_DIR.is_dir():
        return out
    for slug_dir in sorted(_config.PROJECTS_DIR.iterdir()):
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


# --- RSS self-check backstop (#45) -----------------------------------------

DEFAULT_MAX_RSS_MB = 2048


def _current_rss_mb() -> float | None:
    """Best-effort CURRENT (not peak) resident set size of this process, in MiB.

    macOS ``ru_maxrss`` is peak-only and never shrinks, so it can't drive a
    restart-on-current-bloat backstop. We read live RSS instead:

    - Linux: ``/proc/self/statm`` field 2 (resident pages) × page size.
    - macOS / other POSIX: ``ps -o rss= -p <pid>`` (KiB).

    Returns None when neither source is readable (the caller then skips the
    backstop rather than restarting on a bad read).
    """
    statm = Path("/proc/self/statm")
    if statm.is_file():
        try:
            resident_pages = int(statm.read_text().split()[1])
            page_size = os.sysconf("SC_PAGE_SIZE")
            return resident_pages * page_size / (1024 * 1024)
        except Exception:  # noqa: BLE001 — fall through to ps
            pass
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True,
            text=True,
            check=False,
        )
        kib = int(out.stdout.strip())
        return kib / 1024.0
    except Exception:  # noqa: BLE001
        return None


def _max_rss_mb() -> float:
    """Configured RSS ceiling (MiB) from ``OT_WATCHER_MAX_RSS_MB``."""
    raw = os.environ.get("OT_WATCHER_MAX_RSS_MB")
    if not raw:
        return float(DEFAULT_MAX_RSS_MB)
    try:
        value = float(raw)
    except ValueError:
        return float(DEFAULT_MAX_RSS_MB)
    return value if value > 0 else float(DEFAULT_MAX_RSS_MB)


def _reexec_self(interval: int) -> None:
    """Replace this process with a fresh ``run-forever``, preserving interval.

    Called ONLY between sweeps (never mid-tick) when RSS breaches the ceiling.
    The installer's launchd plist uses StartInterval+RunAtLoad+ThrottleInterval
    with no KeepAlive, so a self-replacement is supervisor-safe — the supervisor
    sees one continuously-running PID, not a crash. ``os.execv`` keeps the same
    PID and inherits stdio/log handles.
    """
    argv = [sys.executable, "-m", "opentraces.watcher.daemon",
            "run-forever", "--interval", str(int(interval))]
    logger.warning(
        "RSS backstop re-exec: argv=%s",
        " ".join(argv),
    )
    os.execv(sys.executable, argv)


# --- budgeted child ticks + one-shot sweep (#65) ---------------------------

# 4096MiB: the worst LEGITIMATE tick measured on the #65 repo (872K-event log,
# maturation needle working set ~18K patches × ~34KB authored_text) peaks
# around 2-3GiB; the budget leaves headroom above that while still killing
# the pathological unbounded growth (8-15GB observed) long before it takes
# the machine. Tune per-host via OT_WATCHER_TICK_MAX_RSS_MB.
DEFAULT_TICK_MAX_RSS_MB = 4096
DEFAULT_TICK_TIMEOUT_S = 900


def _tick_budget_mb() -> float:
    raw = os.environ.get("OT_WATCHER_TICK_MAX_RSS_MB")
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        value = 0.0
    return value if value > 0 else float(DEFAULT_TICK_MAX_RSS_MB)


def _tick_timeout_s() -> float:
    raw = os.environ.get("OT_WATCHER_TICK_TIMEOUT_S")
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        value = 0.0
    return value if value > 0 else float(DEFAULT_TICK_TIMEOUT_S)


# #65 soak finding (run 1): a real home can carry hundreds of enlistments, and
# a sweep with only PER-CHILD budgets exceeded an hour — overlapping every
# supervision interval. The sweep itself gets a wall budget; projects that
# don't fit are DEFERRED, and the least-recently-ticked-first ordering below
# guarantees deferred tails go first next sweep (no starvation).
DEFAULT_SWEEP_BUDGET_S = 1500.0


def _sweep_budget_s() -> float:
    raw = os.environ.get("OT_WATCHER_SWEEP_BUDGET_S")
    try:
        value = float(raw) if raw else 0.0
    except ValueError:
        value = 0.0
    return value if value > 0 else DEFAULT_SWEEP_BUDGET_S


# Minimum leftover budget worth starting a child for: below this, defer.
MIN_CHILD_BUDGET_S = 30.0


def _attempt_marker_path(project_cwd: Path) -> Path | None:
    """Sweep-attempt marker inside the enlistment dir (pruned with it)."""
    try:
        return get_project_state_path(project_cwd).parent / "last_sweep_attempt"
    except Exception:  # noqa: BLE001
        return None


def _stamp_attempt(project_cwd: Path) -> None:
    """Record that a sweep ATTEMPTED this project, regardless of verdict.

    #65 (codex round 3): killed/errored ticks never stamp state.json, so
    ordering by state mtime alone re-sorts the same pathological project to
    the FRONT of every bounded sweep — starving healthy projects behind it.
    The marker makes the ordering attempt-aware: one try per rotation.
    """
    marker = _attempt_marker_path(project_cwd)
    if marker is None:
        return
    try:
        marker.touch()
    except OSError:
        pass


def _last_tick_mtime(project_cwd: Path) -> float:
    """Last tick OR attempt time for fair sweep ordering; 0.0 = never.

    max(state.json mtime, attempt-marker mtime): successful ticks advance
    state.json, killed/errored ticks advance only the marker — either way
    the project goes to the BACK of the next sweep's rotation.
    """
    newest = 0.0
    try:
        state_path = get_project_state_path(project_cwd)
        if state_path.is_file():
            newest = state_path.stat().st_mtime
    except Exception:  # noqa: BLE001
        pass
    marker = _attempt_marker_path(project_cwd)
    try:
        if marker is not None and marker.is_file():
            newest = max(newest, marker.stat().st_mtime)
    except OSError:
        pass
    return newest


def _child_rss_mb(pid: int) -> float | None:
    """CURRENT RSS of ``pid`` in MiB, or None when unreadable."""
    statm = Path(f"/proc/{pid}/statm")
    if statm.is_file():
        try:
            resident_pages = int(statm.read_text().split()[1])
            return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
        except Exception:  # noqa: BLE001 — fall through to ps
            pass
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True, text=True, check=False,
        )
        return int(out.stdout.strip()) / 1024.0
    except Exception:  # noqa: BLE001
        return None


def _tick_in_child(
    project_cwd: Path,
    *,
    budget_mb: float,
    timeout_s: float,
    _argv: list[str] | None = None,
) -> str:
    """Run one project tick in a budgeted child process.

    Returns ``"ok"``, ``"rss_killed"``, ``"timeout_killed"``, or
    ``"error:<rc>"``. The #65 invariant this enforces: a pathological project
    can never take the sweep parent's memory with it — the child is killed at
    the budget and the sweep continues with the next project.

    ``_argv`` overrides the child command (tests substitute a controlled
    allocator/sleeper so the kill paths are exercised against REAL processes
    and REAL RSS readings, not monkeypatched meters).
    """
    argv = _argv or [sys.executable, "-m", "opentraces.watcher.daemon",
                     "tick", str(project_cwd)]
    proc = subprocess.Popen(
        argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    started = time.monotonic()
    verdict = "ok"
    while True:
        rc = proc.poll()
        if rc is not None:
            verdict = "ok" if rc == 0 else f"error:{rc}"
            break
        elapsed = time.monotonic() - started
        rss = _child_rss_mb(proc.pid)
        if rss is not None and rss >= budget_mb:
            logger.warning(
                "tick child for %s breached RSS budget (%.0fMiB >= %.0fMiB) — killed",
                project_cwd, rss, budget_mb,
            )
            proc.kill()
            proc.wait(timeout=10)
            verdict = "rss_killed"
            break
        if elapsed >= timeout_s:
            logger.warning(
                "tick child for %s breached wall budget (%.0fs >= %.0fs) — killed",
                project_cwd, elapsed, timeout_s,
            )
            proc.kill()
            proc.wait(timeout=10)
            verdict = "timeout_killed"
            break
        time.sleep(0.5)
    return verdict


def run_sweep(
    *,
    projects: list[Path] | None = None,
    in_process: bool = False,
) -> dict:
    """One bounded pass over the enlisted projects, then return.

    The production entrypoint for launchd supervision (#65): the installed
    plist uses StartInterval+RunAtLoad with no KeepAlive, so a process that
    exits after each sweep is restarted on the interval — the supervisor IS
    the service loop, and a process that exits cannot accumulate memory
    across sweeps by construction. Each project ticks in a budgeted child
    (see ``_tick_in_child``) unless ``in_process`` is set (tests / debugging).
    """
    _configure_logging()
    _write_status_file("run-sweep")
    prune_summary: dict | None = None
    if projects is None:
        # #23 finished: drop leaked/dead enlistments BEFORE enumerating, so
        # the sweep surface is the real project population, not 800+ test
        # fixture leaks. Best-effort; never blocks the sweep.
        try:
            from .prune import prune_enlistments

            prune_summary = prune_enlistments()
        except Exception:  # noqa: BLE001
            logger.exception("enlistment pruning failed")
    targets = projects if projects is not None else discover_enlisted_projects()
    # Least-recently-ticked first: combined with the sweep budget below this
    # is a fair rotation — projects deferred by one budget-cut sweep are at
    # the FRONT of the next one.
    targets = sorted(targets, key=_last_tick_mtime)
    budget_mb = _tick_budget_mb()
    timeout_s = _tick_timeout_s()
    sweep_deadline = time.monotonic() + _sweep_budget_s()
    summary = {"projects": len(targets), "ok": 0, "rss_killed": 0,
               "timeout_killed": 0, "errors": 0, "deferred": 0}
    if prune_summary is not None:
        summary["pruned"] = (
            prune_summary.get("pruned_tmp", 0)
            + prune_summary.get("pruned_missing", 0)
        )
    for index, p in enumerate(targets):
        # #65 (codex round 3): cap each child to the REMAINING sweep budget —
        # a child started with seconds left but a 900s per-child timeout
        # would overrun the sweep budget by minutes and overlap the
        # supervisor interval. Below MIN_CHILD_BUDGET_S, defer instead.
        remaining = sweep_deadline - time.monotonic()
        if remaining < MIN_CHILD_BUDGET_S:
            summary["deferred"] = len(targets) - index
            logger.info(
                "sweep budget reached: deferring %d projects to the next sweep",
                summary["deferred"],
            )
            break
        try:
            if in_process:
                run_once(p)
                verdict = "ok"
            else:
                verdict = _tick_in_child(
                    p, budget_mb=budget_mb,
                    timeout_s=min(timeout_s, remaining),
                )
        except Exception:  # noqa: BLE001
            logger.exception("sweep tick escaped for %s", p)
            verdict = "error:exception"
        finally:
            _stamp_attempt(p)
        if verdict == "ok":
            summary["ok"] += 1
        elif verdict == "rss_killed":
            summary["rss_killed"] += 1
        elif verdict == "timeout_killed":
            summary["timeout_killed"] += 1
        else:
            summary["errors"] += 1
    logger.info(
        "sweep complete: %d projects, %d ok, %d rss-killed, %d timeout-killed, "
        "%d errors, %d deferred",
        summary["projects"], summary["ok"], summary["rss_killed"],
        summary["timeout_killed"], summary["errors"], summary["deferred"],
    )
    return summary


def run_forever(interval: int = DEFAULT_INTERVAL, *,
                projects: list[Path] | None = None) -> None:
    """Legacy service loop. Ticks every `interval` seconds across enlisted
    projects, in-process.

    Production installs use the one-shot ``run-sweep`` verb under launchd
    StartInterval supervision instead (#65). This loop remains for older
    shims and tests; the #45 re-exec backstop now ALSO runs mid-sweep
    (between projects — never mid-tick) so a multi-hour sweep can no longer
    outrun the ceiling check.
    """
    _configure_logging()
    _write_status_file("run-forever")
    logger.info("watcher service starting (interval=%ds)", interval)
    max_rss_mb = _max_rss_mb()

    def _check_backstop() -> None:
        rss_mb = _current_rss_mb()
        if rss_mb is not None and rss_mb >= max_rss_mb:
            logger.warning(
                "watcher RSS %.0fMiB >= ceiling %.0fMiB — re-exec backstop",
                rss_mb, max_rss_mb,
            )
            _reexec_self(interval)

    while True:
        targets = projects if projects is not None else discover_enlisted_projects()
        for p in targets:
            try:
                run_once(p)
            except Exception:  # noqa: BLE001
                # run_once swallows its own errors; this is pure belt-and-braces.
                logger.exception("run_once escaped for %s", p)
            # #65: the between-sweeps-only check was unreachable on real
            # machines (869 enlistments × multi-minute ticks = the sweep never
            # ends), which is how a 2048MiB ceiling coexisted with a 15.8GB
            # daemon. Checking between projects keeps the "never mid-tick"
            # safety property with a bound that can actually fire.
            _check_backstop()
        _check_backstop()
        time.sleep(max(1, int(interval)))


# --- module entrypoint -----------------------------------------------------

def _cli_entry(argv: list[str]) -> int:
    """Minimal argv dispatcher used by the installed shim.

    Usage:
        python -m opentraces.watcher.daemon run-sweep [--in-process]
        python -m opentraces.watcher.daemon run-forever [--interval 300]
        python -m opentraces.watcher.daemon tick <project>
    """
    if not argv or argv[0] in ("-h", "--help"):
        print(_cli_entry.__doc__ or "")
        return 0
    if argv[0] == "run-sweep":
        summary = run_sweep(in_process="--in-process" in argv[1:])
        print(json.dumps(summary))
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
            "sessions_created": r.sessions_created,
            "sessions_refreshed": r.sessions_refreshed,
            "sessions_new_generations": r.sessions_new_generations,
            "sessions_noops": r.sessions_noops,
            "sessions_errored": r.sessions_errored,
            "fs_observations": r.fs_observations,
            "fs_reconciled": r.fs_reconciled,
            "fs_patches_created": r.fs_patches_created,
            "fs_patches_upgraded": r.fs_patches_upgraded,
            "trail_maturation_searches": r.trail_maturation_searches,
            "trail_maturation_anchors": r.trail_maturation_anchors,
            "bucket_sync_state": r.bucket_sync_state,
            "bucket_sync_digest": r.bucket_sync_digest,
            "bucket_sync_error": r.bucket_sync_error,
            "error": r.error,
        }, indent=2))
        return 1 if r.error else 0
    print(f"unknown subcommand: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli_entry(sys.argv[1:]))
