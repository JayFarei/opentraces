---
title: opentraces Explorer
emoji: "\U0001F50D"
colorFrom: green
colorTo: blue
sdk: gradio
sdk_version: "4.44.1"
app_file: app.py
pinned: false
tags:
  - opentraces
  - agent-traces
---

# opentraces Explorer

A community-powered trace explorer for AI agent sessions. Browse, search, and analyze traces contributed by the opentraces community on HuggingFace Hub.

## Features

- **Search & Browse**: Find traces by keyword, model, agent, language ecosystem, or outcome.
- **Trace Viewer**: Read full agent sessions as a conversation with tool calls, sub-agent steps, and reasoning blocks.
- **Contributor Dashboard**: Per-user analytics including session trends, model distribution, token usage, and efficiency metrics.
- **Community Stats**: Aggregate statistics across all opentraces-tagged datasets on HuggingFace Hub.

## Data Source

The explorer discovers all HuggingFace datasets tagged with `opentraces` and loads traces conforming to the opentraces schema. When no real datasets are available yet, sample data demonstrates the full experience.

## Running Locally

```bash
pip install -r requirements.txt
python app.py
```

## Schema

Traces follow the [opentraces schema](https://opentraces.ai), which captures agent sessions including conversation turns, tool usage, token accounting, attribution, and outcome signals.
