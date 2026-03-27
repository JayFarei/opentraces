"""Interactive CLI for reviewing staged traces.

Provides a terminal-based interface for Tier 3 manual review,
an alternative to the web UI for quick reviews.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ..config import STAGING_DIR


def _load_staged_traces(staging_dir: Path) -> list[dict[str, Any]]:
    """Load traces from the staging directory."""
    traces = []
    if not staging_dir.exists():
        return traces

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

    return traces


def _print_trace_summary(trace: dict[str, Any], index: int) -> None:
    """Print a one-line summary of a trace."""
    task = (trace.get("task", {}).get("description") or "No description")[:80]
    agent = trace.get("agent", {}).get("name", "unknown")
    model = (trace.get("agent", {}).get("model") or "unknown").split("/")[-1]
    steps = trace.get("metrics", {}).get("total_steps", len(trace.get("steps", [])))
    tool_calls = sum(len(s.get("tool_calls", [])) for s in trace.get("steps", []))
    flags = len(trace.get("_security_flags", []))

    flag_indicator = f" [{flags} flags]" if flags > 0 else ""
    print(f"  [{index + 1}] {trace['trace_id'][:12]}... | {agent}/{model}")
    print(f"      {task}")
    print(f"      {steps} steps, {tool_calls} tool calls{flag_indicator}")
    print()


def _print_trace_detail(trace: dict[str, Any]) -> None:
    """Print detailed view of a trace."""
    print("\n" + "=" * 72)
    print(f"  Trace: {trace['trace_id']}")
    print(f"  Task:  {trace.get('task', {}).get('description', 'N/A')}")
    print(f"  Agent: {trace.get('agent', {}).get('name', 'N/A')}")
    print(f"  Model: {trace.get('agent', {}).get('model', 'N/A')}")
    print("=" * 72)

    for step in trace.get("steps", []):
        role = step.get("role", "unknown")
        idx = step.get("step_index", "?")
        content = step.get("content") or ""
        is_subagent = step.get("call_type") == "subagent"

        indent = "    " if is_subagent else "  "
        prefix = f"[sub]" if is_subagent else ""

        print(f"\n{indent}--- Step {idx} ({role}) {prefix} ---")

        if step.get("reasoning_content"):
            print(f"{indent}  [Reasoning]: {step['reasoning_content'][:200]}...")

        if content:
            for line in content.splitlines():
                print(f"{indent}  {line}")

        for tc in step.get("tool_calls", []):
            print(f"{indent}  > {tc['tool_name']} ({tc['tool_call_id']})")
            if tc.get("duration_ms"):
                print(f"{indent}    Duration: {tc['duration_ms']}ms")

        for obs in step.get("observations", []):
            output = (obs.get("content") or obs.get("output_summary") or "")[:200]
            if obs.get("error"):
                print(f"{indent}  ! Error: {obs['error']}")
            elif output:
                print(f"{indent}  < {output}")

    # Security flags
    flags = trace.get("_security_flags", [])
    if flags:
        print(f"\n  SECURITY FLAGS ({len(flags)}):")
        for flag in flags:
            print(f"    - [{flag.get('severity', 'unknown')}] {flag.get('type', 'unknown')}: {flag.get('reason', '')}")

    print("\n" + "=" * 72)


def run_cli_review(staging_dir: Path = None) -> None:
    """Interactive CLI for reviewing staged traces."""
    if staging_dir is None:
        staging_dir = STAGING_DIR

    traces = _load_staged_traces(staging_dir)

    if not traces:
        print("No staged traces found in", staging_dir)
        print("Run 'opentraces enrich' to stage traces for review.")
        return

    decisions: dict[str, str] = {}
    pending = [t for t in traces if t["trace_id"] not in decisions]

    print(f"\nopentraces CLI Review")
    print(f"{'=' * 40}")
    print(f"  {len(traces)} sessions to review\n")

    idx = 0
    while idx < len(pending):
        trace = pending[idx]
        tid = trace["trace_id"]

        _print_trace_summary(trace, idx)

        try:
            choice = input("  [a]pprove / [r]eject / [s]kip / [v]iew / [q]uit > ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\n\nReview interrupted.")
            break

        if choice == "a":
            decisions[tid] = "approved"
            print(f"  -> Approved\n")
            idx += 1
        elif choice == "r":
            decisions[tid] = "rejected"
            print(f"  -> Rejected\n")
            idx += 1
        elif choice == "s":
            print(f"  -> Skipped\n")
            idx += 1
        elif choice == "v":
            _print_trace_detail(trace)
            # Don't advance index, let user decide after viewing
        elif choice == "q":
            print("\nQuitting review.")
            break
        else:
            print("  Invalid choice. Use a/r/s/v/q.\n")

    # Summary
    approved = sum(1 for v in decisions.values() if v == "approved")
    rejected = sum(1 for v in decisions.values() if v == "rejected")
    skipped = len(traces) - len(decisions)

    print(f"\nReview Summary")
    print(f"{'=' * 40}")
    print(f"  Approved: {approved}")
    print(f"  Rejected: {rejected}")
    print(f"  Skipped:  {skipped}")
    print()

    if approved > 0:
        print(f"  {approved} session(s) approved and ready to push.")
        print(f"  Run 'opentraces push' to upload to HF Hub.")
    print()
