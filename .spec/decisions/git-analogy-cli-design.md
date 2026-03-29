---
schema_version: "1.0"
title: Git-Analogy CLI Design
scope: src/opentraces/cli.py
date_detected: 2026-03-28
confidence: medium
---

# Git-Analogy CLI Design

## What

The CLI commands mirror the git workflow: `init`, `status`, `parse` (like `add`), `review` (like `diff`), `commit`, `push`. The git commit message from `1bed1fb` explicitly names this: "feat: git-analogy CLI (init/status/review/push) + lazytraces TUI."

## Why

The target audience (developers who use coding agents) already understands the git mental model. By mapping trace lifecycle operations to familiar git commands, the CLI reduces cognitive overhead. The trace lifecycle (discovered -> parsed -> staged -> reviewing -> approved -> committed -> uploading -> uploaded) parallels git's staging area workflow.

## Tradeoff

**Gained**: Familiar mental model for the target audience. Reduces documentation burden since users can reason by analogy. The commit/push separation enables batching traces before upload, similar to how git enables batching changes before push.

**Lost**: Some semantic mismatch: `opentraces commit` bundles approved traces into a commit group, which is not exactly the same as `git commit`. Users may expect `parse` to be more like `add` (explicit file selection) when it actually runs the full ingestion pipeline.

## Alternatives Rejected

1. **CRUD-style commands** (create, list, update, delete): Less familiar, more generic.
2. **Pipeline-stage commands** (ingest, scan, enrich, stage, upload): More technically accurate but requires learning a new vocabulary.

## Source

- Git commit `1bed1fb`: "feat: git-analogy CLI (init/status/review/push) + lazytraces TUI"
- `src/opentraces/cli.py` (command structure)
- `src/opentraces/state.py` (TraceStatus lifecycle mirrors git staging)

## Transferability

Medium. The git-analogy pattern works when: (a) the target audience is developers, (b) the workflow has a natural staging/review/publish lifecycle, and (c) the system involves local processing before remote distribution. It is less useful for non-developer audiences or workflows that do not map to the stage-commit-push model.
