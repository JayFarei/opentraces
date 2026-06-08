# Intent PR Example

## Task

Inspect a pull request report that explains why a branch exists, how its commits
map back to trace evidence, and what reviewers should check beside the diff.

## Inputs

- `sample-pr-report.md` - a synthetic public-safe report rendered from branch
  trace lineage.

## Run

```bash
opentraces trail blame pr render --base main --no-llm > pr-report.md
opentraces trail blame pr create --base main --no-llm
opentraces trail blame pr update --base main --number 123 --no-llm
```

## Expected Output

The report should separate the intent, implementation summary, trace evidence,
tests, and review notes. It should make the originating sessions visible without
requiring reviewers to inspect raw traces. The `--no-llm` form is deterministic
and best for public examples; teams can omit it when they want the optional
LLM-polished headline and commit summaries.

## Public Safety

The committed report is synthetic. Real PR reports can contain local branch
names, filenames, trace summaries, and reviewer context, so only sanitized or
already-public reports belong here.
