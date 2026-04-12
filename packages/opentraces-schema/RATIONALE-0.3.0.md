# Rationale: opentraces-schema 0.3.0

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
