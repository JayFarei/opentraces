# Spotlight Example

## Task

Inspect a bounded trace-search packet and the follow-up commands that load only
the smallest useful context for a handoff, review, or mid-session lookup.

## Inputs

- `candidate-packet.sample.json` - a synthetic public-safe candidate packet for
  an auth middleware regression query.

## Run

Inspect the committed public fixture:

```bash
jq '{query,total_returned,candidates:[.candidates[]|{trace_id,unit_id,kind,headline,commands}]}' \
  examples/spotlight/candidate-packet.sample.json
```

Run against a local retained trace index:

```bash
opentraces trace query --cwd --lex "auth middleware regression" --json
opentraces trace map trace-auth-regression --candidate tu:trace-auth-regression:burst:1 --json
opentraces trace slice trace-auth-regression --around-step 42 --json
opentraces trace get tu:trace-auth-regression:burst:1 --json
```

## Expected Output

The query should return a bounded candidate packet, not a full transcript dump.
Each candidate should identify the trace unit, explain why it matched, and point
to the next command that loads the relevant map, slice, or unit.

## Public Safety

The committed packet is synthetic. Public search examples should show query and
navigation shape without exposing private prompts, raw tool outputs, or local
paths.
