# CI/CD & Automation

Use the same explicit workflow in automation that you use locally: initialize, capture, run dataset workflows, publish.

## Authentication

`HF_TOKEN` is the preferred CI path:

```bash
export HF_TOKEN=hf_...
```

You do not need to run `opentraces auth login` when `HF_TOKEN` is already set in the environment.

## Recommended Pattern

For headless runs:

```bash
opentraces init --agent claude-code
opentraces dataset new my-dataset --workflow my-workflow --schema schema.json
opentraces dataset run my-dataset --executor claude-code-headless
opentraces dataset review my-dataset approve --all
opentraces dataset publish my-dataset --to my-org/dataset
```

If the runner is seeding from an existing JSONL file instead of running a workflow:

```bash
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
opentraces dataset review my-import approve --all
opentraces dataset publish my-import --to my-org/dataset
```

## Health Checks

Run these before a gated publish:

```bash
opentraces doctor
opentraces doctor --security
```

If you rely on optional integrations, configure them explicitly in automation:

```bash
opentraces setup trufflehog --enable
opentraces setup llm-review --enable
```

Those commands assume the required binary or endpoint is already available.

## GitHub Actions Example

```yaml
- name: Install opentraces
  run: pipx install opentraces

- name: Initialize project
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: opentraces init --agent claude-code

- name: Create dataset and run workflow
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    opentraces dataset new ci-dataset --workflow ci-workflow --schema schema.json
    opentraces dataset run ci-dataset --executor claude-code-headless

- name: Approve and publish
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    opentraces dataset review ci-dataset approve --all
    opentraces dataset remote create ci-dataset my-org/dataset --private
    opentraces dataset publish ci-dataset
```

## Notes

- Use `--private` (the default for `dataset remote create`) for proprietary codebases
- Use `dataset publish --to owner/dataset` for one-shot destination overrides
- Use `dataset publish --check-only` to validate gates without uploading
- Tier 2 LLM review runs inside the workflow; rows arrive at `publish` already verdicted
