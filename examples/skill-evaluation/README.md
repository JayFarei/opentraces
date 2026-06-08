# Skill Evaluation Example

## Task

Inspect a synthetic verifier-candidate packet for one skill. The example shows
the public shape of an evaluation artifact: skill episodes, archetypes, evidence
markers, and the questions a verifier should answer before a skill change is
trusted.

## Inputs

- `skill-verifier-candidate.sample.json` - a synthetic public-safe candidate
  packet for the `review` skill.

## Run

Inspect the committed public fixture:

```bash
jq '{schema_version,total_skill_invocation_units,skills:[.skills[]|{skill_id,usable_episodes,sources,archetypes,leakage_safe_split}]}' \
  examples/skill-evaluation/skill-verifier-candidate.sample.json
```

Run against local retained skill evidence:

```bash
opentraces workflow templates
opentraces workflow create skill-verifier-candidates-example --template skill-verifier-candidates-v1 --json
opentraces skill-verifier status review --json
```

## Expected Output

The packet should identify the skill being evaluated, summarize usable episodes,
list one or more behavior archetypes, and preserve enough provenance to avoid
training on the same evidence used for scoring. The sample is not a calibrated
verifier and does not claim a pass verdict. On a live near-one-class bucket, a
healthy status may honestly be `blocked_*` until trustworthy labels exist.

## Public Safety

The committed packet is synthetic. Real bucket-derived skill episodes, rollout
notes, and verifier-factory artifacts stay in the private knowledge base unless
they are explicitly redacted into a public fixture.
