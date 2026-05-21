# Clients & Use Cases

Clients consume either published dataset rows or private bucket evidence. The
important distinction is whether the client needs curated rows or the retained
trace environment.

## Published Dataset Rows

Use this path for evaluation jobs, teacher/student training, SFT/RL pipelines,
dashboards, and public or private dataset sharing.

```python
from datasets import load_dataset

ds = load_dataset("owner/team-traces", split="train")
print(ds[0])
```

For streaming:

```python
from datasets import load_dataset

ds = load_dataset("owner/team-traces", streaming=True)
for row in ds["train"]:
    print(row)
```

Rows are workflow-specific. A command-trajectory eval dataset and a PR intent
summary dataset will not have the same row schema, even if they came from the
same bucket traces.

## Private Bucket Evidence

Use the CLI when a client needs raw trace evidence, Git anchors, or Context
Tree nodes:

```bash
opentraces trace query --remote-bucket --cwd --json
opentraces trace get <trace-id> --remote owner/private-bucket --json
opentraces trail blame commit <sha> --json
opentraces ctx tree <trace-id> --json
opentraces bucket prefetch <trace-id> --remote owner/private-bucket
```

Bucket evidence can be large and private. Do not treat a bucket remote as a
public dataset unless you intentionally made that storage public.

## Context Warmup

Context warmup is the agent-memory use case: before starting new work, an agent
searches session history, progressively loads the relevant trace evidence, and
uses a small context packet to orient the next session.

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

The result is not a dataset row by default. It is a compact working packet the
agent can read before continuing.

## Manual Trace Capsule

The future capsule command is planned, not shipped yet. For now, emulate a
trace capsule as a dataset of one: one trace, one bounded slice, one context
resume packet, one Trail evidence closure, and one explicit redaction pass.

Natural-language prompt:

```text
Create a one-row trace capsule for this bug. Use the smallest trace slice that
captures the failing intent, attach the context resume packet and Trail anchors,
run explicit security tools, then create a local dataset with one reviewed row.
Do not claim this is the future opentraces capsule command; this is the manual
dataset-of-1 version.
```

Commands:

```bash
opentraces trace query --cwd --lex "bug reproduction failure" --json
opentraces trace map <trace-id> --bursts --json
opentraces trace slice <trace-id> --around-step <step> --radius 4 --json
opentraces ctx step <trace-id> <step> --json
opentraces ctx resume <context-node-id> --json
opentraces trail track <trace-id> --json

opentraces workflow create trace-capsule --template default \
  --description "One-row bug capsule projection"
opentraces dataset new trace-capsule-001 --workflow ./workflows/trace-capsule/
opentraces dataset run trace-capsule-001 --scope trace --trace <trace-id> --dry-run
opentraces dataset run trace-capsule-001 --scope trace --trace <trace-id>
opentraces dataset review trace-capsule-001 --json
opentraces dataset review approve trace-capsule-001 --all
```

If the one-row capsule is meant to leave the machine, run explicit security
tooling inside the workflow or over the candidate row before approval:

```bash
printf '%s\n' '{"row": {...}}' \
  | opentraces security sanitize --tools regex,entropy,path_anonymizer
```

The planned capsule feature will make this a first-class command surface. Until
then, the dataset-of-1 pattern keeps the concept honest: it is just a workflow
projection over a private bucket trace.

## File-Oriented Access

For published Hugging Face datasets, `hf-mount` can expose shards as files:

```bash
hf-mount start repo datasets/your-org/agent-traces /mnt/traces
ls /mnt/traces/data/
head -n 1 /mnt/traces/data/*.jsonl
hf-mount stop /mnt/traces
```

For private/gated datasets, authenticate with Hugging Face first.
