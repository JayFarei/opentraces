# Troubleshooting

## First Checks

Start with:

```bash
opentraces status
opentraces doctor
opentraces doctor --security
```

`status` tells you what the current repo thinks is staged or waiting. `doctor` tells you whether required integrations are misconfigured.

## Common Problems

### Not Initialized

If the CLI says the repo is not initialized, run:

```bash
opentraces init
```

The current repo marker is `.opentraces.json`, not `.opentraces/config.json`.

### No Traces Showing Up

Check:

```bash
opentraces status
opentraces list --stage inbox
opentraces setup claude-code
```

If you are using Claude Code, make sure the capture hooks are installed and that the repo has actual Claude Code session files under `~/.claude/projects/`.

### Blocked Traces

Inspect them with:

```bash
opentraces list --stage blocked
opentraces show <trace-id>
```

Then either redact, reset, or reject as needed:

```bash
opentraces redact <trace-id>
opentraces reset <trace-id>
opentraces reject <trace-id>
```

### Push Fails

Common causes:

- no Hugging Face auth
- no remote configured
- `--llm-review` requested but staged traces do not have clean verdicts
- a configured integration is broken

Useful commands:

```bash
opentraces auth whoami
opentraces remote list
opentraces llm-review --scope staged
opentraces doctor
```

### TruffleHog Enabled But Missing

If `doctor` reports that TruffleHog is enabled but unavailable:

```bash
opentraces setup trufflehog
# or
opentraces setup trufflehog --disable
```

### LLM Review Unreachable

Re-test or reconfigure it:

```bash
opentraces setup llm-review --test
opentraces setup llm-review
opentraces setup llm-review --disable
```

### Resetting A Repo

To remove opentraces from the current repo cleanly:

```bash
opentraces remove
opentraces remove --all
```

To clear the stored Hugging Face credential:

```bash
opentraces auth logout
```
