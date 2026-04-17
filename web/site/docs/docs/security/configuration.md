# Security Configuration

Security settings now live in two places:

- global machine-local config: `~/.opentraces/config.json`
- per-repo portable marker: `<repo>/.opentraces.json`

Machine-local traces and runtime state live separately under `~/.opentraces/projects/<slug>/`.

## Global Config

Inspect it with:

```bash
opentraces config show
opentraces --json config show
```

Common global keys include:

- `excluded_projects`
- `custom_redact_strings`
- `classifier_sensitivity`
- `dataset_visibility`
- `security.trufflehog.*`
- `security.llm_review.*`

Examples:

```bash
opentraces config set classifier_sensitivity high
opentraces config set custom_redact_strings ACME_INTERNAL_TOKEN --append
opentraces config set excluded_projects /path/to/client-repo --append
```

## Project Marker

The repo-local `.opentraces.json` carries portable policy:

```json
{
  "marker_version": "2",
  "project_id": "...",
  "review_policy": "review",
  "push_policy": "manual",
  "remotes": {
    "origin": {
      "url": "owner/opentraces",
      "visibility": "private"
    }
  },
  "active_remote": "origin",
  "default_visibility": "private",
  "agents": ["claude-code"]
}
```

Depending on the repo, it may also carry fields like `root_commit_sha` and `first_run_backfill_decision`.

Write project-scoped values with:

```bash
opentraces config set review_policy auto --project
opentraces config set default_visibility private --project
```

## Preferred Setup Commands

For the security integrations themselves, prefer the dedicated setup commands over raw `config set`:

```bash
opentraces setup trufflehog
opentraces setup llm-review
opentraces setup review-policy --review
```

These commands validate the environment and keep the config shape correct.

## Exclusions

Exclude entire repos from collection:

```bash
opentraces config set excluded_projects /path/to/private-repo --append
```

## Custom Redaction Strings

Add strings that should always be scrubbed:

```bash
opentraces config set custom_redact_strings corp-api-prefix- --append
opentraces config set custom_redact_strings INTERNAL_BILLING_TOKEN --append
```

## TruffleHog Settings

Tier 1.5 is stored under `security.trufflehog` in the global config:

```json
{
  "security": {
    "trufflehog": {
      "enabled": true,
      "verify_secrets": false
    }
  }
}
```

`verify_secrets` stays off by default so the scanner does not make outbound verification calls.

## LLM Review Settings

Tier 2 review is stored under `security.llm_review`:

```json
{
  "security": {
    "llm_review": {
      "enabled": true,
      "api_format": "openai-compat",
      "base_url": "http://localhost:11434/v1",
      "model": "gemma4:latest",
      "api_key_env": "",
      "timeout": 120.0,
      "prompt_version": "1"
    }
  }
}
```

The reviewer config is machine-local and shared across projects unless you explicitly scope setup to a project.
