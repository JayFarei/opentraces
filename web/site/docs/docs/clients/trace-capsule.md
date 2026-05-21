# Trace Capsule

The future capsule command is planned, not shipped yet. For now, emulate a
trace capsule as a dataset of one: one trace, one bounded slice, one context
resume packet, one Trail evidence closure, and one explicit redaction pass.

Think of this as a manual client pattern over the existing dataset workflow
system. The future `opentraces capsule ...` command will make it first-class.

## Natural-Language Prompt

```text
Create a one-row trace capsule for this bug. Use the smallest trace slice that
captures the failing intent, attach the context resume packet and Trail anchors,
run explicit security tools, then create a local dataset with one reviewed row.
Do not claim this is the future opentraces capsule command; this is the manual
dataset-of-1 version.
```

## Build The Evidence Closure

```bash
opentraces trace query --cwd --lex "bug reproduction failure" --json
opentraces trace map <trace-id> --bursts --json
opentraces trace slice <trace-id> --around-step <step> --radius 4 --json
opentraces ctx step <trace-id> <step> --json
opentraces ctx resume <context-node-id> --json
opentraces trail track <trace-id> --json
```

The closure should include only what the recipient needs: the captured intent,
the relevant slice, the context resume packet, Trail anchors, and enough repo
pinning information to understand where the bug happened.

## Project It As One Dataset Row

```bash
opentraces workflow create trace-capsule --template default \
  --description "One-row bug capsule projection"
opentraces dataset new trace-capsule-001 --workflow ./workflows/trace-capsule/
opentraces dataset run trace-capsule-001 --scope trace --trace <trace-id> --dry-run
opentraces dataset run trace-capsule-001 --scope trace --trace <trace-id>
opentraces dataset review trace-capsule-001 --json
opentraces dataset review approve trace-capsule-001 --all
```

If the capsule is meant to leave the machine, run explicit security tooling
inside the workflow or over the candidate row before approval:

```bash
printf '%s\n' '{"row": {...}}' \
  | opentraces security sanitize --tools regex,entropy,path_anonymizer
```

## Share Or Re-run Manually

For a private handoff, bind a private dataset remote and publish the single
approved row:

```bash
opentraces dataset remote create trace-capsule-001 owner/trace-capsule-001 --private
opentraces dataset publish trace-capsule-001 --check-only
opentraces dataset publish trace-capsule-001
```

For a local agent replay, use the context packet and trace slice as the prompt
material for a fresh agent session. The replay is intent-based, not byte-for-byte
execution replay.
