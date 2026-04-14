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
import re
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
    # blame --line-porcelain on files containing non-UTF-8 bytes (binary
    # assets checked into the repo) emits raw bytes interleaved with
    # ASCII metadata. Decode permissively so those bytes don't crash
    # attribution; line-count integrity is preserved because the
    # porcelain framing is ASCII-only.
    result = subprocess.run(
        ["git", *args], cwd=cwd, input=input,
        capture_output=True, encoding="utf-8", errors="replace", env=env,
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


def _read_trace_id_from_jsonl(path: Path) -> str | None:
    """Return the ``trace_id`` from the first JSON line of a Claude Code
    capture JSONL, or ``None`` if the file is empty / malformed / missing
    a non-empty ``trace_id`` field.

    Claude Code names session JSONL files by ``session_id``, NOT by
    ``trace_id`` — those identifiers are distinct concepts. The canonical
    trace identifier lives in the first record's JSON payload. Callers
    that conflate the filename stem with the trace_id end up propagating
    session_ids under the trace_id name, which leaks an upstream-agent
    implementation detail through the rest of the pipeline.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            first = fh.readline().strip()
    except OSError:
        return None
    if not first:
        return None
    try:
        d = json.loads(first)
    except (ValueError, TypeError):
        return None
    if not isinstance(d, dict):
        return None
    tid = d.get("trace_id") or ""
    return tid if isinstance(tid, str) and tid else None


def load_snapshots_for_project(project_cwd: Path) -> list[Snapshot]:
    """Combine file-history snapshots (from ~/.claude), tool-use
    reconstructions from the JSONL (when blobs are absent), and
    watcher-captured working-tree snapshots (from sidecar in
    <project>/.git/).
    """
    snapshots: list[Snapshot] = []
    proj_dir = Path.home() / ".claude" / "projects" / _encode_cwd(project_cwd)
    if proj_dir.is_dir():
        for jsonl_path in sorted(proj_dir.glob("*.jsonl")):
            # The JSONL filename is session_id (Claude Code's convention),
            # but the trace_id is the canonical identifier we want to
            # propagate downstream. Read the first line for the real
            # trace_id and only fall back to the stem if the file is
            # empty / malformed (test fixtures, race during write).
            trace_id = _read_trace_id_from_jsonl(jsonl_path) or jsonl_path.stem
            # file-history snapshots capture the BEFORE-prompt state for
            # rollback, so they often record `backupFileName=None` for
            # newly-created files — which the spike treats as a delete
            # marker. On its own this can leave the final session state
            # uncaptured. The tool-use reconstruction emits AFTER-tool
            # state and fills the gap. Run both and merge; the timestamp
            # ordering preserves causality.
            snapshots.extend(_iter_snapshots(jsonl_path, trace_id))
            snapshots.extend(
                _reconstruct_snapshots_from_jsonl(jsonl_path, trace_id,
                                                   project_cwd)
            )
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
# JSONL tool-use reconstruction (fallback when file-history blobs are absent)
# --------------------------------------------------------------------------- #

def _hash_content_into_repo(project_cwd: Path, content: str) -> str:
    """Hash an in-memory string into the project's git object store."""
    return git("-C", str(project_cwd), "hash-object", "-w", "--stdin",
               input=content).strip()


def _read_head_content(project_cwd: Path, rel: str,
                        ref: str = "HEAD") -> str | None:
    """Read the blob at <ref>:<rel>. Defaults to HEAD but can be pointed
    at an earlier commit (e.g. the one that was HEAD at session-start
    time, for backfill reconstruction of historical sessions)."""
    if not git_ok("-C", str(project_cwd), "cat-file", "-e", f"{ref}:{rel}"):
        return None
    return git("-C", str(project_cwd), "show", f"{ref}:{rel}", check=False)


# Bash-command effects: derive (rel_path, new_content_or_None) tuples
# from a single Bash tool_use command string. This is the "pattern layer"
# that closes the backfill Bash gap without requiring a live watcher.
# Parsing is deliberately conservative: when confidence is low, return
# an empty list rather than fabricate attribution.

_BASH_SHLEX_EXC = (ValueError,)


def _split_shlex(s: str) -> list[str] | None:
    try:
        import shlex
        return shlex.split(s, comments=False, posix=True)
    except _BASH_SHLEX_EXC:
        return None


def _interpret_printf(fmt: str, args: list[str]) -> str:
    """Minimal printf interpreter: handles the common escapes (\\n, \\t,
    \\r, \\\\, \\0, \\xHH) and the %s conversion cycling through args.
    Unknown conversions abort (return None)."""
    out = []
    i = 0
    arg_iter = iter(args)
    consumed_any_conversion = False
    while i < len(fmt):
        ch = fmt[i]
        if ch == "\\" and i + 1 < len(fmt):
            nxt = fmt[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\",
                       "0": "\0", "a": "\a", "b": "\b", "f": "\f",
                       "v": "\v", "'": "'", '"': '"'}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
            if nxt == "x" and i + 3 < len(fmt):
                try:
                    out.append(chr(int(fmt[i+2:i+4], 16)))
                    i += 4
                    continue
                except ValueError:
                    pass
            # unknown escape — treat backslash literally
            out.append(ch)
            i += 1
            continue
        if ch == "%" and i + 1 < len(fmt):
            nxt = fmt[i + 1]
            if nxt == "%":
                out.append("%")
                i += 2
                continue
            if nxt == "s":
                try:
                    out.append(next(arg_iter))
                except StopIteration:
                    out.append("")
                i += 2
                consumed_any_conversion = True
                continue
            # Anything else (e.g. %d, %x, %.2f) — opaque; bail
            return None
        out.append(ch)
        i += 1
    return "".join(out)


def _interpret_echo(args: list[str]) -> str:
    """Interpret `echo` args: respects -n (no newline) and -e (enable
    escapes). Returns the stdout bytes as a string."""
    suppress_newline = False
    interpret_escapes = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            i += 1
            break
        if not a.startswith("-") or len(a) < 2:
            break
        body = a[1:]
        if not all(c in "neE" for c in body):
            break
        if "n" in body:
            suppress_newline = True
        if "e" in body:
            interpret_escapes = True
        if "E" in body:
            interpret_escapes = False
        i += 1
    text = " ".join(args[i:])
    if interpret_escapes:
        text = _interpret_printf(text.replace("%", "%%"), [])
        if text is None:
            return ""
    return text + ("" if suppress_newline else "\n")


def _parse_trailing_heredoc(cmd: str) -> tuple[str, str] | None:
    """If `cmd` contains a heredoc (`<<[-]DELIM\\nbody\\nDELIM`), return
    the command-line prefix (everything before the heredoc body) and the
    body. Otherwise None.
    """
    m = re.search(r"<<-?\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_]\w*))", cmd)
    if not m:
        return None
    delim = m.group(1) or m.group(2) or m.group(3)
    nl = cmd.find("\n", m.end())
    if nl < 0:
        return None
    pre = cmd[:m.start()].rstrip()
    body_start = nl + 1
    pattern = re.compile(rf"\n\s*{re.escape(delim)}\s*(?:\n|$)")
    end_m = pattern.search(cmd, body_start - 1)
    if not end_m:
        return None
    body = cmd[body_start:end_m.start()]
    if body and not body.endswith("\n"):
        body += "\n"
    return pre, body


def _find_trailing_redirect(cmd_line: str) -> tuple[str, str, int] | None:
    """Locate the last top-level `> path` or `>> path` in a single-line
    command (no heredoc). Returns (op, path_token, span_start) or None."""
    # Walk right-to-left to find the final redirect.
    # Skip anything inside quotes.
    quote = None
    escape = False
    i = 0
    redir = None
    while i < len(cmd_line):
        ch = cmd_line[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == ">":
            op = ">>" if cmd_line.startswith(">>", i) else ">"
            # Skip fd redirects (2>, &>) — not our concern
            if i > 0 and cmd_line[i-1].isdigit():
                i += len(op)
                continue
            op_end = i + len(op)
            rest = cmd_line[op_end:].lstrip()
            # Take first token (may be quoted)
            if not rest:
                i = op_end
                continue
            tok = _split_shlex(rest)
            if not tok:
                i = op_end
                continue
            path = tok[0]
            redir = (op, path, i)
            i = op_end
            continue
        i += 1
    return redir


def _effects_of_bash_command(cmd: str, running: dict[str, str],
                              project_cwd: Path,
                              seed_ref: str = "HEAD") -> list[tuple[str, str | None]]:
    """Reconstruct per-file effects of a Bash command.
    Returns a list of (rel_path, new_content_or_None). None means delete.
    Returns [] when the command is read-only, opaque, or unparseable —
    never fabricate attribution.
    """
    s = cmd.strip()
    if not s:
        return []

    # Handle heredoc shape first
    heredoc = _parse_trailing_heredoc(s)
    if heredoc:
        pre_line, body = heredoc
        redir = _find_trailing_redirect(pre_line)
        if redir:
            op, target, span = redir
            rel = _relative_path(target, project_cwd)
            if rel is None:
                return []
            if op == ">":
                return [(rel, body)]
            current = running.get(rel)
            if current is None:
                current = _read_head_content(project_cwd, rel, seed_ref) or ""
            return [(rel, current + body)]
        # Heredoc but no redirect; might be `tee file <<EOF`
        toks = _split_shlex(pre_line)
        if toks and toks[0] == "tee":
            append = False
            i = 1
            while i < len(toks) and toks[i].startswith("-"):
                if toks[i] in ("-a", "--append"):
                    append = True
                i += 1
            out = []
            for t in toks[i:]:
                rel = _relative_path(t, project_cwd)
                if rel is None:
                    continue
                if append:
                    current = running.get(rel)
                    if current is None:
                        current = _read_head_content(project_cwd, rel, seed_ref) or ""
                    out.append((rel, current + body))
                else:
                    out.append((rel, body))
            return out
        return []

    # No heredoc: look for trailing redirect on a single-line command
    # (allow multi-line commands too, redirect must be on the outermost chain).
    one_line = s.replace("\n", " ") if "\n" in s and "<<" not in s else s
    redir = _find_trailing_redirect(one_line)
    if redir:
        op, target, span = redir
        rel = _relative_path(target, project_cwd)
        if rel is None:
            return []
        left = one_line[:span].rstrip()
        content = _content_from_producer(left, running, project_cwd, seed_ref)
        if content is None:
            return []  # opaque producer; don't fabricate
        if op == ">":
            return [(rel, content)]
        current = running.get(rel)
        if current is None:
            current = _read_head_content(project_cwd, rel, seed_ref) or ""
        return [(rel, current + content)]

    # No redirect — dispatch on first word
    toks = _split_shlex(one_line)
    if not toks:
        return []
    cmd0 = toks[0]

    if cmd0 == "mv":
        # last arg is destination (unless --target-directory flag — ignore for now)
        flagless = [t for t in toks[1:] if not t.startswith("-")]
        if len(flagless) < 2:
            return []
        dst = flagless[-1]
        srcs = flagless[:-1]
        results: list[tuple[str, str | None]] = []
        for src in srcs:
            src_rel = _relative_path(src, project_cwd)
            dst_rel = _relative_path(dst, project_cwd)
            if src_rel is None or dst_rel is None:
                continue
            src_content = running.get(src_rel)
            if src_content is None:
                src_content = _read_head_content(project_cwd, src_rel, seed_ref) or ""
            # Intentionally do NOT emit a deletion for the source path.
            # Leaving it as a ghost in the audit tree lets
            # `attribute_commit` still blame through to whoever authored
            # its content, so renamed/deleted lines retain attribution.
            # (HEAD will report the path as deleted; attribute_commit
            # handles that via its exists_in_audit check.)
            results.append((dst_rel, src_content))
        return results

    if cmd0 == "rm":
        results = []
        for t in toks[1:]:
            if t.startswith("-"):
                continue
            rel = _relative_path(t, project_cwd)
            if rel:
                results.append((rel, None))
        return results

    if cmd0 == "cp":
        flagless = [t for t in toks[1:] if not t.startswith("-")]
        if len(flagless) < 2:
            return []
        dst = flagless[-1]
        srcs = flagless[:-1]
        results = []
        for src in srcs:
            src_rel = _relative_path(src, project_cwd)
            dst_rel = _relative_path(dst, project_cwd)
            if src_rel is None or dst_rel is None:
                continue
            src_content = running.get(src_rel)
            if src_content is None:
                src_content = _read_head_content(project_cwd, src_rel, seed_ref) or ""
            results.append((dst_rel, src_content))
        return results

    if cmd0 == "touch":
        results = []
        for t in toks[1:]:
            if t.startswith("-"):
                continue
            rel = _relative_path(t, project_cwd)
            if rel is None:
                continue
            if rel in running:
                continue
            if _read_head_content(project_cwd, rel, seed_ref) is not None:
                continue
            results.append((rel, ""))
        return results

    if cmd0 in ("chmod", "chown"):
        return []  # mode-only; no content change

    if cmd0 == "ln":
        return []  # symlink handling is a separate design question

    if cmd0 == "git" and len(toks) >= 2:
        sub = toks[1]
        if sub == "mv" and len(toks) >= 4:
            # git mv [flags] src dst
            flagless = [t for t in toks[2:] if not t.startswith("-")]
            if len(flagless) < 2:
                return []
            src, dst = flagless[0], flagless[-1]
            src_rel = _relative_path(src, project_cwd)
            dst_rel = _relative_path(dst, project_cwd)
            if src_rel and dst_rel:
                src_content = running.get(src_rel)
                if src_content is None:
                    src_content = _read_head_content(project_cwd, src_rel, seed_ref) or ""
                # Preserve attribution of the source (see mv branch above).
                return [(dst_rel, src_content)]
            return []
        if sub == "rm" and len(toks) >= 3:
            results = []
            for t in toks[2:]:
                if t.startswith("-"):
                    continue
                rel = _relative_path(t, project_cwd)
                if rel:
                    results.append((rel, None))
            return results
        return []

    if cmd0 == "sed" and "-i" in toks:
        return _apply_sed_inplace(toks, running, project_cwd, seed_ref)

    return []


def _content_from_producer(left: str, running: dict[str, str],
                           project_cwd: Path,
                           seed_ref: str = "HEAD") -> str | None:
    """Parse the command LEFT of a `>` / `>>` redirect and return the
    stdout bytes it would produce. None = opaque."""
    left = left.strip()
    if not left:
        return ""
    toks = _split_shlex(left)
    if not toks:
        return None
    cmd0 = toks[0]
    if cmd0 == "echo":
        return _interpret_echo(toks[1:])
    if cmd0 == "printf":
        if len(toks) < 2:
            return None
        out = _interpret_printf(toks[1], toks[2:])
        return out
    if cmd0 == "cat":
        parts = []
        for t in toks[1:]:
            if t.startswith("-"):
                continue
            rel = _relative_path(t, project_cwd)
            if rel is None:
                return None
            c = running.get(rel)
            if c is None:
                c = _read_head_content(project_cwd, rel, seed_ref) or ""
            parts.append(c)
        return "".join(parts)
    return None  # opaque (jq, node -e, python -c, etc.)


def _apply_sed_inplace(toks: list[str], running: dict[str, str],
                       project_cwd: Path,
                       seed_ref: str = "HEAD") -> list[tuple[str, str | None]]:
    """Apply `sed -i [''] '<script>' FILE...` to running state. Supports
    the common `s/a/b/[flags]` substitution; otherwise opaque."""
    i = toks.index("-i")
    cursor = i + 1
    # macOS: `-i ''` — the empty-string backup suffix is a separate arg
    if cursor < len(toks) and toks[cursor] == "":
        cursor += 1
    scripts = []
    # Scripts may be preceded by further flags like -e
    while cursor < len(toks):
        t = toks[cursor]
        if t == "-e" and cursor + 1 < len(toks):
            scripts.append(toks[cursor + 1])
            cursor += 2
            continue
        if t.startswith("-") and t != "-":
            cursor += 1
            continue
        # First positional is the script, the rest are files
        if not scripts:
            scripts.append(t)
            cursor += 1
            continue
        break
    files = [t for t in toks[cursor:] if not t.startswith("-")]
    if not scripts or not files:
        return []
    results = []
    for fpath in files:
        rel = _relative_path(fpath, project_cwd)
        if rel is None:
            continue
        current = running.get(rel)
        if current is None:
            current = _read_head_content(project_cwd, rel, seed_ref) or ""
        new = current
        for script in scripts:
            applied = _apply_sed_script(script, new)
            if applied is None:
                new = None
                break
            new = applied
        if new is None:
            continue
        results.append((rel, new))
    return results


def _apply_sed_script(script: str, content: str) -> str | None:
    """Handle `s/PAT/REPL/FLAGS` substitution scripts. Returns new content
    or None if the script is not a recognized substitution."""
    m = re.match(r"\s*s(.)(.*)", script, re.DOTALL)
    if not m:
        return None
    sep = m.group(1)
    rest = m.group(2)
    # Split on unescaped separator
    parts = []
    buf = []
    esc = False
    for ch in rest:
        if esc:
            buf.append(ch)
            esc = False
            continue
        if ch == "\\":
            esc = True
            buf.append(ch)
            continue
        if ch == sep:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    if len(parts) < 3:
        return None
    pat_raw, repl_raw, flags = parts[0], parts[1], parts[2] if len(parts) > 2 else ""
    # Translate sed ERE-ish syntax to Python; we handle only BRE literal +
    # common escapes for MVP. No capture-group magic.
    try:
        pat = re.compile(pat_raw, re.MULTILINE) if "E" in flags.upper() \
              else re.compile(pat_raw, re.MULTILINE)
    except re.error:
        return None
    repl = repl_raw.replace("\\n", "\n").replace("\\t", "\t")
    count = 0 if "g" in flags else 1
    try:
        return pat.sub(repl, content, count=count)
    except re.error:
        return None


def _reconstruct_snapshots_from_jsonl(jsonl: Path, trace_id: str,
                                       project_cwd: Path) -> list[Snapshot]:
    """Replay the session's Write/Edit/MultiEdit tool_uses to reconstruct
    per-turn file state when file-history blobs are missing (dominant
    real-world case: /clear wipes blobs, new-file Writes don't produce
    backups, blob dirs age out).

    Returns a list of Snapshot objects carrying the reconstructed content
    as prehashed shas — same shape as watcher events, so the downstream
    index/commit machinery is unchanged.
    """
    try:
        text = jsonl.read_text(errors="replace")
    except OSError:
        return []

    # Pass 1: map tool_use_id -> success (is_error absent or false).
    lines = text.splitlines()
    success: dict[str, bool] = {}
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "user":
            continue
        for c in (e.get("message", {}).get("content") or []):
            if not (isinstance(c, dict) and c.get("type") == "tool_result"):
                continue
            tid = c.get("tool_use_id")
            if tid:
                success[tid] = not bool(c.get("is_error"))

    # Determine the seed ref: the commit that was HEAD at session-start
    # time. Reading prior file state from HEAD would be wrong for any
    # session older than HEAD (downstream replays would compound error).
    session_start = ""
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = e.get("timestamp", "")
        if ts:
            session_start = ts
            break
    seed_ref = _parent_commit_at(project_cwd, session_start) if session_start else None
    if not seed_ref:
        seed_ref = "HEAD"

    # Pass 2: walk tool_uses in order, replay, emit per-tool snapshots.
    running: dict[str, str] = {}  # rel → current content
    snaps: list[Snapshot] = []
    for line in lines:
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "assistant":
            continue
        ts = e.get("timestamp", "")
        outer_id = e.get("message", {}).get("id") or e.get("uuid") or ""
        for c in (e.get("message", {}).get("content") or []):
            if not (isinstance(c, dict) and c.get("type") == "tool_use"):
                continue
            tu_id = c.get("id") or ""
            # Skip tool_uses known to have errored. Absent entries (no
            # matching tool_result yet — abrupt exit) are permissive:
            # treat as attempted and apply optimistically.
            if success.get(tu_id) is False:
                continue
            name = c.get("name")
            inp = c.get("input") or {}

            # Bash: derive per-file effects from the command string.
            # This is the backfill Bash-pattern layer — closes the gap
            # between JSONL-tracked Write/Edit/MultiEdit and the actual
            # on-disk state when the agent used the shell.
            if name == "Bash":
                cmd = inp.get("command", "") or ""
                effects = _effects_of_bash_command(cmd, running, project_cwd, seed_ref)
                if not effects:
                    continue
                prehashed: dict[str, str | None] = {}
                for rel, new_content in effects:
                    if new_content is None:
                        running.pop(rel, None)
                        prehashed[rel] = None
                        continue
                    running[rel] = new_content
                    try:
                        sha = _hash_content_into_repo(project_cwd, new_content)
                    except RuntimeError:
                        continue
                    prehashed[rel] = sha
                if prehashed:
                    snaps.append(Snapshot(
                        trace_id=trace_id,
                        jsonl_path=jsonl,
                        message_id=f"tu-{(outer_id or tu_id)[:12]}",
                        timestamp=ts or "",
                        source="working-tree",
                        prehashed=prehashed,
                    ))
                continue

            raw_path = inp.get("file_path")
            if not raw_path:
                continue
            rel = _relative_path(raw_path, project_cwd)
            if rel is None:
                continue

            if name == "Write":
                running[rel] = inp.get("content", "") or ""
            elif name == "Edit":
                current = running.get(rel)
                if current is None:
                    current = _read_head_content(project_cwd, rel, seed_ref) or ""
                old = inp.get("old_string", "") or ""
                new = inp.get("new_string", "") or ""
                if old and old not in current:
                    continue  # Edit would fail; no authorship event
                if bool(inp.get("replace_all")):
                    current = current.replace(old, new)
                else:
                    current = current.replace(old, new, 1)
                running[rel] = current
            elif name == "MultiEdit":
                current = running.get(rel)
                if current is None:
                    current = _read_head_content(project_cwd, rel, seed_ref) or ""
                ok = True
                for ed in (inp.get("edits") or []):
                    old = ed.get("old_string", "") or ""
                    new = ed.get("new_string", "") or ""
                    if old and old not in current:
                        ok = False
                        break
                    if bool(ed.get("replace_all")):
                        current = current.replace(old, new)
                    else:
                        current = current.replace(old, new, 1)
                if not ok:
                    continue
                running[rel] = current
            else:
                continue  # Read/Grep/Bash/etc. produce no authorship event

            try:
                sha = _hash_content_into_repo(project_cwd, running[rel])
            except RuntimeError:
                continue
            snaps.append(Snapshot(
                trace_id=trace_id,
                jsonl_path=jsonl,
                message_id=f"tu-{(outer_id or tu_id)[:12]}",
                timestamp=ts or "",
                source="working-tree",
                prehashed={rel: sha},
            ))
    return snaps


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


def _iter_porcelain_z(project_cwd: Path):
    """Yield (xy, path) records from `git status --porcelain -z -uall`.

    `-z` emits NUL-terminated raw paths (no quoting), which is the only
    reliable way to parse paths containing spaces, quotes, or newlines.
    For rename/copy records the format is `XY SP NEW \\0 OLD \\0`; we
    yield only the NEW path and skip the OLD follow-up record.
    """
    out = git("-C", str(project_cwd), "status", "--porcelain", "-z", "-uall",
              check=False)
    records = out.split("\0")
    i = 0
    while i < len(records):
        rec = records[i]
        if len(rec) < 4:
            i += 1
            continue
        xy, rel = rec[:2], rec[3:]
        yield xy, rel
        # Rename/copy: the original path follows as its own record — skip it.
        if xy[0] in ("R", "C") or xy[1] in ("R", "C"):
            i += 2
        else:
            i += 1


def _working_tree_extras(project_cwd: Path,
                         already_in_snapshot: set[str]) -> dict[str, str]:
    """Return {rel_path: blob_sha} for files in the working tree that the
    snapshot did not capture (e.g., Bash-created, user-touched-outside-agent).
    Uses `git status --porcelain -z -uall` to enumerate untracked + modified.
    """
    extras: dict[str, str] = {}
    for xy, rel in _iter_porcelain_z(project_cwd):
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
        # Files already hash-object'd by the watcher; just install in index.
        # A sha value of None signals deletion (Bash-reconstruction path
        # uses this for `rm`, `mv`-source, `git rm`, etc.).
        # All updates are best-effort: backfill across long project
        # histories routinely hits paths that flip between file and
        # directory over time, or other git-index conflicts. Better to
        # skip the offending cell than abort the whole audit build.
        for rel, sha in (snap.prehashed or {}).items():
            if sha is None:
                subprocess.run(
                    ["git", "-C", str(project_cwd), "update-index", "--remove", rel],
                    env=env, capture_output=True, check=False,
                )
                touched.append(rel)
                continue
            if _safe_update_index(project_cwd, rel, sha, env):
                touched.append(rel)
            elif verbose:
                print(f"  ⚠ skipped {rel}: index conflict", file=sys.stderr)
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
        if not _safe_update_index(project_cwd, rel, sha, env):
            continue
        touched.append(rel)
    return touched


def _safe_update_index(project_cwd: Path, rel: str, sha: str,
                       env: dict) -> bool:
    """Install `100644,{sha},{rel}` into the index; retry once after
    clearing any conflicting directory/file entry (common in backfill
    when a path changed kind across a project's history). Returns True
    on success.
    """
    r = subprocess.run(
        ["git", "-C", str(project_cwd), "update-index",
         "--add", "--replace", "--cacheinfo", f"100644,{sha},{rel}"],
        env=env, capture_output=True, check=False,
    )
    if r.returncode == 0:
        return True
    subprocess.run(
        ["git", "-C", str(project_cwd), "rm", "--cached",
         "-r", "-f", "--quiet", "--ignore-unmatch", rel],
        env=env, capture_output=True, check=False,
    )
    retry = subprocess.run(
        ["git", "-C", str(project_cwd), "update-index",
         "--add", "--replace", "--cacheinfo", f"100644,{sha},{rel}"],
        env=env, capture_output=True, check=False,
    )
    return retry.returncode == 0


def _apply_extras_to_index(project_cwd: Path, extras: dict[str, str],
                           env: dict) -> None:
    for rel, sha in extras.items():
        _safe_update_index(project_cwd, rel, sha, env)


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
    changed: dict[str, str] = {}
    for xy, rel in _iter_porcelain_z(project_cwd):
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
    have_audit = git_ok("-C", str(project_cwd), "rev-parse", "--verify", ref)

    commit_sha = git("-C", str(project_cwd), "rev-parse", commit_ref).strip()
    subject = git("-C", str(project_cwd), "show", "-s",
                  "--format=%s", commit_sha).strip()

    # Files changed in the target commit
    files = git("-C", str(project_cwd), "diff-tree", "--no-commit-id",
                "--name-only", "-r", commit_sha).strip().splitlines()

    # When no audit history exists (common: session did only Read/Grep, or
    # produced no file-history snapshots), fall back to blaming against the
    # commit itself. Every line will attribute to pre-audit:<sha> via
    # _audit_commit_metadata (which returns trace_id=None for non-audit commits).
    audit_tip = (git("-C", str(project_cwd), "rev-parse", ref).strip()
                 if have_audit else commit_sha)

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
        else:
            print("(no audit history built — no snapshots for this project)")
        return 0

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
