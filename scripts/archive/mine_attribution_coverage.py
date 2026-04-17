#!/usr/bin/env python3
"""Attribution-coverage distribution over ~/.claude/projects/*.jsonl.

For every tool_use that MUTATES files, classify how recoverable it is
for backfill attribution:

  JSONL_WRITE          Write tool (input.content is full file)
  JSONL_EDIT           Edit tool (old_string → new_string; replayable)
  JSONL_MULTIEDIT      MultiEdit tool (list of edits; replayable)
  JSONL_NOTEBOOKEDIT   NotebookEdit tool (cell edit; replayable in principle)
  BASH_REDIRECT_WRITE  Bash `... > file`      (content recoverable from command string)
  BASH_REDIRECT_APPEND Bash `... >> file`     (content recoverable)
  BASH_HEREDOC         Bash `... << EOF ...`  (content recoverable)
  BASH_SED_INPLACE     Bash `sed -i …`        (substitution recoverable)
  BASH_MV              Bash `mv a b`          (rename recoverable)
  BASH_RM              Bash `rm …`            (deletion recoverable)
  BASH_CP              Bash `cp src dst`      (content recoverable from src)
  BASH_TOUCH           Bash `touch …`         (empty-file create)
  BASH_CHMOD           Bash `chmod …`         (mode only; no content)
  BASH_LN              Bash `ln …`            (symlink)
  BASH_GIT_MV          Bash `git mv …`        (recoverable)
  BASH_GIT_RM          Bash `git rm …`        (recoverable)
  BASH_TEE             Bash `tee file`        (content recoverable IF heredoc/stdin captured)
  BASH_PATCH           Bash `patch …`         (content recoverable IF patch captured)
  BASH_INTERPRETER     Bash `python -c ...`, `node -e ...`, etc. — OPAQUE
  BASH_APPLY_DIFF      Bash `git apply …`     — content depends on a diff we may not have
  BASH_DOWNLOAD        Bash `curl -o`, `wget`, `rsync` — content from network, OPAQUE
  BASH_TAR             Bash `tar -x…`         — content from archive, OPAQUE
  BASH_OTHER_MAYBE     Bash with unclear effect (shell script, opaque binary)
  BASH_READ_ONLY       Bash with clear read-only first word (ls, grep, cat, ...)

The first four are already handled by the JSONL-fallback loader.
The BASH_* classes with (recoverable) notes are candidates for the
backfill Bash-pattern layer. BASH_INTERPRETER / BASH_DOWNLOAD / BASH_TAR
/ BASH_OTHER_MAYBE are the irreducible backfill gap.

Emits:
  1) global distribution of mutation events by class
  2) per-session "coverage tier" — one of
       GREEN  100% recoverable today (JSONL-only, no Bash mutation)
       AMBER  would be recoverable with Bash pattern layer
       RED    has opaque Bash mutations that can't be backfilled
  3) leaderboard of opaque command first-words so we can prioritize
     which heuristics to build next.

Run: python3 scripts/mine_attribution_coverage.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"

READ_ONLY_FIRST_WORDS = {
    # core unix read
    "ls", "cat", "head", "tail", "less", "more", "wc", "file",
    "grep", "egrep", "fgrep", "rg", "find", "locate", "which",
    "awk", "sed", "sort", "uniq", "cut", "tr", "tee",
    # note: sed/awk/tee appear here but with inplace modifiers they mutate —
    # handled by dedicated classification below
    "test", "echo", "printf", "true", "false",
    "env", "printenv", "uname", "hostname", "whoami", "id", "pwd",
    "date", "stat", "du", "df", "uptime", "uptime", "type",
    "ps", "top", "jobs", "lsof", "pgrep",
    # git read
    "git",  # needs deeper classification below
    "gh",   # mostly read (review PR, list issues) but some mutate
    # build/test read (they may write build artifacts but we don't care for attribution)
    "cargo", "bun", "pnpm", "npm", "npx", "node", "python", "python3",
    "pytest", "go", "deno", "ruby",
    # browse / HTTP
    "curl", "wget",  # may download — classified separately
    # nav
    "cd", "pushd", "popd",
    # misc
    "sleep", "wait", "kill", "xargs",
    # users of this repo
    "tmux", "zsh", "bash", "sh",
    "agent-browser", "B", "USAGE",
}

INTERPRETER_INLINE_FLAGS = {"-c", "-e", "--eval", "-E"}
INTERPRETERS = {"python", "python3", "node", "bun", "deno", "ruby",
                "perl", "php", "lua", "sh", "bash", "zsh"}

MUTATING_FIRST_WORDS = {
    "mv", "rm", "cp", "chmod", "chown", "touch", "ln",
    "mkdir",  # structural, low attribution interest
    "patch", "rsync",
    "tar",
}


def classify_bash(cmd: str) -> tuple[str, str]:
    """(class_label, first_token_for_leaderboard)."""
    s = cmd.strip()
    first_match = re.match(r"^\s*([A-Za-z_][\w./-]*)", s)
    first = first_match.group(1) if first_match else "<empty>"

    # Redirection / heredoc BEFORE first-word, since `printf X > file` has
    # first word `printf` but is really a file write.
    # Redirection variants
    if re.search(r"(?<![<>])>>\s", s):
        return ("BASH_REDIRECT_APPEND", first)
    if re.search(r"<<\s*-?\s*\w+", s):
        return ("BASH_HEREDOC", first)
    if re.search(r"(?<![<>])>\s*\S", s) and not re.search(r"2>\s*\S", s.split(">")[0] if ">" in s else ""):
        # crude: any `> file` that isn't a 2> redirect of stderr only
        return ("BASH_REDIRECT_WRITE", first)

    # sed -i / awk -i inplace
    if re.match(r"^\s*sed\s+[^|&;]*-i(?![a-zA-Z])", s):
        return ("BASH_SED_INPLACE", first)
    if re.match(r"^\s*awk\s+[^|&;]*-i\s+inplace", s):
        return ("BASH_SED_INPLACE", first)  # lump together
    # perl -pi -e
    if re.match(r"^\s*perl\s+[^|&;]*-p?i(?![a-zA-Z])", s):
        return ("BASH_SED_INPLACE", first)

    # git mv / git rm (before generic git)
    if re.match(r"^\s*git\s+mv\b", s):
        return ("BASH_GIT_MV", first)
    if re.match(r"^\s*git\s+rm\b", s):
        return ("BASH_GIT_RM", first)
    if re.match(r"^\s*git\s+apply\b", s):
        return ("BASH_APPLY_DIFF", first)

    # patch
    if first == "patch":
        return ("BASH_PATCH", first)

    # tee into file
    if re.search(r"\btee\s+-?a?\s+\S", s):
        return ("BASH_TEE", first)

    # common mutating first words
    if first == "mv": return ("BASH_MV", first)
    if first == "rm": return ("BASH_RM", first)
    if first == "cp": return ("BASH_CP", first)
    if first == "touch": return ("BASH_TOUCH", first)
    if first == "chmod": return ("BASH_CHMOD", first)
    if first == "chown": return ("BASH_CHMOD", first)  # lump
    if first == "ln": return ("BASH_LN", first)
    if first == "mkdir": return ("BASH_READ_ONLY", first)  # structural, no content
    if first == "rmdir": return ("BASH_RM", first)
    if first == "rsync": return ("BASH_DOWNLOAD", first)
    if first == "tar": return ("BASH_TAR", first)

    # network downloads that write files
    if first in ("curl", "wget"):
        # if -o / -O appears we're writing, classify as download
        if re.search(r"\s-[oO]\s|\s--output\b|\s--output-document\b", s):
            return ("BASH_DOWNLOAD", first)
        return ("BASH_READ_ONLY", first)

    # interpreters with inline code (-c / -e)
    if first in INTERPRETERS:
        tokens = s.split()[1:6]
        for t in tokens:
            if t in INTERPRETER_INLINE_FLAGS:
                return ("BASH_INTERPRETER", first)
        # else treat as script invocation; script may mutate, but we can't tell
        return ("BASH_OTHER_MAYBE", first)

    # read-only heuristic
    if first in READ_ONLY_FIRST_WORDS:
        return ("BASH_READ_ONLY", first)

    # anything unknown: potentially mutating
    return ("BASH_OTHER_MAYBE", first)


TIER_GREEN = "GREEN"   # only JSONL-recoverable tool_uses
TIER_AMBER = "AMBER"   # has Bash mutations that WOULD be recoverable via pattern layer
TIER_RED   = "RED"     # has opaque Bash mutations (interpreter -c, downloads, tar, opaque binaries)

RECOVERABLE_VIA_PATTERNS = {
    "BASH_REDIRECT_WRITE", "BASH_REDIRECT_APPEND", "BASH_HEREDOC",
    "BASH_SED_INPLACE", "BASH_MV", "BASH_RM", "BASH_CP",
    "BASH_TOUCH", "BASH_CHMOD", "BASH_LN",
    "BASH_GIT_MV", "BASH_GIT_RM", "BASH_TEE", "BASH_PATCH",
}
IRRECOVERABLE = {
    "BASH_INTERPRETER", "BASH_DOWNLOAD", "BASH_TAR",
    "BASH_APPLY_DIFF", "BASH_OTHER_MAYBE",
}


def main() -> int:
    if not PROJECTS.is_dir():
        print(f"no such dir: {PROJECTS}", file=sys.stderr)
        return 1

    class_counts: Counter[str] = Counter()
    opaque_first_word: Counter[str] = Counter()
    tier_counts: Counter[str] = Counter()

    # per-session rollups
    sessions_scanned = 0
    sessions_with_any_mutation = 0

    # per-project: tier distribution
    per_project_tiers: dict[str, Counter] = defaultdict(Counter)

    for proj_dir in sorted(PROJECTS.iterdir()):
        if not proj_dir.is_dir():
            continue
        for jsonl in proj_dir.glob("*.jsonl"):
            sessions_scanned += 1
            had_any_mutation = False
            saw_recoverable_bash = False
            saw_opaque_bash = False
            try:
                text = jsonl.read_text(errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("type") != "assistant":
                    continue
                for block in (o.get("message", {}) or {}).get("content", []) or []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
                    name = block.get("name", "")
                    inp = block.get("input", {}) or {}

                    if name == "Write":
                        class_counts["JSONL_WRITE"] += 1
                        had_any_mutation = True
                    elif name == "Edit":
                        class_counts["JSONL_EDIT"] += 1
                        had_any_mutation = True
                    elif name == "MultiEdit":
                        class_counts["JSONL_MULTIEDIT"] += 1
                        had_any_mutation = True
                    elif name == "NotebookEdit":
                        class_counts["JSONL_NOTEBOOKEDIT"] += 1
                        had_any_mutation = True
                    elif name == "Bash":
                        cmd = inp.get("command", "") or ""
                        kls, first = classify_bash(cmd)
                        class_counts[kls] += 1
                        if kls == "BASH_READ_ONLY":
                            continue
                        # everything else is a mutation candidate
                        had_any_mutation = True
                        if kls in RECOVERABLE_VIA_PATTERNS:
                            saw_recoverable_bash = True
                        elif kls in IRRECOVERABLE:
                            saw_opaque_bash = True
                            opaque_first_word[first] += 1
            if had_any_mutation:
                sessions_with_any_mutation += 1
                if saw_opaque_bash:
                    tier = TIER_RED
                elif saw_recoverable_bash:
                    tier = TIER_AMBER
                else:
                    tier = TIER_GREEN
                tier_counts[tier] += 1
                per_project_tiers[proj_dir.name][tier] += 1

    # ------------------ report ------------------ #
    total_mutations = sum(v for k, v in class_counts.items() if k != "BASH_READ_ONLY")
    total_all = sum(class_counts.values())
    print()
    print("─" * 72)
    print("Attribution-coverage distribution")
    print("─" * 72)
    print(f"  sessions scanned:                 {sessions_scanned:>6}")
    print(f"  sessions with any file mutation:  {sessions_with_any_mutation:>6}  "
          f"({100*sessions_with_any_mutation/max(sessions_scanned,1):4.1f}%)")
    print(f"  total tool_use events:            {total_all:>6}")
    print(f"    of which mutating:              {total_mutations:>6}  "
          f"({100*total_mutations/max(total_all,1):4.1f}%)")
    print()

    def pct(n): return f"{100*n/max(total_mutations,1):5.1f}%"

    jsonl_classes = [c for c in class_counts if c.startswith("JSONL_")]
    bash_pattern = [c for c in class_counts if c in RECOVERABLE_VIA_PATTERNS]
    bash_opaque = [c for c in class_counts if c in IRRECOVERABLE]

    def _group_sum(cs): return sum(class_counts[c] for c in cs)

    print("Mutation events by coverage tier")
    print("  " + "─" * 56)
    gs_jsonl = _group_sum(jsonl_classes)
    gs_pattern = _group_sum(bash_pattern)
    gs_opaque = _group_sum(bash_opaque)
    print(f"  {'JSONL tool_use (Write/Edit/…)':<38} {gs_jsonl:>6}  {pct(gs_jsonl)}")
    print(f"  {'Bash recoverable via pattern layer':<38} {gs_pattern:>6}  {pct(gs_pattern)}")
    print(f"  {'Bash opaque (interpreter, network, …)':<38} {gs_opaque:>6}  {pct(gs_opaque)}")
    print(f"  {'TOTAL MUTATION EVENTS':<38} {total_mutations:>6}")
    print()

    print("Detailed class breakdown")
    print("  " + "─" * 56)
    for cls in sorted(class_counts, key=lambda k: (-class_counts[k], k)):
        if cls == "BASH_READ_ONLY":
            continue
        tag = "JSONL" if cls.startswith("JSONL_") else \
              ("pat  " if cls in RECOVERABLE_VIA_PATTERNS else "OPAQUE")
        print(f"  [{tag:<6}] {cls:<26} {class_counts[cls]:>6}  {pct(class_counts[cls])}")
    # Read-only Bash as a separate line for context
    ro = class_counts.get("BASH_READ_ONLY", 0)
    print(f"  {'[ ro ] BASH_READ_ONLY (non-mutating)':<36} {ro:>6}")
    print()

    print("Per-session coverage tier (among sessions with ≥1 mutation)")
    print("  " + "─" * 56)
    mutating = sum(tier_counts.values()) or 1
    for tier in [TIER_GREEN, TIER_AMBER, TIER_RED]:
        n = tier_counts.get(tier, 0)
        bar = "█" * int(50 * n / max(tier_counts.values(), default=1))
        print(f"  {tier:<7} {n:>5}  ({100*n/mutating:4.1f}%)  {bar}")
    print()

    print("Opaque-Bash leaderboard (top 15 first-words)")
    print("  These drive prioritization for future Bash-pattern heuristics.")
    print("  " + "─" * 56)
    for word, n in opaque_first_word.most_common(15):
        print(f"  {word:<24} {n:>6}")
    print()

    print("Top 10 projects by mutation activity (tier breakdown)")
    print("  " + "─" * 56)
    proj_totals = sorted(per_project_tiers.items(),
                         key=lambda kv: -sum(kv[1].values()))[:10]
    for pname, tc in proj_totals:
        total = sum(tc.values())
        g, a, r = tc.get(TIER_GREEN, 0), tc.get(TIER_AMBER, 0), tc.get(TIER_RED, 0)
        print(f"  {pname[:56]:<56} n={total:>4}  "
              f"G={g:>3} A={a:>3} R={r:>3}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
