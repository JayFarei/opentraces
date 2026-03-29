# Security Configuration

Security settings are split between the user config in `~/.opentraces/config.json` and the per-project inbox config in `.opentraces/config.json`.

## User Config

The user config stores defaults shared across projects:

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
  "agents": ["claude-code"],
  "remote": "your-name/opentraces",
  "visibility": "private"
}
```

## Per-Project Setup

```bash
cd ~/project-a
opentraces init --review-policy review

cd ~/project-b
opentraces init --review-policy auto
```

## Exclusions

Exclude whole projects from trace collection:

```bash
opentraces config set --exclude /path/to/client-project
opentraces config set --exclude /path/to/another-sensitive-project
```

Excluded projects are skipped during capture and batch parsing.

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

Higher sensitivity adds more heuristic flags (internal hostnames, deep file paths, identifier density).
