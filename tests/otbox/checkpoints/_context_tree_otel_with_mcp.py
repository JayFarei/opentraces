"""``c-context-tree-otel-with-mcp`` — plan 078 fixture-stage checkpoint.

Composes onto ``c-context-tree-otel-linear``: same shape plus one
MCP server connection event folded into the staged OTel snapshot's
``mcp_servers`` list. Drives the ``context-tree-otel-{mcp,plugin,
hook}-lifecycle`` journeys which assert the OTel-side lifecycle
streams (R4) populate the runtime_state / tool_registry layers.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..drivers.base import Driver
from ..env import Box
from . import Checkpoint, CheckpointError, register

_CHECKPOINT_NAME = "c-context-tree-otel-with-mcp"
_MCP_SERVER_NAME = "test-mcp-server"
_WITH_MCP_TRACE_ID = "otel-mcp-trace-0001"
_WITH_MCP_SESSION_ID = "otel-mcp-sess-0001"


def _check(result, label: str) -> None:
    if not result.ok:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} step {label!r} failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )


def _delta(driver: Driver, box: Box) -> None:
    parent_audit = box.notes.get("c_context_tree_otel_linear_audit") or {}
    session_id = parent_audit.get("session_id")
    if not session_id:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} requires parent c-context-tree-otel-linear "
            "to have populated c_context_tree_otel_linear_audit; not found."
        )
    # Re-derive snapshot path against the CURRENT box's home (the parent
    # audit's absolute path is stale once the parent box is torn down or
    # restored under a different box id).
    home_dir = driver.paths(box)["home"]
    snapshot_path = f"{home_dir}/.opentraces/staging/otel/{session_id}.json"

    snap = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    snap["session_id"] = _WITH_MCP_SESSION_ID
    snap["mcp_servers"] = [
        {
            "server_name": _MCP_SERVER_NAME,
            "transport_type": "stdio",
            "server_scope": "user",
            "status": "connected",
            "is_plugin": False,
            "tool_names": ["mcp_tool_a", "mcp_tool_b"],
        },
    ]
    # Sync into the daemon's would-be runtime_state aggregator too.
    snap["runtime_state"]["mcp_servers"] = snap["mcp_servers"]
    # Rename prompt ids so the with-mcp session is content-distinct from
    # the parent's flushed events (otherwise the dedup-by-content-hash
    # collapses everything back into the parent's trace).
    snap["nodes_by_prompt"] = {
        f"prompt-mcp-{i:04d}": v
        for i, (_k, v) in enumerate(snap["nodes_by_prompt"].items(), start=1)
    }

    # Write the with-mcp snapshot as a NEW session file (parent's stays intact).
    home_dir_path = Path(home_dir)
    with_mcp_snapshot_path = home_dir_path / ".opentraces" / "staging" / "otel" / f"{_WITH_MCP_SESSION_ID}.json"
    driver.put_text(
        box, str(with_mcp_snapshot_path),
        json.dumps(snap, indent=2, sort_keys=True),
    )

    # Flush the with-mcp session into the project event log under a new trace_id.
    # ALWAYS use the current box's project (parent audit's path is stale once the parent box is torn down).
    project_dir = str(box.project)
    cli = f"{project_dir}/.testvenv/bin/opentraces"
    result = driver.exec(
        box,
        [
            cli, "capture-otlp", "flush",
            "--session", _WITH_MCP_SESSION_ID,
            "--project", project_dir,
            "--trace-id", _WITH_MCP_TRACE_ID,
            "--json",
        ],
        cwd=project_dir,
    )
    if not result.ok:
        raise CheckpointError(
            f"{_CHECKPOINT_NAME} step 'flush-with-mcp' failed (rc={result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:300]}"
        )

    box.notes["c_context_tree_otel_with_mcp_audit"] = {
        **parent_audit,
        "trace_id": _WITH_MCP_TRACE_ID,
        "session_id": _WITH_MCP_SESSION_ID,
        "mcp_server_name": _MCP_SERVER_NAME,
        "mcp_servers_connected": 1,
    }


register(
    Checkpoint(
        name=_CHECKPOINT_NAME,
        composed_from="c-context-tree-otel-linear",
        delta=_delta,
        cache=True,
        description=(
            "c-context-tree-otel-linear + one mcp_server_connection event "
            "folded into the staged session's mcp_servers list. Drives the "
            "mcp/plugin/hook lifecycle journeys."
        ),
        provides={
            "captured_traces": 1,
            "context_tree_built": True,
            "otel_captures_present": True,
            "otel_settings_patched": True,
            "otlp_receiver_running": True,
            "otel_bypass_active": True,
            "mcp_servers_connected": 1,
        },
    )
)
