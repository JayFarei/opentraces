# Troubleshooting

## Diagnostic Tool

```bash
opentraces status
```

Shows authentication status, project configuration, staged trace counts, and push history.

## Common Issues

### "No HF token found"

Run `opentraces login` or set `HF_TOKEN`:

```bash
export HF_TOKEN=hf_...
```

### "No sessions found"

Verify Claude Code sessions exist:

```bash
ls ~/.claude/projects/
```

If the directory is empty, you haven't used Claude Code in any projects yet. Run a Claude Code session first, then try `opentraces discover`.

### "Not initialized"

Run `opentraces init` in your project directory:

```bash
cd /path/to/your/project
opentraces init
```

### Parse Errors

If `opentraces parse` fails on a specific session:

```bash
# Limit to a smaller batch
opentraces parse --limit 1

# Check status for details
opentraces status
```

### Push Fails with 403

Your HF token may lack write permissions. Generate a new token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) with `write` scope.

### Schema Version Mismatch

If traces were staged with an older schema version:

```bash
opentraces migrate
```

## Reset

To clear all local state and start fresh:

```bash
rm -rf .opentraces/
opentraces init
```

To also clear credentials:

```bash
opentraces logout
```
