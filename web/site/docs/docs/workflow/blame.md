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
