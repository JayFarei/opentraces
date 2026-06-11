# CI/CD & Automation

Use the same explicit workflow in automation that you use locally: initialize, capture, run dataset workflows, publish.

## Authentication

`HF_TOKEN` is the preferred CI path:

```bash
export HF_TOKEN=hf_...
```

You do not need to run `opentraces auth login` when `HF_TOKEN` is already set in the environment.

## Recommended Pattern

For headless runs, seed from a JSONL file produced earlier in the pipeline:

```bash
opentraces init --agent claude-code
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
opentraces dataset review approve my-import --all
opentraces dataset publish my-import --to my-org/dataset
```

Workflow-driven runs (`opentraces dataset new my-dataset --workflow my-workflow
--schema schema.json`, then `opentraces dataset run my-dataset`) are designed to
execute inside an agent session with `--executor current-agent`. The
`--executor claude-code-headless` choice is a reserved seam: it currently exits
with an executor-unavailable error instead of invoking Claude Code, so it is
not yet a working CI pattern.

## Health Checks

Run these before a gated publish:

```bash
opentraces doctor
opentraces doctor --security
```

If you rely on optional integrations, configure them explicitly in automation:

```bash
opentraces setup trufflehog --enable
opentraces setup privacy-filter --enable
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

- name: Create dataset from prepared rows
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    opentraces dataset new ci-dataset --rows-file rows.jsonl --schema schema.json

- name: Approve and publish
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    opentraces dataset review approve ci-dataset --all
    opentraces dataset remote create ci-dataset my-org/dataset --private
    opentraces dataset publish ci-dataset
```

## Notes

- Use `--private` (the default for `dataset remote create`) for proprietary codebases
- Use `dataset publish --to owner/dataset` for one-shot destination overrides
- Use `dataset publish --check-only` to validate gates without uploading
- Optional LLM review runs inside the workflow; rows arrive at `publish` already verdicted when the workflow requires it
