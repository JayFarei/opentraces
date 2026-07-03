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
--schema schema.json`, then `opentraces dataset run my-dataset`) have two
executors. `--executor current-agent` is the human/agent-in-the-loop path: it
writes `RUN.md` instructions and executes no script, so it needs a live agent
session and is not a CI pattern by itself. `--executor script` is the
deterministic CI path: it subprocess-runs the workflow package's
`scripts/build_rows.py` with the run packet on `OT_RUN_PACKET`/
`OT_DATASET_OUTPUT`, under an isolated-subprocess primitive (allowlisted env,
redirected `$HOME`) with no live agent required — this is the executor CI
should use for a workflow-backed dataset:

```bash
opentraces dataset run my-dataset --executor script --json
```

Before executing, the run re-verifies the installed workflow package's digest
against the digest the dataset pinned; a mismatch warns by default or, under
`--strict`, fails the run before anything executes:

```bash
opentraces dataset run my-dataset --executor script --strict --json
```

The historical `claude-code-headless` executor value was a permanent stub
that never produced rows; it has been removed (the workflow engine collapse,
seal-family M1) and `dataset run --executor` now only accepts
`current-agent`/`script`. The value stays readable on old serialized records
so they still deserialize, and a recurring `dataset schedule` trigger stored
against the removed value is coerced onto `script` when its next run command
is regenerated.

## Health Checks

Run these before a gated publish:

```bash
opentraces doctor
opentraces doctor --security
opentraces status --short
```

`opentraces status` is the fleet bucket safety dashboard: it reports how many captured traces are cleared for sync versus still unscanned, and the "safe to sync" verdict is structurally impossible while any trace is unscanned. `--short` prints a stable, scriptable one-line summary (`clean` / `not-cleared` / `empty`) suitable for a CI gate; use `--json` for the full `opentraces.bucket.status.v1` envelope.

If you rely on optional integrations, configure them explicitly in automation:

```bash
opentraces setup trufflehog --enable
opentraces setup privacy-filter --enable
```

Those commands assume the required binary or endpoint is already available. LLM row review is no longer configured through `setup llm-review` (that command is hidden but still callable); the canonical row-review surface is `opentraces dataset review` / `opentraces dataset publish`, which apply LLM verdicts as part of the dataset workflow when the workflow requires it.

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
