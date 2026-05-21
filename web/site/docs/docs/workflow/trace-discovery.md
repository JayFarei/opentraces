# Trace Discovery

Trace discovery is the deterministic search layer over the private bucket. It
lets workflows find the right trace evidence without loading full transcripts.

## Trace Index

```bash
opentraces trace index rebuild
opentraces trace index status
```

The Trace Index projects retained traces into bounded search documents with
lexical, semantic, file, tool, skill, dependency, and survival facets.

## Query

```bash
opentraces trace query --lex "bug fix failing test" --cwd --json
opentraces trace query --skill opentraces --since 7d
opentraces trace query --files "src/**/*.py" --signal failing-test
opentraces trace query --survival alive_on_path --candidate-kind patch
opentraces trace query --remote-bucket --force-remote-bucket
```

`trace query` returns `CandidatePacket` rows. Use `--include-slice intent` or
`--include-slice evidence` when the consumer needs a bounded packet attached
to the search hit.

## Map

```bash
opentraces trace map <trace-id> --json
opentraces trace map <trace-id> --bursts --json
opentraces trace map <trace-id> --around s42 --depth 2
```

`trace map` returns a deterministic evidence graph. `--bursts` groups nearby
file edits into `change_burst` nodes with structured intent:

```json
{
  "intent": {
    "trigger": "...",
    "most_substantive_spec": { "text": "...", "step": 12 },
    "spec_chain": [],
    "burst_commit_sha": "abc123...",
    "commit_subject": "fix: parser edge case"
  }
}
```

## Slice

```bash
opentraces trace slice <trace-id> --template bursts --json
opentraces trace slice <trace-id> --around-step 7 --radius 3
opentraces trace slice <trace-id> --around-patch <patch-id>
opentraces trace slice <trace-id> --from-step 5 --to-step 12
```

Trace Slices are bounded packets for workflows. They are not dataset rows by
themselves; workflow templates decide how to project them.

## Get

```bash
opentraces trace get <trace-id> --json
opentraces trace get <trace-id> --bursts --json
opentraces trace get <trace-id> --remote-bucket
opentraces trace get <trace-id> --remote owner/private-bucket
opentraces trace get ot://trace/<id>/patches/<patch-id>/trail --json
```

`trace get` is the full retrieval and resolver surface. Use it after `query`,
`map`, or `slice` points to the exact trace/unit/resource a workflow needs.
