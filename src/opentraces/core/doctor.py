"""Collect health signals for `opentraces doctor`.

Pure aggregator: reads config + filesystem state, returns a dict. The CLI
layer is responsible for rendering. Keeping the logic here means doctor's
shape is testable without going through Click, and any future surfaces
(web status panel, CI health check) can reuse it.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..capture import get_hook_installers
from ..core.config import ProjectConfig, load_project_config, project_is_opted_in
from ..core.integration_repair import integration_version_report
from ..core.integration_versions import (
    current_cli_version,
    extract_version_stamp,
    version_status,
)
from ..core.processors import probe_processors
from ..enrichment.entities import EntityRunner
from ..enrichment.entities.installer import _safe_platform
from ..enrichment.entities.runner import resolve_binary_path
from ..security.trufflehog import find_trufflehog
from ..security.version import SECURITY_VERSION


_BUCKET_MANIFEST_SCHEMA = "opentraces.bucket.manifest.v2"
_DOCTOR_BUCKET_MANIFEST_MAX_BYTES = 16 * 1024 * 1024
_DOCTOR_EVENT_LOG_MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
_DOCTOR_EVENT_LOG_MAX_BATCHES = 1_000


# --- Trace Trails event-log panel (plan 054 phase 1) ----------------------


# Issue #96: map the live ``search_snapshot`` state onto the top-level
# ``trace_index.state`` machine contract. ``wrong_schema`` is a recoverable
# stale-shaped condition (rebuild fixes it); ``unreadable``/anything unexpected
# is a hard error.
_SNAPSHOT_TO_TRACE_INDEX_STATE = {
    "ok": "ok",
    "stale": "stale",
    "missing": "missing",
    "wrong_schema": "stale",
    "unreadable": "error",
    "error": "error",
}


def _trace_index_status() -> dict[str, Any]:
    """Report local Trace Index cache status for Plan 56."""
    from . import paths
    from .trace_index import INDEX_VERSION, default_index_path

    index_path = default_index_path()
    search_snapshot_advice = "opentraces trace index rebuild"
    legacy_rebuild_advice = "opentraces trace index rebuild --legacy"
    legacy_artifacts = _legacy_trace_index_artifacts()
    # Plan 087 U5: report search-projection freshness cheaply (no heavy repair).
    # ``search_projection_freshness`` is a read-only digest comparison; the
    # remedy when stale is ``trace index refresh`` (cheap sync).
    try:
        from .search_projection import search_projection_freshness

        search_freshness = search_projection_freshness()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash.
        search_freshness = {"state": "error", "error": str(exc)}
    source_files = sorted(paths.PROJECTS_DIR.glob("*/traces/*.jsonl")) if paths.PROJECTS_DIR.exists() else []
    source_latest_mtime = max((p.stat().st_mtime for p in source_files), default=None)
    # The read-only search snapshot is the surface ``trace query`` actually
    # serves from; report it alongside the legacy index it replaced.
    try:
        from .trace_search_snapshot import snapshot_status

        search_snapshot = snapshot_status()
    except Exception as exc:  # noqa: BLE001 — doctor must never crash.
        search_snapshot = {"state": "error", "error": str(exc)}
    # Issue #96: the top-level ``state``/``rebuild_advice`` are the machine
    # contract an agent reads, and they MUST describe the live snapshot that
    # ``trace query`` actually serves — not the deprecated ``index.db``.
    # ``legacy_index_state`` carries the old legacy-driven concern, so the
    # absence of the legacy cache (the normal post-#89 state) never reads as a
    # current failure when search is healthy.
    snapshot_state = (search_snapshot or {}).get("state", "missing")
    top_state = _SNAPSHOT_TO_TRACE_INDEX_STATE.get(snapshot_state, "error")
    base = {
        "index_path": str(index_path),
        "expected_version": INDEX_VERSION,
        "source_trace_files": len(source_files),
        "source_latest_mtime": source_latest_mtime,
        # Authoritative top-level state/advice = the live search snapshot.
        "state": top_state,
        "rebuild_advice": search_snapshot_advice,
        "search_snapshot_advice": search_snapshot_advice,
        "legacy_rebuild_advice": legacy_rebuild_advice,
        "legacy_artifacts": legacy_artifacts,
        "legacy_warning": bool(legacy_artifacts),
        "search_projection_freshness": search_freshness,
        "search_snapshot": search_snapshot,
    }
    if not index_path.exists():
        return {
            **base,
            "legacy_index_state": "missing",
            "trace_count": 0,
            "unit_count": 0,
            "map_node_count": 0,
        }

    try:
        with sqlite3.connect(index_path) as conn:
            version_row = conn.execute(
                "select value from meta where key = 'index_version'"
            ).fetchone()
            trace_count = conn.execute("select count(*) from traces").fetchone()[0]
            unit_count = conn.execute("select count(*) from units").fetchone()[0]
            map_node_count = conn.execute("select count(*) from trace_map_nodes").fetchone()[0]
    except Exception as exc:
        return {
            **base,
            "legacy_index_state": "error",
            "error": str(exc),
            "trace_count": 0,
            "unit_count": 0,
            "map_node_count": 0,
        }

    index_mtime = index_path.stat().st_mtime
    version = version_row[0] if version_row else None
    stale = bool(source_latest_mtime is not None and source_latest_mtime > index_mtime)
    legacy_index_state = "stale" if stale or version != INDEX_VERSION else "ok"
    return {
        **base,
        "legacy_index_state": legacy_index_state,
        "index_version": version,
        "index_mtime": index_mtime,
        "trace_count": trace_count,
        "unit_count": unit_count,
        "map_node_count": map_node_count,
    }


def _bucket_status() -> dict[str, Any]:
    """Report local bucket health without regenerating bucket projections."""
    from . import paths
    from .bucket_layout import bucket_manifest_path

    manifest_path = bucket_manifest_path()
    base = {
        "root": str(paths.bucket_dir()),
        "digest": None,
        "trace_records": {},
        "trail": {},
        "sync": {},
        "manifest_path": str(manifest_path),
        "context_tree": _bucket_context_tree_section(None),
    }
    if not manifest_path.exists():
        return {
            **base,
            "state": "not-built",
            "advice": "run 'opentraces bucket status' to build the bucket manifest",
        }

    try:
        manifest_bytes = manifest_path.stat().st_size
    except OSError as exc:
        return {**base, "state": "error", "error": str(exc)}

    max_bytes = _doctor_bucket_manifest_max_bytes()
    if manifest_bytes > max_bytes:
        return {
            **base,
            "state": "too-large",
            "manifest_bytes": manifest_bytes,
            "max_manifest_bytes": max_bytes,
            "advice": "run 'opentraces bucket status' for a full bucket scan",
        }

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {**base, "state": "error", "error": str(exc)}
    if not isinstance(manifest, dict):
        return {**base, "state": "error", "error": "manifest is not a JSON object"}
    if manifest.get("schema_version") != _BUCKET_MANIFEST_SCHEMA:
        return {
            **base,
            "state": "error",
            "error": (
                "manifest schema is "
                f"{manifest.get('schema_version')!r}, expected "
                f"{_BUCKET_MANIFEST_SCHEMA!r}"
            ),
        }

    trace_records = manifest.get("trace_records") or {}
    return {
        **base,
        "state": "ok",
        "root": manifest.get("root") or str(paths.bucket_dir()),
        "digest": manifest.get("digest") or manifest.get("bucket_digest"),
        "trace_records": trace_records,
        "trail": manifest.get("trail") or {},
        "sync": manifest.get("sync") or {},
        "security_remediation": _bucket_security_remediation_for_doctor(trace_records),
        "manifest_bytes": manifest_bytes,
        "context_tree": _bucket_context_tree_section(manifest),
    }


def _bucket_security_remediation_for_doctor(
    trace_records: dict[str, Any],
) -> dict[str, Any] | None:
    """Actionable next step when bucket records are not remote-sync eligible.

    Cheap: counts come from the already-loaded manifest and ``configured`` is a
    config read — no bucket scan. Distinguishes ``not_configured`` (filter off)
    from ``pending`` (filter configured but not yet applied) so doctor can name
    the exact command (issue #89).
    """

    unfiltered = int(trace_records.get("unfiltered_count") or 0)
    stale = int(trace_records.get("security_stale_count") or 0)
    if unfiltered == 0 and stale == 0:
        return None
    try:
        from .bucket_security import bucket_sync_security_gate

        # Convergence by construction (issue #94): doctor and `bucket remote
        # status` both drive the same shared gate off the persisted manifest
        # counts, so their remediation can never drift.
        return bucket_sync_security_gate(unfiltered=unfiltered, stale=stale)[
            "remediation"
        ]
    except Exception:
        return None


def _doctor_bucket_manifest_max_bytes() -> int:
    # Single source of truth (issue #94): doctor's bucket panel and the
    # `bucket remote status` security gate degrade on an oversized manifest at
    # the SAME byte cap.
    from .bucket_store import bucket_manifest_max_bytes

    return bucket_manifest_max_bytes()


def _bucket_context_tree_section(
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a bounded Context Tree bucket summary from the manifest.

    ``compute_context_tree_status`` walks every projected head and opted-in
    event log. Doctor is a health check, so it reports the latest persisted
    manifest summary instead of rebuilding that state.
    """
    defaults = {
        "state": "not-built",
        "last_projection_at": None,
        "events_since_last_projection": 0,
        "oldest_unprojected_event_time": None,
        "trace_count": 0,
        "layer_blob_count": 0,
        "dangling_layer_refs": 0,
        "remote_sync_eligible": False,
        "source": "persisted_manifest",
    }
    if not manifest:
        return defaults
    snapshot = manifest.get("context_trees") or {}
    if not isinstance(snapshot, dict) or not snapshot:
        return defaults

    def _int_field(name: str) -> int:
        try:
            return int(snapshot.get(name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    return {
        **defaults,
        "state": snapshot.get("state") or "ok",
        "last_projection_at": snapshot.get("last_projection_at"),
        "events_since_last_projection": _int_field("events_since_last_projection"),
        "oldest_unprojected_event_time": snapshot.get(
            "oldest_unprojected_event_time"
        ),
        "trace_count": _int_field("trace_count"),
        "layer_blob_count": _int_field("unique_layer_blob_count"),
        "dangling_layer_refs": _int_field("dangling_layer_refs_count"),
        "remote_sync_eligible": bool(snapshot.get("remote_sync_eligible", False)),
    }


def _legacy_trace_index_artifacts() -> list[dict[str, Any]]:
    from . import paths

    candidates = [
        paths.OPENTRACES_DIR / "trace_index.json",
        paths.OPENTRACES_DIR / "db" / "ot.sqlite",
    ]
    out: list[dict[str, Any]] = []
    for artifact in candidates:
        if artifact.exists():
            out.append(
                {
                    "path": str(artifact),
                    "status": "ignored",
                    "replacement": str(paths.OPENTRACES_DIR / "index" / "index.db"),
                }
            )
    return out


def _trail_event_log_status(cwd: Path) -> dict[str, Any]:
    """Report integrity for the canonical local Trace Trails event log."""
    try:
        from .trails import event_log_status
        from .trails.event_log import EVENT_LOG_REF, _load_verify_watermark, _ref_head

        head = _ref_head(cwd)
        if head is None:
            return {
                "ref": EVENT_LOG_REF,
                "exists": False,
                "head": None,
                "batch_count": 0,
                "event_count": 0,
                "batch_parents_linear": False,
                "content_hashes_valid": False,
                "event_chain_valid": False,
                "state": "missing",
                "errors": [],
            }

        skip_reason = _doctor_event_log_skip_reason(cwd, head=head)
        if skip_reason is not None:
            batch_count = _event_log_batch_count(cwd, head)
            watermark = _load_verify_watermark(cwd)
            base = {
                "ref": EVENT_LOG_REF,
                "exists": True,
                "head": head,
                "batch_count": batch_count,
                "doctor_scan_skipped": True,
                "skip_reason": skip_reason,
                "advice": (
                    "run 'opentraces trail verify --mode quick --json' for "
                    "a bounded event-log summary; use '--mode full --progress "
                    "plain' for full verification"
                ),
                "errors": [],
            }
            if watermark and watermark.get("head") == head:
                return {
                    **base,
                    "state": "ok",
                    "event_count": watermark.get("last_event_sequence", 0),
                    "last_event_sequence": watermark.get("last_event_sequence"),
                    "last_event_id": watermark.get("last_event_id"),
                    "batch_parents_linear": True,
                    "content_hashes_valid": True,
                    "event_chain_valid": True,
                    "verification_source": "cached_watermark",
                }
            return {
                **base,
                "state": "unverified_large",
                "event_count": None,
                "batch_parents_linear": None,
                "content_hashes_valid": None,
                "event_chain_valid": None,
                "verification_source": "skipped_large_log",
            }

        status = event_log_status(cwd)
        if status.get("state") in ("invalid", "error"):
            status["advice"] = (
                "run 'opentraces trail verify --mode full --progress plain' "
                "to inspect canonical event-log failures"
            )
        return status
    except Exception as exc:
        return {
            "ref": "refs/opentraces/local/events/v1",
            "exists": False,
            "head": None,
            "batch_count": 0,
            "event_count": 0,
            "batch_parents_linear": False,
            "content_hashes_valid": False,
            "event_chain_valid": False,
            "state": "error",
            "errors": [str(exc)],
        }


def _doctor_event_log_skip_reason(cwd: Path, *, head: str | None = None) -> str | None:
    """Return why doctor should avoid a whole event-log scan, or None."""
    try:
        from .trails.event_log import _event_cache_path, _ref_head
    except Exception:
        return None

    head = head or _ref_head(cwd)
    if head is None:
        return None

    snapshot_max = _doctor_event_log_max_snapshot_bytes()
    snapshot_path = _event_cache_path(cwd)
    if snapshot_path is not None and snapshot_path.is_file():
        try:
            size = snapshot_path.stat().st_size
        except OSError:
            size = 0
        if size > snapshot_max:
            return f"event snapshot {size} bytes exceeds doctor limit {snapshot_max}"

    batch_count = _event_log_batch_count(cwd, head)
    batch_max = _doctor_event_log_max_batches()
    if batch_count is not None and batch_count > batch_max:
        return f"event log has {batch_count} batches; doctor limit is {batch_max}"
    return None


def _doctor_event_log_max_snapshot_bytes() -> int:
    raw = os.environ.get("OPENTRACES_DOCTOR_EVENT_LOG_MAX_SNAPSHOT_BYTES")
    if raw:
        try:
            return max(1024, int(raw))
        except ValueError:
            return _DOCTOR_EVENT_LOG_MAX_SNAPSHOT_BYTES
    return _DOCTOR_EVENT_LOG_MAX_SNAPSHOT_BYTES


def _doctor_event_log_max_batches() -> int:
    raw = os.environ.get("OPENTRACES_DOCTOR_EVENT_LOG_MAX_BATCHES")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            return _DOCTOR_EVENT_LOG_MAX_BATCHES
    return _DOCTOR_EVENT_LOG_MAX_BATCHES


def _event_log_batch_count(cwd: Path, head: str) -> int | None:
    try:
        import subprocess as _sp

        proc = _sp.run(
            ["git", "rev-list", "--count", head],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            return None
        return int(proc.stdout.strip())
    except (OSError, ValueError):
        return None


# --- post-commit hook panel (plan 047) ------------------------------------

def _post_commit_hook_status(cwd: Path) -> dict[str, Any]:
    """Report on the post-commit correlator: hook installed, last run
    from the hook log, recent candidate counts, notes-ref reachable."""
    import json as _json
    import subprocess as _sp

    hook_file = cwd / ".git" / "hooks" / "opentraces-post-commit"
    chained = cwd / ".git" / "hooks" / "post-commit"
    log_path = cwd / ".git" / "opentraces-hook.log"

    installed = hook_file.is_file()
    chained_in = False
    if chained.is_file():
        try:
            chained_in = "opentraces-post-commit" in chained.read_text()
        except OSError:
            chained_in = False

    last_entry: dict[str, Any] | None = None
    recent_runs = 0
    if log_path.is_file():
        try:
            # Read tail (last 64 KiB) so huge logs don't blow up doctor.
            raw = log_path.read_bytes()[-65536:]
            for line in reversed(raw.decode("utf-8", "replace").splitlines()):
                if not line.strip():
                    continue
                try:
                    rec = _json.loads(line)
                except ValueError:
                    continue
                if last_entry is None:
                    last_entry = rec
                recent_runs += 1
                if recent_runs >= 20:
                    break
        except OSError:
            pass

    notes_reachable = False
    try:
        res = _sp.run(
            ["git", "show-ref", "--verify", "refs/notes/opentraces"],
            cwd=cwd, capture_output=True, text=True, check=False,
        )
        notes_reachable = res.returncode == 0
    except (FileNotFoundError, OSError):
        notes_reachable = False

    if installed and chained_in and last_entry is not None:
        state = "ok"
    elif installed and chained_in:
        state = "installed_never_ran"
    elif installed and not chained_in:
        state = "installed_not_chained"
    else:
        state = "missing"

    last_trail_anchors_created = None
    last_trail_anchor_error = None
    if last_entry is not None:
        last_trail_anchors_created = last_entry.get("trail_anchors_created")
        last_trail_anchor_error = last_entry.get("trail_anchor_error")

    return {
        "state": state,
        "installed": installed,
        "chained_in_post_commit": chained_in,
        "log_path": str(log_path) if log_path.is_file() else None,
        "recent_runs": recent_runs,
        "last_run": last_entry,
        "last_trail_anchors_created": last_trail_anchors_created,
        "last_trail_anchor_error": last_trail_anchor_error,
        "notes_ref_reachable": notes_reachable,
    }


# --- baked-interpreter health panel (issue #86) ---------------------------

# A Homebrew Cellar interpreter path is an upgrade time-bomb: the
# ``/Cellar/<formula>/<version>/`` segment is deleted on ``brew upgrade`` even
# though it resolves fine right now. ``stable_interpreter`` remaps these at
# install time, but hooks baked by an older opentraces (before that remap) can
# still carry the pinned path. Flag them so the user re-renders the glue.
_CELLAR_PIN_RE = re.compile(r"/Cellar/[^/]+/[^/]+/")

_INTERPRETER_REMEDY = "Run: opentraces setup upgrade --integrations-only"


def _interpreter_token(command: str) -> str | None:
    """Extract the interpreter (first executable token) from a hook command.

    Hook commands bake the interpreter as the leading token, e.g.
    ``'/path/python' -m opentraces ...`` (shlex-quoted for Claude/Codex) or
    ``"/path/python" -m opentraces ...`` (double-quoted in the Git shim). Parse
    with ``shlex`` and return the first token; on any failure return ``None``.
    """
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return None
    return tokens[0] if tokens else None


def _is_opentraces_hook_command(command: object) -> bool:
    """True if ``command`` is an opentraces-owned hook command."""
    return isinstance(command, str) and "opentraces" in command


def _interpreter_finding(
    interpreter: str | None,
    *,
    integration: str,
    event: str,
) -> dict[str, Any] | None:
    """Return a finding dict if ``interpreter`` is unstable, else ``None``.

    Flags an interpreter when EITHER it does not exist on disk OR it matches a
    version-pinned Homebrew Cellar path. Never raises.
    """
    if not interpreter:
        return None
    reason: str | None = None
    try:
        exists = os.path.exists(interpreter)
    except OSError:
        exists = False
    if not exists:
        reason = "interpreter path does not exist on disk"
    elif _CELLAR_PIN_RE.search(interpreter):
        reason = "version-pinned Homebrew Cellar path (breaks on brew upgrade)"
    if reason is None:
        return None
    return {
        "integration": integration,
        "event": event,
        "interpreter": interpreter,
        "reason": reason,
        "remedy": _INTERPRETER_REMEDY,
    }


# Friendly runner names for the runtime-provenance surface, keyed by the
# legacy ``integration`` token the hook scanners emit.
_RUNNER_DISPLAY_NAMES = {
    "codex": "codex-cli",
    "claude": "claude-code",
    "git": "git",
}


def _collect_json_hook_runners(
    path: Path, integration: str, runners: list[dict[str, Any]]
) -> None:
    """Collect opentraces-owned runners from a Codex/Claude hook config tree.

    Both Codex (``~/.codex/hooks.json``) and Claude
    (``~/.claude/settings.json``, under the ``hooks`` key) share this shape.
    Appends one ``{name, integration, event, command, interpreter}`` entry per
    opentraces-owned hook command — REGARDLESS of interpreter health (codex
    finding #1). Missing or corrupt files are silently skipped (never raise).
    """
    try:
        if not path.is_file():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(data, dict):
        return
    hooks_root = data.get("hooks")
    if not isinstance(hooks_root, dict):
        return
    for event, entries in hooks_root.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            inner = entry.get("hooks")
            if not isinstance(inner, list):
                continue
            for hook in inner:
                if not isinstance(hook, dict):
                    continue
                command = hook.get("command")
                if not _is_opentraces_hook_command(command):
                    continue
                runners.append(
                    {
                        "name": _RUNNER_DISPLAY_NAMES.get(integration, integration),
                        "integration": integration,
                        "event": str(event),
                        "command": command,
                        "interpreter": _interpreter_token(command),
                    }
                )


def _collect_git_hook_runners(cwd: Path, runners: list[dict[str, Any]]) -> None:
    """Collect the repo's ``.git/hooks/opentraces-post-commit`` shim runner.

    The shim bakes the interpreter as ``"<python>" -m opentraces ...``; pull
    that token off the line carrying ``-m opentraces``. Missing/corrupt files
    are skipped silently. Emitted regardless of interpreter health.
    """
    hook_file = cwd / ".git" / "hooks" / "opentraces-post-commit"
    try:
        if not hook_file.is_file():
            return
        content = hook_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    for line in content.splitlines():
        if "-m opentraces" not in line:
            continue
        # Strip a trailing shell line-continuation (`... \`) before tokenising:
        # the real owned-hook template (capture/git/install.py) is multi-line, so
        # the ``-m opentraces`` line ends with ``\`` which makes ``shlex.split``
        # raise (No escaped character) → a None interpreter. Single-line shims
        # (e.g. the #93 checkpoint) are unaffected.
        clean = line.strip()
        while clean.endswith("\\"):
            clean = clean[:-1].rstrip()
        runners.append(
            {
                "name": "git",
                "integration": "git",
                "event": "post-commit",
                "command": clean,
                "interpreter": _interpreter_token(clean),
            }
        )


def _opentraces_hook_runners(cwd: Path) -> list[dict[str, Any]]:
    """Enumerate EVERY opentraces-owned hook runner (codex finding #1).

    Returns one ``{name, integration, event, command, interpreter}`` entry per
    opentraces-owned Codex / Claude / Git hook command — healthy AND unhealthy
    — so ``runtime_provenance`` can see the runner whose interpreter is a stable
    pipx/brew install (the #93 case). ``_interpreter_health`` is rebuilt on top
    of this (it keeps filtering to unstable interpreters only). Never raises.
    """
    runners: list[dict[str, Any]] = []

    try:
        from ..capture.codex_cli.sessions import codex_home

        codex_dir = codex_home()
    except Exception:
        codex_dir = Path.home() / ".codex"
    try:
        _collect_json_hook_runners(codex_dir / "hooks.json", "codex", runners)
    except Exception:  # noqa: BLE001 — doctor must never crash.
        pass

    try:
        _collect_json_hook_runners(
            Path.home() / ".claude" / "settings.json", "claude", runners
        )
    except Exception:  # noqa: BLE001 — doctor must never crash.
        pass

    try:
        _collect_git_hook_runners(cwd, runners)
    except Exception:  # noqa: BLE001 — doctor must never crash.
        pass

    return runners


def _interpreter_health(cwd: Path) -> dict[str, Any]:
    """Flag hook commands whose baked interpreter is an upgrade time-bomb.

    Inspects EVERY opentraces-owned Codex / Claude / Git hook runner (via the
    shared ``_opentraces_hook_runners`` extractor) and flags any whose
    interpreter (a) does not exist on disk, or (b) is a version-pinned Homebrew
    Cellar path. Returns a structured sub-report; missing or corrupt config
    files yield an empty, ``ok`` report. Never raises.
    """
    findings: list[dict[str, Any]] = []
    try:
        runners = _opentraces_hook_runners(cwd)
    except Exception:  # noqa: BLE001 — doctor must never crash.
        runners = []
    for runner in runners:
        finding = _interpreter_finding(
            runner.get("interpreter"),
            integration=str(runner.get("integration") or "?"),
            event=str(runner.get("event") or "?"),
        )
        if finding is not None:
            findings.append(finding)

    return {
        "status": "warn" if findings else "ok",
        "findings": findings,
    }


# --- runtime provenance (issue #93) ---------------------------------------

# The probe a bounded subprocess runs in each distinct runner interpreter to
# learn its REAL opentraces module file + dist version (codex finding #3 —
# substring heuristics on the interpreter path cannot honestly populate these).
_PROVENANCE_PROBE_CODE = (
    "import opentraces,importlib.metadata as m;"
    "print(opentraces.__file__);"
    "print(m.version('opentraces'))"
)

# Trust gate (codex finding #1 — SECURITY). doctor must NEVER execute an
# arbitrary binary lifted from a hook config. A runner interpreter is only
# probed when it is a genuine ``<python> -m opentraces...`` shape: the
# interpreter token is a plausible CPython basename AND the command invokes the
# opentraces module. Console-scripts / arbitrary binaries become honest
# ``unknown`` (``probe: "skipped_untrusted"``).
_PY_BASENAME_RE = re.compile(r"^python(?:3(?:\.\d+)?)?(?:\.exe)?$", re.IGNORECASE)

# Aggregate probe budget (codex finding #2). A bounded per-probe timeout does
# not cap the NUMBER of distinct interpreters, so N opentraces-looking hook
# commands cost N×timeout. Cap the distinct interpreters actually executed;
# excess become ``probe: "skipped_probe_budget_exceeded"`` honest unknowns.
MAX_PROBE_INTERPRETERS = 8


def _is_plausible_python(interpreter: str | None) -> bool:
    """True iff ``interpreter`` basename looks like a CPython executable.

    ``python`` / ``python3`` / ``python3.12`` (optional ``.exe``). Used as the
    trust gate before executing a runner — an arbitrary first token (e.g.
    ``/tmp/payload``) is rejected.
    """
    if not interpreter or not isinstance(interpreter, str):
        return False
    return bool(_PY_BASENAME_RE.match(os.path.basename(interpreter)))


def _is_trusted_probe_command(command: object, interpreter: str | None) -> bool:
    """True iff this runner is safe to PROBE (codex finding #1).

    Requires BOTH a plausible-python interpreter token AND a ``-m opentraces``
    module invocation in the command. Anything else — an arbitrary binary, a
    console-script (``opentraces``/``ot``) — is refused and never executed.
    """
    if not _is_plausible_python(interpreter):
        return False
    if not isinstance(command, str):
        return False
    return "-m opentraces" in command


def _realpath(p: str | None) -> str | None:
    if not p:
        return None
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def _abspath_no_realpath(p: str | os.PathLike[str] | None) -> str | None:
    """Absolute path normalization that preserves venv symlink identity."""
    if not p:
        return None
    return os.path.abspath(os.path.expanduser(os.fspath(p)))


def _classify_source_kind(module_file: str | None) -> str:
    """Classify an opentraces install from its resolved module file path.

    Generalises ``cli._detect_install_method``'s logic to an arbitrary path
    (that helper only classifies the CURRENT process from ``Path(__file__)``).
    Returns ``brew`` / ``pipx`` / ``source`` / ``pip``, or ``unknown`` when the
    probe could not resolve a module file (never fabricated).
    """
    if not module_file:
        return "unknown"
    low = module_file.lower()
    # brew ONLY for canonical formula layouts — a Cellar/opentraces/<ver> root,
    # or the opt/...opentraces/libexec symlink. A bare "/homebrew/" or
    # "/libexec/" substring is NOT enough: a project venv living under a
    # Homebrew-looking parent (or a foreign tool's libexec) must not be
    # mislabeled brew (codex finding #3).
    if "/cellar/opentraces/" in low or re.search(
        r"/opt/(?:[^/]+/)*opentraces/libexec/", low
    ):
        return "brew"
    if "/pipx/venvs/" in low or "/pipx/" in low:
        return "pipx"
    # Editable / source install: not in site-packages.
    if "site-packages" not in module_file:
        return "source"
    return "pip"


def _probe_interpreter(python: str) -> tuple[str | None, str | None]:
    """Bounded best-effort probe of one interpreter's opentraces install.

    Runs ``<python> -c <probe>`` with a short timeout to read the REAL
    ``opentraces.__file__`` + dist version. On timeout / failure / any error
    returns ``(None, None)`` — the caller records an honest ``unknown`` install
    rather than guessing. Never raises.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [python, "-c", _PROVENANCE_PROBE_CODE],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except Exception:  # noqa: BLE001 — probe failure is an honest unknown.
        return None, None
    if proc.returncode != 0:
        return None, None
    lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None, None
    # Validate the probe OUTPUT shape before trusting it (codex finding #1,
    # layer 2): a real probe prints the opentraces ``__init__.py`` path then a
    # version string. Spoofed/garbage stdout from a trusted-but-broken
    # interpreter is treated as an honest unknown, never trusted.
    raw_module, dist_version = lines[0], lines[1]
    if not raw_module.endswith("opentraces/__init__.py"):
        return None, None
    if not dist_version:
        return None, None
    module_file = _realpath(raw_module) or None
    return module_file, dist_version


def _current_runtime() -> dict[str, Any]:
    """Provenance of the CURRENT (foreground) opentraces process. Never raises."""
    import sys

    out: dict[str, Any] = {
        "argv0": sys.argv[0] if sys.argv else None,
        "python": _realpath(sys.executable),
        "module_file": None,
        "dist_version": None,
        "source_kind": "unknown",
        "git_root": None,
        "git_commit": None,
    }
    try:
        import opentraces as _ot

        out["module_file"] = _realpath(getattr(_ot, "__file__", None))
    except Exception:  # noqa: BLE001
        pass
    try:
        out["dist_version"] = current_cli_version()
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..cli import _detect_install_method

        out["source_kind"] = _detect_install_method()
    except Exception:  # noqa: BLE001
        out["source_kind"] = _classify_source_kind(out["module_file"])

    if out["source_kind"] == "source" and out["module_file"]:
        out.update(_editable_git_provenance(out["module_file"]))
    return out


def _editable_git_provenance(module_file: str) -> dict[str, Any]:
    """Best-effort git root + commit for an editable/source install.

    The single subprocess on the common ``doctor`` path, short-timeout and
    exception-isolated. Returns ``{}`` on any failure.
    """
    import subprocess

    module_dir = os.path.dirname(module_file)
    out: dict[str, Any] = {}
    for key, args in (
        ("git_root", ["git", "-C", module_dir, "rev-parse", "--show-toplevel"]),
        ("git_commit", ["git", "-C", module_dir, "rev-parse", "--short", "HEAD"]),
    ):
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, timeout=2
            )
            if proc.returncode == 0:
                out[key] = (proc.stdout or "").strip() or None
        except Exception:  # noqa: BLE001
            pass
    return out


def _watcher_runner_interpreter() -> str | None:
    """The interpreter frozen into the watcher worker shim, if any. Never raises."""
    try:
        prov = _watcher_provenance()
    except Exception:  # noqa: BLE001
        return None
    return prov.get("shim_frozen_interpreter")


def _otlp_runner_interpreter() -> str | None:
    """Best-effort interpreter behind the OTLP receiver launch unit.

    Reads the macOS launchd plist / Linux systemd unit's program arguments for
    a ``*python*`` token. Returns ``None`` when the unit is absent or
    unparseable. Never raises.
    """
    try:
        from ..capture.otlp.lifecycle import LAUNCHD_PLIST_PATH

        plist = LAUNCHD_PLIST_PATH
        if not plist.is_file():
            return None
        text = plist.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    for match in re.findall(r"<string>([^<]*)</string>", text):
        token = match.strip()
        if "python" in token.lower() and "/" in token:
            return token
    return None


def runtime_selection_marker_path(cwd: Path) -> Path:
    """Path of the project-local runtime-selection marker (issue #99).

    Lives under the PROJECT's ``.opentraces/`` config dir — NEVER under the
    ``~/.opentraces`` bucket/dataset/staging tree. Records a deliberate dev
    runtime so ``doctor`` reports it as intent, not drift.
    """
    return Path(cwd) / ".opentraces" / "runtime_selection.json"


def _read_runtime_selection(cwd: Path) -> dict[str, Any] | None:
    """Read the runtime-selection marker (path-only, never executes). None if
    absent / unreadable / malformed."""
    import json as _json

    path = runtime_selection_marker_path(cwd)
    try:
        if not path.is_file():
            return None
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a broken marker is simply "no marker".
        return None
    return data if isinstance(data, dict) else None


def _runtime_provenance(
    cwd: Path | None = None, *, probe: bool = False
) -> dict[str, Any]:
    """Surface dev/prod install provenance + mixed integration runtimes (#93).

    SECURITY (codex round 2 — RCE close): by DEFAULT this executes NOTHING
    derived from a hook config. Anyone who can write ``~/.codex/hooks.json`` can
    also write the binary it points at, so no path-prefix allowlist is both safe
    AND permits legit user-dir pipx installs. The honest default therefore
    INSPECTS only:

      * ``current`` — real values introspected from the RUNNING process
        (``sys.executable`` / ``opentraces.__file__`` / ``__version__``); safe.
      * runners — ``source_kind`` is INFERRED from the interpreter PATH via
        ``_classify_source_kind`` (string inspection, no execution); their
        ``module_file`` / ``dist_version`` are ``null`` and ``verified: false``,
        ``probe: "not_run"``.
      * ``state`` — ``mixed_runtimes`` vs ``single_runtime`` from the DISTINCT
        install keys (verified module_file when known, else interpreter realpath)
        across current + runners. This keeps #93's detection value WITHOUT
        executing anything.

    Only when ``probe=True`` (the opt-in ``--probe-runtimes`` flag /
    ``OT_DOCTOR_PROBE_RUNTIMES=1``) does the bounded subprocess run — and even
    then it stays gated (plausible-python basename + ``-m opentraces`` shape +
    ``MAX_PROBE_INTERPRETERS`` budget + output-shape validation) as hygiene, and
    it is user-consented execution. A successful probe upgrades a runner to
    ``verified: true`` / ``probe: "ok"`` with a real module_file + version.

    ``severity`` is ``warning`` (never ``error``) and never feeds ``exit_code``.
    ``advice`` is an informational STRING. Every probe is exception-isolated;
    never raises.
    """
    cwd = cwd or Path.cwd()

    try:
        current = _current_runtime()
    except Exception:  # noqa: BLE001
        current = {
            "argv0": None, "python": None, "module_file": None,
            "dist_version": None, "source_kind": "unknown",
            "git_root": None, "git_commit": None,
        }

    cur_python = current.get("python")
    cur_module = current.get("module_file")

    try:
        runners = _opentraces_hook_runners(cwd)
    except Exception:  # noqa: BLE001
        runners = []

    # Map each runner to a stable (name, interpreter) tuple; add the watcher
    # and OTLP runners, which live outside the hook configs. Dedup by
    # (name, interpreter) so the same integration configured across several
    # hook events collapses to ONE provenance row (plan: one entry per runner).
    # Each spec carries a ``trusted`` flag (codex finding #1): only a genuine
    # ``<python> -m opentraces...`` runner is ever executed. Hook runners derive
    # trust from their command; the watcher/OTLP units are our own rendered
    # shims, so they only require a plausible-python interpreter.
    raw_specs: list[dict[str, Any]] = [
        {
            "name": r.get("name") or r.get("integration"),
            "raw_interpreter": _abspath_no_realpath(r.get("interpreter")),
            "interpreter": _realpath(r.get("interpreter")),
            "trusted": _is_trusted_probe_command(
                r.get("command"), _realpath(r.get("interpreter"))
            ),
        }
        for r in runners
    ]
    try:
        watcher_interp = _watcher_runner_interpreter()
    except Exception:  # noqa: BLE001
        watcher_interp = None
    if watcher_interp is not None:
        _wi = _realpath(watcher_interp)
        raw_specs.append(
            {
                "name": "watcher",
                "raw_interpreter": _abspath_no_realpath(watcher_interp),
                "interpreter": _wi,
                "trusted": _is_plausible_python(_wi),
            }
        )
    try:
        otlp_interp = _otlp_runner_interpreter()
    except Exception:  # noqa: BLE001
        otlp_interp = None
    if otlp_interp is not None:
        _oi = _realpath(otlp_interp)
        raw_specs.append(
            {
                "name": "otlp",
                "raw_interpreter": _abspath_no_realpath(otlp_interp),
                "interpreter": _oi,
                "trusted": _is_plausible_python(_oi),
            }
        )
    runner_specs: list[dict[str, Any]] = []
    _spec_by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for spec in raw_specs:
        key = (spec.get("name"), spec.get("interpreter"))
        if key in _spec_by_key:
            # A duplicate runner (same name+interpreter across hook events) is
            # trusted if ANY occurrence is a genuine module invocation.
            if spec.get("trusted"):
                _spec_by_key[key]["trusted"] = True
            continue
        _spec_by_key[key] = spec
        runner_specs.append(spec)

    # Resolve per-interpreter provenance. By DEFAULT (probe=False) nothing is
    # executed: ``source_kind`` is inferred from the interpreter PATH and the
    # module_file/version stay unknown (``verified=False``, ``probe="not_run"``).
    # Only ``probe=True`` runs the gated, budgeted, output-validated subprocess
    # to UPGRADE a trusted runner to verified values.
    interp_module: dict[str, str | None] = {}
    interp_kind: dict[str, str] = {}
    interp_version: dict[str, str | None] = {}
    interp_probe: dict[str, str] = {}
    interp_verified: dict[str, bool] = {}
    if cur_python:
        # The current process is introspected, not config-derived-executed.
        interp_module[cur_python] = cur_module
        interp_kind[cur_python] = current.get("source_kind") or "unknown"
        interp_version[cur_python] = current.get("dist_version")
        interp_probe[cur_python] = "current"
        interp_verified[cur_python] = cur_module is not None

    probed_count = 0
    for spec in runner_specs:
        interp = spec.get("interpreter")
        if not interp or interp in interp_module:
            continue
        # Honest, execution-free classification from the interpreter PATH.
        path_kind = _classify_source_kind(interp)
        interp_module[interp] = None
        interp_kind[interp] = path_kind
        interp_version[interp] = None
        interp_verified[interp] = False

        if not probe:
            # DEFAULT: never execute a config-derived interpreter.
            interp_probe[interp] = "not_run"
            continue
        # OPT-IN probe path (user-consented execution) — still gated as hygiene.
        if not spec.get("trusted"):
            interp_probe[interp] = "skipped_untrusted"
            continue
        if probed_count >= MAX_PROBE_INTERPRETERS:
            interp_probe[interp] = "skipped_probe_budget_exceeded"
            continue
        probed_count += 1
        try:
            module_file, dist_version = _probe_interpreter(interp)
        except Exception:  # noqa: BLE001
            module_file, dist_version = None, None
        if module_file is not None:
            interp_module[interp] = module_file
            interp_kind[interp] = _classify_source_kind(module_file)
            interp_version[interp] = dist_version
            interp_verified[interp] = True
            interp_probe[interp] = "ok"
        else:
            interp_probe[interp] = "error"

    def _install_key(interp: str | None) -> str:
        """Dedup/identity key: the verified module_file when known, else the
        interpreter realpath (honest under the no-execution default)."""
        if interp and interp_verified.get(interp) and interp_module.get(interp):
            return str(interp_module[interp])
        return interp_module.get(interp) or f"interp:{interp or 'current'}"

    # discovered_installs: one entry per distinct install key. Unverified
    # entries carry null module_file/version (never fabricated) + verified flag.
    discovered: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    cur_key = _install_key(cur_python)
    discovered.append(
        {
            "module_file": cur_module,
            "dist_version": current.get("dist_version"),
            "source_kind": current.get("source_kind") or "unknown",
            "interpreter": cur_python,
            "verified": bool(interp_verified.get(cur_python)),
            "probe": interp_probe.get(cur_python, "current"),
        }
    )
    seen_keys.add(cur_key)
    for interp in interp_module:
        if interp == cur_python:
            continue
        key = _install_key(interp)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        discovered.append(
            {
                "module_file": interp_module.get(interp),
                "dist_version": interp_version.get(interp),
                "source_kind": interp_kind.get(interp, "unknown"),
                "interpreter": interp,
                "verified": bool(interp_verified.get(interp)),
                "probe": interp_probe.get(interp, "not_run"),
            }
        )

    # integration_runners: matches_current is true when the runner is literally
    # the current interpreter, or (when probed) shares the current module_file.
    integration_runners: list[dict[str, Any]] = []
    for spec in runner_specs:
        interp = spec.get("interpreter")
        module_file = interp_module.get(interp) if interp else None
        verified = bool(interp and interp_verified.get(interp))
        matches_current = bool(
            (interp is not None and cur_python is not None and interp == cur_python)
            or (
                verified
                and module_file is not None
                and cur_module is not None
                and module_file == cur_module
            )
        )
        integration_runners.append(
            {
                "name": spec.get("name"),
                "runner": interp,
                "python": interp,
                "runner_raw": spec.get("raw_interpreter"),
                "matches_current": matches_current,
                "matches_install": interp_kind.get(interp) if interp else None,
                "verified": verified,
                "probe": interp_probe.get(interp) if interp else None,
            }
        )

    # state: mixed when ≥2 distinct install keys execute across current +
    # runners. Keys are verified module roots where known, else interpreter
    # realpaths — so mixed detection survives the no-execution default.
    distinct_keys = {_install_key(i) for i in interp_module}
    state = "mixed_runtimes" if len(distinct_keys) >= 2 else "single_runtime"
    severity = "warning" if state == "mixed_runtimes" else "ok"

    if state == "mixed_runtimes":
        advice = (
            "Integrations execute opentraces from more than one install root. "
            "This is usually harmless for a dev setup, but hooks/watcher can run "
            "stale code. To converge on one runtime, re-render the integrations "
            "from your chosen install (e.g. 'opentraces setup uninstall "
            "--integrations-only' then reinstall, or keep them pinned "
            "deliberately). No data is touched."
        )
    else:
        advice = "All configured integrations resolve to the current install root."

    # Mode 2 (issue #99): a deliberate dev runtime is INTENT, not drift. When
    # the project-local marker says ``mode == "dev"`` and the integration
    # runners all resolve to the recorded dev interpreter, report
    # ``dev_runtime_active`` and downgrade ``severity`` warning→ok (the honest
    # ``state`` is preserved). Path-only read; never executes anything.
    dev_runtime_active = False
    marker = _read_runtime_selection(cwd)
    if isinstance(marker, dict) and marker.get("mode") == "dev":
        marker_interp = _abspath_no_realpath(marker.get("interpreter"))
        dev_interp = _realpath(marker_interp)
        # SECURITY / data-safety (codex finding): only downgrade severity when
        # the recorded dev interpreter STILL EXISTS + is executable AND every
        # runner resolves to it. A stale marker pointing at a DELETED dev venv
        # must NOT hide drift — keep severity `warning` and flag the stale marker.
        raw_interp = marker_interp
        interp_live = bool(
            raw_interp
            and os.path.isfile(raw_interp)
            and os.access(raw_interp, os.X_OK)
        )
        runner_interps = [
            _abspath_no_realpath(r.get("runner_raw")) for r in integration_runners
        ]
        all_match_dev = bool(runner_interps) and all(
            ri == marker_interp for ri in runner_interps
        )
        if dev_interp and all_match_dev and interp_live:
            dev_runtime_active = True
            if severity == "warning":
                severity = "ok"
            advice = (
                "Dev runtime deliberately active: every integration runner "
                "resolves to the editable checkout interpreter recorded by "
                "'opentraces setup runtime use-dev'. This is intentional, not "
                "drift. Run 'opentraces setup runtime use <pipx|homebrew>' to "
                "switch back to an installed release. No data is touched."
            )
        elif dev_interp and not interp_live:
            # Stale dev marker: the recorded interpreter is gone. Keep drift
            # VISIBLE (do not downgrade) and name the remedy.
            advice = (
                "A 'setup runtime use-dev' marker points at an interpreter that "
                f"no longer exists ({raw_interp}); the dev checkout may have "
                "been deleted or rebuilt. Drift is NOT downgraded. Re-run "
                "'opentraces setup runtime use-dev' or 'use <pipx|homebrew>' to "
                "re-pin the glue. No data is touched."
            )

    return {
        "current": current,
        "discovered_installs": discovered,
        "integration_runners": integration_runners,
        "state": state,
        "severity": severity,
        "advice": advice,
        "dev_runtime_active": dev_runtime_active,
        # Whether the opt-in interpreter probe executed. False = inspection-only
        # (no config-derived execution); module_file/dist_version are unverified.
        "probed": bool(probe),
    }


def _safe_runtime_provenance(
    cwd: Path | None = None, *, probe: bool = False
) -> dict[str, Any]:
    """``_runtime_provenance`` wrapped so an unexpected error never sinks the
    whole ``doctor`` report. Returns a safe single-runtime shape on failure."""
    try:
        return _runtime_provenance(cwd, probe=probe)
    except Exception:  # noqa: BLE001 — doctor must never crash.
        return {
            "current": {},
            "discovered_installs": [],
            "integration_runners": [],
            "state": "single_runtime",
            "severity": "ok",
            "advice": "runtime-provenance probe failed; not enough signal to report.",
            "probed": bool(probe),
            "dev_runtime_active": False,
        }


# --- attribution panel (plan 043 phase 7) ---------------------------------

def _attribution_status(cwd: Path) -> dict[str, Any]:
    """Report on the attribution cache + last backfill for this project.

    Returns a panel with cached_commits, last_backfilled_commit,
    last_backfill_at, first_run_backfill_decision, and a health label.
    """
    import datetime as _dt

    from .cache import AttributionCache
    from .config import (
        get_first_run_backfill_decision,
        get_project_state_path,
        _project_slug_for,
    )
    from .state import StateManager

    if not project_is_opted_in(cwd):
        return {
            "project_slug": None,
            "project_root_sha": None,
            "attribution_cache_dir": None,
            "cached_commits": 0,
            "last_backfilled_commit": None,
            "last_backfill_at": None,
            "first_run_backfill_decision": None,
            "coverage_estimate": None,
            "health": "no-project",
        }

    try:
        slug = _project_slug_for(cwd)
    except Exception:
        slug = None

    cache = AttributionCache(cwd)
    cached = cache.list_attributed_shas()

    try:
        sm = StateManager(state_path=get_project_state_path(cwd))
        last_sha = sm.get_last_backfilled_commit()
        last_at = sm.get_last_backfill_at()
    except Exception:
        last_sha, last_at = None, None

    try:
        decision = get_first_run_backfill_decision(cwd)
    except Exception:
        decision = None

    try:
        from .repo_identity import root_commit_sha
        root_sha = root_commit_sha(cwd)
    except Exception:
        root_sha = None

    # Watcher-aware staleness: if the watcher is running, missed ticks are
    # the watcher's problem, not ours. If no watcher + last_at > 24h old,
    # warn.
    health = "ok"
    if not cached:
        health = "empty"
    elif last_at:
        try:
            when = _dt.datetime.fromisoformat(last_at.replace("Z", "+00:00"))
            now = _dt.datetime.now(_dt.timezone.utc)
            age = (now - when).total_seconds()
            if age > 86400:
                try:
                    from ..watcher import installer as _wi
                    st = _wi.status()
                    running = bool(st.running)
                except Exception:
                    running = False
                if not running:
                    health = "stale"
        except ValueError:
            pass

    return {
        "project_slug": slug,
        "project_root_sha": root_sha,
        "attribution_cache_dir": str(cache.root),
        "cached_commits": len(cached),
        "last_backfilled_commit": last_sha,
        "last_backfill_at": last_at,
        "first_run_backfill_decision": decision,
        "coverage_estimate": None,
        "health": health,
    }


def _watcher_provenance() -> dict[str, Any]:
    """Cross-check shim / running-daemon / installed-CLI provenance (#65).

    The pipx→brew migration left the worker shim pointing at a deleted
    interpreter while a dev-venv daemon kept running unfixed code — and no
    surface could tell. This check reads the shim for frozen interpreter
    paths, reads the daemon's self-declared status file, and compares both
    against the installed CLI. ``drift`` lists every mismatch found.
    """
    import re

    from ..core import paths as _paths

    drift: list[str] = []
    out: dict[str, Any] = {"drift": drift}

    shim_path = _paths.OPENTRACES_DIR / "bin" / "ot-watcher"
    out["shim_path"] = str(shim_path)
    shim_text: str | None = None
    if shim_path.is_file():
        try:
            shim_text = shim_path.read_text()
        except OSError:
            shim_text = None
    out["shim_exists"] = shim_text is not None
    if shim_text is not None:
        for frozen in re.findall(r'exec "([^"]*python[^"]*)"', shim_text):
            out["shim_frozen_interpreter"] = frozen
            if not Path(frozen).exists():
                drift.append("shim-interpreter-missing")
        if "run-sweep" not in shim_text and "setup watcher sweep" not in shim_text:
            drift.append("shim-legacy-verb")

    cli_version = current_cli_version()
    out["cli_version"] = cli_version
    out["shim_version"] = extract_version_stamp(shim_text)
    if shim_text is not None:
        if not out["shim_version"]:
            drift.append("shim-version-missing")
        elif out["shim_version"] != cli_version:
            drift.append("shim-version-drift")

    status_path = _paths.OPENTRACES_DIR / "watcher.status.json"
    out["status_file"] = str(status_path)
    if status_path.is_file():
        try:
            declared = json.loads(status_path.read_text()) or {}
        except (OSError, json.JSONDecodeError):
            declared = {}
        out["daemon_version"] = declared.get("version")
        out["daemon_executable"] = declared.get("executable")
        out["daemon_started_at"] = declared.get("started_at")
        executable = declared.get("executable")
        if executable and not Path(str(executable)).exists():
            drift.append("daemon-executable-missing")
        if (
            cli_version
            and declared.get("version")
            and declared["version"] != cli_version
        ):
            drift.append("daemon-version-drift")
    return out


def _watcher_status() -> dict[str, Any]:
    """Report on watcher installation + running state."""
    try:
        from ..watcher import installer as _wi
    except Exception:
        return {
            "platform": "unsupported",
            "installed": False,
            "running": False,
            "last_run_at": None,
            "interval_seconds": None,
            "unit_path": None,
            "health": "unsupported-platform",
        }

    try:
        st = _wi.status()
    except RuntimeError:
        # current_platform() raises on Windows etc.
        return {
            "platform": "unsupported",
            "installed": False,
            "running": False,
            "last_run_at": None,
            "interval_seconds": None,
            "unit_path": None,
            "health": "unsupported-platform",
        }
    except Exception:
        return {
            "platform": "unsupported",
            "installed": False,
            "running": False,
            "last_run_at": None,
            "interval_seconds": None,
            "unit_path": None,
            "health": "unsupported-platform",
        }

    if not st.installed:
        health = "not-installed"
    elif not st.running:
        health = "not-running"
    else:
        health = "ok"
        # stale-heartbeat check: if last_run_at exists and is older than
        # 3x interval, flag as stale-heartbeat
        if st.last_run_at and st.interval_seconds:
            import datetime as _dt
            try:
                age = (_dt.datetime.now(_dt.timezone.utc) - st.last_run_at).total_seconds()
                if age > 3 * st.interval_seconds:
                    health = "stale-heartbeat"
            except Exception:
                pass

    try:
        provenance = _watcher_provenance()
    except Exception:  # noqa: BLE001
        provenance = {"drift": [], "error": "provenance-check-failed"}
    # Only HARD provenance drift degrades watcher health, so the health label
    # agrees with exit_code (a benign shim-version-missing must not make the
    # report say "provenance-drift" while the process exits 0). The full drift
    # list stays in ``provenance["drift"]`` for anyone who wants the staleness.
    if _has_hard_drift(provenance.get("drift")) and health == "ok":
        health = "provenance-drift"

    return {
        "platform": st.platform,
        "installed": bool(st.installed),
        "running": bool(st.running),
        "last_run_at": st.last_run_at.isoformat() if st.last_run_at else None,
        "interval_seconds": st.interval_seconds,
        "unit_path": str(st.unit_path) if st.unit_path else None,
        "health": health,
        "provenance": provenance,
    }


def _regex_pattern_count() -> int:
    try:
        from ..security.secrets import _PATTERNS  # type: ignore

        return len(_PATTERNS)
    except Exception:
        return 0


def _security_tools(
    cfg,
    trufflehog_version: str | None,
    review_llm: dict,
    review_policy: str | None,
) -> list[dict[str, Any]]:
    """Ordered list of security/privacy tools shown in ``opentraces doctor``.

    The registered tools come from ``security.tools._registry``. Two synthetic
    entries are appended that aren't in the registry: the on-demand LLM trace
    review workflow and the human-review policy gate.
    """
    from ..security.pipeline import list_tools

    entries: list[dict[str, Any]] = []
    for info in list_tools(cfg):
        entry: dict[str, Any] = {
            "name": info.display_name,
            "state": info.state,
            "detail": info.detail,
            "enable_cmd": info.setup_cmd,
            "disable_cmd": info.disable_cmd,
            "blocks": False,
        }
        if info.name == "trufflehog":
            entry["binary_version"] = trufflehog_version
        entries.append(entry)

    # On-demand LLM trace review — not in the tool registry (it's an
    # expensive workflow, not part of the per-record sanitize step) but
    # users still need to see its enable state in doctor output.
    rl_enabled = bool(review_llm.get("enabled"))
    rl_reachable = review_llm.get("reachable")
    if not rl_enabled:
        rl_state, rl_detail = "disabled", None
    elif rl_reachable is False:
        rl_state = "unreachable"
        rl_detail = review_llm.get("status") or "endpoint unreachable"
    else:
        rl_state = "on-demand"
        backend = review_llm.get("backend") or review_llm.get("api_format") or "?"
        rl_detail = f"{backend} / {review_llm.get('model') or '?'}"
    entries.append(
        {
            "name": "LLM trace review",
            "state": rl_state,
            "detail": rl_detail,
            "enable_cmd": "opentraces setup llm-review",
            "disable_cmd": "opentraces setup llm-review --disable",
            "blocks": False,
            "api_format": review_llm.get("api_format"),
            "backend": review_llm.get("backend"),
            "model": review_llm.get("model"),
            "base_url": review_llm.get("base_url"),
            "api_key_env": review_llm.get("api_key_env"),
            "probe_status": review_llm.get("status"),
            "reachable": rl_reachable,
        }
    )

    # Human review — gated by project review policy, not a tool.
    if review_policy == "auto":
        hr_state = "not-required"
        hr_detail = "project policy: auto (safe traces auto-approve)"
    elif review_policy == "review":
        hr_state = "required"
        hr_detail = "project policy: review (every trace needs approval)"
    else:
        hr_state = "not-initialized"
        hr_detail = "run 'opentraces init' to set a project policy"
    entries.append(
        {
            "name": "Human review",
            "state": hr_state,
            "detail": hr_detail,
            "enable_cmd": None,
            "disable_cmd": None,
            "blocks": False,
            "review_policy": review_policy,
        }
    )

    return entries


def _build_security_section(cfg, th_version, llm_review, review_policy) -> dict[str, Any]:
    """Compute the doctor ``security`` block; tools list is rendered once."""
    return {
        "version": SECURITY_VERSION,
        "tools": _security_tools(cfg, th_version, llm_review, review_policy),
        "classifier_sensitivity": getattr(cfg, "classifier_sensitivity", "medium"),
        "review_policy": review_policy,
        "blocked_reasons": ["parse_error", "trufflehog_finding"],
    }


def _trufflehog_status(enabled: bool, version: str | None) -> str:
    if not enabled:
        return "disabled (opt in via 'opentraces setup trufflehog')"
    if version is None:
        return "ENABLED-BUT-MISSING — run 'opentraces setup trufflehog --enable'"
    return f"enabled ({version})"


def _infer_backend_label(api_format: str, base_url: str) -> str:
    """Translate (api_format, base_url) into a user-facing backend name.

    ``api_format`` is our internal HTTP-shape dispatch key
    (openai-compat, anthropic, ollama native, fake). ``base_url`` is
    where the bytes actually go. Users think in vendor names (Ollama,
    Groq, LM Studio), not dispatch keys — so we infer one from the URL.
    """
    if api_format == "anthropic":
        return "anthropic"
    if api_format == "fake":
        return "fake"
    if api_format == "ollama":
        return "ollama"
    url = (base_url or "").lower()
    if "localhost:11434" in url or "127.0.0.1:11434" in url:
        return "ollama"
    if "localhost:1234" in url or "127.0.0.1:1234" in url:
        return "lm-studio"
    if "localhost:8080" in url or "127.0.0.1:8080" in url:
        return "llama.cpp"
    if "localhost:8000" in url or "127.0.0.1:8000" in url:
        return "vllm"
    if "localhost" in url or "127.0.0.1" in url:
        return "local"
    if "api.openai.com" in url:
        return "openai"
    if "api.groq.com" in url:
        return "groq"
    if "openrouter.ai" in url:
        return "openrouter"
    if "api.together" in url:
        return "together"
    if "api.anthropic.com" in url:
        return "anthropic"
    return base_url or api_format


def _review_llm_status(rc) -> dict[str, Any]:
    """Probe the configured review-LLM endpoint.

    Never raises: failures become human-readable status strings so the
    surrounding doctor report stays renderable.
    """
    import os

    backend = _infer_backend_label(rc.api_format, rc.base_url)
    if not rc.enabled:
        return {
            "enabled": False,
            "status": "disabled (opt in via 'opentraces setup llm-review')",
            "api_format": rc.api_format, "backend": backend, "model": rc.model,
        }

    result: dict[str, Any] = {
        "enabled": True,
        "api_format": rc.api_format,
        "backend": backend,
        "model": rc.model,
        "base_url": rc.base_url,
        "api_key_env": rc.api_key_env,
    }

    if rc.api_key_env and not os.environ.get(rc.api_key_env):
        result["reachable"] = False
        result["status"] = (
            f"enabled but env var ${rc.api_key_env} is not set"
        )
        return result

    try:
        if rc.api_format == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError:
                result["reachable"] = False
                result["status"] = "anthropic SDK not installed (pip install anthropic)"
                return result
            result["reachable"] = True
            result["status"] = f"enabled ({backend} / {rc.model})"
            return result

        if rc.api_format in ("openai-compat", "ollama"):
            from .. import security as _sec  # noqa: F401 — ensure package importable
            from ..security.llm_provider import OpenAICompatProvider

            p = OpenAICompatProvider(
                model=rc.model,
                base_url=rc.base_url,
                api_key_env=rc.api_key_env,
                timeout=min(10.0, rc.timeout),
            )
            ping = p.ping()
            models = ping.get("models") or []
            note = f"{len(models)} models available" if models else "endpoint reachable"
            if models and rc.model not in models:
                note += f"; model '{rc.model}' not found"
            result["reachable"] = True
            result["status"] = f"enabled ({backend} / {rc.model}) — {note}"
            return result

        result["reachable"] = True
        result["status"] = f"enabled ({backend})"
        return result
    except Exception as exc:
        result["reachable"] = False
        result["status"] = f"UNREACHABLE — {exc}"
        return result


def _schema_version() -> str | None:
    try:
        from opentraces_schema import SCHEMA_VERSION  # type: ignore

        return SCHEMA_VERSION
    except Exception:
        return None


def _post_processors(cwd: Path) -> list[dict[str, Any]]:
    try:
        raw = load_project_config(cwd)
        proj_cfg = ProjectConfig.model_validate(raw) if raw else None
        specs = proj_cfg.post_processors if proj_cfg else []
    except Exception:
        specs = []
    out: list[dict[str, Any]] = []
    for spec, resolved in probe_processors(specs):
        out.append(
            {
                "name": spec.name,
                "command": spec.command,
                "resolved_path": resolved,
                "status": "detected" if resolved else "missing",
            }
        )
    return out


def _project_review_policy(cwd: Path) -> str | None:
    """Return the project's review_policy ("review"/"auto"), or None if no project.

    load_project_config() returns defaults rather than None, so check the
    actual file to distinguish "initialized with default" from "never
    initialized here."
    """
    if not project_is_opted_in(cwd):
        return None
    try:
        raw = load_project_config(cwd)
        proj = ProjectConfig.model_validate(raw)
        return proj.review_policy
    except Exception:
        return None


def _entity_parser_status() -> dict[str, Any]:
    """Report on the entity-parser binary.

    Shape:
      {
        "binary_path": "/abs/path",
        "installed": bool,
        "version": str | None,
        "platform": "darwin-arm64" | "unknown" | …,
        "advice": str | None,   # what to run if missing
      }
    """
    path = resolve_binary_path()
    runner = EntityRunner(binary_path=path)
    installed = runner.available()
    version = runner.version() if installed else None
    return {
        "binary_path": str(path),
        "installed": installed,
        "version": version,
        "platform": _safe_platform(),
        "advice": None if installed else "run 'opentraces setup entity-parser'",
    }


def _hook_installers() -> list[dict[str, Any]]:
    """Call .status() on every registered HookInstaller."""
    out: list[dict[str, Any]] = []
    for name, cls in get_hook_installers().items():
        try:
            st = cls().status()
        except Exception as e:
            st = {"installer": name, "installed": False, "error": str(e)}
        out.append(st)
    return out


def _registered_project_paths(cfg, cwd: Path | None = None) -> list[str]:
    """Return project paths visible through config, sidecars, or cwd marker."""
    project_paths = set(getattr(cfg, "projects", {}).keys())

    try:
        from . import paths

        if paths.PROJECTS_DIR.is_dir():
            for project_json in sorted(paths.PROJECTS_DIR.glob("*/project.json")):
                try:
                    data = json.loads(project_json.read_text())
                except (OSError, json.JSONDecodeError):
                    continue
                raw_path = data.get("path") or data.get("project_dir")
                if not isinstance(raw_path, str) or not raw_path.strip():
                    continue
                try:
                    project_paths.add(str(Path(raw_path).expanduser().resolve()))
                except OSError:
                    project_paths.add(str(Path(raw_path).expanduser()))
    except Exception:
        pass

    if cwd is not None:
        try:
            if project_is_opted_in(cwd):
                project_paths.add(str(cwd.resolve()))
        except Exception:
            pass

    return sorted(project_paths)


def report(
    cfg, cwd: Path | None = None, *, probe_runtimes: bool = False
) -> dict[str, Any]:
    """Build the doctor payload.

    Args:
        cfg: loaded global config (has security.trufflehog.enabled, hf_token)
        cwd: project directory for post-processor probing (defaults to CWD)
        probe_runtimes: opt-in — when True, the runtime-provenance section
            EXECUTES the configured interpreters to read verified install
            facts. Default False keeps doctor execution-free (security).
    """
    cwd = cwd or Path.cwd()
    th_version = find_trufflehog()
    th_enabled = cfg.security.trufflehog.enabled
    llm_review = _review_llm_status(cfg.security.llm_review)
    review_policy = _project_review_policy(cwd)

    opted_in = _registered_project_paths(cfg, cwd)
    cli_status = version_status()
    watcher = _watcher_status()
    hooks = _hook_installers()

    return {
        "cli": cli_status,
        "security_version": SECURITY_VERSION,
        "schema_version": _schema_version(),
        "tracking_mode": getattr(
            getattr(cfg, "capture", None), "tracking_mode", "global"
        ),
        "opted_in_projects": {
            "count": len(opted_in),
            "paths": opted_in,
        },
        "security": _build_security_section(cfg, th_version, llm_review, review_policy),
        # Flat keys for `doctor --json` consumers; the structured view is
        # under ``security.tools``.
        "trufflehog": {
            "enabled": th_enabled,
            "binary_version": th_version,
            "status": _trufflehog_status(th_enabled, th_version),
        },
        "llm_review": llm_review,
        "hf_auth": "ok" if cfg.hf_token else "missing",
        "post_processors": _post_processors(cwd),
        "entity_parser": _entity_parser_status(),
        "attribution": _attribution_status(cwd),
        "watcher": watcher,
        "hooks": hooks,
        "integrations": integration_version_report(watcher=watcher, hooks=hooks),
        "bucket": _bucket_status(),
        "trace_index": _trace_index_status(),
        "trail_event_log": _trail_event_log_status(cwd),
        "post_commit_hook": _post_commit_hook_status(cwd),
        "interpreter_health": _interpreter_health(cwd),
        "runtime_provenance": _safe_runtime_provenance(cwd, probe=probe_runtimes),
        "trail_capture_audit": _trail_capture_audit(cwd),
        "context_tree": _context_tree_status(cwd),
    }


def _context_tree_status(cwd: Path | None = None) -> dict[str, Any]:
    """Plan 077 + plan 078 R11 doctor surface for Context Tree state.

    Reads ``~/.opentraces/otlp-receiver.status.json`` which the receiver
    daemon refreshes periodically (R11). The CLI's ``capture-otlp status``
    verb is the live counterpart. Also scans the project's canonical
    event log for the most recent ``context_tree_reconciled`` event so
    consumers can see whether the substrate has ingested any sessions.
    """
    from .paths import (
        otlp_receiver_pid_path,
        otlp_receiver_status_path,
        raw_bodies_dir,
    )
    pid_path = otlp_receiver_pid_path()
    status_path = otlp_receiver_status_path()
    raw_dir = raw_bodies_dir()

    otel_receiver: dict[str, Any] = {
        "enabled": False,
        "port": None,
        "uptime_seconds": None,
        "last_capture_at": None,
        "last_capture_at_present": False,
        "captures_total": None,
        "raw_body_dir": str(raw_dir),
        "raw_body_dir_size_bytes": _dir_size_bytes(raw_dir),
    }

    pid_alive = False
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
            try:
                os.kill(pid, 0)
                pid_alive = True
                otel_receiver["pid"] = pid
            except (OSError, ProcessLookupError):
                pid_alive = False
        except (ValueError, OSError):
            pid_alive = False

    # Status file persists across stop/start so historical config and
    # capture totals remain visible to dashboards even when the daemon
    # is currently down. ``enabled`` reflects live PID state; everything
    # else reflects the last-known good snapshot.
    if status_path.exists():
        try:
            data = json.loads(status_path.read_text(encoding="utf-8"))
            otel_receiver["port"] = data.get("port")
            otel_receiver["uptime_seconds"] = (
                data.get("uptime_seconds") if pid_alive else None
            )
            otel_receiver["last_capture_at"] = data.get("last_capture_at")
            otel_receiver["captures_total"] = data.get("captures_total")
        except (json.JSONDecodeError, OSError):
            pass
    otel_receiver["enabled"] = bool(pid_alive)
    # ``last_capture_at_present`` is the convenience boolean for journeys
    # that don't want to walk null-vs-missing — true once tracking is
    # operational (live daemon OR persisted history shows captures).
    otel_receiver["last_capture_at_present"] = bool(
        otel_receiver["last_capture_at"] is not None or pid_alive
    )

    # Plan 077 R8 surface: scan the canonical event log ONCE for the
    # most recent context_tree_reconciled event + aggregated capture
    # limitations. Both the JSONL pipeline and the OTLP flush emit
    # these, so these fields reflect "any source has reconciled at
    # least one session" across the substrate.
    context_scan_skip_reason = _doctor_event_log_skip_reason(cwd) if cwd else None
    last_reconciled_at, capture_limitations_by_trace = (
        (None, {})
        if context_scan_skip_reason is not None
        else (_scan_context_tree_reconciled(cwd) if cwd else (None, {}))
    )

    return {
        "otel_receiver": otel_receiver,
        "last_reconciled_at": last_reconciled_at,
        "capture_limitations_by_trace": capture_limitations_by_trace,
        "scan_skipped": context_scan_skip_reason is not None,
        "skip_reason": context_scan_skip_reason,
    }


def _scan_context_tree_reconciled(cwd: Path) -> tuple[str | None, dict[str, list[str]]]:
    """Single-pass scan: latest event_time + per-trace capture_limitations."""
    if _doctor_event_log_skip_reason(cwd) is not None:
        return None, {}
    try:
        from .trails.event_log import read_events
    except ImportError:
        return None, {}
    try:
        events = read_events(cwd, verify=False)
    except Exception:
        return None, {}
    latest: str | None = None
    by_trace: dict[str, list[str]] = {}
    for ev in events:
        if ev.event_type != "context_tree_reconciled":
            continue
        if latest is None or ev.event_time > latest:
            latest = ev.event_time
        trace_id = ev.payload.get("trace_id")
        if trace_id:
            entries = by_trace.setdefault(trace_id, [])
            for lim in ev.payload.get("capture_limitations") or []:
                if lim not in entries:
                    entries.append(lim)
    return latest, by_trace


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(p.stat().st_size for p in path.glob("*") if p.is_file())
    except OSError:
        return 0


def _event_time_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def audit_trail_capture(
    repo: Path,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Scan the project's TrailEvent log for ``trail_capture_incomplete``.

    Returns a panel dict with:
      * ``window_days`` — the lookback window applied;
      * ``traces_scanned`` — total distinct traces seen in window;
      * ``incomplete`` — list of ``{trace_id, file_edits, patches}``
        for any trace where file_edits > 0 AND patches == 0;
      * ``state`` — ``"ok"`` / ``"warn"`` / ``"missing"``.

    ``state`` is ``"warn"`` when at least one incomplete trace is found,
    ``"missing"`` if the event log itself is unreadable, ``"ok"``
    otherwise.

    Lives in ``core`` (not ``cli``) so the doctor aggregator can call it
    directly without a core→cli import; cluster-C tests target it here.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(days, 1))

    try:
        from .trails.event_log import read_events
    except Exception as exc:
        return {
            "state": "missing",
            "error": f"failed_to_import_trails: {exc}",
            "window_days": days,
            "traces_scanned": 0,
            "incomplete": [],
        }

    try:
        events = read_events(repo)
    except Exception as exc:
        return {
            "state": "missing",
            "error": str(exc),
            "window_days": days,
            "traces_scanned": 0,
            "incomplete": [],
        }

    file_edit_counts: dict[str, int] = defaultdict(int)
    patch_counts: dict[str, int] = defaultdict(int)
    seen_traces: set[str] = set()

    for event in events:
        trace_id = event.trace_id
        if not trace_id:
            continue
        event_dt = _event_time_to_dt(event.event_time)
        if event_dt is None or event_dt < cutoff:
            continue
        seen_traces.add(trace_id)
        if event.event_type == "file_edit":
            file_edit_counts[trace_id] += 1
        elif event.event_type == "trace_patch_created":
            patch_counts[trace_id] += 1

    incomplete: list[dict[str, Any]] = []
    for trace_id in sorted(seen_traces):
        edits = file_edit_counts.get(trace_id, 0)
        patches = patch_counts.get(trace_id, 0)
        if edits > 0 and patches == 0:
            incomplete.append(
                {
                    "trace_id": trace_id,
                    "file_edits_count": edits,
                    "patch_created_count": patches,
                }
            )

    return {
        "state": "warn" if incomplete else "ok",
        "window_days": days,
        "traces_scanned": len(seen_traces),
        "incomplete": incomplete,
    }


def _trail_capture_audit(cwd: Path) -> dict[str, Any]:
    """Cluster C-4: surface traces with ``file_edit`` events but zero
    ``trace_patch_created`` events in the last 7 days."""
    skip_reason = _doctor_event_log_skip_reason(cwd)
    if skip_reason is not None:
        return {
            "state": "skipped",
            "reason": skip_reason,
            "window_days": 7,
            "traces_scanned": 0,
            "incomplete": [],
            "advice": "run a focused trail-capture audit outside doctor",
        }
    try:
        return audit_trail_capture(cwd, days=7)
    except Exception as exc:  # pragma: no cover — defensive
        return {
            "state": "missing",
            "error": str(exc),
            "window_days": 7,
            "traces_scanned": 0,
            "incomplete": [],
        }


# Drift reasons that mean "this hook/shim is present but carries no version
# stamp" — i.e. it was rendered by an older or stamp-less CLI. The glue is
# functional, just unstamped; this is benign WARN-severity and must never drive
# a non-zero exit, or a freshly-restored captured world / any older-stamp
# install makes `doctor` fail scripts / CI / `&&` chains for a non-problem.
# Every OTHER drift reason is a real break that still drives exit 3: a genuine
# version mismatch (``version-drift`` / ``shim-version-drift`` / the skill at a
# different version, ``daemon-version-drift``) means the deployed glue is from a
# different CLI and the user should `setup upgrade`; ``shim-interpreter-missing``
# / ``shim-legacy-verb`` / ``daemon-executable-missing`` / broken harnesses mean
# it cannot run. The classification is applied uniformly everywhere drift is
# acted on — exit_code AND the watcher health label — so the report never
# contradicts the exit code.
_SOFT_DRIFT_REASONS: frozenset[str] = frozenset({"version-missing", "shim-version-missing"})


def _has_hard_drift(drift: Any) -> bool:
    """True when ``drift`` contains a genuinely-broken reason string (anything
    other than a benign stamp-absence in :data:`_SOFT_DRIFT_REASONS`).

    Only a list/tuple/set of reason strings can carry a hard reason. A non-list
    drift — the skill hook's coarse version-staleness bool (``drift = installed
    and inst_ver != __version__``) or pi's ``None`` — has no classifiable hard
    reason; staleness alone is a warning, never a break, so it is never hard.
    (The skill's hard signal is ``broken_harnesses``, checked separately.) This
    also keeps :func:`exit_code` from ever raising on an unexpected drift shape.
    """
    if not isinstance(drift, (list, tuple, set)):
        return False
    return any(reason not in _SOFT_DRIFT_REASONS for reason in drift)


def exit_code(report_data: dict[str, Any]) -> int:
    """Non-zero when a configured integration is genuinely broken.

    Benign stamp-absence drift (``version-missing`` / ``shim-version-missing``)
    is WARN-severity and does NOT drive a non-zero exit; the human render
    already shows it yellow, so the exit code must agree.
    """
    sec = report_data.get("security") or {}
    entries = sec.get("tools") or []
    for t in entries:
        state = t.get("state")
        if state in ("missing", "unreachable"):
            return 3
    for h in report_data.get("hooks") or []:
        if h.get("installer") == "skill" and h.get("installed") and (
            _has_hard_drift(h.get("drift")) or h.get("broken_harnesses")
        ):
            return 3
        if h.get("installed") and _has_hard_drift(h.get("drift")):
            return 3
    watcher = report_data.get("watcher") or {}
    if _has_hard_drift((watcher.get("provenance") or {}).get("drift")):
        return 3
    if (report_data.get("trail_event_log") or {}).get("state") in ("invalid", "error"):
        return 3
    return 0
