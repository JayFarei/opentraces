# opentraces - Share Agent Traces

When the user says "share this session to opentraces", "publish traces",
"upload my traces", "share traces", or similar:

## Workflow

1. **Discover** available sessions:
   ```bash
   opentraces discover
   ```

2. **Parse** sessions into trace format:
   ```bash
   opentraces parse
   ```

3. **Review** pending traces (shows summary, lets user approve/reject):
   ```bash
   opentraces review
   ```

4. **Push** approved traces to HuggingFace Hub:
   ```bash
   opentraces push
   ```

## Prerequisites

- opentraces CLI installed: `pip install opentraces`
- HF Hub authenticated: `opentraces auth` or set `HF_TOKEN` environment variable

## Quick Share (Tier 1, Open Mode)

For open-source projects where you trust the content:

```bash
opentraces parse --auto && opentraces push --approved-only
```

This skips interactive review and uploads all successfully parsed traces.

## Configuration

- Set default security tier: `opentraces config set default_tier 3`
- Exclude a project: `opentraces config exclude /path/to/project`
- Set dataset name: `opentraces config set dataset_name_template "{username}/my-traces"`

## Security Tiers

- **Tier 1**: Minimal filtering, for open-source projects
- **Tier 2**: Moderate filtering, redacts common secrets
- **Tier 3** (default): Strict filtering, redacts secrets, PII, and sensitive paths

## Troubleshooting

- If `opentraces push` fails with auth errors, run `opentraces auth` to refresh your token
- If no sessions are found, check that you have Claude Code session files in `~/.claude/projects/`
- Use `opentraces status` to see the current state of all traces in the pipeline
