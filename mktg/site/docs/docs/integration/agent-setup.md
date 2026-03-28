# Agent Setup

opentraces ships with a `SKILL.md` file for Claude Code integration. This lets you share traces directly from within an agent conversation.

## How It Works

The opentraces CLI is designed to be operated by agents. Every command emits structured JSON with `next_steps` and `next_command` fields:

```json
{
  "status": "ok",
  "next_steps": ["Push 3 traces to HuggingFace"],
  "next_command": "opentraces push"
}
```

## Claude Code Integration

opentraces includes a `SKILL.md` that Claude Code discovers automatically. Once installed, you can say:

- "parse my traces"
- "push to huggingface"
- "check opentraces status"

The agent reads the skill file, executes the CLI commands, and chains them based on `next_command`.

## Hidden Capture Command

For automation, the `_capture` command (hidden from help) captures a specific session:

```bash
opentraces _capture --session-dir /path/to/session --project-dir /path/to/project
```

This is used by hooks and automation scripts that need to target a specific session rather than discovering all sessions.

## Machine Discovery

Two commands support agent-driven exploration:

```bash
# Feature and version discovery
opentraces capabilities --json

# Full API schema with parameter types
opentraces introspect
```

These enable agents to discover available commands, their parameters, and valid values without parsing help text.
