# BLOCKED: Existing-Bucket Case Study

Attempted path: `opentraces workflow skill-intelligence --dry-run --out runs/skill-intelligence-pipeline --json`

Evidence gathered: `runs/skill-intelligence-pipeline/corpus-audit.json` found
`total_skill_invocation_units=0`, selected the fallback `opentraces` skill, and
set `seeded_ci_corpus_required=true`.

Blocker: the current local bucket does not contain enough Trace Index
`skill_invocation` units to support an existing-bucket Dtrain/Dsel/Dtest case
study.

Input that would unlock progress: captured Claude/Codex traces with normalized
skill invocation evidence, then rerun the corpus audit.
