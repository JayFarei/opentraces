#!/usr/bin/env python3
"""Mine ~/.claude/projects/ for session-shape statistics.

Walks every JSONL, classifies sessions by tool-usage patterns, end-event
shapes, length distribution, and Bash command patterns. Produces a report
that drives scenario prioritization for the test harness.

Run: python3 scripts/mine_claude_projects.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"


def main() -> int:
    if not PROJECTS.is_dir():
        print(f"no such dir: {PROJECTS}", file=sys.stderr)
        return 1

    n_projects = 0
    n_sessions = 0
    n_skipped = 0

    # Distributions
    last_event_types = Counter()
    tool_use_total = Counter()                  # tool_name → count
    sessions_using_tool = Counter()             # tool_name → distinct sessions
    bash_command_first_word = Counter()         # 'rm', 'sed', 'mkdir', etc.
    bash_redirection_kind = Counter()           # '>', '>>', heredoc, none
    file_extensions_touched = Counter()
    snapshot_count_buckets = Counter()          # '0', '1', '2-5', '6-20', '21-100', '100+'
    sessions_with_subagent_worktree = 0
    sessions_with_subagent_no_isolation = 0
    bash_creates_files_via_redirect = 0         # session count
    sessions_per_project = Counter()
    edit_per_session_buckets = Counter()
    write_per_session_buckets = Counter()
    bash_per_session_buckets = Counter()
    sessions_with_no_file_history = 0           # zero file-history-snapshot entries
    end_without_assistant_finish = 0            # last entry isn't 'assistant'
    sessions_using_compaction = 0
    sessions_with_post_turn_summary = 0

    for proj_dir in sorted(PROJECTS.iterdir()):
        if not proj_dir.is_dir():
            continue
        n_projects += 1
        local_sessions = 0
        for jsonl in proj_dir.glob("*.jsonl"):
            local_sessions += 1
            n_sessions += 1
            try:
                _analyze(
                    jsonl,
                    last_event_types=last_event_types,
                    tool_use_total=tool_use_total,
                    sessions_using_tool=sessions_using_tool,
                    bash_command_first_word=bash_command_first_word,
                    bash_redirection_kind=bash_redirection_kind,
                    file_extensions_touched=file_extensions_touched,
                    snapshot_count_buckets=snapshot_count_buckets,
                    edit_per_session_buckets=edit_per_session_buckets,
                    write_per_session_buckets=write_per_session_buckets,
                    bash_per_session_buckets=bash_per_session_buckets,
                    counters_inc={
                        # Will be added per-session by _analyze via mutation hook
                    },
                )
            except Exception:
                n_skipped += 1
        if local_sessions:
            sessions_per_project[proj_dir.name] = local_sessions

    # Re-pass for counters that need session-level scoping (cleaner than passing 50 args)
    for proj_dir in sorted(PROJECTS.iterdir()):
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            try:
                flags = _session_flags(jsonl)
            except Exception:
                continue
            if flags.get("subagent_worktree"):
                sessions_with_subagent_worktree += 1
            if flags.get("subagent_no_isolation"):
                sessions_with_subagent_no_isolation += 1
            if flags.get("bash_creates_via_redirect"):
                bash_creates_files_via_redirect += 1
            if flags.get("no_file_history"):
                sessions_with_no_file_history += 1
            if flags.get("end_not_assistant"):
                end_without_assistant_finish += 1
            if flags.get("compaction"):
                sessions_using_compaction += 1
            if flags.get("post_turn_summary"):
                sessions_with_post_turn_summary += 1

    # ----------------- report ----------------- #
    print()
    print("─" * 72)
    print("Claude Code session-shape analysis")
    print("─" * 72)
    print(f"  projects scanned:         {n_projects:>7}")
    print(f"  sessions analyzed:        {n_sessions:>7}")
    print(f"  sessions skipped (error): {n_skipped:>7}")
    print()

    print("End-of-JSONL event types (how sessions terminate)")
    print("  " + "─" * 50)
    for evt, count in last_event_types.most_common(8):
        pct = 100 * count / max(n_sessions, 1)
        print(f"  {evt:<28} {count:>6}  ({pct:5.1f}%)")
    print(f"  {'sessions ending NOT in assistant':<40} {end_without_assistant_finish:>6}  "
          f"({100*end_without_assistant_finish/max(n_sessions,1):5.1f}%)")
    print()

    print("Tool usage — total invocations across all sessions")
    print("  " + "─" * 50)
    for tool, count in tool_use_total.most_common(15):
        sess = sessions_using_tool[tool]
        print(f"  {tool:<22} {count:>6}  uses  in {sess:>5} sessions")
    print()

    print("Snapshot-count distribution per session (file-history-snapshot entries)")
    print("  " + "─" * 50)
    for bucket in ["0", "1", "2-5", "6-20", "21-100", "100+"]:
        c = snapshot_count_buckets.get(bucket, 0)
        bar = "█" * int(50 * c / max(max(snapshot_count_buckets.values(), default=1), 1))
        print(f"  {bucket:>8}  {c:>6}  {bar}")
    print(f"  {'sessions with 0 file-history':<28} {sessions_with_no_file_history}")
    print()

    print("Edit / Write / Bash counts per session")
    print("  " + "─" * 50)
    for label, dist in [
        ("Edit", edit_per_session_buckets),
        ("Write", write_per_session_buckets),
        ("Bash", bash_per_session_buckets),
    ]:
        for bucket in ["0", "1", "2-5", "6-20", "21-100", "100+"]:
            c = dist.get(bucket, 0)
            print(f"  {label} {bucket:>8}  {c:>6}")
        print()

    print("Bash command first words (top 25)")
    print("  " + "─" * 50)
    for cmd, count in bash_command_first_word.most_common(25):
        print(f"  {cmd:<22} {count:>6}")
    print()

    print("Bash redirection use (creates/appends to files via shell)")
    print("  " + "─" * 50)
    for kind, count in bash_redirection_kind.most_common():
        print(f"  {kind:<22} {count:>6}")
    print(f"  sessions w/ ≥1 file-creating redirect:  {bash_creates_files_via_redirect}")
    print()

    print("File extensions touched (top 20)")
    print("  " + "─" * 50)
    for ext, count in file_extensions_touched.most_common(20):
        print(f"  {ext:<22} {count:>6}")
    print()

    print("Other patterns")
    print("  " + "─" * 50)
    print(f"  sessions w/ Agent isolation=worktree (sub-agents w/ worktree)  {sessions_with_subagent_worktree}")
    print(f"  sessions w/ Agent isolation!=worktree (sub-agents inline)      {sessions_with_subagent_no_isolation}")
    print(f"  sessions w/ compact_boundary (context compaction kicked in)    {sessions_using_compaction}")
    print(f"  sessions w/ post_turn_summary entries                          {sessions_with_post_turn_summary}")
    print()

    print("Top 10 most-active project dirs (session counts)")
    print("  " + "─" * 50)
    for proj, count in sessions_per_project.most_common(10):
        print(f"  {proj[:65]:<65} {count:>5}")
    print()

    return 0


# --------------------------------------------------------------------------- #
# Per-session analysis
# --------------------------------------------------------------------------- #

def _bucket(n: int) -> str:
    if n == 0: return "0"
    if n == 1: return "1"
    if n <= 5: return "2-5"
    if n <= 20: return "6-20"
    if n <= 100: return "21-100"
    return "100+"


def _analyze(jsonl: Path,
             last_event_types: Counter,
             tool_use_total: Counter,
             sessions_using_tool: Counter,
             bash_command_first_word: Counter,
             bash_redirection_kind: Counter,
             file_extensions_touched: Counter,
             snapshot_count_buckets: Counter,
             edit_per_session_buckets: Counter,
             write_per_session_buckets: Counter,
             bash_per_session_buckets: Counter,
             counters_inc: dict) -> None:
    n_snap = 0
    n_edit = 0
    n_write = 0
    n_bash = 0
    last_type = None
    seen_tools = set()
    text = jsonl.read_text(errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = o.get("type")
        if t:
            last_type = t
        if t == "file-history-snapshot":
            n_snap += 1
            tfb = (o.get("snapshot", {}) or {}).get("trackedFileBackups", {}) or {}
            for path in tfb:
                ext = Path(path).suffix.lower() or "<no-ext>"
                file_extensions_touched[ext] += 1
        elif t == "assistant":
            msg = o.get("message", {}) or {}
            content = msg.get("content", []) or []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "?")
                tool_use_total[name] += 1
                seen_tools.add(name)
                if name == "Edit":
                    n_edit += 1
                elif name == "Write":
                    n_write += 1
                elif name == "Bash":
                    n_bash += 1
                    cmd = (block.get("input", {}) or {}).get("command", "")
                    if cmd:
                        first = re.match(r"^\s*([A-Za-z_][\w./-]*)", cmd)
                        if first:
                            bash_command_first_word[first.group(1)] += 1
                        if " > " in cmd or cmd.lstrip().startswith(">"):
                            bash_redirection_kind["> (overwrite)"] += 1
                        if " >> " in cmd:
                            bash_redirection_kind[">> (append)"] += 1
                        if "<<EOF" in cmd or "<<-EOF" in cmd or "<< EOF" in cmd:
                            bash_redirection_kind["heredoc"] += 1

    snapshot_count_buckets[_bucket(n_snap)] += 1
    edit_per_session_buckets[_bucket(n_edit)] += 1
    write_per_session_buckets[_bucket(n_write)] += 1
    bash_per_session_buckets[_bucket(n_bash)] += 1
    if last_type:
        last_event_types[last_type] += 1
    for tool in seen_tools:
        sessions_using_tool[tool] += 1


def _session_flags(jsonl: Path) -> dict:
    """Second-pass flags that need session-level scope."""
    flags = {
        "subagent_worktree": False,
        "subagent_no_isolation": False,
        "bash_creates_via_redirect": False,
        "no_file_history": True,
        "end_not_assistant": False,
        "compaction": False,
        "post_turn_summary": False,
    }
    last_type = None
    text = jsonl.read_text(errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = o.get("type")
        if t:
            last_type = t
        if t == "file-history-snapshot":
            flags["no_file_history"] = False
        elif t == "system":
            sub = o.get("subtype")
            if sub == "compact_boundary":
                flags["compaction"] = True
            elif sub == "post_turn_summary":
                flags["post_turn_summary"] = True
        elif t == "assistant":
            for block in (o.get("message", {}) or {}).get("content", []) or []:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                inp = block.get("input", {}) or {}
                if name == "Agent":
                    if inp.get("isolation") == "worktree":
                        flags["subagent_worktree"] = True
                    elif inp.get("subagent_type"):
                        flags["subagent_no_isolation"] = True
                if name == "Bash":
                    cmd = inp.get("command", "")
                    if " > " in cmd or " >> " in cmd or "<<" in cmd:
                        flags["bash_creates_via_redirect"] = True
    if last_type and last_type != "assistant":
        flags["end_not_assistant"] = True
    return flags


if __name__ == "__main__":
    sys.exit(main())
