# opentraces

Open schema + CLI for capturing agent traces into a private bucket, searching
and slicing them locally, and publishing workflow-projected dataset rows to
Hugging Face Hub.

opentraces has four separate surfaces:

1. **Trace workflow:** capture agent sessions into a portable bucket, then use
   Trace Discovery, Trace Trails, and Context Tree to inspect the retained
   environment.
2. **Dataset workflows:** skill-format row builders that use trace discovery,
   context, and trail evidence to project purposeful rows.
3. **Datasets:** local HuggingFace-shaped row stores that can be reviewed,
   scheduled, and published.
4. **Clients:** evaluation jobs, training loops, dashboards, context warmup,
   and manual trace-capsule patterns that consume either rows or bucket
   evidence.

## Trace Workflow

```bash
opentraces setup                          # install capture hooks and optional integrations
opentraces init                           # explicitly enroll this project, if not auto-enrolled
opentraces bucket status                  # inspect private retained trace evidence
opentraces trace query --since 7d         # search retained traces
opentraces trace map <trace-id> --bursts  # deterministic edit/intent map
opentraces trace slice <trace-id> --template bursts
opentraces trail blame commit <sha>       # which traces contributed to a commit
opentraces ctx tree <trace-id>            # what the agent saw across the trace
opentraces workflow templates             # choose a row projection template
opentraces dataset new my-dataset --workflow my-workflow
opentraces dataset run my-dataset         # synthesize dataset rows from retained traces
opentraces dataset review approve my-dataset --all
opentraces dataset publish my-dataset     # upload reviewed rows to the active remote
```

`init` writes the committable project marker at `.opentraces.json`. Captured
traces, bucket state, and upload bookkeeping stay machine-local under
`~/.opentraces/`.

## What You Get

**For individual developers.** A private trace bucket, deterministic local
search, and dataset publishing only when you choose to project and approve rows.

**For teams.** Shared bucket remotes for retained evidence, per-dataset
HuggingFace remotes for curated rows, and reproducible workflow templates.

**For dataset consumers.** Schema-valid row streams for training, evaluation,
teacher/student reinforcement learning, analytics, and attribution.

## Start Here

| Section | What's inside |
|---------|---------------|
| **[Installation](/docs/getting-started/installation)** | Install, verify, upgrade, uninstall |
| **[Authentication](/docs/getting-started/authentication)** | OAuth, PATs, `HF_TOKEN`, auth precedence |
| **[Quick Start](/docs/getting-started/quickstart)** | Capture into a bucket, search traces, build and publish a dataset |
| **[Commands](/docs/cli/commands)** | Current `opentraces` command reference |
| **[Portable Bucket](/docs/workflow/bucket)** | Raw trace envelopes, companions, manifests, sync, replay |
| **[Trace Discovery](/docs/workflow/trace-discovery)** | `trace query`, `trace map`, `trace slice`, `trace get`, `trace index` |
| **[Trace Trails](/docs/workflow/blame)** | Git anchors, survival states, blame, graph, PR body generation |
| **[Context Tree](/docs/workflow/context-tree)** | `ctx` commands and OTLP capture for what the agent saw |
| **[Dataset Workflows](/docs/workflow/workflow-templates)** | Build deterministic row projections from bucket traces |
| **[Dataset Rows](/docs/workflow/datasets)** | Local HF-shaped datasets, review states, remotes, schedules |
| **[Publish](/docs/workflow/pushing)** | Publication gates, shards, visibility, bucket-vs-dataset split |
| **[Security Tools](/docs/security/tiers)** | Optional default-off security/privacy tool registry |
| **[Schema](/docs/schema/overview)** | `TraceRecord` and schema `0.6.0` field semantics |
| **[Clients & Use Cases](/docs/workflow/consume)** | Datasets, private bucket reads, context warmup, and manual trace capsules |
