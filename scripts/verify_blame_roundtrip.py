#!/usr/bin/env python3
"""Local smoke test: top-N inbox traces, bi-directional blame round-trip.

For each of the ``N`` newest inbox traces:

1. Trace → commits (forward): ``opentraces blame t:<id> --json``.
2. Commit → traces (reverse): for each commit we got in step 1, run
   ``opentraces blame c:<sha> --json`` and check the original trace
   appears in the commit's sessions/traces list (attribution cache) OR
   in the notes-backed reverse map (``refs/notes/opentraces``).

The reverse check has to look at BOTH sources because the commit-mode
``blame`` only surfaces attribution-cache sessions today — while
hook/backfill-only trace→commit pairs live in ``refs/notes/opentraces``.
A healthy round-trip means EITHER source confirms the original trace.

Not a pytest — this is a diagnostic you run against a real
``~/.opentraces/projects/<slug>`` with a populated inbox. Prints a table
and exits 0 on success (>=80% round-trip), 1 otherwise.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _cli_json(args: list[str], cwd: Path) -> dict:
    """Run ``opentraces <args> --json`` and parse stdout."""
    r = subprocess.run(
        ["opentraces", *args, "--json"],
        cwd=cwd, capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return {"__error__": r.stderr.strip() or r.stdout.strip()}
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"__error__": "non-json stdout"}


def _notes_lines(cwd: Path, sha: str) -> list[str]:
    r = subprocess.run(
        ["git", "notes", "--ref=refs/notes/opentraces", "show", sha],
        cwd=cwd, capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def _top_n_inbox_traces(cwd: Path, n: int) -> list[dict]:
    """Return the ``n`` newest staged traces by timestamp_end."""
    from opentraces.core.config import get_project_traces_dir
    staging = get_project_traces_dir(cwd)
    rows = []
    for p in staging.glob("*.jsonl"):
        try:
            with p.open() as f:
                rec = json.loads(f.readline())
        except Exception:
            continue
        task = rec.get("task") or {}
        desc = (task.get("description") if isinstance(task, dict) else None) or ""
        rows.append({
            "timestamp_end": rec.get("timestamp_end") or "",
            "trace_id": rec.get("trace_id"),
            "session_id": rec.get("session_id") or "",
            "task": desc[:60],
        })
    rows.sort(key=lambda r: r["timestamp_end"], reverse=True)
    return rows[:n]


def main() -> int:
    cwd = Path.cwd()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    traces = _top_n_inbox_traces(cwd, n)
    if not traces:
        print("No inbox traces found.")
        return 1

    print(f"=== Verifying blame round-trip for the {len(traces)} newest inbox traces ===\n")

    stats = {"forward_nonempty": 0, "reverse_hits": 0, "reverse_checks": 0}
    report_rows: list[dict] = []

    for i, t in enumerate(traces, 1):
        tid = t["trace_id"]
        sid = t["session_id"]
        short = tid[:8]
        # Identifiers the reverse mapping might key on — both trace_id
        # (new) and session_id (legacy v1 attribution cache entries).
        ids = {x for x in (tid, sid) if x}

        fwd = _cli_json(["blame", f"t:{tid}"], cwd)
        if "__error__" in fwd:
            print(f"[{i:>2}] t:{short}  FORWARD ERROR: {fwd['__error__']}")
            continue

        commits = fwd.get("commits", [])
        if commits:
            stats["forward_nonempty"] += 1

        # Reverse-check the first 2 commits for each trace (keep the
        # sample small — 10 traces * 2 commits = 20 CLI calls).
        reverse_hits = 0
        reverse_checks = 0
        reverse_details = []
        for c in commits[:2]:
            reverse_checks += 1
            stats["reverse_checks"] += 1
            sha = c["sha"]

            # Attribution-cache check (commit-mode CLI). Match either the
            # canonical trace_id or the legacy session_id — some v2 caches
            # still surface the session form (the normalizer only runs on
            # v1 payloads, so v2 writes from before the attribute_commit
            # fix stay in session-id form permanently).
            back = _cli_json(["blame", f"c:{sha}"], cwd)
            attr_hit = False
            if "__error__" not in back:
                for s in back.get("sessions", []) or back.get("traces", []) or []:
                    s_tid = s.get("trace_id") or s.get("id") or ""
                    if any(s_tid.startswith(x) or x.startswith(s_tid)
                           for x in ids):
                        attr_hit = True
                        break

            # Notes-ref check (covers hook/backfill-only pairs).
            notes = _notes_lines(cwd, sha)
            notes_hit = any(any(x in ln for x in ids) for ln in notes)

            if attr_hit or notes_hit:
                reverse_hits += 1
                stats["reverse_hits"] += 1
                via = ("both" if attr_hit and notes_hit
                       else "attribution" if attr_hit else "notes")
            else:
                via = "MISS"
            reverse_details.append(f"{sha[:8]}:{via}")

        report_rows.append({
            "trace": short,
            "ts": t["timestamp_end"][:19],
            "commits": len(commits),
            "reverse": f"{reverse_hits}/{reverse_checks}",
            "sample": " ".join(reverse_details),
            "task": t["task"],
        })

    # Render.
    print(f"{'#':<3} {'trace':<10} {'ts':<20} {'commits':>8} {'reverse':<9} {'sample roundtrip':<40} task")
    print("-" * 120)
    for i, r in enumerate(report_rows, 1):
        print(f"{i:<3} {r['trace']:<10} {r['ts']:<20} {r['commits']:>8} "
              f"{r['reverse']:<9} {r['sample']:<40} {r['task']}")

    total = len(traces)
    fwd_pct = 100 * stats["forward_nonempty"] / total if total else 0
    rev_pct = (100 * stats["reverse_hits"] / stats["reverse_checks"]
               if stats["reverse_checks"] else 0)
    print()
    print(f"Forward coverage: {stats['forward_nonempty']}/{total} traces "
          f"have >=1 commit ({fwd_pct:.0f}%)")
    print(f"Reverse round-trip: {stats['reverse_hits']}/{stats['reverse_checks']} "
          f"commit-check hits ({rev_pct:.0f}%)")

    return 0 if (fwd_pct >= 50 and rev_pct >= 80) else 1


if __name__ == "__main__":
    sys.exit(main())
