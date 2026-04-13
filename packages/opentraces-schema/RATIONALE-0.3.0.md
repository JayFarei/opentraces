# Rationale: opentraces-schema 0.3.0

> **Deprecated in 0.4.0.** The `Intent` model and `TraceRecord.intent` field
> were removed in 0.4.0. This document is retained for historical context
> only. See `CHANGELOG.md` for the removal note.

Released: 2026-04-12

## Summary

0.3.0 adds a single new top-level session field: `Intent`. It sits parallel
to `Task` and `Outcome`, not nested under either. This file records why.

## Why a new block, and not a `Task.summary` field

`Task.description` carries strict deterministic semantics: "what the user
typed to start this session." It is sourced from the first user prompt or
a CLI arg, never rewritten, and consumers rely on that stability. Overloading
it with an LLM-paraphrased summary would silently change what every downstream
dataset consumer thinks `task.description` means.

An `Intent` separate from `Task` also lets a single session carry **multiple**
interpretations over time: the original user prompt (Task), the LLM-hook
paraphrase at session end (Intent, `source=llm_hook`), a later post-processor
rewrite (Intent, `source=post_processor`), and a human edit (Intent,
`source=user`). Each is distinguishable by `source`, which is a closed enum
so dataset filters stay stable.

## Why session-level, not nested under Task

Intent is a property of the whole session, not of any single task. A session
can progress across multiple tasks (the user pivots mid-session), across
multiple sub-agents, and still have a single coherent "what this was about."
Nesting Intent inside Task would imply otherwise.

This follows the precedent set when `Outcome` was pulled out of `Task` in
earlier versions: outcome is about the session, not the task.

## Why all fields are optional

A trace without summarization must load cleanly. Absence of `Intent` is the
default for:

- traces captured before this feature existed;
- traces where `intent.mode=off` in project config;
- traces where summarization was attempted but failed non-fatally.

Consumers filtering on `Intent.source` should treat `None` as "no summary
present, fall back to Task.description or first-message snippet." This is
what the web viewer and inbox do.

## Why `source` is a closed Literal enum

Downstream dataset consumers — training pipelines, filters on HF datasets —
need a stable vocabulary. Leaving `source` as a free string would invite
`"llm-hook"`, `"llm_hook_v2"`, `"hook"`, and other churn that would silently
break filters. The enum is explicitly extensible via a MINOR bump when a new
provenance category appears.

## Why `intent` is excluded from `compute_content_hash()`

`content_hash` is used for dedup. Running or re-running summarization must
not change a trace's identity. Excluding `intent` from the hash makes
summarization idempotent at the dedup layer.

## Deferred

`ProcessingHistory` and `ProcessingRequest` — structured audit of every
post-processor run — are deliberately not in the schema. Runtime logs go
to stderr today. We add a schema field only when a second post-processor
or a hosted instance needs structured consumption.

## Plan 041 additions (commit-anchored traces + Agent Trace fidelity)

0.3.0 also absorbs plan 041's schema surface so the commit-link and
attribution-fidelity work ship in a single MINOR bump, fully additive.
All new fields default to `None` or empty collections, so 0.2.0-emitted
JSONL continues to deserialize cleanly.

**New top-level `GitLink` model** — a trace carries `git_links: list[GitLink]`
because one trace maps to many commits (rebase, squash, long session) and
one commit maps to many traces (cherry-pick, composition). Each link is
evidence-graded by `tier` (`tool_emitted`, `tool_emitted_with_divergence`,
`overlapping`, `orphan`) and optionally carries liveness booleans
(`commit_reachable`, `content_alive`) computed lazily at read time, not
stamped at commit time, because liveness changes as the tree evolves.

**`TraceRecord.lifecycle`** — `provisional` (session ended, not yet
correlated) vs `final` (revision-anchored). Follows Agent Trace RFC #25.
Defaults to `provisional` so in-flight traces are valid.

**`Attribution.revision`** — pins an attribution block to the commit it
describes. Enables `opentraces blame` to walk from a line at HEAD back
through `git blame` → revision → revision-pinned attribution.

**`Attribution.unaccounted_files`** — surfaces Bash-applied edits (sed,
codemods) whose hunks appear in the commit diff but map to no tracked
Edit/Write tool call. Low-confidence by construction.

**`AttributionRange.original`** — records the agent's pre-divergence
coordinates and hash when a formatter or human rewrote the output
(RFC #5). Divergence is first-class, not hidden evidence.

**`AttributionRange.change_type`** — `addition` (default, most cases),
`modification`, `deletion` (RFC #11). Enables agent-driven deletions to
be attributed explicitly.

**`AttributionRange.contributor`** — per-range override for the enclosing
conversation's contributor. Used to stamp `mixed` on divergent ranges
while keeping the surrounding conversation's `ai` contributor intact.

**`AttributionConversation.ids`** — provider-native identifiers (RFC #9).
A map from provider to message id (or list of ids), so Anthropic
`msg_01xyz` or an OpenAI response chain survives round-trip without
forcing any single provider's shape onto the schema.

**`AttributionConversation.related`** — baseline vocabulary (RFC #16)
for linking to plans, issues, PRs, or documentation that shaped the
conversation. List of `{type, url}` entries; leaves us a hook point for
Plan Records without committing to their schema yet.

**`Task.repository_url`** — canonical remote URL (RFC #22). `repository`
stays as `owner/repo`; the URL is the portable identifier for downstream
consumers that don't want to reconstruct it.

### Why one bump, not two

Plan 041 originally implied a separate 0.4.0 bump once the commit-link
work landed. Shipping the fidelity fields (range.original, change_type,
unaccounted_files, revision) alongside the commit-link fields (git_links,
lifecycle) in one 0.3.0 avoids an artificial coordination window where
one is valid but the other isn't. Everything is additive; consumers that
ignore the new fields keep working.
