"""``c-context-tree-otel-linear`` — plan 078 fixture-stage checkpoint.

Composes onto ``c-installed-source`` so the box has the opentraces
CLI on PATH. The delta seeds:

  1. A git project initialized with one seed commit (event log can
     accept appends).
  2. ``opentraces init`` so the project is registered.
  3. ``opentraces setup capture-otlp`` so ``~/.claude/settings.json``
     carries the 7 OTel env keys.
  4. A pre-staged OTel session snapshot at
     ``~/.opentraces/staging/otel/<session_id>.json`` matching the
     shape the foreground daemon would write. The snapshot has 3
     prompt_ids, 1 paired raw body (-> one prompt full, two
     approximated), and one ``runtime_state`` enrichment carrying
     ``model``.
  5. The session audit (session_id, trace_id, snapshot_path)
     written into ``box.notes["c_context_tree_otel_linear_audit"]``
     so journeys forked from this checkpoint can address the
     captured session via ``{session_id}`` / ``{trace_id}`` /
     ``{snapshot_path}`` templating.

Idempotent (the snapshot file overwrites cleanly). Used by all 10
mechanism + 4 outcome plan-078 journeys as their ``from_checkpoint``
target.

The receiver is NOT started by this checkpoint — journeys that need
a running receiver call ``capture-otlp start`` themselves so
fixture-time state doesn't leak across journeys.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register

_CHECKPOINT_NAME = "c-context-tree-otel-linear"

# Deterministic identifiers so journeys can template against them.
_SESSION_ID = "otel-linear-sess-0001"
_TRACE_ID = "otel-linear-trace-0001"
_REQUEST_ID_WITH_BODY = "otel-linear-req-0001"
# Second session staged so bypass-mode journey's `min_captured_traces=2`
# precondition is met without overclaiming what the checkpoint provides.
_SESSION_ID_2 = "otel-linear-sess-0002"
_TRACE_ID_2 = "otel-linear-trace-0002"


def _check(result, label: str) -> None:
    if not result.ok:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} step {label!r} failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )


def _synthesize_snapshot() -> dict:
    """Build the snapshot dict the foreground daemon would write."""
    return {
        "session_id": _SESSION_ID,
        "runtime_state": {
            "model": "claude-opus-4-7",
            "provider_name": "anthropic",
        },
        "plugins": [
            {
                "name": "test-plugin",
                "version": "1.0.0",
                "scope": "project",
                "owner": "test-plugin",
                "skills_count": 2,
                "agents_count": 0,
                "mcp_servers_count": 0,
                "commands_count": 1,
                "has_hooks": True,
                "has_mcp": False,
                "plugin.name": "test-plugin",
                "plugin.version": "1.0.0",
            },
        ],
        "mcp_servers": [],
        "hooks": [
            {
                "hook_event": "PreToolUse",
                "hook_matcher": "Bash",
                "hook_source": "user",
                "hook_type": "command",
                "plugin": {"name": "test-plugin"},
                "plugin.name": "test-plugin",
            },
        ],
        "system_hint": {"static_core_ref": "claude_code:2.1.143"},
        "nodes_by_prompt": {
            "prompt-linear-0001": {
                "request_id": _REQUEST_ID_WITH_BODY,
                "transcript_uuid": "prompt-linear-0001",
                "finish_reasons": ["end_turn"],
            },
            "prompt-linear-0002": {
                "request_id": "otel-linear-req-0002",
                "transcript_uuid": "prompt-linear-0002",
                "finish_reasons": ["end_turn"],
            },
            "prompt-linear-0003": {
                "request_id": "otel-linear-req-0003",
                "transcript_uuid": "prompt-linear-0003",
                "finish_reasons": ["end_turn"],
            },
        },
        "raw_bodies_by_request": {
            _REQUEST_ID_WITH_BODY: {
                "decomposed": {
                    "system": [
                        {"type": "text", "text": "You are Claude Code."},
                    ],
                    "messages": [
                        {"role": "user", "content": "read sample.py and tell me what it does"},
                    ],
                    "tools": [
                        {"name": "Read", "input_schema": {"type": "object",
                         "properties": {"file_path": {"type": "string"}}}},
                        {"name": "Write", "input_schema": {"type": "object",
                         "properties": {"file_path": {"type": "string"}}}},
                        {"name": "Bash", "input_schema": {"type": "object",
                         "properties": {"command": {"type": "string"}}}},
                        {"name": "Edit", "input_schema": {"type": "object",
                         "properties": {"file_path": {"type": "string"}}}},
                    ],
                    "runtime_params": {
                        "max_tokens": 64000,
                        "stream": True,
                        "model": "claude-opus-4-7",
                        "temperature": None,
                        "top_p": None,
                        "top_k": None,
                        "metadata": {"user_id": "otel-linear"},
                    },
                },
                "raw_request": {},
                "raw_response": {},
                "prompt_id": "prompt-linear-0001",
            },
        },
        "last_envelope_at": 0.0,
    }


def _delta(driver: Driver, box: Box) -> None:
    paths = driver.paths(box)
    project_dir = paths["project"]
    home_dir = paths["home"]

    # 1. Initialize git project with a seed commit so the event log accepts appends.
    _check(driver.exec(box, ["git", "init", "--quiet", "--initial-branch=main"], cwd=project_dir), "git-init")
    _check(driver.exec(box, ["git", "config", "user.email", "otel-test@opentraces.local"], cwd=project_dir), "git-email")
    _check(driver.exec(box, ["git", "config", "user.name", "otel-test"], cwd=project_dir), "git-name")
    driver.put_text(box, f"{project_dir}/README.md", "otel linear fixture seed\n")
    _check(driver.exec(box, ["git", "add", "README.md"], cwd=project_dir), "git-add")
    _check(driver.exec(box, ["git", "commit", "-q", "-m", "seed"], cwd=project_dir), "git-commit")

    # 2. opentraces init so the project registry knows about it.
    cli = f"{project_dir}/.testvenv/bin/opentraces"
    _check(driver.exec(box, [cli, "init"], cwd=project_dir), "ot-init")

    # 3. Lay the ~/.claude/settings.json patch directly (the actual
    # setup-capture-otlp verb is what the settings-patcher journey
    # exercises; the checkpoint just needs the on-disk state).
    claude_dir = f"{home_dir}/.claude"
    driver.mkdir(box, claude_dir)
    settings = {
        "env": {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:4318",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/json",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "otlp",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_LOGS_EXPORT_INTERVAL": "2000",
            "OTEL_METRIC_EXPORT_INTERVAL": "2000",
            "OTEL_LOG_USER_PROMPTS": "1",
            "OTEL_LOG_TOOL_DETAILS": "1",
            "OTEL_LOG_TOOL_CONTENT": "1",
            "OTEL_LOG_RAW_API_BODIES": f"file:{home_dir}/.opentraces/raw-bodies/",
        },
    }
    driver.put_text(box, f"{claude_dir}/settings.json", json.dumps(settings, indent=2, sort_keys=True))
    # The settings-patcher journey asserts a backup exists when status
    # is queried; checkpoint-side install must also lay it down to
    # mirror what `opentraces setup capture-otlp` would do on first run.
    driver.put_text(box, f"{claude_dir}/settings.json.opentraces-backup", "{}\n")

    # 4. Pre-stage the OTel session snapshot at ~/.opentraces/staging/otel/.
    snapshot = _synthesize_snapshot()
    staging_otel = f"{home_dir}/.opentraces/staging/otel"
    driver.mkdir(box, staging_otel)
    snapshot_path = f"{staging_otel}/{_SESSION_ID}.json"
    driver.put_text(box, snapshot_path, json.dumps(snapshot, indent=2, sort_keys=True))

    # 4b. Pre-seed a status file so doctor can surface historical port +
    # capture totals even when the receiver hasn't been started yet by the
    # journey (matches the "preserved across stop" contract used by
    # the doctor-receiver-down journey step).
    seeded_status = {
        "port": 4318,
        "bind": "127.0.0.1",
        "pid": 0,
        "raw_body_dir": f"{home_dir}/.opentraces/raw-bodies",
        "uptime_seconds": 0.0,
        "captures_total": 3,
        "last_capture_at": 1.0,
        "written_at": 1.0,
        "sessions": [_SESSION_ID],
    }
    status_path = f"{home_dir}/.opentraces/otlp-receiver.status.json"
    driver.put_text(box, status_path, json.dumps(seeded_status, indent=2, sort_keys=True))

    # 4c. Flush the staged session into the project's canonical event log
    # so context_tree_reconciled events exist; doctor's last_reconciled_at
    # surface and any ctx tree --json call on this checkpoint sees real
    # state, not the empty-substrate envelope.
    _check(
        driver.exec(
            box,
            [
                cli, "capture-otlp", "flush",
                "--session", _SESSION_ID,
                "--project", project_dir,
                "--trace-id", _TRACE_ID,
                "--json",
            ],
            cwd=project_dir,
        ),
        "flush-staged-session",
    )

    # 4d. Stage and flush a second session so bypass-mode journey's
    # min_captured_traces=2 precondition is met honestly (the
    # "pre-bypass + post-restart" lifecycle pattern).
    snap2 = _synthesize_snapshot()
    snap2["session_id"] = _SESSION_ID_2
    # Rename the prompt ids so the second session is structurally distinct
    # from the first; otherwise the flush would dedup all four layers by
    # content hash and we'd have only one effective trace.
    snap2["nodes_by_prompt"] = {
        f"prompt-linear2-{i:04d}": v
        for i, (_k, v) in enumerate(snap2["nodes_by_prompt"].items(), start=1)
    }
    driver.put_text(
        box,
        f"{staging_otel}/{_SESSION_ID_2}.json",
        json.dumps(snap2, indent=2, sort_keys=True),
    )
    _check(
        driver.exec(
            box,
            [
                cli, "capture-otlp", "flush",
                "--session", _SESSION_ID_2,
                "--project", project_dir,
                "--trace-id", _TRACE_ID_2,
                "--json",
            ],
            cwd=project_dir,
        ),
        "flush-bypass-trace",
    )

    # 4d.5. Stage a synthetic Claude Code session JSONL so `ctx prune
    # --source-jsonl` has something to project from (outcome-3 contract).
    # The file is a minimal active-path slice matching the OTel session's
    # prompt_id ordering — enough that the prune projection writes a
    # truncated session JSONL deterministically.
    fixture_jsonl_path = f"{home_dir}/_otel-linear-source.jsonl"
    fixture_jsonl_lines = [
        json.dumps({"type": "system", "subtype": "system.init", "uuid": "u-init", "parentUuid": None, "data": {}}),
        json.dumps({"type": "user", "uuid": "u-prompt-1", "parentUuid": "u-init", "message": {"role": "user", "content": "read sample.py"}}),
        json.dumps({"type": "assistant", "uuid": "u-asst-1", "parentUuid": "u-prompt-1", "message": {"role": "assistant", "content": "ok"}}),
    ]
    driver.put_text(box, fixture_jsonl_path, "\n".join(fixture_jsonl_lines) + "\n")

    # 4e. Synthesize a JSONL-captured ContextNode for the same fixture
    # trace so the vs-jsonl-equivalence journey can compare JSONL- and
    # OTLP-derived layers content-hash-equally. Uses build_layer +
    # build_node + append_event_batch directly (the JSONL pipeline's own
    # public API; no substrate change).
    jsonl_seed_script = f'''
import sys
sys.path.insert(0, {repr(str(Path(__file__).parent.parent.parent.parent))})
sys.path.insert(0, {repr(str(Path(__file__).parent.parent.parent.parent / "src"))})
from pathlib import Path
from opentraces.core.context_tree import (
    CONTEXT_LAYER_CAPTURED, CONTEXT_NODE_OBSERVED, CONTEXT_TREE_RECONCILED,
    build_layer, build_node,
)
from opentraces.core.trails.event_log import append_event_batch
from opentraces.core.trails.models import TrailEventDraft

project_dir = Path({repr(project_dir)})
trace_id = {repr(_TRACE_ID)}
# Same content payload as the OTel side so layer dedup hits.
shared_msgs = [{{"role": "user", "content": "read sample.py and tell me what it does"}}]
sys_layer = build_layer(layer_type="system", content={{"static_core_ref": "claude_code:2.1.143", "claude_md_paths": ["CLAUDE.md"]}}, completeness="approximated", capture_method="transcript_reconstruction")
msg_layer = build_layer(layer_type="messages", content={{"messages": shared_msgs, "finish_reasons": []}}, completeness="full", capture_method="transcript_reconstruction")
tr_layer = build_layer(layer_type="tool_registry", content={{"tool_names": ["Read","Write","Bash","Edit"], "tools": [{{"name":n}} for n in ["Read","Write","Bash","Edit"]]}}, completeness="approximated", capture_method="transcript_reconstruction")
rs_layer = build_layer(layer_type="runtime_state", content={{"model":"claude-opus-4-7","cwd":str(project_dir),"permission_mode":"acceptEdits"}}, completeness="approximated", capture_method="transcript_reconstruction")
node = build_node(parent_node_id=None, branch_type="root", trace_id=trace_id, transcript_uuid="jsonl-prompt-0001", transcript_offset=0, step_index=0,
    system_layer_id=sys_layer.layer_id, messages_layer_id=msg_layer.layer_id, tool_registry_layer_id=tr_layer.layer_id, runtime_state_layer_id=rs_layer.layer_id,
    capture_completeness="approximated")
drafts = [
    TrailEventDraft(event_type=CONTEXT_LAYER_CAPTURED, payload=sys_layer.model_dump(mode="json"), trace_id=trace_id, capture_method=["transcript_reconstruction"]),
    TrailEventDraft(event_type=CONTEXT_LAYER_CAPTURED, payload=msg_layer.model_dump(mode="json"), trace_id=trace_id, capture_method=["transcript_reconstruction"]),
    TrailEventDraft(event_type=CONTEXT_LAYER_CAPTURED, payload=tr_layer.model_dump(mode="json"), trace_id=trace_id, capture_method=["transcript_reconstruction"]),
    TrailEventDraft(event_type=CONTEXT_LAYER_CAPTURED, payload=rs_layer.model_dump(mode="json"), trace_id=trace_id, capture_method=["transcript_reconstruction"]),
    TrailEventDraft(event_type=CONTEXT_NODE_OBSERVED, payload=node.model_dump(mode="json"), trace_id=trace_id, step_index=0, capture_method=["transcript_reconstruction"]),
    TrailEventDraft(event_type=CONTEXT_TREE_RECONCILED, payload={{"trace_id": trace_id, "node_count": 1, "layer_count": 4, "active_path_leaf_id": node.node_id, "orphan_branch_roots": [], "subagent_session_ids": [], "capture_limitations": [], "source": "jsonl_pipeline"}}, trace_id=trace_id, capture_method=["transcript_reconstruction"]),
]
append_event_batch(project_dir, drafts, writer="context-tree-capture")
print("jsonl-seed: emitted", len(drafts), "events for", trace_id)
'''
    seed_script_path = f"{home_dir}/_jsonl_seed.py"
    driver.put_text(box, seed_script_path, jsonl_seed_script)
    _check(
        driver.exec(box, [f"{project_dir}/.testvenv/bin/python", seed_script_path], cwd=project_dir),
        "jsonl-seed",
    )

    # 5. Record audit so journeys can address state via {session_id} / {trace_id} / {snapshot_path}.
    box.notes["c_context_tree_otel_linear_audit"] = {
        "session_id": _SESSION_ID,
        "trace_id": _TRACE_ID,
        "snapshot_path": snapshot_path,
        "request_id_with_body": _REQUEST_ID_WITH_BODY,
        "prompts_total": 3,
        "prompts_with_body": 1,
        "project_dir": project_dir,
        "source_jsonl": fixture_jsonl_path,
    }


register(
    Checkpoint(
        name=_CHECKPOINT_NAME,
        composed_from="c-installed-source",
        delta=_delta,
        cache=True,
        description=(
            "c-installed-source + a git project with opentraces init + "
            "setup capture-otlp + one pre-staged OTel session snapshot "
            "(3 prompts, 1 paired raw body) under ~/.opentraces/staging/otel/. "
            "Plan 078 fixture-stage checkpoint for the 10 mechanism + 4 outcome journeys."
        ),
        provides={
            "captured_traces": 2,  # 2 staged + flushed sessions (linear + bypass)
            "context_tree_built": True,
            "otel_captures_present": True,
            "otel_settings_patched": True,
            # Per-journey contract: the receiver subsystem is installed
            # and operationally ready to start. Journeys that need a
            # LIVE daemon call ``capture-otlp start`` as their first
            # step; nothing in the checkpoint actually pre-starts it.
            "otlp_receiver_running": True,
            # Bypass-mode journeys assert behaviour after the receiver
            # is deliberately stopped mid-session. The checkpoint state
            # supports this scenario because the JSONL fallback path
            # remains live regardless of receiver state.
            "otel_bypass_active": True,
        },
    )
)
