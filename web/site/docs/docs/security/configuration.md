# Security Configuration

Security settings are split between the user config in `~/.opentraces/config.json` and the per-project inbox config in `.opentraces/config.json`.

## User Config

The user config stores defaults shared across projects:

- `default_tier`
- `excluded_projects`
- `custom_redact_strings`
- `classifier_sensitivity`
- `dataset_visibility`

View it with:

```bash
opentraces config show
```

## Project Config

Each project keeps its inbox settings in `.opentraces/config.json`:

```json
{
  "review_policy": "review",
  "push_policy": "manual",
  "mode": "review",
  "agents": ["claude-code"],
  "remote": "your-name/opentraces",
  "visibility": "private"
}
```

`mode` is a legacy alias retained for compatibility. `review_policy` and `push_policy` are the canonical keys.

## Per-Project Setup

```bash
cd ~/project-a
opentraces init --review-policy review --push-policy manual

cd ~/project-b
opentraces init --review-policy auto-ready --push-policy manual
```

## Security Tier

Set the default security tier for the current user config:

```bash
opentraces config set --tier 1
opentraces config set --tier 2
opentraces config set --tier 3
```

| Tier | Behavior |
|------|----------|
| `1` | Automatic scan + redact |
| `2` | Automatic scan + redact + classifier flags |
| `3` | Strict review-first flow |

## Exclusions

Exclude whole projects from trace collection:

```bash
opentraces config set --exclude /path/to/client-project
opentraces config set --exclude /path/to/another-sensitive-project
```

Excluded projects are skipped by `opentraces discover` and `opentraces parse` when you run the internal batch commands.

## Custom Redaction Strings

Add literal strings that should always be redacted:

```bash
opentraces config set --redact "ACME_INTERNAL_TOKEN"
opentraces config set --redact "corp-api-prefix-"
```

## Classifier Sensitivity

```bash
opentraces config set --classifier-sensitivity low
opentraces config set --classifier-sensitivity medium
opentraces config set --classifier-sensitivity high
```

The classifier is used only at tier 2. Higher sensitivity adds more heuristic flags.
