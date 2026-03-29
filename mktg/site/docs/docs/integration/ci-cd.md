# CI/CD & Automation

opentraces works in any environment that supports Python and HTTP. In headless environments, use token-based authentication instead of the browser login flow.

## Authentication

Set `HF_TOKEN` as a CI secret:

```bash
export HF_TOKEN=hf_...
```

Or pass it to `opentraces login`:

```bash
opentraces login --token
```

No browser login needed when `HF_TOKEN` is set.

## GitHub Actions

### Post-Session Sharing

```yaml
- name: Install opentraces
  run: pip install opentraces

- name: Parse and push traces
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    opentraces init --tier open
    opentraces parse --auto
    opentraces push --private
```

### With Custom Dataset

```yaml
- name: Push to team dataset
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    opentraces init --mode auto
    opentraces parse --auto
    opentraces push --repo my-org/agent-traces --private
```

## Docker

```dockerfile
RUN pip install opentraces
```

Run with token:

```bash
docker run -e HF_TOKEN=hf_... my-image \
  bash -c "opentraces init --tier open && opentraces parse --auto && opentraces push"
```

## Headless / SSH

```bash
export HF_TOKEN=hf_...
opentraces init --tier open
opentraces parse --auto
opentraces push
```

## Git Post-Commit Hook

Automate trace capture after each commit:

```bash
# .git/hooks/post-commit
#!/bin/bash
opentraces parse --auto && opentraces push
```

This enriches traces with commit metadata (SHA, branch, diff) automatically.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HF_TOKEN` | Yes (in CI) | HuggingFace write token |

## Tips

- Use `--private` in CI for proprietary codebases
- Use `--tier open` with `--auto` for maximum throughput in open-source CI
- Store `HF_TOKEN` as a repository secret, never commit it
- Each push creates a new JSONL shard, safe for parallel CI runs
