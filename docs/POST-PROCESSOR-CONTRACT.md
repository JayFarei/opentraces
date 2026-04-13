# Post-processor contract

opentraces can pipe any trace through an ordered chain of external
commands pre-upload (during `opentraces push`). A post-processor is any
executable on `PATH` (or an absolute path) that speaks this small
contract.

## Protocol

Per invocation:

- **stdin**: one complete trace as JSON, matching the current
  `TraceRecord` schema (`packages/opentraces-schema`).
- **stdout**: one trace as JSON, same shape. This replaces the in-flight
  trace for the next step in the chain.
- **exit 0**: success.
- **non-zero exit**: failure. By default non-fatal (logged, chain
  continues with the pre-invocation trace). Under `--strict`, promoted
  to a hard error that halts the pipeline.
- **byte-identical stdout**: explicit no-op. Recorded as `status=noop`,
  never as a failure.

Environment variables and argv are passed through from the processor's
config entry. `opentraces doctor` probes every configured processor and
reports whether the binary resolves.

## Configuration

Declared as an ordered list under a project's `.opentraces/config.json`:

```json
{
  "post_processors": [
    {
      "name": "my-tagger",
      "command": "/usr/local/bin/my-tagger",
      "args": ["--some-flag"],
      "env": {"LOG_LEVEL": "debug"}
    }
  ]
}
```

Fields:

| Field     | Type                          | Default    | Meaning                                    |
|-----------|-------------------------------|------------|--------------------------------------------|
| `name`    | string                        | (required) | Human label; shown in `doctor` + logs.     |
| `command` | string                        | (required) | Executable on `PATH` or absolute path.     |
| `args`    | `list[str]`                   | `[]`       | argv passed through.                       |
| `env`     | `dict[str, str]`              | `{}`       | Extra env vars layered on the inherited env.|

## Invariants

- **Redaction ordering** — processors always see a post-redaction trace.
  `opentraces.pipeline` runs the security scrubber first; the ordering
  is covered by a test that fails if anyone reorders the pipeline.
- **Schema validation** — stdout is parsed as `TraceRecord`. Invalid
  output is rejected; under `--strict` it raises, otherwise it's
  recorded as `status=invalid_output` and the pre-invocation trace is
  preserved.

## Minimal example (Python)

```python
#!/usr/bin/env python3
"""Annotate every trace with a static tag."""
import json, sys

trace = json.loads(sys.stdin.read())
trace.setdefault("metadata", {})["processed_by"] = "my-tool"
sys.stdout.write(json.dumps(trace))
```

Make it executable (`chmod +x`), put it on `PATH`, and list it under
`post_processors` in your project config.

## Reference implementation

`opentraces.processors.run_processor` / `run_chain`
(`src/opentraces/processors.py`) drive every invocation. See
`tests/test_processors.py` for the complete matrix of happy-path and
failure-mode tests built against stub binaries.
