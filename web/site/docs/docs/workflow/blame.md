# Blame

Blame resolves any commit or shipped line back to the agent session(s) that produced it.

When agents write the code, `git blame` tells you the commit, not the prompt. `opentraces blame` closes that gap: every commit maps to the sessions whose `Edit`/`Write` tool calls produced its staged hunks, each link tagged with an evidence tier so consumers can filter by how tightly a session actually wrote the committed bytes.

## Prerequisites

Blame needs two things:

1. **Capture hook installed.** Claude Code sessions are captured automatically after `opentraces init` (or `opentraces setup claude-code`).
2. **Post-commit hook installed.** This attaches a note under `refs/notes/opentraces` linking each commit to the contributing traces.

   ```bash
   opentraces setup git
   ```

Old commits cannot be backfilled automatically, correlation starts with the first commit after install. Run `opentraces backfill --rebuild` to re-attribute everything from `HEAD` using cached tool-call data.

## Graph View

`opentraces graph` renders the git log as a spine. Each commit shows the sessions that contributed to it, with inline entity summaries and a coverage percentage.

```bash
opentraces graph --limit 8
```

![opentraces graph --limit 8](/docs/assets/blame/graph-limit-8.png)

Reading the spine:

| Glyph | Meaning |
|---|---|
| `●` | Commit node |
| `╭┄` / `├┄` | Session contributing to the next commit |
| `├╯` | End of a commit's session group |
| `c:<sha>` | Commit id (prefix-resolvable by `opentraces show`, `opentraces blame`) |
| `s:<id>` | Session id (trace prefix) |
| `+N ~M -K fns` | Added / modified / deleted functions or entities |
| `100%` | Fraction of the commit's diff covered by traced Edit/Write tool calls |

Commits with no attached sessions (`c:7c3b1927 marketing skill`) appear as bare nodes — either pre-hook commits, or commits whose hunks came from non-tracked edits (manual rewrites, Bash codemods).

### Graph flags

```bash
opentraces graph --trace <id>                     # Pivot to trace-primary view
opentraces graph --since HEAD~20 --until HEAD     # Scope by ref range
opentraces graph --entities                       # Expand entity subline per session
opentraces graph --all                            # Disable pagination
```

## Blame for a Commit

`opentraces blame <sha>` resolves one commit to its contributing traces, with per-trace diff coverage, entity-level deltas, and per-file attribution counts.

```bash
opentraces blame ac019172
```

![opentraces blame ac019172](/docs/assets/blame/blame-commit.png)

The output is four sections:

1. **Commit header.** Overall coverage: how many diff lines map to any traced tool call, how many traces contributed, how many files were touched.
2. **Per-trace rows.** Each `◆ s:<id>` row shows the session's short slug, the model, and its slice of the diff (`<N> of <M> diff lines . <pct>%`). Added/modified entities are listed inline.
3. **File list.** Every file in the commit with its attributed-vs-pre-audit line counts. `pre-audit` lines exist in the file but predate the attribution cache — they'll be fully attributed once `opentraces backfill --rebuild` runs.
4. **Attribution cache reference** (when `--json` is passed): the audit ref and revision so consumers can round-trip back to raw evidence.

### Blame flags

```bash
opentraces blame <sha>                            # Commit-scoped summary
opentraces blame <sha> <path>                     # Single-file slice
opentraces blame <sha> <path> --lines             # Per-line (git-blame-style)
opentraces blame <sha> --entities                 # Expand per-trace entity lists
opentraces blame <sha> --json                     # Structured output for consumers
```

## Web Viewer

`opentraces web` exposes the same blame data in the browser. Switch to the `graph` tab to browse the commit spine on the left and the per-commit blame on the right.

![opentraces web — graph / blame view](/docs/assets/blame/web-blame-view.png)

The viewer is keyboard-first: `j`/`k` navigates commits, `enter` loads the blame panel, `q` quits.

## Evidence Tiers

Every `GitLink` from trace to commit is evidence-graded. Consumers can filter datasets to a tier floor and drop orphan traces.

| Tier | Meaning |
|---|---|
| `tool_emitted` | Hashes emitted by Edit/Write tool calls appear verbatim in the commit's staged hunks. Gold-standard signal. |
| `tool_emitted_with_divergence` | File set lines up, but the committed bytes don't hash-match — a formatter, pre-commit hook, or human rewrote the output. Combine with `AttributionRange.original` for recovery. |
| `overlapping` | File-set and time-window overlap only, no hash match. Treat as weakly linked. |
| `orphan` | No viable commit link. Trace is kept, but don't claim authorship. |

The tier appears in `git_links[].tier` on every trace and in the `--json` output of `blame` and `graph`. See [Outcome & Attribution](/docs/schema/outcome-attribution) for the full evidence model and RFC references.

## How It Works

`opentraces blame` isn't a wrapper around `git blame`. It builds a parallel Git history — an *audit ref* — that records exactly what each session wrote, then blames against that. You don't need this section to use blame, but it helps when reading the raw refs, debugging coverage, or thinking about where semantic attribution is headed.

### Git in four primitives

Git is four stacked concepts. Knowing them makes everything else obvious.

| Primitive | What it is |
|---|---|
| **Blob** | File content plus a hash. No name, no metadata. Content-addressable, so identical bytes dedupe automatically. |
| **Tree** | A directory snapshot — a list of `(name, mode, blob-or-tree-hash)` entries. |
| **Commit** | A pointer to a root tree plus metadata (author, message, parent(s)). Commits form a DAG through their parents. |
| **Reference** | A named pointer to a commit. `main`, `HEAD`, `refs/notes/*` — all just names; updating a branch means moving the pointer. |

Git stores **snapshots, not diffs**. A diff is two trees compared on demand. That matters for attribution: we don't need a parallel database to track who wrote what — we can build one out of the same primitives and run existing Git tools against it.

### Why `git blame` alone isn't enough

`git blame src/auth.py` tells you which commit last touched each line and who authored that commit. When an agent writes the code and a human commits it, blame still points at the human. The reasoning, the prompt, and the session context are all discarded at commit time.

We need a second authorship layer: one where the author is the *session*, not the committer.

### A parallel audit history

opentraces builds that second layer out of the same primitives:

```
main branch (refs/heads/main)
    c:abc123  "feat: auth flow"      by alice
    c:def456  "fix: token refresh"   by bob
    c:ghi789  "docs: update"         by alice
        │
        │   correlated via refs/notes/opentraces
        ▼
audit history (refs/opentraces/audit/<project_id>)
    t:s1abc   "Edit src/auth.py"     by <trace_id>@opentraces.local
    t:s2def   "Write src/token.py"   by <trace_id>@opentraces.local
    t:s3ghi   "Edit README.md"       by <trace_id>@opentraces.local
```

Each time a session runs an Edit or Write tool call, the capture hook:

1. **Snapshot → blob.** Captures the file's post-edit bytes. Content-addressed, so identical content never stores twice.
2. **Assemble → tree.** Combines touched files into a tree matching the project layout at that moment.
3. **Seal → commit.** Writes a synthetic commit authored by `<trace_id>@opentraces.local` to `refs/opentraces/audit/<project_id>`. One commit per snapshot.
4. **Correlate → notes.** When a real commit lands on `main`, the post-commit hook from `opentraces setup git` writes a note to `refs/notes/opentraces` linking the real commit to the audit commits whose bytes appear in its staged hunks.

All four steps use native Git. Nothing lives in a parallel database, there is no custom file format, and no server roundtrip is required. `git log refs/opentraces/audit/<project_id>` just works, and `git notes --ref=refs/notes/opentraces show <sha>` shows the correlation directly.

### Blame derives from the audit ref

With the audit graph in place, per-line attribution reduces to a familiar primitive:

```bash
git blame --line-porcelain <path> <audit_ref>
```

...run against the audit ref instead of `main`. Every line comes back attributed to the session that wrote it, because the author email is `<trace_id>@opentraces.local`. `opentraces blame` wraps this with the correlation from `refs/notes/opentraces` so you can start from either side — a commit SHA or a trace ID — and land on the other.

The [evidence tiers](#evidence-tiers) above aren't subjective labels either. They're hash comparisons between the audit ref's tree and the real commit's tree. If the blobs match, the evidence is `tool_emitted`; if the file set matches but the bytes don't, a formatter or human rewrote the output and the tier becomes `tool_emitted_with_divergence`; and so on.

### Where this is going: semantic attribution

Line-level blame is the baseline. The next question — "did this *function* come from that session, even after it moved, got rebased, or was partially rewritten?" — is a three-way tree merge:

- **base** = tree before the session ran
- **ours** = base plus just that session's Edit ranges applied
- **theirs** = the real committed tree

The merge result tells you whether the committed code still carries the session's change, partially carries it (touched by a formatter, rebased, cherry-picked, or refactored), or diverged entirely. `AttributionRange.content_hash` is the hook we're preparing for this direction.

## Common Flows

### "Why did this line change?"

```bash
git blame src/auth.py | head -5      # Find the commit
opentraces blame <sha> src/auth.py   # Find the session(s)
opentraces show s:<id>               # Read the prompt + reasoning
```

### "Rebuild attribution after a rebase or squash"

```bash
opentraces backfill --rebuild
```

This clears the cache and re-attributes every commit reachable from `HEAD` using the stored tool-call data. The underlying trace JSONL files are not modified — generations with the same `session_id` are replacement snapshots, not appends.

### "Filter a pushed dataset to tool-emitted traces"

```python
from datasets import load_dataset

ds = load_dataset("owner/my-traces", split="train")
clean = ds.filter(
    lambda r: any(link["tier"] == "tool_emitted" for link in r.get("git_links", []))
)
```

## See Also

- [Schema — Outcome & Attribution](/docs/schema/outcome-attribution) — `GitLink`, `Attribution.revision`, `AttributionRange`
- [Schema — Versioning](/docs/schema/versioning) — schema 0.3.0 additive changes
- [CLI Reference — `blame`, `graph`, `backfill`](/docs/cli/commands)
- [Carol Nichols, "Taming Git complexity with Rust and Gitoxide" (FOSDEM 2026)](https://www.youtube.com/watch?v=iSAMvE3yzfc) — the four-primitive framing this page's "How It Works" section is built on.
