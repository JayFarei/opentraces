# Blame

When agents write the code, `git blame` tells you the commit, not the prompt. `opentraces trail blame` closes that gap: given a commit it returns the sessions that produced the committed bytes; given a trace id it returns the commits that carry that session's output. See [How It Works](#how-it-works) for the mechanism.

> **Experimental — not 100% accurate yet.** Attribution rests on three moving parts (capture hook, post-commit correlator, and watcher-driven audit refs) and any one of them falling behind produces incomplete or misleading results. Treat coverage numbers as best-effort until the pipeline stabilises, and cross-check against `opentraces doctor`, `opentraces trail graph`, and the raw `refs/notes/opentraces` notes when a number looks wrong.

## Prerequisites

Blame needs three things to produce trustworthy output:

1. **Capture hook installed.** Claude Code sessions are captured automatically after `opentraces init` (or `opentraces setup claude-code`). Without this there is no audit ref to blame against.
2. **Post-commit hook installed.** This attaches a note under `refs/notes/opentraces` linking each commit to the contributing traces at the moment the commit lands.

   ```bash
   opentraces setup git
   ```

3. **Watcher installed.** The background watcher polls the repo, rebuilds the attribution cache from the audit ref, and keeps the entity graph in sync. Running blame without the watcher means the cache can lag HEAD, percentages will read low, traces will read as orphans, and hook-linked commits will not pick up per-line attribution. Install it once per machine:

   ```bash
   opentraces setup watcher install
   ```

   `opentraces doctor` surfaces the watcher status alongside capture and git-hook state; if any of the three is red, blame output should be treated as preliminary.

Old commits cannot be backfilled by the hook — the hook only sees commits after install. Two escape hatches exist:

- `opentraces backfill --rebuild` clears the per-line attribution cache and re-attributes everything reachable from `HEAD` using the stored tool-call data. Run this after a rebase, squash, or any time the on-disk cache drifts from the audit ref.
- `opentraces git-backfill` walks first-parent history, re-runs the live correlator, and writes `refs/notes/opentraces` + per-trace `git_links` for any old commits the hook missed. Useful after a first-time install of the post-commit hook, or after a period where the hook was silently failing (for example, the pre-`0.3.0` PATH-silent-failure bug).

## Graph View

`opentraces trail graph` renders the git log as a spine. Each commit shows the sessions that contributed to it, with inline entity summaries and a coverage percentage.

```bash
opentraces trail graph --limit 8
```

![opentraces trail graph --limit 8](/docs/assets/blame/graph-limit-8.png)

Reading the spine:

| Glyph | Meaning |
|---|---|
| `●` | Commit node |
| `╭┄` / `├┄` | Session contributing to the next commit |
| `├╯` | End of a commit's session group |
| `c:<sha>` | Commit id (prefix-resolvable by `opentraces trace get`, `opentraces trail blame`) |
| `s:<id>` | Session id (trace prefix) |
| `+N ~M -K fns` | Added / modified / deleted functions or entities |
| `100%` | Fraction of the commit's diff covered by bytes recorded in the session's audit ref (Edit/Write tool calls plus reconstructed Bash effects) |

Commits with no attached sessions (`c:7c3b1927 marketing skill`) appear as bare nodes — either pre-hook commits, or commits whose hunks came from mutations the reconstructor could not prove (see `pre-audit` under [How It Works](#how-it-works)).

### Graph flags

```bash
opentraces trail graph --trace <id>                     # Pivot to trace-primary view
opentraces trail graph --since HEAD~20 --until HEAD     # Scope by ref range
opentraces trail graph --entities                       # Expand entity subline per session
opentraces trail graph --all                            # Disable pagination
```

## Blame for a Commit

`opentraces trail blame <sha>` resolves one commit to its contributing traces, with per-trace diff coverage, entity-level deltas, and per-file attribution counts.

```bash
opentraces trail blame ac019172
```

![opentraces trail blame ac019172](/docs/assets/blame/blame-commit.png)

The output is four sections:

1. **Commit header.** Overall coverage: how many diff lines map to any traced tool call, how many traces contributed, how many files were touched.
2. **Per-trace rows.** Each `◆ s:<id>` row shows the session's short slug, the model, and its slice of the diff (`<N> of <M> diff lines . <pct>%`). Added/modified entities are listed inline.
3. **File list.** Every file in the commit with its attributed-vs-pre-audit line counts. `pre-audit` lines exist in the file but predate the attribution cache — they'll be fully attributed once `opentraces backfill --rebuild` runs.
4. **Attribution cache reference** (when `--json` is passed): the audit ref and revision so consumers can round-trip back to raw evidence.

Traces that the hook linked but whose per-line attribution isn't in the cache yet appear in a separate **Hook-linked traces** block below the per-trace rows; run `opentraces backfill` to promote them into the main breakdown.

### Blame flags

```bash
opentraces trail blame <sha>                            # Commit-scoped summary
opentraces trail blame c:<sha> <path>                   # Single-file slice
opentraces trail blame <sha> <path> --lines             # Per-line (git-blame-style)
opentraces trail blame <sha> --entities                 # Expand per-trace entity lists
opentraces trail blame <sha> --json                     # Structured output for consumers
```

## Blame for a Trace (inverse blame)

Given a trace id instead of a commit, `opentraces trail blame` walks the relationship in the other direction: which commits carry this session's output.

```bash
opentraces trail blame t:2cfe7e14                       # Canonical (ingested) trace
opentraces trail blame s:6606fc1f                       # Attribution-only session (upstream, pre-init, or forked)
opentraces trail blame 2cfe7e14-…-full-uuid             # Bare hyphenated UUID auto-detects
opentraces trail blame t:2cfe7e14 --include-overlapping # Include weak file+time links
opentraces trail blame t:2cfe7e14 --json                # Structured output for consumers
```

The argument accepts either prefix form. `t:` resolves against canonical traces in the local inbox; `s:` resolves against the staging session ids or attribution-cache entries (useful for forks, or for sessions that never landed in the inbox). A bare hyphenated UUID auto-detects as a trace id; a bare hex string is treated as a commit first and falls back to trace resolution if the commit does not exist.

Output is a trace header and a list of commits this trace contributed to:

![opentraces trail blame t:2cfe7e14](/docs/assets/blame/blame-trace.png)

Rows with line-level attribution show real line counts and a coverage percentage; hook-linked rows (where the post-commit hook recorded a link but the attribution cache doesn't yet have per-line data) show a tier badge instead. `--include-overlapping` additionally shows commits with only a weak file+timestamp overlap — off by default because that's coincidence rather than contribution.

`--lines`, `--entities`, and a `PATH` argument are commit-mode only; trace-mode output is always summary-level.

## Web Viewer

`opentraces dataset review <name> --web` exposes the same blame data in the browser. Switch to the `graph` tab to browse the commit spine on the left and the per-commit blame on the right.

![opentraces dataset review --web — graph / blame view](/docs/assets/blame/web-blame-view.png)

The viewer is keyboard-first: `j`/`k` navigates commits, `enter` loads the blame panel, `q` quits. The trace-side panel mirrors the CLI, with hook-linked commits collapsed under a `▸ N hook-linked commits (no line counts)` disclosure so the primary list stays dense with line-attributed rows.

## Evidence Tiers

Every `GitLink` from trace to commit is evidence-graded. Consumers can filter datasets to a tier floor and drop orphan traces.

| Tier | Meaning |
|---|---|
| `tool_emitted` | Bytes recorded in the session's audit ref (from Edit/Write tool calls or reconstructed Bash effects) appear verbatim in the commit's staged hunks. Gold-standard signal. |
| `tool_emitted_with_divergence` | File set lines up, but the committed bytes don't hash-match — a formatter, pre-commit hook, or human rewrote the output. Combine with `AttributionRange.original` for recovery. |
| `overlapping` | File-set and time-window overlap only, no hash match. Treat as weakly linked. |
| `orphan` | No viable commit link. Trace is kept, but don't claim authorship. |

The tier appears in `git_links[].tier` on every trace and in the `--json` output of `blame` and `graph`. See [Outcome & Attribution](/docs/schema/outcome-attribution) for the full evidence model and RFC references.

## How It Works

`opentraces trail blame` isn't a wrapper around `git blame`. It builds a parallel Git history — an *audit ref* — that records exactly what each session wrote, then blames against that. You don't need this section to use blame, but it helps when reading the raw refs, debugging coverage, or thinking about where semantic attribution is headed.

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

Each time a session mutates a tracked file — through an `Edit`/`Write` tool call or through a Bash command whose effect the reconstructor can prove (redirects, heredocs, `mv`/`cp`/`rm`, `sed -i`, `echo`/`printf`/`cat` redirects) — the capture hook:

1. **Snapshot → blob.** Captures the file's post-mutation bytes. Content-addressed, so identical content never stores twice.
2. **Assemble → tree.** Combines touched files into a tree matching the project layout at that moment.
3. **Seal → commit.** Writes a synthetic commit authored by `<trace_id>@opentraces.local` to `refs/opentraces/audit/<project_id>`. One commit per snapshot.
4. **Correlate → notes.** When a real commit lands on `main`, the post-commit hook from `opentraces setup git` writes a note to `refs/notes/opentraces` linking the real commit to the audit commits whose bytes appear in its staged hunks — whether the commit lands during the session or much later, as long as the session's stored bytes still show up.

Bash mutations whose effect the reconstructor cannot prove deterministically (arbitrary scripts, binary producers, commands with external state) fall through to `pre-audit`: the file is tracked but the line's authorship is left unclaimed rather than fabricated.

All four steps use native Git. Nothing lives in a parallel database, there is no custom file format, and no server roundtrip is required. `git log refs/opentraces/audit/<project_id>` just works, and `git notes --ref=refs/notes/opentraces show <sha>` shows the correlation directly.

### Blame derives from the audit ref

With the audit graph in place, per-line attribution reduces to a familiar primitive:

```bash
git blame --line-porcelain <path> <audit_ref>
```

...run against the audit ref instead of `main`. Every line comes back attributed to the session that wrote it, because the author email is `<trace_id>@opentraces.local`. `opentraces trail blame` wraps this with the correlation from `refs/notes/opentraces` so you can start from either side — a commit SHA or a trace ID — and land on the other.

The [evidence tiers](#evidence-tiers) above aren't subjective labels either: they're hash comparisons between the audit ref's tree and the real commit's tree.

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
opentraces trail blame <sha> src/auth.py   # Find the session(s)
opentraces trace get s:<id>          # Read the prompt + reasoning
```

### "Which commits carry this session's output?"

```bash
opentraces trail blame t:<trace-id>                        # Canonical inbox trace
opentraces trail blame s:<session-id>                      # Upstream / fork / pre-init
opentraces trail blame t:<trace-id> --include-overlapping  # Include weak file+time links
```

### "Rebuild attribution after a rebase or squash"

```bash
opentraces backfill --rebuild
```

This clears the cache and re-attributes every commit reachable from `HEAD` using the stored tool-call data. The underlying trace JSONL files are not modified — generations with the same `session_id` are replacement snapshots, not appends.

### "I just installed the post-commit hook, link my older commits"

```bash
opentraces git-backfill
opentraces git-backfill --max-commits 2000 --window-hours 48
```

Walks first-parent history and retro-correlates inbox traces against each commit. Writes `refs/notes/opentraces` and persists `git_links` onto the staged trace JSONLs so old commits start showing up in `ot trail graph`, `ot trail blame c:<sha>`, and `ot trail blame t:<id>`. Safe to re-run: notes dedupe on append and `git_links` dedupe before rewrite.

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
- [CLI Reference — `trail blame`, `trail graph`, `backfill`](/docs/cli/commands)
- [Carol Nichols, "Taming Git complexity with Rust and Gitoxide" (FOSDEM 2026)](https://www.youtube.com/watch?v=iSAMvE3yzfc) — the four-primitive framing this page's "How It Works" section is built on.
