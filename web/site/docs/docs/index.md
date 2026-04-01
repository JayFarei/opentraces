# opentraces

Open schema + CLI for agent trace capture, review, and upload to Hugging Face Hub.

Every coding session with an AI agent produces action trajectories, tool-use sequences, and reasoning chains. These are the most valuable dataset nobody is collecting in the open. opentraces captures them automatically, scans for secrets, and publishes structured JSONL datasets to HuggingFace Hub. Private by default. You control what leaves your machine.

## What you get

**As a developer.** Share traces, get analytics back. Cost per session, cache hit rates, tool usage patterns, success rates. Your Spotify Wrapped for coding agents. Traces are searchable by dependency, so framework maintainers and researchers can find sessions relevant to their stack.

**As an ML team.** Real workflows, not synthetic benchmarks. Outcome signals for RL. Tool sequences for SFT. Sub-agent hierarchy for orchestration research. One dataset, many consumers, no vendor lock-in.

**As a team lead.** Commit traces automatically to a shared private dataset. Run downstream analytics, fine-tuning, or evaluation jobs on top of it. All on HuggingFace, all standard tooling.

Push traces and inspect what was collected:

```bash
opentraces push
opentraces assess your-org/agent-traces
```

**Loading in a notebook or training script.** Use the [HuggingFace `datasets` library](https://huggingface.co/docs/datasets/en/loading) when you want structured access, pandas integration, or a PyTorch DataLoader:

```python
from datasets import load_dataset

ds = load_dataset("your-org/agent-traces")

# pandas
df = ds["train"].to_pandas()

# PyTorch
ds["train"].with_format("torch")
```

**Loading inside an agent.** Use [hf-mount](https://github.com/huggingface/hf-mount) to expose the dataset as a virtual filesystem. No full download, no library dependency — the agent reads files directly, the same way it reads any local path:

```bash
hf-mount your-org/agent-traces /mnt/traces
```

```python
import json, pathlib

traces = [
    json.loads(line)
    for p in pathlib.Path("/mnt/traces").glob("*.jsonl")
    for line in p.read_text().splitlines()
    if line.strip()
]
```

The mount approach suits agents because it avoids Python library overhead and works with any file-reading tool call, not just code that imports `datasets`.

## Schema designed for downstream use

The [schema](/docs/schema/overview) is built for the people who consume traces, not just the tools that produce them. It is a superset of ATIF, informed by ADP and Agent Trace, and works across Claude Code, Cursor, Cline, Codex, and future agents without agent-specific fields.

- **Training / SFT** — Clean message sequences with role labels, tool-use as tool_call/tool_result pairs, outcome signals.
- **RL / RLHF** — Trajectory-level reward signals, step-level annotations, decision point identification via sub-agent hierarchy.
- **Telemetry** — Token counts, latency, model identifiers, cache hit rates, cost estimates per step.
- **Code attribution** *(experimental)* — File and line-level attribution linking each edit back to the agent step that produced it. Confidence varies by session complexity.

## Docs

| Section | What's inside |
|---------|---------------|
| **[Installation](/docs/getting-started/installation)** | Install, verify, upgrade |
| **[Authentication](/docs/getting-started/authentication)** | Hugging Face login and credentials |
| **[Quick Start](/docs/getting-started/quickstart)** | Init, inbox, commit, push |
| **[Commands](/docs/cli/commands)** | Public and hidden CLI surface |
| **[Security Modes](/docs/security/tiers)** | Review policy, security pipeline |
| **[Schema](/docs/schema/overview)** | TraceRecord, steps, outcome, attribution |
| **[Workflow](/docs/workflow/parsing)** | Parse, review, commit, push lifecycle |
| **[CI/CD](/docs/integration/ci-cd)** | Headless automation and token auth |
| **[Contributing](/docs/contributing/development)** | Local dev and schema changes |

## Links

- [GitHub](https://github.com/jayfarei/opentraces)
- [Schema Rationale](/docs/schema/overview)
- [opentraces.ai](https://opentraces.ai)

