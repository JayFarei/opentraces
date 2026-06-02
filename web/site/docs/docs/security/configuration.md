# Security Configuration

Security settings live in the same two config scopes as other opentraces
settings:

- global machine-local config: `~/.opentraces/config.json`
- per-repo portable marker: `<repo>/.opentraces.json`

Inspect the effective config:

```bash
opentraces config show
opentraces --json config show
opentraces security tools list --json
opentraces doctor --security
```

## Defaults

Every per-record security tool defaults off in a fresh config:

```json
{
  "security": {
    "regex": { "enabled": false },
    "entropy": { "enabled": false },
    "trufflehog": { "enabled": false, "verify_secrets": false },
    "privacy_filter": { "enabled": false, "model_name": "openai/privacy-filter" },
    "llm_pii": { "enabled": false },
    "business_logic": { "enabled": false },
    "path_anonymizer": { "enabled": false },
    "capsule_scope": { "enabled": false },
    "classifier": { "enabled": false, "sensitivity": "medium" },
    "llm_review": { "enabled": false }
  }
}
```

Existing user config may show enabled tools if you previously opted in. The
CLI reports the active state rather than the package default.

## Dedicated Setup Commands

Prefer setup commands for tools with dependencies or provider configuration:

```bash
opentraces setup trufflehog --enable
opentraces setup trufflehog --disable
opentraces setup privacy-filter --enable --install-deps
opentraces setup privacy-filter --disable
opentraces setup llm-review
opentraces setup llm-review --print
opentraces setup llm-review --disable
```

`trufflehog.verify_secrets` remains false by default so opentraces does not
make outbound verification calls unless you explicitly configure that behavior.

## Direct Config

Lightweight local tools can be enabled directly when you want `--use-config`
to pick them up:

```bash
opentraces config set security.regex.enabled true
opentraces config set security.entropy.enabled true
opentraces config set security.path_anonymizer.enabled true
opentraces config set security.classifier.enabled true
```

Use explicit `--tools` in workflow scripts when you want the tool list to be
auditable from the command itself:

```bash
printf '%s\n' '{"row":{"text":"..."}}' \
  | opentraces security sanitize --tools regex,entropy,path_anonymizer
```

## LLM Review

`llm_review` is stored under `security.llm_review`, but it is not part of the
per-record sanitize registry. It is a dataset publication reviewer.

```json
{
  "security": {
    "llm_review": {
      "enabled": true,
      "api_format": "openai-compat",
      "base_url": "http://localhost:11434/v1",
      "model": "gemma3n:e4b",
      "api_key_env": "",
      "timeout": 120.0,
      "prompt_version": "1"
    }
  }
}
```

## Project Marker

The repo-local `.opentraces.json` carries portable project policy such as
review policy, agents, remotes, and bucket defaults. Security tool settings can
be scoped there with `--project`, but machine-local provider credentials and
large optional dependencies usually belong in global config.

```bash
opentraces config set review_policy review --project
opentraces setup trufflehog --project --enable
```

## Exclusions And Custom Strings

```bash
opentraces config set excluded_projects /path/to/private-repo --append
opentraces config set custom_redact_strings corp-api-prefix- --append
opentraces config set custom_redact_strings INTERNAL_BILLING_TOKEN --append
```

Exclusions prevent collection. Custom strings are available to tools/workflows
that consult config during sanitization.
