# CI/CD & Automation

Use the same explicit workflow in automation that you use locally: initialize, inspect, stage, push.

## Authentication

`HF_TOKEN` is the preferred CI path:

```bash
export HF_TOKEN=hf_...
```

You do not need to run `opentraces auth login` when `HF_TOKEN` is already set in the environment.

## Recommended Pattern

For headless runs:

```bash
opentraces init --review-policy review --remote my-org/opentraces --no-hook
opentraces list
opentraces add --all
opentraces push --private --yes
```

If the runner needs to import traces from another dataset first:

```bash
opentraces pull owner/dataset --parser hermes --auto
opentraces push --private --yes
```

## Health Checks

Run these before a gated push:

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
  run: pip install opentraces

- name: Initialize project
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: opentraces init --review-policy review --remote my-org/opentraces --no-hook

- name: Stage traces
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: opentraces add --all

- name: Push traces
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: opentraces push --private --yes
```

## Notes

- Use `--private` for proprietary codebases
- Use `--repo owner/dataset` or `--remote ...` for shared team datasets
- Use `push --llm-review` only if llm-review is already configured and reachable on the runner
