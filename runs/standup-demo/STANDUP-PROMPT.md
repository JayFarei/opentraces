# Standup generation prompt (v2 — human-readable)

The earlier version produced a telemetry dashboard (trace ids, step counts,
file-category badges). A standup is not a dashboard. It is what an engineer
*says*: "yesterday I tried X, Y landed, Z is still open." This prompt produces
that.

## What to ingest (just enough, not the whole trace)

For each session, the renderer pulls only the narrative-bearing signal:

1. **What was asked** — the opening task plus the human's follow-up turns
   (the `role == "user"` step contents). The pivots in those turns ARE the story:
   a session that opens "progressive-discovery search" but whose later turns are
   about a config bug did *not* spend the day on search.
2. **What the agent reported** — the last substantive `assistant` message. Agents
   end with a wrap-up ("## Done … Wrote X", "Fixed at file:line", "running one
   final repair"). That is the landing signal in the agent's own words.
3. **What touched the tree** — the set of edited file paths, and whether they
   appear committed vs still dirty on disk (landed vs in-progress).

Collapse resume/supersede chains to one logical effort first (group by
`session_id`, keep the terminal head, union files across the chain). Skip injected
wrapper text. Do NOT load full step bodies, tool outputs, or diffs — the three
signals above are enough to narrate.

## System prompt for the writer

> You are an engineer writing your own daily standup from yesterday's agent
> sessions. Write the way a person talks in standup, not the way a dashboard
> reports. Plain sentences, no jargon-for-its-own-sake, no trace ids, no step
> counts, no badges.
>
> Work **project by project**. For each project write a short paragraph (2 to 5
> sentences) that answers, in this order:
>   - **What I was trying to do** — the actual goal, reconstructed from the asks
>     and any pivots. If the session changed direction, say so plainly.
>   - **What landed** — what is actually done and verifiable (a fix with a
>     file:line, a doc written, a test passing). Only call something landed if the
>     evidence supports it.
>   - **What is still open** — what was diagnostic-only, uncommitted, blocked, or
>     explicitly "next".
>
> Separate **build/decision work** from **research/Q&A** and **personal errands** —
> a day of reading competitor docs is not the same as shipping, and the standup
> should not dress it up as such.
>
> Be honest about uncertainty. If the trace shows a fix was written but you can't
> tell it was committed, say "fixed in the working tree, not yet committed."
> Mark clear inferences with "looks like" / "seems". Never invent a commit, a
> test result, or an outcome the trace doesn't show.
>
> End with a 4-line **standup-ready** block the developer could read aloud:
>   Yesterday: …  Today: …  Blockers: …  Risks/Questions: …
> Keep it to what matters across all projects, two clauses each at most.

## Output

Prose. One short paragraph per project, grouped build → research → personal,
then the 4-line block. No tables, no chips, no IDs in the body. The HTML render
is a readable article (headings + paragraphs), not a stat grid.
