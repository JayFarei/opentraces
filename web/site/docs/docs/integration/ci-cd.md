# CI/CD & Automation

Headless environments can use the same inbox and upload model, but the commands should be run explicitly.

## Authentication

```bash
export HF_TOKEN=hf_...
```

or:

```bash
opentraces login --token
```

`HF_TOKEN` is the preferred path in CI.

## Recommended Pattern

If you are running opentraces in automation, keep the steps explicit:

```bash
opentraces init --review-policy review --remote my-org/opentraces --no-hook
opentraces session list
opentraces session commit <trace-id>
opentraces commit --all
opentraces push --private
```

Your CI script should call `commit` and `push` directly.

## GitHub Actions

```yaml
- name: Install opentraces
  run: pip install opentraces

- name: Authenticate with Hugging Face
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: opentraces login --token

- name: Commit and push traces
  env:
    HF_TOKEN: ${{ secrets.HF_TOKEN }}
  run: |
    opentraces commit --all
    opentraces push --private
```

## Notes

- Use `--private` for proprietary codebases
- Use `--repo owner/dataset` if you want a shared team dataset
- If you need to capture a specific session directory yourself, wire the hidden `_capture` command into your own hook or runner
