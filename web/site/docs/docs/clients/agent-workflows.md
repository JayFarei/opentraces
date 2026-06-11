# Agent Workflows

Agent workflows are clients that use opentraces as working memory. They search
prior session history, progressively load relevant trace evidence, and hand a
small packet to the next agent step.

This is not dataset publication by default. It is an agent using the trace
bucket to orient itself.

## Context Warmup

Natural-language prompt:

```text
Find prior traces in this repo related to the current task. Start with a broad
trace query, inspect only the most relevant candidates, then load the smallest
context and Trail evidence needed to brief the next agent step. Do not publish
anything.
```

Commands:

```bash
opentraces trace query --cwd --lex "auth middleware regression" --json
opentraces trace map <trace-id> --bursts --json
opentraces trace slice <trace-id> --template bursts --json
opentraces ctx step <trace-id> 7 --json
opentraces ctx resume <context-node-id> --json
opentraces trail track <trace-id> --json
```

The output is a compact working packet: the relevant intent, bounded trace
slice, context resume packet, and Trail survival evidence.

## Progressive Discovery

Use the cheapest read first:

1. `trace query` for candidates.
2. `trace map --bursts` for intent and edit clusters.
3. `trace slice` for the bounded step window.
4. `ctx step` or `ctx resume` for model-visible context.
5. `trail track` for whether the trace output still survives.

The agent only loads full trace content after the candidate is clearly useful.

## Session History Search

```bash
opentraces trace query --cwd --since 30d --lex "database migration" --json
opentraces trace query --skill opentraces --semantic "huggingface push failing" --json
opentraces trace get <trace-id> --bursts --json
```

`--semantic` matches a small concept dictionary (services and libraries seen in
trace evidence); queries outside it fall back to all-tokens-must-match lexical
search, so keep query terms concrete.

This lets an agent ask, "Have we solved something like this before?" without
turning the answer into a dataset row.
