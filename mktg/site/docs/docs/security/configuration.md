# Security Configuration

All security settings live in `.opentraces/config.json`, created by `opentraces init`.

## View Current Config

```bash
opentraces config show
```

Displays the full configuration with sensitive values (redaction strings) masked.

## Per-Project Settings

Each project gets its own configuration:

```bash
cd ~/project-a
opentraces init --mode auto

cd ~/project-b
opentraces init --mode review
```

## Configuration Options

### Security Mode

```bash
opentraces config set --mode review
```

| Value | Description |
|-------|-------------|
| `auto` | Scan, redact, and push automatically |
| `review` | Review every trace before pushing (default) |

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

### Custom Pricing

Override the default token pricing table for cost estimation:

```bash
opentraces config set --pricing-file /path/to/pricing.json
```

## Config File Format

The `.opentraces/config.json` file:

```json
{
  "mode": "review",
  "visibility": "private",
  "remote": "username/opentraces",
  "exclude_paths": ["/path/to/sensitive-project"],
  "redact_strings": ["ACME_INTERNAL_TOKEN"]
}
```

## Per-Session Override

Override the mode for a single parse:

```bash
opentraces parse --auto   # auto mode for this run
```
