# Troubleshooting

## First Checks

Start with:

```bash
opentraces status
opentraces doctor
opentraces doctor --security
```

`status` tells you what the current repo thinks is captured or pending. `doctor` tells you whether required integrations are misconfigured.

## Common Problems

### Not Initialized

If the CLI says the repo is not initialized, run:

```bash
opentraces init
```

The current repo marker is `.opentraces.json`, not `.opentraces/config.json`.

### No Traces Showing Up

Check:

```bash
opentraces status
opentraces trace query --cwd --since 1d
opentraces setup claude-code
```

If you are using Claude Code, make sure the capture hooks are installed and that the repo has actual Claude Code session files under `~/.claude/projects/`.

### Stale Trace Trails

If `trail blame`, `trail graph`, or `dataset run` complain about stale projections, the watcher or backfill may need to catch up:

```bash
opentraces setup watcher status
opentraces setup watcher tick
opentraces backfill
opentraces git-backfill
```

`git-backfill` is particularly useful after a first-time install of the post-commit hook or after a period where the hook failed silently.

### Blocked Rows in a Dataset

Inspect the dataset state:

```bash
opentraces dataset status my-dataset
opentraces dataset review my-dataset
```

Then approve, reject, or reset rows directly from the CLI. The legacy `--tui`
and `--web` flags currently return decommission notices.

```bash
opentraces dataset review reset my-dataset <row-id>
opentraces dataset review reject my-dataset <row-id>
opentraces dataset review approve my-dataset <row-id>
```

### Publish Fails

Common causes:

- no Hugging Face auth (`opentraces auth login`)
- no remote bound to the dataset (`opentraces dataset remote create ...`)
- survival-state filter excluded every row (`--min-retention` too high, or `--exclude-state` covered everything)
- a configured integration is broken

Useful commands:

```bash
opentraces auth whoami
opentraces dataset remote list my-dataset
opentraces dataset publish my-dataset --check-only --json
opentraces doctor
```

### Bucket Sync Diverged

If `bucket remote status` reports a diverged or remote-ahead bucket:

```bash
opentraces bucket remote status
opentraces bucket remote diff
opentraces bucket remote pull --force   # remote wins
opentraces bucket remote push --force   # local wins
```

Use `--force` carefully: it overwrites the slower side.

### TruffleHog Enabled But Missing

If `doctor` reports that TruffleHog is enabled but unavailable:

```bash
opentraces setup trufflehog
# or
opentraces setup trufflehog --disable
```

### LLM Review Unreachable

First check what `doctor` sees:

```bash
opentraces doctor --security
```

The LLM trace review row shows the configured backend and model, the endpoint URL, whether the `api_key_env` variable is set, and the probe result against that endpoint. Common signals:

- `probe: ... not found`, the configured model is not in the endpoint's catalog (pull it or update the model name)
- `probe: UNREACHABLE ...`, the endpoint did not respond (start the local server, check the URL)
- `api key env: $VAR (unset)`, remote backend needs an API key that is not exported in this shell

Re-test or reconfigure:

```bash
opentraces setup llm-review --test
opentraces setup llm-review
opentraces setup llm-review --disable
```

### Resetting A Repo

To remove opentraces from the current repo cleanly:

```bash
opentraces remove
opentraces remove --all
```

To clear the stored Hugging Face credential:

```bash
opentraces auth logout
```
