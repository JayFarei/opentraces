# Quick Start

From local capture to a published Hugging Face dataset.

## 1. Install

```bash
pipx install opentraces
```

## 2. Set Up

```bash
opentraces setup
```

`setup` is the machine-wide wizard. It walks each integration with one prompt, defaults in brackets:

- **claude-code, git, skill** capture hooks [yes], Stop/PostCompact hooks, post-commit correlator, and the Claude Code skill.
- **watcher** [yes], background incremental backfill after each commit. Powers `opentraces trail blame`.
- **bucket** [yes], configure the private bucket sync target (the private workspace state that backs the trace index and Trace Trails).
- **entity-parser (sem)** [yes], entity-level diffs for richer commit attribution.
- **HuggingFace login** [yes], device-code flow, needed before you can publish a dataset or sync the bucket to a remote. You can defer and run `opentraces auth login` later.
- **trufflehog** (Tier 1.5) [no], global secret-scanner toggle. Findings redact in place and force review.
- **llm-review** (Tier 2) [no], global toggle for third-party LLM review. Configure provider via `opentraces setup llm-review`.

Per-project review policy and dataset remotes are not set here; they live in `opentraces init` and `opentraces dataset remote ...` respectively.

## 3. Initialize the Project

```bash
opentraces init
```

`init` wires the current repo into opentraces and prompts you for:

- **Agents** to connect (e.g. `claude-code`).
- **Import existing traces**, if this repo already has Claude Code sessions, `init` asks whether to import them now or start fresh.

It writes the committable marker at `.opentraces.json`, registers machine-local storage under `~/.opentraces/projects/<slug>/`, and installs the per-repo capture hook for the chosen agent.

## 4. Inspect Retained Traces

```bash
opentraces status
opentraces trace query --since 7d
opentraces trace get <trace-id>
```

`status` reports the project snapshot, stage counts, and recent traces. `trace query` is the full search surface across retained traces, with lexical, semantic, faceted, and survival-state filters. `trace get` resolves one trace, trace unit, map node, or `ot://` Trail resource.

For commit-level attribution:

```bash
opentraces trail blame <sha>           # which traces contributed to a commit
opentraces trail graph                  # commit + trace history
opentraces trail track <trace-id>       # walk trace lineage through Git history
```

## 5. Create a Dataset

Datasets are the publication unit in 0.4. Each dataset has its own schema, workflow, remotes, and publication state. Create one:

```bash
opentraces workflow create my-workflow
opentraces dataset new my-dataset --workflow my-workflow
```

For ad-hoc seeding from an existing JSONL file:

```bash
opentraces dataset new my-dataset --rows-file rows.jsonl --schema schema.json
```

## 6. Run the Workflow

```bash
opentraces dataset run my-dataset
opentraces dataset run my-dataset --dry-run
opentraces dataset run my-dataset --since-last-run
```

`dataset run` invokes the workflow against retained traces and appends rows into the dataset, advancing the cursor for subsequent runs.

## 7. Review the Rows

```bash
opentraces dataset review my-dataset --tui     # terminal review
opentraces dataset review my-dataset --web     # browser review
opentraces dataset review my-dataset approve <row-id>
opentraces dataset review my-dataset approve --all
```

![Web review](/docs/assets/web-review.png)

![Web graph](/docs/assets/web-graph.png)

![Terminal review](/docs/assets/tui.png)

## 8. Publish

```bash
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset publish my-dataset
```

`dataset publish` uploads reviewed rows and contract files to the bound remote as a new shard. Pass `--check-only` to run all gates without uploading.

## What Happens Next

Your dataset is available on Hugging Face:

```python
from datasets import load_dataset

ds = load_dataset("owner/team-traces")
```

## Next Steps

- [Inbox & Review](/docs/workflow/review), dataset review (TUI and web) and CLI review flows
- [Publish](/docs/workflow/pushing), remotes, visibility, migrations, and gates
- [Security Tiers](/docs/security/tiers), review policy and layered scanning
- [CLI Reference](/docs/cli/commands), full 0.4 command surface
