# Security Configuration

All security settings live in `.opentraces/config.yml`, created by `opentraces init`.

## View Current Config

```bash
opentraces config show
```

Displays the full configuration with sensitive values (redaction strings) masked.

## Per-Project Settings

Each project gets its own configuration:

```bash
cd ~/project-a
opentraces init --tier open

cd ~/project-b
opentraces init --tier strict
```

## Configuration Options

### Security Tier

```bash
opentraces config set --tier guarded
```

| Value | Description |
|-------|-------------|
| `open` | Regex scan only, auto-upload |
| `guarded` | Classifier + escalation (default) |
| `strict` | Full human review |

### Excluded Projects

Exclude entire projects from trace collection:

```bash
opentraces config set --exclude /path/to/client-project
opentraces config set --exclude /path/to/another-sensitive-project
```

Exclusions are append-only. Excluded projects are skipped during `opentraces discover` and `opentraces parse`.

### Custom Redaction Strings

Add literal strings to always redact:

```bash
opentraces config set --redact "ACME_INTERNAL_TOKEN"
opentraces config set --redact "corp-api-prefix-"
```

### Classifier Sensitivity

For guarded tier, control how aggressively the classifier flags content:

```bash
opentraces config set --classifier-sensitivity high    # more flags, fewer misses
opentraces config set --classifier-sensitivity medium   # balanced (default)
opentraces config set --classifier-sensitivity low      # fewer flags, more throughput
```

### Custom Pricing

Override the default token pricing table for cost estimation:

```bash
opentraces config set --pricing-file /path/to/pricing.json
```

## Config File Format

The `.opentraces/config.yml` file:

```yaml
tier: guarded
exclude_paths:
  - /path/to/sensitive-project
redact_strings:
  - ACME_INTERNAL_TOKEN
classifier_sensitivity: medium
```

## Per-Session Override

Override the tier for a single parse:

```bash
opentraces parse --auto   # treat as open tier for this run
```
