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
opentraces trace query --survival alive_on_path --candidate-kind trace
opentraces bucket remote pull --force --json  # sync remote traces before querying
opentraces trace query --cwd --json
```

`trace query` returns `CandidatePacket` rows. Use `--include-slice intent` or
`--include-slice evidence` when the consumer needs a bounded packet attached
to the search hit.

The `--semantic` facet is a lightweight concept layer, not embeddings: it
expands a small static dictionary of service/library concepts (for example
Hugging Face, MongoDB, OpenAI) and joins on concept ids when the query matches
one. Anything else degrades to lexical full-text search where every token must
match, so paraphrased queries can return zero hits even when related traces
exist. Prefer query terms that actually appear in the trace.

## Map

```bash
opentraces trace map <trace-id> --json
opentraces trace map <trace-id> --bursts --json
opentraces trace map <trace-id> --waste --json
opentraces trace map <trace-id> --run-intel --json
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
opentraces trace get <trace-id> --waste --json
opentraces trace get <trace-id> --run-intel --json
opentraces trace get <trace-id> --remote-bucket
opentraces trace get <trace-id> --remote owner/private-bucket
opentraces trace get ot://trace/<id>/patches/<patch-id>/trail --json
```

`trace get` is the full retrieval and resolver surface. Use it after `query`,
`map`, or `slice` points to the exact trace/unit/resource a workflow needs.

## Intelligence

Deterministic, derive-on-demand signals about how a run actually went, layered
on the same trace surface. Nothing is persisted; each is computed on read and
emitted as a frozen JSON envelope. `--waste` and `--run-intel` are accepted on
both `trace map` and `trace get` (byte-identical payloads), and are mutually
exclusive with `--bursts` and each other.

```bash
opentraces trace get <trace-id> --waste --json       # context-waste findings
opentraces trace get <trace-id> --run-intel --json   # resteer/recovery/loop/failure
opentraces trace compare <trace-a> <trace-b> --json  # two-run delta
```

- `--waste` emits `opentraces.context_waste.v1`: oversized tool outputs
  (>= 12000 chars), the same file read 3+ times in 20 minutes, and search
  commands repeated 5+ times in 10 minutes, with a `summary` count block.
  Override the thresholds with `--large-output-chars`, `--file-read-window-min`,
  and `--search-window-min`.
- `--run-intel` emits `opentraces.run_intel.v1`: deterministic `resteer` /
  `recovery` / `loop` / `failure` signals plus `counts`. Recovery only fires
  after an uncleared failure; a repeated command is one `loop` signal carrying
  `evidence.repeat_count`.

## Compare

```bash
opentraces trace compare <trace-a> <trace-b> --json
opentraces trace compare <trace-a> <trace-b> --no-quality --json
opentraces trace compare <trace-a> <trace-b> --burst-gap 50 --json
```

`trace compare` emits `opentraces.trace_compare.v1`: per-side `fidelity` plus
`{a, b, delta}` triples over token/cost metrics, deterministic quality persona
scores (skip with `--no-quality`), and burst/error/security signals. Both
traces are pinned to the same burst gap (`--burst-gap`, default 35) so the
deltas are comparable. See [Commands](/docs/cli/commands) for the full
envelope shapes.
