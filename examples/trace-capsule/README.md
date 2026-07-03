# Trace Capsule Example

## Task

Inspect a shareable Trace Capsule for a single synthetic usage episode. The
example demonstrates how a real agent experience can be attached to a GitHub
issue without exposing raw transcript content, reasoning, secrets, or local
paths.

## Inputs

- `capsule.sample.json` - a synthetic public-safe capsule for a parser
  regression.

## Run

Inspect the committed public fixture:

```bash
opentraces capsule get examples/trace-capsule/capsule.sample.json --json
opentraces capsule get examples/trace-capsule/capsule.sample.json --summary
```

For a real retained trace, preview first, then seal or render an issue body:

```bash
opentraces capsule preview <trace-id> --project <repo> --json
opentraces capsule create <trace-id> --project <repo> --json > capsule.json
opentraces capsule issue <trace-id> --project <repo> --issue-repo owner/project
```

(`capsule create <trace-id>` seals the whole trace; use `<trace-id>:<step>` or
`<trace-id>:A-B` to bound the scope, or `--from-step`/`--to-step`. The pre-v7
`capsule export`/`capsule open` spellings from older docs still work, hidden
from `--help`.)

## Expected Output

The capsule should describe one reproducible problem, the bounded evidence that
supports it, the test command that proves the failure, and the privacy scope used
before sharing. A GitHub issue body can reference the capsule as evidence instead
of copying raw trace logs. `capsule get` should succeed against the committed
fixture because it is a complete `opentraces.capsule.v1` envelope, not just a
sketch of the shape.

## Public Safety

The committed sample is synthetic. Public capsules must be redacted, bounded,
and marked as untrusted input when a third party or model may read them.
