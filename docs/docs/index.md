# open traces

**Your agent traces are training data.**

open traces is an open-source CLI for crowdsourcing AI coding agent session traces as structured JSONL datasets on Hugging Face Hub. Three security tiers. Training-first schema. Zero config.

## The Problem

There is a growing ecosystem of tools that _capture_ code-agent traces, but no standard path from capture to _contribution_. The training and RL communities are starved for high-quality agentic trajectory data.

| Layer | Tools | Gap |
|-------|-------|-----|
| **Capture** | claude-trace, Langfuse hooks, OTel logs | Proprietary formats, no upload path |
| **Sharing** | traces.com (proprietary SaaS) | Data locked in walled garden, no training utility |
| **Datasets** | Nemotron-RL, SWE-bench traces | Synthetic/benchmark only, not real-world |
| **Open Source** | DataClaw (2k stars, 32 HF datasets) | Shallow schema, single security tier |
| **Standards** | Agent Trace spec (Cursor, 10+ backers) | Solves attribution, not trajectory |
| **Privacy** | Anthropic sandboxing patterns | No reusable pipeline for trace sanitization |

## The Insight

Every commit systematically discards the reasoning that produced the code. Agent Trace preserves _which_ lines came from AI. ATIF/ADP preserve _how_ the agent reasoned. Neither alone tells the complete story.

open traces is the format that connects the full conversation trajectory to the specific code output at line granularity. ADP + Agent Trace, unified.

## Quick Start

```bash
pip install opentraces
opentraces publish --tier automated
```

That's it. Auto-discovers sessions, auto-detects agents, publishes to your HF dataset.

## Features

- **Passive capture** - Reads existing agent log files from disk. No hooks, no daemons, no runtime overhead.
- **Three security tiers** - Danger mode for OSS. Automated screening for most. Manual review for sensitive code.
- **Training-first schema** - Outcome signals, sub-agent hierarchy, per-step tokens. Designed for SFT and RL.
- **HuggingFace native** - Publishes JSONL to HF Hub. Loadable via `datasets.load_dataset()`.
- **Multi-agent support** - Claude Code, Codex, Gemini CLI, Cursor, Cline. One schema, many agents.
- **Agent Trace attribution** - Embeds code attribution at file:line granularity.
- **Agent-native CLI** - Every command outputs structured JSON with `next_steps`. Built for agents to drive.
