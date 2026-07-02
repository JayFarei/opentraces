# Troubleshooting

## First Checks

Start with:

```bash
opentraces status
opentraces doctor
opentraces doctor --security
```

`status` is the fleet bucket safety dashboard: scanned/unscanned trace counts and a "safe to sync" verdict that is structurally impossible while any trace is unscanned (`--short` / `--full` / `--project SLUG`). `doctor` tells you whether required integrations are misconfigured. The old per-project capture inbox view is now the hidden `opentraces status-inbox`.

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
opentraces setup codex-cli --dry-run
opentraces setup pi --dry-run --json
opentraces capabilities --json
```

If you are using Claude Code, make sure the capture hooks are installed and that the repo has actual Claude Code session files under `~/.claude/projects/`.

If you are using Codex CLI, make sure Codex itself is installed and
authenticated, then run both `opentraces setup codex-cli` and
`opentraces init --agent codex-cli` in the repo. Codex sessions are captured
after hooks are installed; `--import-existing` is not a Codex backfill path.
Sidecar hook files should appear under `.opentraces/codex-cli/hooks/` after a
future Codex session runs.

If you are using Pi, install `opentraces-pi` with `pi install npm:opentraces-pi`,
then run `opentraces init --agent pi` in the repo. `/ot-capture-status` and
`opentraces setup pi --dry-run --json` show the local checklist. Pi sidecars
should appear under `.opentraces/pi/events/`; raw provider bodies are default-off.

### Codex Hooks File Is Corrupt

`opentraces setup codex-cli` validates `~/.codex/hooks.json` before writing. If
it reports `CORRUPT_HOOKS`, fix the JSON or move the file aside, then rerun:

```bash
opentraces setup codex-cli
opentraces doctor
```

The installer is idempotent and preserves unrelated hooks, but it will not try
to repair malformed JSON automatically.

### Stale Trace Trails

If `trail blame`, `trail graph`, or `dataset run` complain about stale projections, the watcher or backfill may need to catch up:

```bash
opentraces setup watcher status
opentraces setup watcher tick
opentraces backfill
opentraces git-backfill
```

`git-backfill` is particularly useful after a first-time install of the post-commit hook or after a period where the hook failed silently. `backfill` (the attribution-cache refresh) is a hidden maintenance verb: it runs when typed but does not appear in `--help`.

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

If `bucket sync status` reports a diverged or remote-ahead bucket:

```bash
opentraces bucket sync status
opentraces bucket sync diff
opentraces bucket sync pull --force   # remote wins
opentraces bucket sync push --force   # local wins
```

`bucket sync` mirrors the whole raw bucket substrate (traces, blobs, events, manifest) against the configured remote; `bucket sync push` additionally refuses (zero bytes, non-zero exit) if any trace is not yet cleared for sync, so a diverged push may mean traces are still unscanned rather than a real conflict — check `opentraces status` first. The old `bucket remote push|pull|diff|status` spellings still work but are hidden from `--help`.

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

`setup llm-review` is now hidden from `--help` (it configures a publication gate, not a per-record sanitize step) but is still callable exactly as above. The row-level surface that consumes it day to day is `opentraces dataset review` / `opentraces dataset publish`.

### Command Hangs Or Exits 2 Under `--json`

If a command that normally prompts interactively (for example an unconfirmed `setup` step or a review action) instead exits immediately with a structured error and code `2` when run with `--json` or from a non-TTY agent shell, that is expected: interactive commands refuse to prompt under `--json`/non-TTY and emit an `INTERACTIVE_REQUIRED` error with zero prompt bytes rather than hanging on stdin. Re-run interactively, or pass the flags needed to answer the prompt non-interactively (check `--help` for the command's non-interactive form).

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
