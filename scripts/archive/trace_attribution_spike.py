#!/usr/bin/env python3
"""Trace → commit attribution spike.

Proof of concept: given a Claude Code session_id and a git repo path, walk the
session's file-history snapshots, compute per-turn unified diffs against the
backup blobs, and match each turn's patch against recent commits via
`git patch-id --stable`.

Usage:
    python3 scripts/trace_attribution_spike.py <session_id> <repo_path>

No storage, no daemon, no schema changes. Read-only over:
    ~/.claude/projects/<project-dir>/<session_id>.jsonl
    ~/.claude/file-history/<session_id>/<hash>@v<N>
    <repo_path>/.git
"""
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #

@dataclass
class Snapshot:
    """One file-history-snapshot entry from the JSONL."""
    message_id: str
    timestamp: str | None
    backups: dict[str, dict]  # file_path → {backupFileName, version, backupTime}


@dataclass
class TurnDiff:
    """The change one prompt boundary introduced, rolled up across files."""
    message_id: str
    timestamp: str | None
    file_diffs: dict[str, str]  # file_path → unified diff text
    combined_patch: str = ""
    patch_id: str | None = None


@dataclass
class CommitInfo:
    sha: str
    timestamp: str
    subject: str
    patch_id: str | None = None


@dataclass
class Attribution:
    turn: TurnDiff
    status: str  # "strong" | "probabilistic" | "abandoned"
    commit: CommitInfo | None = None
    score: float = 0.0
    evidence: str = ""


# --------------------------------------------------------------------------- #
# JSONL + blob reading
# --------------------------------------------------------------------------- #

def find_jsonl(session_id: str) -> Path:
    projects_root = Path.home() / ".claude" / "projects"
    hits = list(projects_root.glob(f"*/{session_id}.jsonl"))
    if not hits:
        raise SystemExit(f"No JSONL found for session_id={session_id} under {projects_root}")
    if len(hits) > 1:
        print(f"⚠ multiple JSONLs matched, using first: {hits[0]}", file=sys.stderr)
    return hits[0]


def load_snapshots(jsonl_path: Path) -> list[Snapshot]:
    out: list[Snapshot] = []
    for line in jsonl_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "file-history-snapshot":
            continue
        snapshot_field = entry.get("snapshot") or {}
        backups = snapshot_field.get("trackedFileBackups") or {}
        out.append(Snapshot(
            message_id=entry.get("messageId", ""),
            timestamp=entry.get("timestamp") or _earliest_backup_time(backups),
            backups=backups,
        ))
    return out


def _earliest_backup_time(backups: dict[str, dict]) -> str | None:
    times = [v.get("backupTime") for v in backups.values() if v.get("backupTime")]
    return min(times) if times else None


def blob_path(session_id: str, backup_name: str) -> Path:
    return Path.home() / ".claude" / "file-history" / session_id / backup_name


# --------------------------------------------------------------------------- #
# Diff computation
# --------------------------------------------------------------------------- #

def diff_files(path_a: Path | None, path_b: Path | None, display_path: str) -> str:
    """git diff --no-index to produce a patch suitable for git patch-id."""
    dev_null = "/dev/null"
    a = str(path_a) if path_a and path_a.exists() else dev_null
    b = str(path_b) if path_b and path_b.exists() else dev_null
    result = subprocess.run(
        ["git", "diff", "--no-index", "--no-color", a, b],
        capture_output=True, text=True,
    )
    # Rewrite blob paths in the diff header to the logical file path for readability
    out_lines = []
    for ln in result.stdout.splitlines():
        if ln.startswith("diff --git ") or ln.startswith("--- ") or ln.startswith("+++ "):
            out_lines.append(_rewrite_header(ln, display_path))
        else:
            out_lines.append(ln)
    return "\n".join(out_lines) + ("\n" if out_lines else "")


def _rewrite_header(line: str, display_path: str) -> str:
    if line.startswith("diff --git "):
        return f"diff --git a/{display_path} b/{display_path}"
    if line.startswith("--- "):
        return f"--- a/{display_path}" if "/dev/null" not in line else "--- /dev/null"
    if line.startswith("+++ "):
        return f"+++ b/{display_path}" if "/dev/null" not in line else "+++ /dev/null"
    return line


def compute_turn_diffs(session_id: str, snapshots: list[Snapshot]) -> list[TurnDiff]:
    """For each snapshot, diff current-version-of-each-file against the previous
    version of that file (looking backward at earlier snapshots of the same file).
    The diff 'belongs' to the snapshot containing the newer version.
    """
    # Track last-seen version per file across the chronological walk
    last_version: dict[str, int] = {}
    last_blob: dict[str, str] = {}  # file_path → backupFileName

    turns: list[TurnDiff] = []
    for snap in snapshots:
        file_diffs: dict[str, str] = {}
        for fpath, meta in snap.backups.items():
            v = meta.get("version")
            blob_name = meta.get("backupFileName")
            if v is None:
                continue
            prev_v = last_version.get(fpath)
            if prev_v is not None and v == prev_v:
                continue  # same version as before — nothing new this turn
            prev_blob = last_blob.get(fpath)
            a_path = blob_path(session_id, prev_blob) if prev_blob else None
            b_path = blob_path(session_id, blob_name) if blob_name else None
            patch = diff_files(a_path, b_path, fpath)
            if patch.strip():
                file_diffs[fpath] = patch
            last_version[fpath] = v
            last_blob[fpath] = blob_name
        if file_diffs:
            combined = "".join(file_diffs.values())
            turns.append(TurnDiff(
                message_id=snap.message_id,
                timestamp=snap.timestamp,
                file_diffs=file_diffs,
                combined_patch=combined,
                patch_id=patch_id_of(combined),
            ))
    return turns


# --------------------------------------------------------------------------- #
# patch-id + git log
# --------------------------------------------------------------------------- #

def _normalize_for_patch_id(unified_diff: str) -> str:
    """Normalize a patch so `git patch-id --stable` produces the same hash
    regardless of whether the diff came from `git diff --no-index` (absolute
    paths) or `git show` (repo-relative paths).

    Strategy: keep the `diff --git` delimiter (patch-id needs it) but replace
    the filenames within it and in `---`/`+++` lines with a constant. Drop the
    `index <blob>..<blob>` line — git blob hashes are content-determined but
    their presence varies by framing. Drop mode/rename metadata that also varies.
    """
    kept: list[str] = []
    for ln in unified_diff.splitlines():
        if ln.startswith("diff --git "):
            kept.append("diff --git a/f b/f")
            continue
        if ln.startswith("index "):
            continue
        if ln.startswith("new file mode ") or ln.startswith("deleted file mode "):
            continue
        if ln.startswith("old mode ") or ln.startswith("new mode "):
            continue
        if ln.startswith("similarity index ") or ln.startswith("rename from ") or ln.startswith("rename to "):
            continue
        if ln.startswith("--- "):
            kept.append("--- a/f" if "/dev/null" not in ln else "--- /dev/null")
            continue
        if ln.startswith("+++ "):
            kept.append("+++ b/f" if "/dev/null" not in ln else "+++ /dev/null")
            continue
        kept.append(ln)
    return "\n".join(kept) + ("\n" if kept else "")


def patch_id_of(unified_diff: str) -> str | None:
    if not unified_diff.strip():
        return None
    normalized = _normalize_for_patch_id(unified_diff)
    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        input=normalized, capture_output=True, text=True,
    )
    out = result.stdout.strip().split()
    return out[0] if out else None


def commits_in_window(repo_path: Path, since_iso: str | None) -> list[CommitInfo]:
    args = ["git", "-C", str(repo_path), "log", "--no-merges",
            "--pretty=format:%H%x09%cI%x09%s"]
    if since_iso:
        args.extend(["--since", since_iso])
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"git log failed: {result.stderr}")
    commits: list[CommitInfo] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        sha, ts, subject = parts
        commits.append(CommitInfo(sha=sha, timestamp=ts, subject=subject,
                                  patch_id=patch_id_of_commit(repo_path, sha)))
    return commits


def patch_id_of_commit(repo_path: Path, sha: str) -> str | None:
    show = subprocess.run(
        ["git", "-C", str(repo_path), "show", "--no-color", "--format=", sha],
        capture_output=True, text=True,
    )
    if show.returncode != 0:
        return None
    return patch_id_of(show.stdout)


# --------------------------------------------------------------------------- #
# Attribution
# --------------------------------------------------------------------------- #

def attribute(turns: list[TurnDiff], commits: list[CommitInfo]) -> list[Attribution]:
    by_pid = {c.patch_id: c for c in commits if c.patch_id}
    attributions: list[Attribution] = []
    for turn in turns:
        if turn.patch_id and turn.patch_id in by_pid:
            attributions.append(Attribution(
                turn=turn, status="strong",
                commit=by_pid[turn.patch_id],
                score=1.0, evidence="exact patch-id match",
            ))
            continue
        # probabilistic: hunk overlap by shared changed file paths
        turn_files = set(turn.file_diffs.keys())
        best: tuple[float, CommitInfo | None] = (0.0, None)
        for c in commits:
            commit_files = commit_changed_paths(c)
            if not commit_files:
                continue
            overlap = len(turn_files & commit_files) / max(len(turn_files | commit_files), 1)
            if overlap > best[0]:
                best = (overlap, c)
        if best[0] >= 0.5 and best[1]:
            attributions.append(Attribution(
                turn=turn, status="probabilistic",
                commit=best[1], score=best[0],
                evidence=f"file-path overlap {best[0]:.2f}",
            ))
        else:
            attributions.append(Attribution(
                turn=turn, status="abandoned",
                evidence="no patch-id match; file overlap below threshold",
            ))
    return attributions


_commit_files_cache: dict[str, set[str]] = {}

def commit_changed_paths(c: CommitInfo) -> set[str]:
    if c.sha in _commit_files_cache:
        return _commit_files_cache[c.sha]
    result = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", c.sha],
        capture_output=True, text=True,
    )
    paths = {p for p in result.stdout.splitlines() if p}
    _commit_files_cache[c.sha] = paths
    return paths


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"

def render(session_id: str, turns: list[TurnDiff],
           commits: list[CommitInfo], attributions: list[Attribution]) -> None:
    print(f"Trace {session_id[:8]}… — {len(turns)} turn(s) with edits")
    print(f"{DIM}git window: {len(commits)} commit(s){RESET}")
    print("─" * 72)
    for att in attributions:
        t = att.turn
        msg = t.message_id[:8] if t.message_id else "?"
        ts = (t.timestamp or "")[:19].replace("T", " ")
        files = ", ".join(sorted(t.file_diffs.keys())[:4])
        more = "" if len(t.file_diffs) <= 4 else f" (+{len(t.file_diffs)-4})"
        print(f"\nmsg={msg}  {ts}  edited [{files}{more}]")
        pid = (t.patch_id or "—")[:12]
        if att.status == "strong":
            print(f"        patch_id={pid}  {GREEN}✓ matched{RESET} "
                  f"{att.commit.sha[:8]}  \"{att.commit.subject[:50]}\"")
        elif att.status == "probabilistic":
            print(f"        patch_id={pid}  {YELLOW}≈ overlap={att.score:.2f}{RESET} with "
                  f"{att.commit.sha[:8]}  \"{att.commit.subject[:50]}\"")
        else:
            print(f"        patch_id={pid}  {RED}✗ abandoned{RESET}  {DIM}{att.evidence}{RESET}")

    strong = sum(1 for a in attributions if a.status == "strong")
    prob = sum(1 for a in attributions if a.status == "probabilistic")
    aband = sum(1 for a in attributions if a.status == "abandoned")
    print(f"\n{DIM}summary: {strong} strong, {prob} probabilistic, {aband} abandoned{RESET}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    session_id, repo_arg = sys.argv[1], sys.argv[2]
    repo_path = Path(repo_arg).expanduser().resolve()

    jsonl = find_jsonl(session_id)
    print(f"{DIM}jsonl: {jsonl}{RESET}", file=sys.stderr)

    snapshots = load_snapshots(jsonl)
    if not snapshots:
        print("No file-history-snapshot entries in this session.", file=sys.stderr)
        return 1

    turns = compute_turn_diffs(session_id, snapshots)
    since = snapshots[0].timestamp
    commits = commits_in_window(repo_path, since)

    attributions = attribute(turns, commits)
    render(session_id, turns, commits, attributions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
