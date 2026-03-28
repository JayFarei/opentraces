"""Flask application for the opentraces web review interface.

Serves a local web UI for Tier 3 strict review: browse sessions,
approve/reject/redact traces, then push to HF Hub.
"""

from __future__ import annotations

import json
import os
import random
import string
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template, request

from ...config import STAGING_DIR
from ...state import StateManager, TraceStatus

# StateManager for persistent review decisions
_state_manager: StateManager | None = None

def _get_state() -> StateManager:
    global _state_manager
    if _state_manager is None:
        _state_manager = StateManager()
    return _state_manager

# In-memory review state as fallback for display (NOT authoritative for push)
_review_state: dict[str, dict[str, Any]] = {}
# Cache for loaded/generated traces (avoids regenerating sample data per request)
_trace_cache: list[dict[str, Any]] | None = None


def _generate_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def _generate_sample_traces() -> list[dict[str, Any]]:
    """Generate sample trace data for demo purposes when no real staged traces exist."""
    models = [
        "anthropic/claude-sonnet-4-20250514",
        "anthropic/claude-opus-4-20250514",
        "openai/gpt-4o",
        "anthropic/claude-3-haiku",
    ]
    agents = ["claude-code", "cursor", "codex-cli", "aider"]
    tasks = [
        "Refactor the authentication module to use JWT tokens instead of session cookies",
        "Fix the race condition in the WebSocket handler causing dropped messages",
        "Add pagination to the /api/users endpoint with cursor-based navigation",
        "Implement rate limiting middleware for the public API endpoints",
        "Write unit tests for the payment processing service",
        "Migrate database schema from v2 to v3 with zero-downtime deploy",
        "Debug memory leak in the image processing pipeline",
        "Add OpenTelemetry tracing to all gRPC service calls",
    ]
    tool_names = [
        "Read", "Edit", "Bash", "Grep", "Glob", "Write",
        "WebSearch", "ListFiles", "RunTests",
    ]

    traces = []
    for i in range(12):
        trace_id = _generate_trace_id()
        model = random.choice(models)
        agent = random.choice(agents)
        task_desc = tasks[i % len(tasks)]
        num_steps = random.randint(4, 20)
        num_tool_calls = random.randint(2, num_steps * 2)
        num_snippets = random.randint(0, 5)
        num_flags = random.choice([0, 0, 0, 1, 2, 3])

        steps = []
        for s in range(num_steps):
            role = "agent" if s % 3 != 0 else ("user" if s % 3 == 1 else "system")
            if s == 0:
                role = "user"

            step_tool_calls = []
            step_observations = []
            step_snippets_list = []

            if role == "agent" and random.random() > 0.3:
                tc_count = random.randint(1, 3)
                for t in range(tc_count):
                    tc_id = f"tc_{s}_{t}"
                    tool = random.choice(tool_names)
                    step_tool_calls.append({
                        "tool_call_id": tc_id,
                        "tool_name": tool,
                        "input": _sample_tool_input(tool),
                        "duration_ms": random.randint(50, 5000),
                    })
                    step_observations.append({
                        "source_call_id": tc_id,
                        "content": _sample_tool_output(tool),
                        "output_summary": f"{tool} completed successfully",
                        "error": None,
                    })

            if role == "agent" and random.random() > 0.7:
                step_snippets_list.append({
                    "file_path": f"src/{''.join(random.choices(string.ascii_lowercase, k=6))}.py",
                    "start_line": random.randint(1, 100),
                    "end_line": random.randint(101, 200),
                    "language": "python",
                    "text": 'def example():\n    """Sample function."""\n    return True\n',
                    "source_step": s,
                })

            content = _sample_content(role, task_desc, s)
            reasoning = None
            if role == "agent" and random.random() > 0.6:
                reasoning = (
                    "Let me think about how to approach this. "
                    "The user wants to modify the existing code, so I need to "
                    "first understand the current structure, then identify the "
                    "specific changes needed, and finally implement them carefully."
                )

            parent_step = None
            call_type = "main"
            if s > 5 and random.random() > 0.8:
                parent_step = random.randint(0, s - 1)
                call_type = "subagent"

            steps.append({
                "step_index": s,
                "role": role,
                "content": content,
                "reasoning_content": reasoning,
                "model": model if role == "agent" else None,
                "system_prompt_hash": "abc123" if s == 0 else None,
                "agent_role": "main" if call_type == "main" else "explore",
                "parent_step": parent_step,
                "call_type": call_type,
                "subagent_trajectory_ref": None,
                "tools_available": tool_names if role == "agent" else [],
                "tool_calls": step_tool_calls,
                "observations": step_observations,
                "snippets": step_snippets_list,
                "token_usage": {
                    "input_tokens": random.randint(500, 5000),
                    "output_tokens": random.randint(100, 2000),
                    "cache_read_tokens": random.randint(0, 3000),
                    "cache_write_tokens": random.randint(0, 1000),
                    "prefix_reuse_tokens": 0,
                },
                "timestamp": f"2026-03-27T{10 + s // 4:02d}:{(s * 7) % 60:02d}:00Z",
            })

        # Security flags
        security_flags = []
        if num_flags > 0:
            flag_types = [
                ("api_key_detected", "Possible API key found in tool output"),
                ("pii_email", "Email address detected in content"),
                ("secret_pattern", "Pattern matching secret/password detected"),
                ("ip_address", "Internal IP address found"),
            ]
            for f in range(num_flags):
                ft = flag_types[f % len(flag_types)]
                security_flags.append({
                    "type": ft[0],
                    "reason": ft[1],
                    "step_index": random.randint(0, num_steps - 1),
                    "severity": random.choice(["high", "medium", "low"]),
                })

        total_input = sum(s_["token_usage"]["input_tokens"] for s_ in steps)
        total_output = sum(s_["token_usage"]["output_tokens"] for s_ in steps)

        trace = {
            "schema_version": "0.1.0",
            "trace_id": trace_id,
            "session_id": f"session-{trace_id[:8]}",
            "content_hash": None,
            "timestamp_start": f"2026-03-{20 + i % 8:02d}T10:00:00Z",
            "timestamp_end": f"2026-03-{20 + i % 8:02d}T10:{random.randint(5, 55):02d}:00Z",
            "task": {
                "description": task_desc,
                "source": "user_prompt",
                "repository": f"org/project-{chr(65 + i % 4).lower()}",
                "base_commit": None,
            },
            "agent": {
                "name": agent,
                "version": "1.0.0",
                "model": model,
            },
            "environment": {
                "os": "darwin",
                "shell": "zsh",
                "vcs": {"type": "git", "base_commit": "abc123", "branch": "main", "diff": None},
                "language_ecosystem": ["python", "typescript"],
            },
            "system_prompts": {"abc123": "You are a helpful coding assistant."},
            "tool_definitions": [],
            "steps": steps,
            "outcome": {
                "success": random.choice([True, True, True, False]),
                "signal_source": "deterministic",
                "signal_confidence": "derived",
                "description": "Task completed" if random.random() > 0.3 else "Partial completion",
                "patch": None,
                "committed": random.choice([True, False]),
                "commit_sha": None,
            },
            "dependencies": [],
            "metrics": {
                "total_steps": num_steps,
                "total_input_tokens": total_input,
                "total_output_tokens": total_output,
                "total_duration_s": random.uniform(30, 600),
                "cache_hit_rate": random.uniform(0.1, 0.9),
                "estimated_cost_usd": round(random.uniform(0.01, 2.5), 4),
            },
            "security": {
                "tier": 3,
                "flags_reviewed": 0,
                "redactions_applied": 0,
                "classifier_version": "0.1.0",
            },
            "attribution": None,
            "metadata": {
                "project": f"project-{chr(65 + i % 4).lower()}",
            },
            "_security_flags": security_flags,
        }
        traces.append(trace)

    return traces


def _sample_tool_input(tool_name: str) -> dict[str, Any]:
    """Generate sample tool input based on tool name."""
    inputs = {
        "Read": {"file_path": "/src/main.py", "limit": 50},
        "Edit": {
            "file_path": "/src/main.py",
            "old_string": "def old_func():",
            "new_string": "def new_func():",
        },
        "Bash": {"command": "python -m pytest tests/ -v", "description": "Run tests"},
        "Grep": {"pattern": "def process_", "path": "/src/", "output_mode": "content"},
        "Glob": {"pattern": "**/*.py", "path": "/src/"},
        "Write": {"file_path": "/src/new_file.py", "content": "# New module\n"},
        "WebSearch": {"query": "python async best practices"},
        "ListFiles": {"path": "/src/"},
        "RunTests": {"test_path": "tests/", "verbose": True},
    }
    return inputs.get(tool_name, {"input": "sample"})


def _sample_tool_output(tool_name: str) -> str:
    """Generate sample tool output."""
    outputs = {
        "Read": '     1\tdef process_request(req):\n     2\t    """Handle incoming request."""\n     3\t    validate(req)\n     4\t    return Response(status=200)\n',
        "Edit": "Successfully edited /src/main.py",
        "Bash": "===== 12 passed, 0 failed in 3.42s =====",
        "Grep": "/src/handlers.py:15: def process_webhook(data):\n/src/utils.py:42: def process_batch(items):",
        "Glob": "/src/main.py\n/src/utils.py\n/src/handlers.py\n/src/models.py",
        "Write": "File written: /src/new_file.py",
        "WebSearch": "Found 5 results for 'python async best practices'",
        "ListFiles": "main.py\nutils.py\nhandlers.py\nmodels.py\ntests/",
        "RunTests": "All 12 tests passed.",
    }
    return outputs.get(tool_name, "Operation completed.")


def _sample_content(role: str, task: str, step_index: int) -> str:
    """Generate sample message content."""
    if role == "user":
        if step_index == 0:
            return task
        return random.choice([
            "Yes, that looks correct. Please proceed.",
            "Can you also add error handling for edge cases?",
            "Good. Now run the tests to make sure nothing is broken.",
        ])
    if role == "system":
        return "You are a helpful coding assistant. Follow best practices."
    # agent
    responses = [
        "I'll start by reading the relevant files to understand the current implementation.",
        "Let me examine the code structure and identify the changes needed.",
        "I've made the changes. Let me run the tests to verify everything works correctly.",
        "The implementation looks good. Here's a summary of what I changed:\n\n1. Updated the main handler\n2. Added input validation\n3. Wrote new test cases",
        "I found a potential issue in the error handling. Let me fix that first.",
    ]
    return responses[step_index % len(responses)]


def _load_traces(staging_dir: Path) -> list[dict[str, Any]]:
    """Load traces from staging directory, or generate samples if none exist.

    Uses a module-level cache so sample data remains stable across requests.
    """
    global _trace_cache
    if _trace_cache is not None:
        return _trace_cache

    traces = []

    if staging_dir.exists():
        for jsonl_file in sorted(staging_dir.glob("*.jsonl")):
            try:
                text = jsonl_file.read_text().strip()
                if text:
                    for line in text.splitlines():
                        line = line.strip()
                        if line:
                            trace = json.loads(line)
                            traces.append(trace)
            except (json.JSONDecodeError, OSError):
                continue

    if not traces:
        traces = _generate_sample_traces()

    _trace_cache = traces
    return traces


def _get_review_status(trace_id: str) -> str:
    """Get the review status of a trace from StateManager."""
    state = _get_state()
    entry = state.get_trace(trace_id)
    if entry:
        status_map = {
            TraceStatus.STAGED: "staged",
            TraceStatus.APPROVED: "approved",
            TraceStatus.REJECTED: "rejected",
            TraceStatus.UPLOADED: "uploaded",
        }
        return status_map.get(entry.status, "pending")
    # Fallback to in-memory state
    if trace_id in _review_state:
        return _review_state[trace_id].get("status", "pending")
    return "pending"


def _get_redacted_steps(trace_id: str) -> set[int]:
    """Get the set of redacted step indices for a trace."""
    if trace_id in _review_state:
        return set(_review_state[trace_id].get("redacted_steps", []))
    return set()


def create_app(staging_dir: str = None) -> Flask:
    """Create the Flask review app."""
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).parent / "templates"),
        static_folder=str(Path(__file__).parent / "static"),
    )
    app.secret_key = "opentraces-review-" + uuid.uuid4().hex[:8]

    staging_path = Path(staging_dir) if staging_dir else STAGING_DIR

    def _traces() -> list[dict[str, Any]]:
        return _load_traces(staging_path)

    # --- Page routes ---

    @app.route("/")
    def index():
        """Session list page."""
        traces = _traces()
        # Extract filter options
        projects = sorted({
            t.get("metadata", {}).get("project", "unknown")
            for t in traces
        })
        models = sorted({
            t.get("agent", {}).get("model", "unknown")
            for t in traces
        })

        # Apply filters
        project_filter = request.args.get("project", "")
        model_filter = request.args.get("model", "")
        status_filter = request.args.get("status", "")

        filtered = traces
        if project_filter:
            filtered = [
                t for t in filtered
                if t.get("metadata", {}).get("project") == project_filter
            ]
        if model_filter:
            filtered = [
                t for t in filtered
                if t.get("agent", {}).get("model") == model_filter
            ]
        if status_filter:
            filtered = [
                t for t in filtered
                if _get_review_status(t["trace_id"]) == status_filter
            ]

        # Enrich with review status
        for t in filtered:
            t["_review_status"] = _get_review_status(t["trace_id"])
            t["_security_flag_count"] = len(t.get("_security_flags", []))

        return render_template(
            "sessions.html",
            traces=filtered,
            total_count=len(traces),
            projects=projects,
            models=models,
            project_filter=project_filter,
            model_filter=model_filter,
            status_filter=status_filter,
        )

    @app.route("/session/<trace_id>")
    def session_detail(trace_id: str):
        """Session detail page."""
        traces = _traces()
        trace = None
        for t in traces:
            if t["trace_id"] == trace_id:
                trace = t
                break

        if trace is None:
            return "Session not found", 404

        trace["_review_status"] = _get_review_status(trace_id)
        trace["_redacted_steps"] = _get_redacted_steps(trace_id)

        return render_template("session_detail.html", trace=trace)

    @app.route("/stats")
    def stats_page():
        """Stats dashboard page."""
        traces = _traces()
        stats = _compute_stats(traces)
        return render_template("stats.html", stats=stats)

    # --- API routes ---

    @app.route("/api/sessions")
    def api_sessions():
        """JSON API for session list."""
        traces = _traces()
        sessions = []
        for t in traces:
            sessions.append({
                "trace_id": t["trace_id"],
                "task": (t.get("task", {}).get("description") or "")[:100],
                "model": t.get("agent", {}).get("model", "unknown"),
                "agent": t.get("agent", {}).get("name", "unknown"),
                "steps": t.get("metrics", {}).get("total_steps", len(t.get("steps", []))),
                "tool_calls": sum(
                    len(s.get("tool_calls", [])) for s in t.get("steps", [])
                ),
                "timestamp": t.get("timestamp_start"),
                "status": _get_review_status(t["trace_id"]),
                "security_flags": len(t.get("_security_flags", [])),
                "project": t.get("metadata", {}).get("project", "unknown"),
            })
        return jsonify(sessions)

    @app.route("/api/stats")
    def api_stats():
        """Dashboard stats."""
        traces = _traces()
        return jsonify(_compute_stats(traces))

    @app.route("/api/session/<trace_id>/approve", methods=["POST"])
    def api_approve(trace_id: str):
        """Approve a session, persisting to StateManager."""
        state = _get_state()
        state.set_trace_status(trace_id, TraceStatus.APPROVED, session_id=trace_id)
        if trace_id not in _review_state:
            _review_state[trace_id] = {}
        _review_state[trace_id]["status"] = "approved"
        return jsonify({"status": "approved", "trace_id": trace_id})

    @app.route("/api/session/<trace_id>/reject", methods=["POST"])
    def api_reject(trace_id: str):
        """Reject a session, persisting to StateManager."""
        state = _get_state()
        state.set_trace_status(trace_id, TraceStatus.REJECTED, session_id=trace_id)
        if trace_id not in _review_state:
            _review_state[trace_id] = {}
        _review_state[trace_id]["status"] = "rejected"
        return jsonify({"status": "rejected", "trace_id": trace_id})

    @app.route("/api/session/<trace_id>/step/<int:step_index>/redact", methods=["POST"])
    def api_redact_step(trace_id: str, step_index: int):
        """Redact a step's content, persisting to the staging JSONL on disk."""
        global _trace_cache

        # Validate trace_id to prevent path traversal
        import re as _re
        if not _re.match(r'^[a-f0-9-]+$', trace_id):
            return jsonify({"error": "Invalid trace ID format"}), 400

        # Locate the staging JSONL file for this trace
        staging_file = staging_path / f"{trace_id}.jsonl"
        if not staging_file.exists():
            return jsonify({"error": f"Staging file not found for {trace_id}"}), 404

        # Load, modify, and atomically rewrite the staging file
        text = staging_file.read_text().strip()
        if not text:
            return jsonify({"error": "Staging file is empty"}), 404

        trace_data = json.loads(text.splitlines()[0])

        # Find and redact the matching step
        steps = trace_data.get("steps", [])
        if step_index < 0 or step_index >= len(steps):
            return jsonify({"error": f"Step index {step_index} out of range"}), 404

        steps[step_index]["content"] = "[REDACTED]"
        steps[step_index]["reasoning_content"] = None
        steps[step_index]["tool_calls"] = []
        steps[step_index]["observations"] = []
        steps[step_index]["snippets"] = []

        # Atomic write: temp file + os.replace for crash safety
        new_line = json.dumps(trace_data, ensure_ascii=False)
        fd = tempfile.NamedTemporaryFile(
            mode="w",
            dir=str(staging_path),
            suffix=".jsonl.tmp",
            delete=False,
        )
        try:
            fd.write(new_line + "\n")
            fd.flush()
            os.fsync(fd.fileno())
            fd.close()
            os.replace(fd.name, str(staging_file))
        except BaseException:
            fd.close()
            try:
                os.unlink(fd.name)
            except OSError:
                pass
            raise

        # Invalidate the in-memory trace cache so next request re-reads from disk
        _trace_cache = None

        # Update in-memory review state for immediate UI feedback
        if trace_id not in _review_state:
            _review_state[trace_id] = {}
        if "redacted_steps" not in _review_state[trace_id]:
            _review_state[trace_id]["redacted_steps"] = []
        if step_index not in _review_state[trace_id]["redacted_steps"]:
            _review_state[trace_id]["redacted_steps"].append(step_index)

        return jsonify({
            "status": "redacted",
            "trace_id": trace_id,
            "step_index": step_index,
        })

    @app.route("/api/push", methods=["POST"])
    def api_push():
        """Push all approved sessions to HF Hub."""
        traces = _traces()
        approved = [
            t for t in traces
            if _get_review_status(t["trace_id"]) == "approved"
        ]
        if not approved:
            return jsonify({"error": "No approved sessions to push"}), 400

        # Try the real upload pipeline
        try:
            import os
            from ...config import load_config, get_dataset_name
            from ...upload.hf_hub import HFUploader
            from ...upload.dataset_card import generate_dataset_card
            from opentraces_schema import TraceRecord

            cfg = load_config()
            if not cfg.hf_token:
                return jsonify({
                    "status": "pushed",
                    "count": len(approved),
                    "trace_ids": [t["trace_id"] for t in approved],
                    "message": f"{len(approved)} session(s) approved. Set HF_TOKEN to push to Hub.",
                    "needs_token": True,
                })

            records = [TraceRecord.model_validate(t) for t in approved]
            from huggingface_hub import HfApi
            api = HfApi(token=cfg.hf_token)
            username = api.whoami().get("name", "unknown")
            repo_id = get_dataset_name(cfg, username)

            uploader = HFUploader(token=cfg.hf_token, repo_id=repo_id)
            uploader.ensure_repo_exists()
            result = uploader.upload_traces(records)

            if result.success:
                state = _get_state()
                for t in approved:
                    state.set_trace_status(t["trace_id"], TraceStatus.UPLOADED)
                return jsonify({
                    "status": "pushed",
                    "count": result.trace_count,
                    "shard": result.shard_name,
                    "repo_url": result.repo_url,
                    "message": f"Pushed {result.trace_count} session(s) to {repo_id}",
                })
            else:
                return jsonify({"error": f"Upload failed: {result.error}"}), 500

        except ImportError:
            return jsonify({
                "status": "pushed",
                "count": len(approved),
                "trace_ids": [t["trace_id"] for t in approved],
                "message": f"{len(approved)} session(s) approved (upload module not available)",
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    def _compute_stats(traces: list[dict[str, Any]]) -> dict[str, Any]:
        """Compute dashboard statistics."""
        total = len(traces)
        approved = sum(1 for t in traces if _get_review_status(t["trace_id"]) == "approved")
        rejected = sum(1 for t in traces if _get_review_status(t["trace_id"]) == "rejected")
        pending = total - approved - rejected

        total_tokens = sum(
            t.get("metrics", {}).get("total_input_tokens", 0)
            + t.get("metrics", {}).get("total_output_tokens", 0)
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
        total_flags = sum(len(t.get("_security_flags", [])) for t in traces)

        # Determine security tier from trace data if available
        security_tier = None
        for t in traces:
            tier = t.get("security", {}).get("tier")
            if tier is not None:
                security_tier = f"Tier {tier}"
                break

        return {
            "total": total,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
            "total_tokens": total_tokens,
            "total_tool_calls": total_tool_calls,
            "total_cost_usd": round(total_cost, 4),
            "total_security_flags": total_flags,
            "security_tier": security_tier,
        }

    return app
