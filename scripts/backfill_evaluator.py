#!/usr/bin/env python3
"""Backfill evaluator for trace_attribution_spike_v2.

Given a local git project that has a matching
~/.claude/projects/<encoded>/*.jsonl corpus, this tool:

  1. Rebuilds the audit ref from every session's JSONL (the fallback
     reconstruction + watcher sidecar).
  2. Attributes one or more commits (default: HEAD) against that audit.
  3. Reports per-commit coverage:
       - total_lines
       - lines credited to a session trace_id
       - lines credited to pre-audit (no agent contributed)
       - files marked missing_from_audit
  4. Runs self-consistency checks per session-touched file:
       - session's JSONL must mention that file path at least once
       - session's start timestamp must precede the commit's timestamp
  5. Emits a JSON report suitable for piping or downstream analysis.

Usage:
  python3 scripts/backfill_evaluator.py <project_path> [--commits N]
                                         [--json] [--no-build]

  --commits N   walk the last N commits (default: 1 = HEAD only)
  --json        emit JSON-only (no human-readable report)
  --no-build    skip audit rebuild (trust what's already there)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SPIKE = Path(__file__).parent / "trace_attribution_spike_v2.py"


def _run(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(map(str, cmd))}\n"
                           f"stderr: {r.stderr}\nstdout: {r.stdout}")
    return r


def _encode_cwd(path: Path) -> str:
    s = str(path.resolve())
    return "".join(c if c.isalnum() or c == "-" else "-" for c in s)


def _sessions_metadata(project_cwd: Path) -> dict[str, dict]:
    """Map trace_id (session_id) -> {start_ts, end_ts, files_touched}."""
    proj_dir = Path.home() / ".claude" / "projects" / _encode_cwd(project_cwd)
    if not proj_dir.is_dir():
        return {}
    out: dict[str, dict] = {}
    for jsonl in sorted(proj_dir.glob("*.jsonl")):
        trace_id = jsonl.stem
        start_ts = ""
        end_ts = ""
        files: set[str] = set()
        try:
            text = jsonl.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = e.get("timestamp")
            if ts:
                if not start_ts:
                    start_ts = ts
                end_ts = ts
            if e.get("type") != "assistant":
                continue
            for c in (e.get("message", {}).get("content") or []):
                if not (isinstance(c, dict) and c.get("type") == "tool_use"):
                    continue
                name = c.get("name")
                inp = c.get("input") or {}
                if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
                    fp = inp.get("file_path")
                    if fp:
                        files.add(str(fp))
                elif name == "Bash":
                    # Rough heuristic: extract file-ish tokens from the
                    # command. We only use this for the "did this session
                    # plausibly touch this file" self-consistency check,
                    # so over-inclusion is safer than under-inclusion.
                    cmd = inp.get("command", "") or ""
                    for tok in cmd.replace(">", " ").replace("<", " ").split():
                        if "/" in tok or "." in tok:
                            files.add(tok.strip("'\""))
        out[trace_id] = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "files": sorted(files),
        }
    return out


def _project_relative_candidates(path: str, project_cwd: Path) -> set[str]:
    """Given a path from a session's `files` field (which may be abs or
    contain a bare file name), return plausible project-relative rels."""
    out = set()
    p = Path(path)
    try:
        p_res = p.resolve() if p.is_absolute() else (project_cwd / p).resolve()
        rel = p_res.relative_to(project_cwd.resolve())
        out.add(str(rel))
    except (ValueError, OSError):
        pass
    # Also accept bare basename match (loose)
    out.add(Path(path).name)
    out.add(str(path))
    return {x for x in out if x}


def _commit_ts_iso(project_cwd: Path, sha: str) -> str:
    r = _run(["git", "-C", str(project_cwd), "log", "-1", "--format=%aI", sha])
    return r.stdout.strip()


def evaluate(project_cwd: Path, commit_shas: list[str],
             rebuild: bool = True, verbose: bool = False) -> dict:
    if not (project_cwd / ".git").exists():
        raise SystemExit(f"not a git repo: {project_cwd}")
    if rebuild:
        if verbose:
            print("• rebuilding audit history from JSONLs…", file=sys.stderr)
        r = _run([sys.executable, str(SPIKE), "build", str(project_cwd)])
        if verbose:
            for line in r.stdout.splitlines()[:5]:
                print(f"    {line}", file=sys.stderr)

    sessions = _sessions_metadata(project_cwd)
    if verbose:
        print(f"• sessions in corpus: {len(sessions)}", file=sys.stderr)

    per_commit = []
    global_lines_total = 0
    global_lines_session = 0
    global_lines_pre_audit = 0
    global_files_missing = 0
    consistency_flags: list[dict] = []
    trace_ids_seen: set[str] = set()

    for sha in commit_shas:
        if verbose:
            print(f"• attributing {sha[:10]}…", file=sys.stderr)
        r = _run([sys.executable, str(SPIKE), "attribute",
                  str(project_cwd), sha, "--json"], check=False)
        if r.returncode != 0:
            per_commit.append({"commit": sha, "error": r.stderr.strip()[:400]})
            continue
        try:
            attr = json.loads(r.stdout)
        except json.JSONDecodeError:
            per_commit.append({"commit": sha, "error": "non-json output"})
            continue
        commit_ts = _commit_ts_iso(project_cwd, sha)

        total_lines = 0
        session_lines = 0              # credit honored (temporally consistent)
        pre_audit_lines = 0
        misattributed_lines = 0        # would-be session credit rejected as impossible
        missing_files = 0
        per_trace: dict[str, int] = defaultdict(int)
        for rel, f in attr.get("files", {}).items():
            status = f.get("status")
            if status == "missing_from_audit":
                missing_files += 1
                continue
            if status != "attributed":
                continue
            tl = f.get("total_lines", 0)
            total_lines += tl
            for tid, entry in (f.get("by_trace") or {}).items():
                cnt = entry.get("count", 0)
                if tid.startswith("pre-audit:"):
                    pre_audit_lines += cnt
                    continue

                # Temporal sanity: a session can't author a commit that
                # predates it. Attribution to a future session is an
                # artifact of blaming against audit-tip instead of
                # audit-at-commit-time — reclassify as pre-audit.
                s_meta = sessions.get(tid)
                if s_meta is None:
                    consistency_flags.append({
                        "commit": sha, "file": rel, "trace": tid,
                        "issue": "trace has no matching JSONL in corpus",
                        "lines_reclassified": cnt,
                    })
                    misattributed_lines += cnt
                    continue
                s_start = s_meta["start_ts"]
                if s_start and commit_ts and s_start > commit_ts:
                    consistency_flags.append({
                        "commit": sha, "file": rel, "trace": tid[:12],
                        "issue": "session started AFTER commit timestamp",
                        "session_start": s_start, "commit_ts": commit_ts,
                        "lines_reclassified": cnt,
                    })
                    misattributed_lines += cnt
                    continue

                # file-path plausibility: session must plausibly mention
                # this path. Loose check — Bash tool uses may not mention
                # exact rels. Flag but don't deduct.
                candidates = _project_relative_candidates(rel, project_cwd)
                touched = set(s_meta["files"])
                touched_rels: set[str] = set()
                for t in touched:
                    touched_rels |= _project_relative_candidates(t, project_cwd)
                if candidates.isdisjoint(touched_rels):
                    consistency_flags.append({
                        "commit": sha, "file": rel, "trace": tid[:12],
                        "issue": "file not mentioned in session's tool_uses",
                        "lines_flagged": cnt,
                    })

                session_lines += cnt
                per_trace[tid] += cnt
                trace_ids_seen.add(tid)

        # Roll misattributed into pre-audit for the honest totals
        effective_pre_audit = pre_audit_lines + misattributed_lines
        global_lines_total += total_lines
        global_lines_session += session_lines
        global_lines_pre_audit += effective_pre_audit
        global_files_missing += missing_files

        per_commit.append({
            "commit": sha,
            "timestamp": commit_ts,
            "subject": attr.get("subject", ""),
            "total_lines": total_lines,
            "session_lines": session_lines,
            "pre_audit_lines": effective_pre_audit,
            "misattributed_lines": misattributed_lines,
            "missing_files": missing_files,
            "per_trace": dict(per_trace),
            "coverage_pct": round(100 * session_lines / total_lines, 1)
                            if total_lines else None,
        })

    return {
        "project": str(project_cwd),
        "sessions_in_corpus": len(sessions),
        "traces_credited": len(trace_ids_seen),
        "global": {
            "total_lines": global_lines_total,
            "session_lines": global_lines_session,
            "pre_audit_lines": global_lines_pre_audit,
            "missing_files": global_files_missing,
            "coverage_pct": round(100 * global_lines_session / global_lines_total, 1)
                             if global_lines_total else None,
        },
        "per_commit": per_commit,
        "consistency_flags": consistency_flags,
    }


def _walk_commits(project_cwd: Path, n: int) -> list[str]:
    r = _run(["git", "-C", str(project_cwd), "log",
              f"-{n}", "--format=%H"])
    return [s for s in r.stdout.strip().splitlines() if s]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("project")
    ap.add_argument("--commits", type=int, default=1,
                    help="walk the last N commits (default: 1, HEAD only)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-build", dest="rebuild", action="store_false")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    project_cwd = Path(args.project).expanduser().resolve()
    shas = _walk_commits(project_cwd, args.commits)
    result = evaluate(project_cwd, shas,
                      rebuild=args.rebuild, verbose=args.verbose)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    # Human digest
    g = result["global"]
    print()
    print("═" * 72)
    print(f"Backfill evaluation — {result['project']}")
    print("═" * 72)
    print(f"  sessions in corpus:        {result['sessions_in_corpus']:>6}")
    print(f"  traces credited by blame:  {result['traces_credited']:>6}")
    print(f"  commits attributed:        {len(result['per_commit']):>6}")
    print()
    print("Aggregate line coverage across attributed commits")
    print("  " + "─" * 56)
    print(f"  total attributed lines:          {g['total_lines']:>7}")
    print(f"  credited to a session:           {g['session_lines']:>7}  "
          f"({g['coverage_pct']}%)" if g['coverage_pct'] is not None else "")
    print(f"  credited to pre-audit:           {g['pre_audit_lines']:>7}")
    print(f"  files missing_from_audit:        {g['missing_files']:>7}")
    print()
    print("Per-commit (most recent first)")
    print("  " + "─" * 64)
    for pc in result["per_commit"]:
        if "error" in pc:
            print(f"  {pc['commit'][:10]}  ERROR: {pc['error'][:60]}")
            continue
        cov = f"{pc['coverage_pct']}%" if pc['coverage_pct'] is not None else "—"
        subj = (pc["subject"] or "")[:40]
        print(f"  {pc['commit'][:10]}  lines={pc['total_lines']:>4}  "
              f"session={pc['session_lines']:>4}  pre={pc['pre_audit_lines']:>4}  "
              f"miss={pc['missing_files']}  {cov:>6}  {subj}")
    print()
    print(f"Consistency flags: {len(result['consistency_flags'])}")
    print("  " + "─" * 56)
    from collections import Counter
    issue_counts = Counter(f["issue"] for f in result["consistency_flags"])
    for issue, n in issue_counts.most_common():
        print(f"  {n:>5}  {issue}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
