# Trace Discovery

Trace discovery is the deterministic search layer over the private bucket. It
lets workflows find the right trace evidence without loading full transcripts.
The four visible trace verbs form a loop: `query` (search) -> `get` (pull up)
-> `map` (dissect) -> `slice` (extract). All four are pure derive-on-demand
reads over the Trace Index — nothing here persists or bumps the schema
version.

## Trace Index

The Trace Index is the underlying projection: retained traces compiled into
bounded search documents with lexical, semantic, file, tool, skill,
dependency, and survival facets. It self-maintains behind `trace query`; the
standalone `trace index` command that used to rebuild/inspect it directly is
now hidden (still callable, off `--help`).

## Query

```bash
opentraces trace query --lex "bug fix failing test" --cwd --json
opentraces trace query --skill opentraces --since 7d
opentraces trace query --files "src/**/*.py" --signal failing-test
opentraces trace query --survival alive_on_path --candidate-kind trace
opentraces bucket sync pull --force --json  # sync remote traces before querying
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

## Skill Usage

```bash
opentraces trace query --skill opentraces --json
opentraces trace query --skill opentraces --since 7d --json
opentraces dataset new opentraces-episodes --from-skill opentraces
opentraces dataset run opentraces-episodes --executor script --json
```

`--skill` is a facet on `trace query` (the old standalone `trace skills`
command is now hidden, folded into this filter). It matches the exact
`skill.name` facet against the compact search snapshot's `skill_invocations`
table, returning candidates ranked by relevance with agent/source/project
breakdowns and telemetry. Use `dataset new --from-skill <skill>` when the
next step is a reviewable `skill-episodes-v1` dataset for one observed skill.

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
opentraces trace slice <trace-id> --by user-turn --json
opentraces trace slice <trace-id> --by change-burst --json
opentraces trace slice <trace-id> --by milestone --json
opentraces trace slice <trace-id> --by subgoal --json
```

Trace Slices are bounded packets for workflows. They are not dataset rows by
themselves; workflow templates decide how to project them. `--by
user-turn|change-burst|milestone|subgoal` tiles the WHOLE trace into a
`Trajectory[]` array (the frozen `opentraces.slicing.v1` envelope) — this
absorbs the former standalone `trace partition --by s1|s2|s3|s4` command,
which is now hidden. `user-turn`/`change-burst` are deterministic;
`milestone`/`subgoal` are cheap-LLM resolved via `--judge` (default `agent`:
when judgments are needed the command exits `rc=10 needs-judgment` with
`JudgmentRequest`s to answer and re-run with `--answers`).

## Get

```bash
opentraces trace get <trace-id> --json
opentraces trace get <trace-id> --bursts --json
opentraces trace get <trace-id> --waste --json
opentraces trace get <trace-id> --run-intel --json
opentraces trace get <trace-id> --remote owner/private-bucket
opentraces trace get ot://trace/<id>/patches/<patch-id>/trail --json
opentraces trace get <trace-id>:7 --json      # step 7 of the trace
opentraces trace get <trace-id>:last --json   # the last step
```

`trace get` is the full retrieval and resolver surface: the "pull up" step
after `query` points to the exact trace/unit/resource a workflow needs. It
also resolves the full `<trace-id>[:step | :last | :A-B]` address grammar — the
same `trace:step` token that `ctx` (`:step` / `:last`) and `trail` (`:step`)
resolve, so the three substrates share one address for the "resume triple"
(action / context / world) at a given step (`ctx`'s `:A-B` span and `trail`'s
`:last`/`:A-B` are reserved for a future release). `--remote` names the HF bucket repo to read from
(replaces the old `--remote-bucket` flag, which still works but is hidden).

## Intelligence

Deterministic, derive-on-demand signals about how a run actually went, layered
on the same trace surface. Nothing is persisted; each is computed on read and
emitted as a frozen JSON envelope. `--waste` and `--run-intel` are accepted on
both `trace map` and `trace get` (byte-identical payloads), and are mutually
exclusive with `--bursts` and each other.

```bash
opentraces trace get <trace-id> --waste --json       # context-waste findings
opentraces trace get <trace-id> --run-intel --json   # resteer/recovery/loop/failure
opentraces trace compare <trace-a> <trace-b> --json  # two-run delta (hidden, still callable)
```

- `--waste` emits `opentraces.context_waste.v2`: oversized tool outputs
  (>= 12000 chars), the same file read 3+ times in 20 minutes, and search
  commands repeated 5+ times in 10 minutes, with a `summary` count block.
  Override the thresholds with `--large-output-chars`, `--file-read-window-min`,
  and `--search-window-min`.
- `--run-intel` emits `opentraces.run_intel.v1`: deterministic `resteer` /
  `recovery` / `loop` / `failure` signals plus `counts`. Recovery only fires
  after an uncleared failure; a repeated command is one `loop` signal carrying
  `evidence.repeat_count`.

## Compare

`trace compare` is not one of the four visible job verbs (query / get / map /
slice) — it's hidden in v7 but still callable and `--json`-scriptable:

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
