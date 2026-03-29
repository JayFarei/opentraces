# Troubleshooting

## First Check

```bash
opentraces status
```

`status` shows the inbox summary, review policy, agents, remote, and stage counts.

## Common Issues

### "No HF token found"

Run:

```bash
opentraces login --token
```

Paste a write-access token from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). Or export `HF_TOKEN` in your shell:

```bash
export HF_TOKEN=hf_...
```

### "Not initialized"

Run `opentraces init` in the project directory. That creates `.opentraces/config.json` and `.opentraces/staging/`.

### "No sessions found"

Claude Code session files live under `~/.claude/projects/`. If there are no session files, start a Claude Code conversation first.

`opentraces discover` is a hidden diagnostic command if you need to inspect the raw session directories.

### Parse Errors

If a specific trace looks wrong:

```bash
opentraces session list
opentraces session show <trace-id>
opentraces session redact <trace-id> --step 3
```

### Push Fails With 403

Your token does not have write access. OAuth device tokens (from `opentraces login`) cannot create or write to dataset repos. Re-authenticate with a personal access token:

```bash
opentraces login --token
```

Paste a token with **write** scope from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).

### Resetting Local State

```bash
rm -rf .opentraces/
opentraces init
```

To clear credentials as well:

```bash
opentraces logout
```
