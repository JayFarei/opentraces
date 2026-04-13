#!/usr/bin/env python3
"""Trace attribution spike v2 — blame over synthetic audit history.

Abandons patch-id as primary attribution. Instead:
  1. Materializes each prompt-boundary snapshot as a git commit under
     `refs/opentraces/audit/<project_id>`, linearised across all traces.
  2. Author identity on each audit commit encodes the trace id.
  3. Attribution of a real commit = `git blame` against that audit history.
     Per-line "who last modified this line?" → the trace that owns the line.

Bash-created files that file-history misses are captured by the watcher at
each snapshot boundary via a `git status --porcelain` sweep of the working
tree. Static `build` works from file-history only (reproducible, partial).

Commands:
  build      one-shot rebuild of audit history for a project (file-history only)
  watch      long-running; polls JSONLs, live-updates audit w/ working-tree
  attribute  blame a commit against the audit history; emit JSON

Not production. Writes refs under refs/opentraces/audit/* (isolated namespace).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


AUDIT_REF_PREFIX = "refs/opentraces/audit"
OPENTRACES_EMAIL_DOMAIN = "opentraces.local"
DIFF_ALGORITHM = "histogram"  # deterministic, pinned per senior-eng recommendation


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Snapshot:
    """One observation-point snapshot. Two sources:
      - "file-history": Claude Code's own per-prompt-boundary store. Backups
        reference per-trace blob files at ~/.claude/file-history/<sid>/.
      - "working-tree": watcher-captured fs state at a poll moment. Files are
        already hash-object'd into the project repo; `prehashed` carries the
        sha map directly.
    """
    trace_id: str          # = claude session_id, or "watcher" if no active trace
    message_id: str
    timestamp: str         # ISO 8601; used for linearization
    source: str = "file-history"
    backups: dict = field(default_factory=dict)         # for source=file-history
    prehashed: dict = field(default_factory=dict)       # for source=working-tree
    jsonl_path: Path | None = None


# --------------------------------------------------------------------------- #
# Git helpers
# --------------------------------------------------------------------------- #

def git(*args: str, cwd: Path | None = None, input: str | None = None,
        env: dict | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, input=input,
        capture_output=True, text=True, env=env,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n  stderr: {result.stderr}")
    return result.stdout


def git_ok(*args: str, cwd: Path | None = None) -> bool:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True,
    ).returncode == 0


def project_audit_ref(project_cwd: Path) -> str:
    pid = hashlib.sha1(str(project_cwd.resolve()).encode()).hexdigest()[:12]
    return f"{AUDIT_REF_PREFIX}/{pid}"


# --------------------------------------------------------------------------- #
# JSONL + blob loading
# --------------------------------------------------------------------------- #

def _encode_cwd(path: Path) -> str:
    """Claude Code's project-dir encoding: every non-alphanumeric, non-hyphen
    character becomes `-`. So `/private/tmp/ot-h-small_tweak` becomes
    `-private-tmp-ot-h-small-tweak` (underscore → dash; slash → dash; dot
    → dash; hyphens preserved).
    """
    s = str(path.resolve())
    return "".join(c if c.isalnum() or c == "-" else "-" for c in s)


def load_snapshots_for_project(project_cwd: Path) -> list[Snapshot]:
    """Combine file-history snapshots (from ~/.claude) and watcher-captured
    working-tree snapshots (from sidecar in <project>/.git/)."""
    snapshots: list[Snapshot] = []
    proj_dir = Path.home() / ".claude" / "projects" / _encode_cwd(project_cwd)
    if proj_dir.is_dir():
        for jsonl_path in sorted(proj_dir.glob("*.jsonl")):
            trace_id = jsonl_path.stem
            snapshots.extend(_iter_snapshots(jsonl_path, trace_id))
    snapshots.extend(_load_working_tree_events(project_cwd))
    return snapshots


def _load_working_tree_events(project_cwd: Path) -> list[Snapshot]:
    f = _watcher_state_file(project_cwd)
    if not f.exists():
        return []
    out = []
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(Snapshot(
            trace_id=evt.get("trace_id", "watcher"),
            message_id=evt.get("message_id", f"wt-{evt.get('timestamp','')[:19]}"),
            timestamp=evt.get("timestamp", ""),
            source="working-tree",
            prehashed=evt.get("files", {}) or {},
        ))
    return out


def _iter_snapshots(jsonl: Path, trace_id: str):
    for line in jsonl.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "file-history-snapshot":
            continue
        snap_field = entry.get("snapshot") or {}
        backups = snap_field.get("trackedFileBackups") or {}
        if not backups:
            continue
        timestamps = [b.get("backupTime") for b in backups.values() if b.get("backupTime")]
        if not timestamps:
            continue
        yield Snapshot(
            trace_id=trace_id,
            jsonl_path=jsonl,
            message_id=entry.get("messageId", ""),
            timestamp=max(timestamps),  # latest backup time in the snapshot
            backups=backups,
        )


def blob_path(trace_id: str, backup_name: str) -> Path:
    return Path.home() / ".claude" / "file-history" / trace_id / backup_name


# --------------------------------------------------------------------------- #
# Audit history construction
# --------------------------------------------------------------------------- #

def _parent_commit_at(project_cwd: Path, iso_timestamp: str) -> str | None:
    """Find the real commit that was HEAD at or before the given timestamp."""
    if not git_ok("-C", str(project_cwd), "rev-parse", "HEAD"):
        return None
    out = git("-C", str(project_cwd), "rev-list", "-1",
              f"--before={iso_timestamp}", "HEAD", check=False).strip()
    return out or None


def _parse_iso_to_utc(ts: str) -> datetime | None:
    """Parse ISO 8601 timestamp (with Z or numeric offset) to a UTC-aware
    datetime. Returns None if unparseable. Used so we can compare timestamps
    across different timezones consistently.
    """
    if not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _root_commit_info(project_cwd: Path) -> tuple[str | None, str | None]:
    """Return (root_commit_sha, root_commit_iso_date) for the project's
    earliest commit, or (None, None) if the repo has no commits.
    Used to filter out pollution from sessions that predate the current
    project history.
    """
    if not git_ok("-C", str(project_cwd), "rev-parse", "HEAD"):
        return (None, None)
    sha = git("-C", str(project_cwd), "rev-list", "--max-parents=0", "HEAD",
              check=False).strip().splitlines()
    if not sha:
        return (None, None)
    root = sha[0]
    date = git("-C", str(project_cwd), "log", "-1", "--format=%aI", root,
               check=False).strip()
    return (root, date or None)


def _relative_path(path_str: str, project_cwd: Path) -> str | None:
    """Paths in trackedFileBackups may be absolute (`/abs/...`) or relative
    to the project root (`README.md`, `subdir/file.md`). Normalize to a
    project-relative string or None if outside the project.
    """
    p = Path(path_str)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(project_cwd.resolve()))
        except ValueError:
            return None
    # relative path — already project-relative
    rel = str(p).lstrip("./")
    return rel if rel else None


def _hash_blob_into_repo(project_cwd: Path, source_file: Path) -> str:
    return git("-C", str(project_cwd), "hash-object", "-w",
               str(source_file)).strip()


def _working_tree_extras(project_cwd: Path,
                         already_in_snapshot: set[str]) -> dict[str, str]:
    """Return {rel_path: blob_sha} for files in the working tree that the
    snapshot did not capture (e.g., Bash-created, user-touched-outside-agent).
    Uses `git status --porcelain -uall` to enumerate untracked + modified.
    """
    extras: dict[str, str] = {}
    out = git("-C", str(project_cwd), "status", "--porcelain", "-uall",
              check=False)
    for line in out.splitlines():
        if len(line) < 4:
            continue
        xy, rel = line[:2], line[3:]
        # Strip trailing -> target in renames
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        # Ignore deletions (handled by snapshot's null markers where possible)
        if xy.strip() == "D":
            continue
        abs_path = (project_cwd / rel).resolve()
        try:
            abs_path.relative_to(project_cwd.resolve())
        except ValueError:
            continue
        if rel in already_in_snapshot or not abs_path.is_file():
            continue
        try:
            extras[rel] = _hash_blob_into_repo(project_cwd, abs_path)
        except RuntimeError:
            continue
    return extras


def _apply_snapshot_to_index(project_cwd: Path, snap: Snapshot,
                             env: dict, verbose: bool) -> list[str]:
    """Overlay the snapshot's files into the git index. Returns the list of
    relative paths touched. Handles both source types.
    """
    touched: list[str] = []
    if snap.source == "working-tree":
        # Files already hash-object'd by the watcher; just install in index
        for rel, sha in (snap.prehashed or {}).items():
            subprocess.run(
                ["git", "-C", str(project_cwd), "update-index",
                 "--add", "--cacheinfo", f"100644,{sha},{rel}"],
                env=env, check=True,
            )
            touched.append(rel)
        return touched

    # source == "file-history": resolve backup blobs
    for abs_path, meta in snap.backups.items():
        rel = _relative_path(abs_path, project_cwd)
        if rel is None:
            continue
        backup_name = meta.get("backupFileName")
        if backup_name is None:
            subprocess.run(
                ["git", "-C", str(project_cwd), "update-index",
                 "--remove", rel],
                env=env, capture_output=True, check=False,
            )
            touched.append(rel)
            continue
        blob = blob_path(snap.trace_id, backup_name)
        if not blob.exists():
            if verbose:
                print(f"  ⚠ missing blob {blob}", file=sys.stderr)
            continue
        try:
            sha = _hash_blob_into_repo(project_cwd, blob)
        except RuntimeError:
            continue
        subprocess.run(
            ["git", "-C", str(project_cwd), "update-index",
             "--add", "--cacheinfo", f"100644,{sha},{rel}"],
            env=env, check=True,
        )
        touched.append(rel)
    return touched


def _apply_extras_to_index(project_cwd: Path, extras: dict[str, str],
                           env: dict) -> None:
    for rel, sha in extras.items():
        subprocess.run(
            ["git", "-C", str(project_cwd), "update-index",
             "--add", "--cacheinfo", f"100644,{sha},{rel}"],
            env=env, check=True,
        )


def _write_audit_commit(project_cwd: Path, env: dict, snap: Snapshot,
                        parent: str | None, n_tracked: int,
                        n_extras: int) -> str:
    tree = git("-C", str(project_cwd), "write-tree", env=env).strip()
    msg = (
        f"audit: trace={snap.trace_id[:8]} msg={snap.message_id[:8]}\n\n"
        f"Trace-Id: {snap.trace_id}\n"
        f"Message-Id: {snap.message_id}\n"
        f"Snapshot-Timestamp: {snap.timestamp}\n"
        f"Tracked-Files: {n_tracked}\n"
        f"Working-Tree-Extras: {n_extras}\n"
    )
    commit_env = {**env,
        "GIT_AUTHOR_NAME": f"trace:{snap.trace_id[:8]}",
        "GIT_AUTHOR_EMAIL": f"{snap.trace_id}@{OPENTRACES_EMAIL_DOMAIN}",
        "GIT_AUTHOR_DATE": snap.timestamp,
        "GIT_COMMITTER_NAME": "opentraces-audit",
        "GIT_COMMITTER_EMAIL": f"audit@{OPENTRACES_EMAIL_DOMAIN}",
        "GIT_COMMITTER_DATE": snap.timestamp,
    }
    args = ["git", "-C", str(project_cwd), "commit-tree", tree, "-m", msg]
    if parent:
        args.extend(["-p", parent])
    return subprocess.run(
        args, env=commit_env, capture_output=True, text=True, check=True,
    ).stdout.strip()


def build_audit_history(project_cwd: Path, capture_working_tree: bool = False,
                        verbose: bool = False) -> str | None:
    """Build/rebuild the audit ref for a project. Returns the ref name."""
    snapshots = load_snapshots_for_project(project_cwd)
    if not snapshots:
        print(f"No trace snapshots for {project_cwd}", file=sys.stderr)
        return None

    snapshots.sort(key=lambda s: s.timestamp)

    # Filter out snapshots that predate the project's root commit. Those are
    # pollution from a previous incarnation of this path (rm -rf + git init
    # without clearing ~/.claude/projects/* or the watcher sidecar).
    root_sha, root_date = _root_commit_info(project_cwd)
    root_dt = _parse_iso_to_utc(root_date) if root_date else None
    if root_dt:
        before = len(snapshots)
        snapshots = [s for s in snapshots
                     if (sd := _parse_iso_to_utc(s.timestamp)) is not None
                     and sd >= root_dt]
        dropped = before - len(snapshots)
        if verbose and dropped:
            print(f"• dropped {dropped} snapshot(s) older than project root "
                  f"({root_date})", file=sys.stderr)
    if not snapshots:
        print(f"No trace snapshots after project root for {project_cwd}",
              file=sys.stderr)
        return None

    traces = {s.trace_id for s in snapshots}
    if verbose:
        print(f"• {len(snapshots)} snapshot(s) across {len(traces)} trace(s)",
              file=sys.stderr)

    ref = project_audit_ref(project_cwd)
    parent = _parent_commit_at(project_cwd, snapshots[0].timestamp) or root_sha
    if verbose:
        print(f"• audit root parent: {parent or '(rootless)'}", file=sys.stderr)

    # Temp index so we don't pollute the real one
    idx_fd, idx_path = tempfile.mkstemp(prefix="ot-audit-index-")
    os.close(idx_fd)
    os.unlink(idx_path)  # git needs to create it fresh
    env = {**os.environ, "GIT_INDEX_FILE": idx_path}

    try:
        if parent:
            git("-C", str(project_cwd), "read-tree", parent, env=env)

        prev = parent
        for i, snap in enumerate(snapshots):
            touched = _apply_snapshot_to_index(project_cwd, snap, env, verbose)
            extras: dict[str, str] = {}
            if capture_working_tree and i == len(snapshots) - 1:
                # Only on the final snapshot, capture working-tree state to
                # pick up Bash-created files. Not per-snapshot in static build.
                extras = _working_tree_extras(project_cwd, set(touched))
                _apply_extras_to_index(project_cwd, extras, env)
            prev = _write_audit_commit(project_cwd, env, snap, prev,
                                       len(touched), len(extras))
            if verbose:
                print(f"  {snap.timestamp}  trace={snap.trace_id[:8]}  "
                      f"msg={snap.message_id[:8]}  "
                      f"files={len(touched)}  extras={len(extras)}  "
                      f"→ {prev[:10]}", file=sys.stderr)

        git("-C", str(project_cwd), "update-ref", ref, prev)
        return ref
    finally:
        try: os.unlink(idx_path)
        except FileNotFoundError: pass


# --------------------------------------------------------------------------- #
# Watcher (dual signal: file-history JSONLs + working-tree polling)
# --------------------------------------------------------------------------- #

def _watcher_state_file(project_cwd: Path) -> Path:
    """Sidecar inside .git so it's not part of the working tree.
    JSONL of {trace_id, timestamp, files: {rel: sha}, message_id, source}.
    """
    return project_cwd / ".git" / "opentraces-watcher-events.jsonl"


def _append_watcher_event(project_cwd: Path, event: dict) -> None:
    f = _watcher_state_file(project_cwd)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a") as fp:
        fp.write(json.dumps(event) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _active_trace_id(project_cwd: Path) -> str | None:
    """Best-effort: trace whose JSONL was most recently written.
    Falls back to None if no JSONLs found."""
    proj_dir = Path.home() / ".claude" / "projects" / _encode_cwd(project_cwd)
    if not proj_dir.is_dir():
        return None
    jsonls = list(proj_dir.glob("*.jsonl"))
    if not jsonls:
        return None
    latest = max(jsonls, key=lambda p: p.stat().st_mtime)
    return latest.stem


def _capture_wt_changes(project_cwd: Path,
                        last_seen_shas: dict[str, str]) -> dict[str, str]:
    """Hash-object every untracked/modified file in the working tree.
    Skip files whose content sha hasn't changed since last call.
    Returns {rel_path: blob_sha} for files whose content is newly observed.
    """
    out = git("-C", str(project_cwd), "status", "--porcelain", "-uall",
              check=False)
    changed: dict[str, str] = {}
    for line in out.splitlines():
        if len(line) < 4:
            continue
        xy, rel = line[:2], line[3:]
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        if xy.strip() == "D":
            continue  # deletions handled separately later
        # ignore our own sidecar (in case .git not skipped — it should be)
        if rel.startswith(".git/") or rel.startswith(".git\\"):
            continue
        abs_path = (project_cwd / rel).resolve()
        try:
            abs_path.relative_to(project_cwd.resolve())
        except ValueError:
            continue
        if not abs_path.is_file():
            continue
        try:
            sha = _hash_blob_into_repo(project_cwd, abs_path)
        except RuntimeError:
            continue
        if last_seen_shas.get(rel) == sha:
            continue
        changed[rel] = sha
        last_seen_shas[rel] = sha
    return changed


def _file_history_signature(project_cwd: Path) -> tuple[int, str]:
    """Identity over file-history snapshots only (excludes watcher events).
    Used to detect new snapshot arrivals."""
    proj_dir = Path.home() / ".claude" / "projects" / _encode_cwd(project_cwd)
    if not proj_dir.is_dir():
        return (0, "")
    n, mx = 0, ""
    for jsonl_path in proj_dir.glob("*.jsonl"):
        for snap in _iter_snapshots(jsonl_path, jsonl_path.stem):
            n += 1
            if snap.timestamp > mx:
                mx = snap.timestamp
    return (n, mx)


def watch(project_cwd: Path, poll_interval: float = 1.0) -> None:
    print(f"watching {project_cwd}", file=sys.stderr)
    print(f"audit ref:    {project_audit_ref(project_cwd)}", file=sys.stderr)
    print(f"sidecar:      {_watcher_state_file(project_cwd)}", file=sys.stderr)
    print(f"poll:         {poll_interval}s   (file-history JSONLs + working tree)",
          file=sys.stderr)
    print("Ctrl-C to stop.\n", file=sys.stderr)

    last_fh_sig: tuple[int, str] = _file_history_signature(project_cwd)
    last_seen_shas: dict[str, str] = {}

    try:
        while True:
            now = time.strftime("%H:%M:%S")

            # 1. Detect file-history snapshots
            fh_sig = _file_history_signature(project_cwd)
            new_fh = (fh_sig != last_fh_sig)

            # 2. Detect working-tree changes
            wt_changes = _capture_wt_changes(project_cwd, last_seen_shas)
            if wt_changes:
                trace_id = _active_trace_id(project_cwd) or "watcher"
                ts = _now_iso()
                _append_watcher_event(project_cwd, {
                    "trace_id": trace_id,
                    "timestamp": ts,
                    "files": wt_changes,
                    "source": "working-tree",
                    "message_id": f"wt-{ts}",
                })
                files_str = ", ".join(sorted(wt_changes.keys())[:3])
                more = "" if len(wt_changes) <= 3 else f" (+{len(wt_changes)-3})"
                print(f"[{now}] +{len(wt_changes)} working-tree change(s) by "
                      f"{trace_id[:8]}: {files_str}{more}", file=sys.stderr)

            # 3. Rebuild if either source changed
            if new_fh or wt_changes:
                if new_fh:
                    delta = fh_sig[0] - last_fh_sig[0]
                    print(f"[{now}] +{delta} file-history snapshot(s) "
                          f"(total={fh_sig[0]})", file=sys.stderr)
                try:
                    ref = build_audit_history(project_cwd, verbose=False)
                    print(f"[{now}] audit updated → {ref}", file=sys.stderr)
                except Exception as e:
                    print(f"[{now}] build failed: {e}", file=sys.stderr)
                last_fh_sig = fh_sig

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Blame-based attribution
# --------------------------------------------------------------------------- #

def _audit_commit_metadata(project_cwd: Path, sha: str) -> dict:
    """Extract trace metadata from an audit commit's author email."""
    out = git("-C", str(project_cwd), "show", "-s",
              "--format=%ae%n%aI%n%an%n%s", sha).strip().split("\n", 3)
    email = out[0] if len(out) > 0 else ""
    ts = out[1] if len(out) > 1 else ""
    name = out[2] if len(out) > 2 else ""
    subject = out[3] if len(out) > 3 else ""
    trace_id = None
    if email.endswith(f"@{OPENTRACES_EMAIL_DOMAIN}"):
        trace_id = email.split("@")[0]
    return {
        "trace_id": trace_id, "timestamp": ts,
        "author": name, "subject": subject, "audit_sha": sha,
    }


def _parse_porcelain_blame(blame_output: str):
    """Yield (sha, line_no) for each line in git blame --line-porcelain output."""
    lines = blame_output.splitlines()
    i = 0
    while i < len(lines):
        hdr = lines[i]
        parts = hdr.split()
        if len(parts) >= 3 and len(parts[0]) == 40:
            sha = parts[0]
            try:
                final_ln = int(parts[2])
            except ValueError:
                i += 1; continue
            # advance past metadata lines to the "\t<content>" line
            i += 1
            while i < len(lines) and not lines[i].startswith("\t"):
                i += 1
            if i < len(lines):
                i += 1
            yield sha, final_ln
        else:
            i += 1


def attribute_commit(project_cwd: Path, commit_ref: str,
                     verbose: bool = False) -> dict:
    ref = project_audit_ref(project_cwd)
    if not git_ok("-C", str(project_cwd), "rev-parse", "--verify", ref):
        raise RuntimeError(f"audit ref not found: {ref} — run `build` first")

    commit_sha = git("-C", str(project_cwd), "rev-parse", commit_ref).strip()
    subject = git("-C", str(project_cwd), "show", "-s",
                  "--format=%s", commit_sha).strip()

    # Files changed in the target commit
    files = git("-C", str(project_cwd), "diff-tree", "--no-commit-id",
                "--name-only", "-r", commit_sha).strip().splitlines()

    audit_tip = git("-C", str(project_cwd), "rev-parse", ref).strip()

    per_file: dict[str, dict] = {}
    trace_summary: dict[str, dict] = {}

    for rel in files:
        exists_in_audit = git_ok("-C", str(project_cwd), "cat-file", "-e",
                                 f"{audit_tip}:{rel}")
        if not exists_in_audit:
            per_file[rel] = {"status": "missing_from_audit", "total_lines": 0,
                             "by_trace": {}}
            if verbose:
                print(f"  {rel}: not in audit history", file=sys.stderr)
            continue

        try:
            blame = git("-C", str(project_cwd),
                        "-c", f"diff.algorithm={DIFF_ALGORITHM}",
                        "blame", "--line-porcelain", audit_tip, "--", rel)
        except RuntimeError as e:
            per_file[rel] = {"status": "blame_failed", "error": str(e)[:200]}
            continue

        # Aggregate per audit-commit then per trace
        per_audit: dict[str, list[int]] = {}
        for sha, ln in _parse_porcelain_blame(blame):
            per_audit.setdefault(sha, []).append(ln)

        by_trace: dict[str, dict] = {}
        for asha, lns in per_audit.items():
            meta = _audit_commit_metadata(project_cwd, asha)
            tid = meta["trace_id"] or f"pre-audit:{asha[:8]}"
            entry = by_trace.setdefault(tid, {
                "count": 0, "line_ranges": [],
                "audit_commits": set(),
                "introducer": meta["trace_id"] is not None,
            })
            entry["count"] += len(lns)
            entry["audit_commits"].add(asha[:8])
            entry.setdefault("_lines", []).extend(lns)

        # Compress line lists to ranges
        for tid, e in by_trace.items():
            lns = sorted(e.pop("_lines"))
            e["line_ranges"] = _compress_ranges(lns)
            e["audit_commits"] = sorted(e["audit_commits"])
            # project-level rollup
            summary = trace_summary.setdefault(tid, {"files": set(),
                                                     "total_lines": 0})
            summary["files"].add(rel)
            summary["total_lines"] += e["count"]

        total = sum(e["count"] for e in by_trace.values())
        per_file[rel] = {"status": "attributed", "total_lines": total,
                         "by_trace": by_trace}

    # Finalize summary
    summary_out = {tid: {"files_touched": sorted(v["files"]),
                         "total_lines": v["total_lines"]}
                   for tid, v in trace_summary.items()}

    return {
        "commit": commit_sha, "subject": subject,
        "audit_ref": ref, "audit_tip": audit_tip,
        "diff_algorithm": DIFF_ALGORITHM,
        "files": per_file,
        "summary": summary_out,
    }


def _compress_ranges(sorted_lines: list[int]) -> list[str]:
    if not sorted_lines:
        return []
    out = []
    start = prev = sorted_lines[0]
    for n in sorted_lines[1:]:
        if n == prev + 1:
            prev = n
        else:
            out.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = n
    out.append(f"{start}" if start == prev else f"{start}-{prev}")
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _pretty_print_attribution(result: dict) -> None:
    GREEN, DIM, YELLOW, RED, RESET = "\033[32m", "\033[2m", "\033[33m", "\033[31m", "\033[0m"
    print(f"\n{DIM}commit{RESET} {result['commit'][:10]}  \"{result['subject'][:60]}\"")
    print(f"{DIM}audit{RESET}  {result['audit_tip'][:10]}  ({result['audit_ref']})")
    print(f"{DIM}diff-algo={result['diff_algorithm']}{RESET}")
    print()

    if not result["summary"]:
        print(f"{YELLOW}no attribution data — all files missing from audit{RESET}")
        return

    print("Per-trace summary:")
    for tid, s in sorted(result["summary"].items(),
                         key=lambda kv: -kv[1]["total_lines"]):
        marker = GREEN if not tid.startswith("pre-audit:") else DIM
        print(f"  {marker}{tid[:30]:<30}{RESET}  {s['total_lines']:>4} line(s)  "
              f"across {len(s['files_touched'])} file(s)")

    print()
    print("Per-file detail:")
    for path, data in result["files"].items():
        if data["status"] == "missing_from_audit":
            print(f"  {YELLOW}{path}{RESET}  {DIM}(not in audit history — "
                  f"likely bash-created or pre-session){RESET}")
            continue
        if data["status"] != "attributed":
            print(f"  {RED}{path}{RESET}  {DIM}({data['status']}){RESET}")
            continue
        print(f"  {path}  ({data['total_lines']} lines)")
        for tid, e in sorted(data["by_trace"].items(),
                              key=lambda kv: -kv[1]["count"]):
            marker = GREEN if not tid.startswith("pre-audit:") else DIM
            ranges = ",".join(e["line_ranges"][:6])
            more = "" if len(e["line_ranges"]) <= 6 else f" (+{len(e['line_ranges'])-6})"
            print(f"    {marker}{tid[:30]:<30}{RESET}  {e['count']:>4} lines "
                  f"{DIM}[{ranges}{more}]{RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Trace attribution spike v2")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Rebuild audit history from file-history")
    b.add_argument("project_dir")
    b.add_argument("-v", "--verbose", action="store_true")
    b.add_argument("--with-working-tree", action="store_true",
                   help="Also hash-object the current working-tree state into "
                        "the final audit commit (catches Bash-created files)")

    w = sub.add_parser("watch", help="Live-update audit history as snapshots arrive")
    w.add_argument("project_dir")
    w.add_argument("--interval", type=float, default=1.0)

    a = sub.add_parser("attribute", help="Blame a commit against the audit history")
    a.add_argument("project_dir")
    a.add_argument("commit", nargs="?", default="HEAD")
    a.add_argument("-v", "--verbose", action="store_true")
    a.add_argument("--json", action="store_true")

    s = sub.add_parser("show-ref", help="Print the project's audit ref info")
    s.add_argument("project_dir")

    args = ap.parse_args()
    project_cwd = Path(args.project_dir).expanduser().resolve()
    if not (project_cwd / ".git").exists():
        print(f"not a git repo: {project_cwd}", file=sys.stderr)
        return 2

    if args.cmd == "build":
        ref = build_audit_history(project_cwd,
                                   capture_working_tree=args.with_working_tree,
                                   verbose=args.verbose)
        if ref:
            print(f"audit ref: {ref}")
            print(git("-C", str(project_cwd), "log", "--oneline", ref))
        return 0 if ref else 1

    if args.cmd == "watch":
        watch(project_cwd, poll_interval=args.interval)
        return 0

    if args.cmd == "attribute":
        try:
            result = attribute_commit(project_cwd, args.commit,
                                       verbose=args.verbose)
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            _pretty_print_attribution(result)
        return 0

    if args.cmd == "show-ref":
        ref = project_audit_ref(project_cwd)
        if git_ok("-C", str(project_cwd), "rev-parse", "--verify", ref):
            print(f"ref: {ref}")
            print(git("-C", str(project_cwd), "log", "--oneline", ref))
        else:
            print(f"ref {ref} does not exist — run `build` first")
            return 1
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
