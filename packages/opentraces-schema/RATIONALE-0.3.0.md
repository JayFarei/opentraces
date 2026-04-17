# Rationale: opentraces-schema 0.3.0

Released: 2026-04-16

## Summary

0.3.0 is a coherent, fully additive bump over `0.2.x`. It folds together
two tracks of work from this cycle: commit-correlation (plan 041) and
richer attribution fidelity. Consumers that ignore the new fields keep
working; 0.2.x-emitted JSONL continues to deserialize cleanly with
`lifecycle="provisional"`, `generation_index=0`, `git_links=[]`, and
null-valued new Attribution fields.

## Commit correlation (plan 041)

**New top-level `GitLink` model.** A trace carries `git_links: list[GitLink]`
because one trace maps to many commits (rebase, squash, long session)
and one commit maps to many traces (cherry-pick, composition). Each
link is evidence-graded by `tier` (`tool_emitted`,
`tool_emitted_with_divergence`, `overlapping`, `orphan`) and optionally
carries liveness booleans (`commit_reachable`, `content_alive`)
computed lazily at read time, not stamped at commit time, because
liveness changes as the tree evolves.

**`TraceRecord.lifecycle`.** `provisional` (session ended, not yet
correlated) vs `final` (revision-anchored). Follows Agent Trace RFC
#25. Defaults to `provisional` so in-flight traces are valid.

**`TraceRecord.generation_index`.** Monotonic counter per `session_id`.
Generations are replacement snapshots, not stitchable supersets, since
later generations may have different redactions, enrichments, or
security-pipeline output. Consumers resolving "latest" should group by
`session_id` and take `max(generation_index)`.

## Richer attribution

**`Attribution.revision`.** Pins an attribution block to the commit it
describes. Enables `opentraces blame` to walk from a line at HEAD back
through `git blame`, revision, and revision-pinned attribution.

**`Attribution.unaccounted_files`.** Surfaces Bash-applied edits (sed,
codemods) whose hunks appear in the commit diff but map to no tracked
Edit/Write tool call. Low-confidence by construction.

**`AttributionRange.original`.** Records the agent's pre-divergence
coordinates and hash when a formatter or human rewrote the output
(RFC #5). Divergence is first-class, not hidden evidence.

**`AttributionRange.change_type`.** `addition` (default, most cases),
`modification`, `deletion` (RFC #11). Enables agent-driven deletions
to be attributed explicitly.

**`AttributionRange.contributor`.** Per-range override for the
enclosing conversation's contributor. Used to stamp `mixed` on
divergent ranges while keeping the surrounding conversation's `ai`
contributor intact.

**`AttributionConversation.ids`.** Provider-native identifiers (RFC #9).
A map from provider to message id (or list of ids), so Anthropic
`msg_01xyz` or an OpenAI response chain survives round-trip without
forcing any single provider's shape onto the schema.

**`AttributionConversation.related`.** Baseline vocabulary (RFC #16)
for linking to plans, issues, PRs, or documentation that shaped the
conversation. List of `{type, url}` entries; leaves us a hook point
for Plan Records without committing to their schema yet.

## Task, metrics

**`Task.repository_url`.** Canonical remote URL (RFC #22). `repository`
stays as `owner/repo`; the URL is the portable identifier for
downstream consumers that don't want to reconstruct it.

**`Metrics.total_cache_read_tokens`, `Metrics.total_cache_creation_tokens`.**
Session-level aggregates rolled up from per-step `TokenUsage`. Make
cross-trace cache economics queryable without reducing over every step.

## Why one bump, not two

Plan 041 originally implied a separate 0.4.0 bump once the commit-link
work landed. Shipping the fidelity fields (`range.original`,
`change_type`, `unaccounted_files`, `revision`) alongside the
commit-link fields (`git_links`, `lifecycle`) in one 0.3.0 avoids an
artificial coordination window where one is valid but the other isn't.
Everything is additive; consumers that ignore the new fields keep
working.

## Deferred

`ProcessingHistory` and `ProcessingRequest`, a structured audit of
every post-processor run, are deliberately not in the schema. Runtime
logs go to stderr today. We add a schema field only when a second
post-processor or a hosted instance needs structured consumption.
