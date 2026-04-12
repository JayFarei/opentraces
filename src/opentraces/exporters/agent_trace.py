"""Agent Trace v0.1.0 exporter.

Converts TraceRecord into a spec-compliant Agent Trace JSON record.
Focuses on the attribution subset — the part the v0.1.0 spec stabilized
around — plus minimal metadata needed to tie the record back to the
original session.

The JSONL emitted here is compatible with cursor/agent-trace readers:
- files[].path
- files[].conversations[].contributor (with optional model_id)
- files[].conversations[].ranges[] (start_line, end_line, content_hash,
  optional original, optional contributor override)
- optional ids, related, revision.

Non-attribution trajectory data (steps, metrics, intent, tool_definitions)
is intentionally NOT included because the Agent Trace v0.1 spec scope is
attribution; round-tripping trajectory data would require a superset
spec, which is what the opentraces schema already does.
"""

from __future__ import annotations

import json
from typing import Any

from opentraces_schema import TraceRecord


def _strip_nones(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def to_agent_trace(trace: TraceRecord) -> dict[str, Any]:
    """Render a TraceRecord as an Agent Trace v0.1.0-shaped dict."""
    out: dict[str, Any] = {
        "schema": "agent-trace/v0.1.0",
        "trace_id": trace.trace_id,
        "attribution": None,
    }
    if trace.attribution is None:
        return out

    files_out: list[dict] = []
    for f in trace.attribution.files:
        convs_out: list[dict] = []
        for conv in f.conversations:
            ranges_out: list[dict] = []
            for rng in conv.ranges:
                ranges_out.append(_strip_nones({
                    "start_line": rng.start_line,
                    "end_line": rng.end_line,
                    "content_hash": rng.content_hash,
                    "confidence": rng.confidence,
                    "change_type": rng.change_type,
                    "original": rng.original,
                    "contributor": rng.contributor,
                }))
            convs_out.append(_strip_nones({
                "contributor": conv.contributor,
                "url": conv.url,
                "ids": conv.ids,
                "related": conv.related,
                "ranges": ranges_out,
            }))
        files_out.append({"path": f.path, "conversations": convs_out})
    out["attribution"] = _strip_nones({
        "revision": trace.attribution.revision,
        "files": files_out,
        "unaccounted_files": trace.attribution.unaccounted_files,
    })
    if trace.task and trace.task.repository_url:
        out["repository_url"] = trace.task.repository_url
    return out


def export_to_jsonl(traces: list[TraceRecord], out_path) -> int:
    """Write each trace as one Agent Trace line. Returns count written."""
    from pathlib import Path
    p = Path(out_path)
    count = 0
    with open(p, "w", encoding="utf-8") as fp:
        for t in traces:
            fp.write(json.dumps(to_agent_trace(t), ensure_ascii=False) + "\n")
            count += 1
    return count


def from_agent_trace(data: dict[str, Any]) -> dict[str, Any]:
    """Convert an Agent Trace record dict into opentraces-schema attribution
    shape. Returns a dict suitable for building a TraceRecord via
    `opentraces_schema.TraceRecord.model_validate`.

    The caller is responsible for supplying agent/session fields not
    present in Agent Trace (session_id, agent.name, timestamps).
    """
    trace_id = data.get("trace_id") or ""
    attribution_in = data.get("attribution") or {}
    files_in = attribution_in.get("files") or []
    files_out: list[dict] = []
    for f in files_in:
        convs = []
        for conv in f.get("conversations", []) or []:
            ranges = []
            for rng in conv.get("ranges", []) or []:
                ranges.append({
                    "start_line": rng.get("start_line"),
                    "end_line": rng.get("end_line"),
                    "content_hash": rng.get("content_hash"),
                    "confidence": rng.get("confidence"),
                    "change_type": rng.get("change_type", "addition"),
                    "original": rng.get("original"),
                    "contributor": rng.get("contributor"),
                })
            convs.append({
                "contributor": conv.get("contributor") or {},
                "url": conv.get("url"),
                "ids": conv.get("ids"),
                "related": conv.get("related"),
                "ranges": ranges,
            })
        files_out.append({"path": f.get("path", ""), "conversations": convs})
    return {
        "trace_id": trace_id,
        "attribution": {
            "experimental": False,
            "revision": attribution_in.get("revision"),
            "files": files_out,
            "unaccounted_files": attribution_in.get("unaccounted_files"),
        },
        "repository_url": data.get("repository_url"),
    }
