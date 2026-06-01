---
name: skill-verifier-candidates-v1
description: Mine latest-generation skill_invocation evidence into verifier candidate rows (one row per skill x archetype) for the Skill Verifier Factory.
mode: agent-skill
---

# skill-verifier-candidates-v1

Project the Trace Index `skill_invocation` read side into **verifier candidate**
rows: one row per `(skill, archetype)` describing how strong the trace-grounded
evidence is for building a verifier of that archetype, which contract markers are
most deficient, whether a leakage-safe Dtrain/Dsel/Dtest split is feasible, and the
verifier-creator interview (with evidence-inferred defaults).

This is the canonical workflow surface over
`opentraces.consumers.verifier_factory.mine_verifier_candidates`; the
`opentraces workflow verifier-factory` consumer reads these rows (and emits the
packages). Recommendations are advisory: no verifier is generated or promoted
without explicit human approval.

## Scope

- `project` (optional): restrict mining to one project slug.
- `skills` (optional list): restrict to specific skills (default: every skill that
  has a registered archetype).
- `min_usable_episodes` (optional int, default 30): data-gap threshold.

## Output

Newline-delimited JSON rows validated against `schemas/row.schema.json`
(`schema_version: opentraces.skill_verifier_candidates_row.v1`).
