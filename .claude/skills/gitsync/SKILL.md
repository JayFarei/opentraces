---
name: gitsync
description: >
  Sync both git repos in this project: opentraces (root) and closedtraces (kb/).
  Runs the full lifecycle for each — pull, check status, commit any pending changes, push.
  Use when the user says "gitsync", "sync repos", "sync both repos", "commit and push both",
  or "sync opentraces and closedtraces".
---

# gitsync

Sync both repos in this project: `opentraces` (root) and `closedtraces` (`kb/`).

## Repos

| Repo | Path | Remote |
|------|------|--------|
| opentraces | `/Users/jayfarei/src/tries/2026-03-27-community-traces-hf` | github.com/JayFarei/opentraces |
| closedtraces | `/Users/jayfarei/src/tries/2026-03-27-community-traces-hf/kb` | github.com/JayFarei/closedtraces |

## Lifecycle (run for each repo in order)

### 1. Pull (rebase)

```bash
git -C <path> pull --rebase
```

If the pull produces a merge conflict, stop immediately and report it to the user with
the conflicted files. Do not attempt to resolve conflicts automatically.

### 2. Check status

```bash
git -C <path> status --short
```

If clean, skip to the next repo — nothing to commit.

### 3. Commit pending changes

Show the user a brief summary of what's pending (file list + diff stat), then commit:

```bash
git -C <path> add -A
git -C <path> commit -m "$(cat <<'EOF'
<descriptive message summarizing the changes>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

Write a meaningful commit message. Do not use generic messages like "sync" or "update files".
Look at `git diff --cached` to understand what changed and summarize it.

### 4. Push

```bash
git -C <path> push
```

If push is rejected (non-fast-forward), pull --rebase first then push again.

## Output format

After completing both repos, report a one-liner per repo:

```
opentraces:    pulled (2 commits), committed "fix(quality): ..." (3 files), pushed
closedtraces:  already up to date, committed "update discussion log" (1 file), pushed
```

Or if nothing needed:

```
opentraces:    already up to date, nothing to commit
closedtraces:  already up to date, nothing to commit
```

## Important notes

- Always pull before committing to avoid push rejections
- The two repos are fully independent — a failure in one does not block the other
- Never force-push either repo
- Commit message must describe the actual changes, not just say "sync"
