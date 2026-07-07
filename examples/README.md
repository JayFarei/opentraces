# Examples

`examples/` is the only public examples/demo folder.

Every committed example must be safe for the public repository and include a
clear task: what the example demonstrates, what input it expects, how to run or
inspect it, and what output to expect. Do not commit real bucket content, local
paths, private prompts, release logs, or work-in-progress notes here.

## Included examples

- `standup/` — task: turn sanitized bucket-session summaries into a narrative
  daily standup article.
- `trace-capsule/` — task: inspect a shareable capsule shape for attaching a
  bounded, redacted trace episode to an issue.
- `intent-pr/` — task: inspect a PR report that ties branch commits back to
  originating sessions and explains the why beside the diff.
- `spotlight/` — task: inspect a bounded trace-search packet and the follow-up
  commands that load the smallest useful slice.
- `skill-evaluation/` — task: inspect a synthetic verifier-candidate packet for
  scoring whether skill changes improve outcomes.
- `trace-intelligence/eval/` — task: inspect the synthetic labeled evaluation
  packet for context-waste, run-intelligence, and trace-compare signals.
