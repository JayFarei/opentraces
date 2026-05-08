"""Flask application for the opentraces web review interface.

Serves a local web UI for trace review: browse sessions,
approve/reject/redact traces, and rescan records through the local
security pipeline.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from ...core.config import (
    auth_identity,
    get_project_state_path,
    get_project_traces_dir,
    load_config,
    load_project_config,
    project_is_opted_in,
    save_project_config,
)
from ...core.inbox import get_stage, load_traces
from ...core.state import StateManager, TraceStatus
from ...core.workflow import (
    DEFAULT_AGENT,
    DEFAULT_PUSH_POLICY,
    DEFAULT_REVIEW_POLICY,
    resolve_visible_stage,
)

logger = logging.getLogger(__name__)


def _coerce_status(val: TraceStatus | str | None) -> TraceStatus:
    """Coerce a TraceStatus-or-string into a TraceStatus enum, defaulting to PARSED."""
    if val is None:
        return TraceStatus.PARSED
    if isinstance(val, TraceStatus):
        return val
    try:
        return TraceStatus(val)
    except ValueError:
        return TraceStatus.PARSED


def _trace_generation_index(
    state: StateManager, trace: dict[str, Any], entry: Any | None,
) -> int:
    """Best-effort generation number for a trace list row."""
    raw = trace.get("generation_index")
    try:
        generation = int(raw)
    except (TypeError, ValueError):
        generation = 0
    if generation > 0:
        return generation

    session_id = str(trace.get("session_id") or (entry.session_id if entry else "") or "").strip()
    if not session_id:
        return 0
    session = state.get_session(session_id)
    if session is None:
        return 0

    trace_id = trace.get("trace_id")
    for gen in reversed(session.generations):
        if gen.trace_id == trace_id:
            return int(gen.generation)
    return 0


def create_app(staging_dir: str | None = None, state_path: str | None = None, viewer_dist: str | None = None) -> Flask:
    """Create the Flask review app."""
    app = Flask(__name__)
    app.secret_key = "opentraces-review-" + uuid.uuid4().hex[:8]
    app.extensions.setdefault("opentraces_web_runtime", {})

    # The web server is always invoked from inside an opted-in project,
    # so cwd is the source of truth for project_dir. Caller may override
    # via the staging_dir/state_path kwargs (used by tests).
    project_dir = Path.cwd()
    if staging_dir:
        staging_path = Path(staging_dir)
    elif project_is_opted_in(project_dir):
        staging_path = get_project_traces_dir(project_dir)
    else:
        staging_path = project_dir
    viewer_dist_path = Path(viewer_dist) if viewer_dist else None

    # All mutable state is closure-local, so each app instance is independent
    _state_mgr: list[StateManager | None] = [None]
    if state_path:
        _state_path: Path | None = Path(state_path)
    elif project_is_opted_in(project_dir):
        _state_path = get_project_state_path(project_dir)
    else:
        _state_path = None
    _trace_cache: list[list[dict[str, Any]] | None] = [None]
    _tree_cache: dict[str, list[dict[str, Any]]] = {}

    def _get_state() -> StateManager:
        if _state_mgr[0] is None:
            if _state_path is None:
                raise RuntimeError(
                    "web server has no state_path; project must be opted in"
                )
            _state_mgr[0] = StateManager(state_path=_state_path)
        return _state_mgr[0]

    def _invalidate_cache() -> None:
        _trace_cache[0] = None
        _tree_cache.clear()
        # Drop the cached StateManager so the next _get_state() re-reads
        # state.json from disk after review/redaction/rescan mutations.
        _state_mgr[0] = None

    # Default cap keeps /api/traces responsive on big inboxes (thousands
    # of staged files). Power users can request more via ?limit=N.
    _default_trace_limit = 500

    def _traces(*, limit: int | None = None) -> list[dict[str, Any]]:
        effective = limit if limit is not None else _default_trace_limit
        if limit is None and _trace_cache[0] is not None:
            return _trace_cache[0]
        traces = load_traces(staging_path, limit=effective if effective > 0 else None)
        if limit is None:
            _trace_cache[0] = traces
        return traces

    def _find_trace(trace_id: str) -> dict[str, Any] | None:
        for trace in _traces():
            if trace.get("trace_id") == trace_id:
                return trace
        return None

    def _total_staged() -> int:
        """Count staged files without reading them — directory entry only."""
        if not staging_path.exists():
            return 0
        return sum(1 for _ in staging_path.glob("*.jsonl"))

    def _get_review_status(trace_id: str) -> str:
        return get_stage(_get_state(), trace_id)

    _context_cache: dict[str, Any] = {}
    _context_cache_time: list[float] = [0.0]
    _client_lock = threading.Lock()
    _clients: dict[str, float] = {}
    _client_seen_once: list[bool] = [False]

    def _context() -> dict[str, Any]:
        now = time.time()
        if _context_cache and now - _context_cache_time[0] < 60:
            return _context_cache
        project_cfg = load_project_config(project_dir)
        cfg = load_config()
        identity = auth_identity(cfg.hf_token)
        # Surface the security pipeline config so the viewer can render
        # the global Security info modal with actual state ("enabled —
        # trufflehog 3.94.3") instead of the stale "opt-in" hint.
        # Version is probed lazily; if the binary disappears we report
        # enabled=True with version=None so the UI can flag the drift.
        from ...security.trufflehog import find_trufflehog
        th_enabled = bool(cfg.security.trufflehog.enabled)
        th_version = find_trufflehog() if th_enabled else None
        result = {
            "project_name": project_dir.name,
            "remote": project_cfg.get("remote"),
            "review_policy": project_cfg.get("review_policy", DEFAULT_REVIEW_POLICY),
            "push_policy": project_cfg.get("push_policy", DEFAULT_PUSH_POLICY),
            "agents": project_cfg.get("agents") or [DEFAULT_AGENT],
            "authenticated": identity is not None,
            "username": identity.get("name") if identity else None,
            "security": {
                "privacy_tier": project_cfg.get("privacy_tier") or cfg.security.privacy_tier,
                "trufflehog": {
                    "enabled": th_enabled,
                    "version": th_version,
                },
                "llm_review": {
                    "enabled": bool(cfg.security.llm_review.enabled),
                    "model": cfg.security.llm_review.model,
                },
            },
        }
        _context_cache.clear()
        _context_cache.update(result)
        _context_cache_time[0] = now
        return result

    def _touch_client(client_id: str | None) -> None:
        if not client_id:
            return
        with _client_lock:
            _clients[client_id] = time.monotonic()
            _client_seen_once[0] = True

    def _drop_client(client_id: str | None) -> None:
        if not client_id:
            return
        with _client_lock:
            _clients.pop(client_id, None)

    def _client_id() -> str | None:
        if request.args.get("client_id"):
            return request.args["client_id"]
        data = request.get_json(silent=True)
        if isinstance(data, dict):
            raw = data.get("client_id")
            if isinstance(raw, str) and raw:
                return raw
        header = request.headers.get("X-Opentraces-Client")
        if header:
            return header
        return None

    def _lifecycle_snapshot(*, stale_after: float = 45.0) -> dict[str, Any]:
        now = time.monotonic()
        with _client_lock:
            stale_ids = [cid for cid, last_seen in _clients.items() if now - last_seen > stale_after]
            for client_id in stale_ids:
                _clients.pop(client_id, None)
            last_seen = max(_clients.values(), default=None)
            return {
                "seen_any_client": _client_seen_once[0],
                "active_clients": len(_clients),
                "last_seen_age_s": None if last_seen is None else max(0.0, now - last_seen),
            }

    def _request_runtime_stop(reason: str) -> None:
        runtime = app.extensions.get("opentraces_web_runtime") or {}
        callback = runtime.get("request_stop")
        if callable(callback):
            callback(reason)

    app.extensions["opentraces_web_lifecycle"] = {
        "snapshot": _lifecycle_snapshot,
    }

    # --- Page routes ---

    # SPA mode: serve the React viewer from web/viewer/dist
    if viewer_dist_path and viewer_dist_path.exists():
        @app.route("/")
        def serve_index():
            return send_from_directory(str(viewer_dist_path), "index.html")

        @app.route("/assets/<path:filename>")
        def serve_assets(filename):
            return send_from_directory(str(viewer_dist_path / "assets"), filename)

        @app.errorhandler(404)
        def not_found(e):
            if request.path.startswith("/api/"):
                return jsonify({"error": "not found"}), 404
            return send_from_directory(str(viewer_dist_path), "index.html")

    # --- API routes ---

    @app.route("/api/traces")
    def api_traces():
        """JSON API for trace list. Accepts ?limit=N to override the default 500-trace cap."""
        try:
            limit_param = request.args.get("limit", type=int)
        except (TypeError, ValueError):
            limit_param = None
        traces = _traces(limit=limit_param) if limit_param is not None else _traces()
        total = _total_staged()
        state = _get_state()
        sessions = []
        for t in traces:
            trace_id = t["trace_id"]
            entry = state.get_trace(trace_id)
            status_enum = _coerce_status(entry.status if entry else None)
            generation_index = _trace_generation_index(state, t, entry)
            sessions.append({
                "trace_id": trace_id,
                "task": (t.get("task", {}).get("description") or "")[:100],
                "model": t.get("agent", {}).get("model") or ", ".join(
                    sorted(set(
                        s.get("model") for s in t.get("steps", [])
                        if s.get("model")
                    ))
                ) or "unknown",
                "agent": t.get("agent", {}).get("name", "unknown"),
                "steps": t.get("metrics", {}).get("total_steps", len(t.get("steps", []))),
                "tool_calls": sum(
                    len(s.get("tool_calls", [])) for s in t.get("steps", [])
                ),
                "timestamp": t.get("timestamp_end") or t.get("timestamp_start"),
                "status": _get_review_status(trace_id),
                "_stage": resolve_visible_stage(status_enum),
                "generation_index": generation_index,
                # Count residual + auto-redacted items so the list badge
                # matches the "flags" figure in the preview header. Counting
                # only residuals under-reports on clean auto-redacted
                # sessions (which are the common case).
                "security_flags": (
                    len(t.get("_security_flags", []))
                    + int((t.get("security") or {}).get("redactions_applied") or 0)
                ),
                "project": t.get("metadata", {}).get("project", "unknown"),
                # Plan 032: surface cached LLM review verdict so reviewers
                # can see at a glance whether a session has been reviewed.
                "llm_review": (t.get("metadata", {}) or {}).get("llm_review") or {"status": "not_run"},
            })
        response = jsonify(sessions)
        response.headers["X-Total-Staged"] = str(total)
        response.headers["X-Returned"] = str(len(sessions))
        return response

    @app.route("/api/context")
    def api_context():
        return jsonify(_context())

    @app.route("/api/refresh", methods=["POST"])
    def api_refresh():
        """Re-capture sessions into the inbox.

        Mirrors the TUI's ``r`` refresh binding: runs ``scan_project``
        in-process so new Claude Code turns land in the staging dir with
        the full enrichment + security pipeline (including TruffleHog
        when enabled). Returns per-action counts for a toast.
        """
        from ...core.ingest import scan_project
        try:
            report = scan_project(project_dir)
        except Exception as exc:  # pragma: no cover — surfaced to user
            logger.exception("refresh failed")
            return jsonify({"error": str(exc)}), 500
        counts = {"new": 0, "updated": 0, "skipped": 0, "errors": 0}
        for r in report.results:
            if r.action == "new":
                counts["new"] += 1
            elif r.action == "updated":
                counts["updated"] += 1
            elif r.action == "skipped":
                counts["skipped"] += 1
            if r.error:
                counts["errors"] += 1
        # Invalidate the _context cache so subsequent /api/context reads
        # pick up any config drift that happened out-of-band.
        _context_cache_time[0] = 0.0
        return jsonify({"status": "ok", **counts, "total": len(report.results)})

    @app.route("/api/_lifecycle/ping", methods=["POST"])
    def api_lifecycle_ping():
        _touch_client(_client_id())
        return jsonify({"status": "ok"})

    @app.route("/api/_lifecycle/disconnect", methods=["POST"])
    def api_lifecycle_disconnect():
        _drop_client(_client_id())
        return ("", 204)

    @app.route("/api/_lifecycle/quit", methods=["POST"])
    def api_lifecycle_quit():
        _drop_client(_client_id())
        _request_runtime_stop("browser quit")
        return jsonify({"status": "stopping"})

    @app.route("/api/remote", methods=["POST"])
    def api_set_remote():
        """Set or update the HuggingFace Hub remote (owner/dataset)."""
        data = request.get_json(silent=True) or {}
        remote = data.get("remote", "").strip()

        if not remote:
            return jsonify({"error": "remote is required"}), 400
        if "/" not in remote or remote.count("/") != 1:
            return jsonify({"error": "Invalid format. Use: owner/dataset"}), 400

        proj_config = load_project_config(project_dir)
        proj_config["remote"] = remote
        save_project_config(project_dir, proj_config)

        # Bust the context cache so the next /api/context read picks it up
        _context_cache.clear()
        _context_cache_time[0] = 0.0

        return jsonify({"status": "ok", "remote": remote})

    @app.route("/api/stats")
    def api_stats():
        """Dashboard stats."""
        traces = _traces()
        return jsonify(_compute_stats(traces))

    @app.route("/api/trace/<trace_id>/add", methods=["POST"])
    @app.route("/api/trace/<trace_id>/approve", methods=["POST"])
    def api_add_trace(trace_id: str):
        """Add a single session to the next push batch (CLI: ``opentraces add``)."""
        state = _get_state()
        task_desc = trace_id[:12]
        try:
            traces = _traces()
            for t in traces:
                if t.get("trace_id") == trace_id:
                    task_desc = (t.get("task", {}).get("description") or "")[:80] or trace_id[:12]
                    break
        except Exception:
            pass
        state.create_commit_group([trace_id], task_desc)
        _invalidate_cache()
        return jsonify({"status": "staged", "trace_id": trace_id})

    @app.route("/api/trace/<trace_id>/reject", methods=["POST"])
    def api_reject(trace_id: str):
        """Reject a session, persisting to StateManager."""
        from ...core.review import reject_trace
        state = _get_state()
        reject_trace(state, trace_id, with_session_kwarg=True)
        _invalidate_cache()
        return jsonify({"status": "rejected", "trace_id": trace_id})

    @app.route("/api/trace/<trace_id>/step/<int:step_index>/redact", methods=["POST"])
    def api_redact_step(trace_id: str, step_index: int):
        """Redact a step's content, persisting to the staging JSONL on disk."""
        # Validate trace_id to prevent path traversal
        import re as _re
        if not _re.match(r'^[a-f0-9-]+$', trace_id):
            return jsonify({"error": "Invalid trace ID format"}), 400

        from ...core.review import redact_step_and_persist
        result = redact_step_and_persist(staging_path, trace_id, step_index)
        if not result.ok:
            return jsonify({"error": result.error}), 404

        _invalidate_cache()

        return jsonify({
            "status": "redacted",
            "trace_id": trace_id,
            "step_index": step_index,
        })

    @app.route("/api/trace/<trace_id>/rescan", methods=["POST"])
    def api_rescan_trace(trace_id: str):
        """Re-run the trace security pipeline and persist the staged record."""
        import re as _re
        if not _re.match(r'^[a-f0-9-]+$', trace_id):
            return jsonify({"error": "Invalid trace ID format"}), 400

        data = request.get_json(silent=True) or {}
        privacy_tier = data.get("privacy_tier")
        from ...core.review import rescan_trace_and_persist
        try:
            result = rescan_trace_and_persist(
                staging_path,
                trace_id,
                load_config(),
                privacy_tier=privacy_tier,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        if not result.ok or result.processed is None:
            return jsonify({"error": result.error}), 404

        if project_is_opted_in(project_dir):
            try:
                from ...core.bucket_store import write_trace_record
                from ...core.config import get_project_dir

                write_trace_record(
                    result.processed.record,
                    project_slug=get_project_dir(project_dir).name,
                    source_layer="canonical",
                    legacy_mirror=True,
                    privacy_tier=privacy_tier,
                )
            except Exception:
                logger.warning(
                    "bucket write failed during web rescan for %s",
                    trace_id,
                    exc_info=True,
                )

        _invalidate_cache()
        security = result.processed.record.metadata.get("security", {}).get("privacy", {})
        return jsonify({
            "status": "rescanned",
            "trace_id": trace_id,
            "privacy_tier": security.get("privacy_tier") or privacy_tier,
            "security_version": result.processed.record.security.classifier_version,
            "redactions_applied": result.processed.record.security.redactions_applied,
            "needs_review": result.processed.needs_review,
        })

    @app.route("/api/trace/<trace_id>/detail")
    def api_trace_detail(trace_id: str):
        """Return full trace JSON for a single session."""
        trace = _find_trace(trace_id)
        if trace is None:
            return jsonify({"error": "Session not found"}), 404

        state = _get_state()
        entry = state.get_trace(trace_id)
        status_enum = _coerce_status(entry.status if entry else None)

        result = dict(trace)
        result["_stage"] = resolve_visible_stage(status_enum)
        return jsonify(result)

    @app.route("/api/traces/<trace_id>/tree")
    def api_trace_tree(trace_id: str):
        from . import graph_api

        trace = _find_trace(trace_id)
        if trace is None:
            return jsonify({"error": "Session not found"}), 404
        cached = _tree_cache.get(trace_id)
        if cached is None:
            cached = graph_api.serialize_tree(trace, step_labels=_get_state().get_step_labels(trace_id))
            _tree_cache[trace_id] = cached
        return jsonify({"tree": cached})

    @app.route("/api/traces/<trace_id>/resume", methods=["POST"])
    def api_trace_resume(trace_id: str):
        from ...capture.claude_code.resume import ResumeError, resolve_at_step

        trace = _find_trace(trace_id)
        if trace is None:
            return jsonify({"error": "Session not found"}), 404

        data = request.get_json(silent=True) or {}
        at_step = data.get("at_step")
        if not at_step:
            return jsonify({"error": "at_step is required"}), 422

        try:
            target = resolve_at_step(
                trace_id,
                str(at_step),
                staging_path,
                project_cwd=project_dir,
                state=_get_state(),
                materialize=False,
            )
        except ResumeError as exc:
            return jsonify({"error": exc.code, "message": exc.message}), 422

        return jsonify({
            "argv": target.argv,
            "new_session_id": target.new_session_id,
            "truncated_at_line": target.truncated_at_line,
            "parent_session_id": target.parent_session_id,
            "parent_step_id": target.parent_step_id,
        })

    @app.route("/api/trace/<trace_id>/stage", methods=["POST"])
    def api_stage(trace_id: str):
        """Transition a session to STAGED status."""
        from ...core.review import stage_trace
        state = _get_state()
        stage_trace(state, trace_id)
        return jsonify({"status": "inbox"})

    @app.route("/api/trace/<trace_id>/unstage", methods=["POST"])
    def api_unstage(trace_id: str):
        """Revert a session to PARSED status."""
        from ...core.review import unstage_trace
        state = _get_state()
        unstage_trace(state, trace_id)
        return jsonify({"status": "inbox"})

    @app.route("/api/add", methods=["POST"])
    def api_add():
        """Stage ("add") sessions into a batch group ready for push.

        Mirrors ``opentraces add`` on the CLI — in the dataset=repo,
        trace=commit mental model, this is the ``git add`` step. Already-added
        traces are silently filtered so the endpoint is idempotent.
        """
        data = request.get_json(silent=True) or {}
        ids = data.get("trace_ids", [])
        message = data.get("message", "")

        if not ids:
            return jsonify({"error": "No trace_ids provided"}), 400

        # Validate all sessions exist, and quietly drop ones already added
        # so repeated clicks don't 400 the whole batch.
        traces = _traces()
        all_trace_ids = {t["trace_id"] for t in traces}
        state = _get_state()

        addable: list[str] = []
        already_added: list[str] = []
        addable_statuses = {
            TraceStatus.STAGED, TraceStatus.PARSED, TraceStatus.DISCOVERED,
            TraceStatus.REVIEWING, TraceStatus.APPROVED,
        }
        for sid in ids:
            if sid not in all_trace_ids:
                return jsonify({"error": f"Session {sid} not found"}), 404
            entry = state.get_trace(sid)
            status_val = _coerce_status(entry.status if entry else None)
            if status_val == TraceStatus.COMMITTED:
                already_added.append(sid)
                continue
            if status_val not in addable_statuses:
                return jsonify({
                    "error": f"Session {sid} cannot be added (status: {status_val.value})"
                }), 400
            addable.append(sid)

        if not addable:
            return jsonify({
                "commit_id": None,
                "session_count": 0,
                "already_added": already_added,
                "created_at": "",
            })

        from ...core.review import commit_bulk
        commit_id = commit_bulk(state, addable, message)
        group = state.get_commit_group(commit_id)

        return jsonify({
            "commit_id": commit_id,
            "session_count": len(addable),
            "already_added": already_added,
            "created_at": group.created_at if group else "",
        })

    @app.route("/api/trace/<trace_id>/redaction-preview")
    def api_redaction_preview(trace_id: str):
        """Preview redaction results for a trace."""
        traces = _traces()
        trace = None
        for t in traces:
            if t["trace_id"] == trace_id:
                trace = t
                break
        if trace is None:
            return jsonify({"error": "Session not found"}), 404

        # Build a simplified redaction preview based on security flags
        preview_steps = []
        security_flags = trace.get("_security_flags", [])
        flagged_steps = {}
        for flag in security_flags:
            si = flag.get("step_index", -1)
            if si not in flagged_steps:
                flagged_steps[si] = []
            flagged_steps[si].append(flag)

        steps = trace.get("steps", [])
        total_fields = 0
        redacted_fields = 0

        for i, step in enumerate(steps):
            total_fields += 1  # content
            if step.get("tool_calls"):
                total_fields += len(step["tool_calls"])
            if step.get("observations"):
                total_fields += len(step["observations"])

            if i in flagged_steps:
                redactions = []
                for flag in flagged_steps[i]:
                    redactions.append({
                        "field": f"steps[{i}].content",
                        "reason": flag.get("reason", "security flag"),
                        "before": (step.get("content") or "")[:80] + "...",
                        "after": "[REDACTED]",
                    })
                    redacted_fields += 1
                if redactions:
                    preview_steps.append({
                        "step_index": i,
                        "redactions": redactions,
                    })

        signal_kept = round(1.0 - (redacted_fields / max(total_fields, 1)), 2)

        return jsonify({
            "trace_id": trace_id,
            "steps": preview_steps,
            "signal_kept": signal_kept,
        })

    @app.route("/api/push", methods=["POST"])
    def api_push():
        """Fail closed for the legacy trace-push endpoint."""
        traces = _traces()
        req_data = request.get_json(silent=True) or {}
        requested_commit_id = req_data.get("commit_id")
        state = _get_state()

        committed_entries = state.get_committed_traces()
        if requested_commit_id:
            group = state.get_commit_group(requested_commit_id)
            if group is None:
                return jsonify({"error": f"Unknown commit {requested_commit_id}"}), 404
            committed_ids = set(group.trace_ids)
        else:
            committed_ids = set(committed_entries.keys())

        committed = [t for t in traces if t["trace_id"] in committed_ids]
        if not committed and not requested_commit_id:
            committed = [t for t in traces if _get_review_status(t["trace_id"]) == "staged"]
        if not committed:
            return jsonify({"error": "No staged sessions to push"}), 400

        return jsonify({
            "status": "disabled",
            "stage": "dataset",
            "count": 0,
            "trace_ids": [],
            "log": "",
            "error": (
                "Legacy trace push is disabled in the v0.4 flow. "
                "Create a dataset with `opentraces dataset run <name>` "
                "and publish reviewed rows with `opentraces dataset publish <name>`."
            ),
            "next_command": "opentraces dataset publish <name>",
        }), 410

    @app.route("/api/graph")
    def api_graph():
        """Commit graph with trace attribution (commit-primary or trace-primary)."""
        from . import graph_api
        try:
            limit = int(request.args.get("limit", 20))
        except (TypeError, ValueError):
            limit = 20
        try:
            page = int(request.args.get("page", 1))
        except (TypeError, ValueError):
            page = 1
        trace_id = request.args.get("trace") or None
        since = request.args.get("since") or None
        until = request.args.get("until") or None
        show_entities = request.args.get("entities", "1") != "0"
        try:
            commits = graph_api.load_graph(
                project_dir,
                limit=limit, page=page, trace_id=trace_id,
                since=since, until=until, show_entities=show_entities,
            )
        except Exception as e:
            logger.exception("graph load failed")
            return jsonify({"error": str(e), "commits": []}), 500
        return jsonify({"commits": commits})

    @app.route("/api/blame/<sha>")
    def api_blame(sha: str):
        """Forward blame for a commit: coverage + session + file breakdown."""
        import re as _re
        if not _re.match(r"^[0-9a-fA-F]{4,40}$", sha):
            return jsonify({"error": "Invalid sha"}), 400
        from . import graph_api
        data = graph_api.load_blame(project_dir, sha)
        if data is None:
            return jsonify({"error": "No attribution for this commit"}), 404
        return jsonify(data)

    @app.route("/api/trace/<trace_id>/commits")
    def api_trace_commits(trace_id: str):
        """Inverse blame: which commits this trace contributed to."""
        import re as _re
        if not _re.match(r"^[a-fA-F0-9-]+$", trace_id):
            return jsonify({"error": "Invalid trace id"}), 400
        from . import graph_api
        data = graph_api.load_inverse_blame(project_dir, trace_id)
        return jsonify(data)

    def _compute_stats(traces: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute dashboard statistics."""
        total = len(traces)
        staged = sum(1 for t in traces if _get_review_status(t["trace_id"]) == "staged")
        pushed = sum(1 for t in traces if _get_review_status(t["trace_id"]) == "pushed")
        rejected = sum(1 for t in traces if _get_review_status(t["trace_id"]) == "rejected")
        inbox = total - staged - pushed - rejected

        # "in" on the TUI/viewer includes cache_read (see conversation.ts
        # traceMeta); keep the aggregate consistent so per-session totals
        # add up to what this stat reports.
        total_tokens = sum(
            (t.get("metrics", {}) or {}).get("total_input_tokens", 0)
            + (t.get("metrics", {}) or {}).get("total_cache_read_tokens", 0)
            + (t.get("metrics", {}) or {}).get("total_output_tokens", 0)
            for t in traces
        )
        total_tool_calls = sum(
            sum(len(s.get("tool_calls", [])) for s in t.get("steps", []))
            for t in traces
        )
        total_cost = sum(
            t.get("metrics", {}).get("estimated_cost_usd", 0) or 0
            for t in traces
        )
        # Residual + redactions_applied, matching the per-trace "flags"
        # figure on the preview and the list badge.
        total_flags = sum(
            len(t.get("_security_flags", []) or [])
            + int((t.get("security") or {}).get("redactions_applied") or 0)
            for t in traces
        )

        # Determine if security scanning was applied
        security_tier = None
        for t in traces:
            scanned = t.get("security", {}).get("scanned")
            if scanned is not None:
                security_tier = "Scanned" if scanned else "Unscanned"
                break

        return {
            "total": total,
            "inbox": inbox,
            "staged": staged,
            "pushed": pushed,
            "rejected": rejected,
            "total_tokens": total_tokens,
            "total_tool_calls": total_tool_calls,
            "total_cost_usd": round(total_cost, 4),
            "total_security_flags": total_flags,
            "security_tier": security_tier,
        }

    return app
