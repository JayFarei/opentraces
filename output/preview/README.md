---
license: cc-by-4.0
tags:
  - opentraces
  - agent-traces
task_categories:
  - text-generation
language:
  - en
size_categories:
  - n<1K
---

# opentraces-claude-code

Community-contributed agent traces in opentraces JSONL format.

## Usage

```python
from datasets import load_dataset

ds = load_dataset("jayfarei/opentraces-claude-code")
```

## Schema

Each JSONL line is a `TraceRecord` containing:

- **trace_id**: Unique identifier for the trace
- **session_id**: Source session identifier
- **agent**: Agent identity (name, version, model)
- **task**: Structured task metadata
- **steps**: List of LLM API calls (thought-action-observation cycles)
- **outcome**: Session outcome signals
- **metrics**: Aggregated token usage and cost estimates
- **environment**: Runtime environment metadata
- **attribution**: Code attribution data (experimental)

Schema version: `0.1.0`

Full schema docs: [opentraces.ai/schema](https://opentraces.ai/schema)

## License

This dataset is licensed under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/).

Contributors retain copyright over their individual traces. By uploading, you agree
to share under CC-BY-4.0 for research and training purposes.

<!-- opentraces:auto-stats-start -->
## Dataset Statistics

| Metric | Value |
|--------|-------|
| Total traces | 7 |
| Total steps | 323 |
| Total tokens | 153,813 |
| Date range | 2026-03-16 to 2026-03-26 |
| Schema version | 0.1.0 |

### Agent Distribution

| Agent | Count |
|-------|-------|
| claude-code | 7 |

<!-- opentraces:auto-stats-end -->
