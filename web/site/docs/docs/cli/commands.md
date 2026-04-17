# Commands

Reference for the current 0.3 `opentraces` CLI.

## Root Command

```bash
opentraces [--json] <command> ...
```

Use `--json` on any command when you want machine-readable output instead of the TTY view.

The current public root commands are:

| Command | What it does |
|---------|---------------|
| `auth` | Log in to Hugging Face, log out, or inspect the active identity |
| `init` | Initialize opentraces in the current repo |
| `remove` | Remove opentraces from the current repo |
| `status` | Show a project snapshot and recent traces |
| `list` | List traces, or list initialized projects with `--projects` |
| `show` | Show one trace in detail |
| `add` | Stage Inbox traces for the next push |
| `reject` | Mark a trace local-only |
| `reset` | Move a trace back to Inbox |
| `redact` | Rewrite sensitive text in a trace |
| `discard` | Permanently delete a local trace |
| `push` | Upload staged traces to Hugging Face Hub |
| `pull` | Import traces from a Hugging Face dataset |
| `llm-review` | Run Tier 2 semantic review over traces |
| `assess` | Score trace quality locally or on a dataset |
| `web` | Open the browser inbox UI |
| `tui` | Open the terminal inbox UI |
| `blame` | Show per-commit attribution for a SHA (optionally one file) |
| `graph` | Render commit + trace history (commit-primary or trace-primary) |
| `resume` | Resume the upstream agent session behind a trace |
| `export` | Export staged traces to another format |
| `log` | List uploaded traces grouped by date |
| `stats` | Show aggregate inbox statistics |
| `backfill` | Backfill per-commit attribution into the local cache |
| `watcher` | Manage the background attribution watcher service |
| `remote` | Manage dataset remotes |
| `config` | Show or set config values |
| `setup` | Install integrations like hooks, TruffleHog, and llm-review |
| `doctor` | Check security pipeline and integration health |
| `completions` | Print or install shell completions |

## Authentication

### `opentraces auth`

```bash
opentraces auth whoami
opentraces auth status
opentraces auth login
opentraces auth logout
```

Subcommands:

- `login` starts the browser device flow by default
- `login --token` accepts a PAT for CI or headless environments
- `status` is an alias for `whoami`
- `logout` clears the stored Hugging Face credential

### `opentraces auth login`

```bash
opentraces auth login
opentraces auth login --token
```

| Flag | Description |
|------|-------------|
| `--token` | Paste a PAT instead of using the browser flow |

## Project Commands

### `opentraces init`

```bash
opentraces init
opentraces init --review-policy review
opentraces init --review-policy auto
opentraces init --remote owner/my-traces --public
opentraces init --import-existing
```

Initializes the current repo, writes `.opentraces.json`, registers machine-local state under `~/.opentraces/projects/<slug>/`, and installs the capture hook unless you pass `--no-hook`.

| Flag | Description |
|------|-------------|
| `--agent [claude-code]` | Agent runtime to connect |
| `--no-hook` | Skip Claude Code hook installation |
| `--import-existing / --start-fresh` | Backfill existing Claude Code traces for this repo, or start from the next run |
| `--review-policy [review&#124;auto]` | Whether safe traces require manual review |
| `--remote TEXT` | Hugging Face dataset repo in `owner/name` form |
| `--private / --public` | Default visibility when creating the remote |

### `opentraces remove`

```bash
opentraces remove
```

Removes opentraces from the current repo.

### `opentraces status`

```bash
opentraces status
opentraces status --limit 0
```

Shows stage counts, the active remote, and recent traces (default limit `10`).

| Flag | Description |
|------|-------------|
| `--limit INTEGER` | How many recent traces to show, `0` for all. Default `10`. |

## Review And Inbox Commands

### `opentraces list`

```bash
opentraces list
opentraces list --stage inbox
opentraces list --remote origin
opentraces list --projects
opentraces list --by-commit
```

| Flag | Description |
|------|-------------|
| `--projects` | List initialized projects instead of traces |
| `--remote TEXT` | Filter to traces missing on the named remote |
| `--stage TEXT` | Filter by visible stage |
| `--model TEXT` | Filter by model |
| `--agent TEXT` | Filter by agent |
| `--limit INTEGER` | Max rows to show |
| `--by-commit` | Group results by commit |

Visible stages are `inbox`, `staged`, `pushed`, `rejected`, and `blocked`.

### `opentraces show`

```bash
opentraces show <trace-id>
opentraces show <trace-id> --verbose
opentraces show <trace-id> --markdown
```

`show` prints the trace prompt, steps, tool calls, observations, and outcome. Human output truncates long step content unless you pass `--verbose`.

| Flag | Description |
|------|-------------|
| `--verbose` | Show full step content |
| `--markdown` | Emit the trace wrapped for safe LLM handoff |

### `opentraces add`

```bash
opentraces add <trace-id>
opentraces add abc12 def34
opentraces add --all
```

Stages Inbox traces for the next push.

| Flag | Description |
|------|-------------|
| `--all` | Stage every Inbox trace |

`add` refuses `blocked` and `rejected` traces.

### `opentraces reject`

```bash
opentraces reject <trace-id>
```

Marks a trace local-only so it will not be pushed.

### `opentraces reset`

```bash
opentraces reset <trace-id>
```

Moves a trace back to Inbox.

### `opentraces redact`

```bash
opentraces redact <trace-id>
opentraces redact <trace-id> --step 3
```

Find and replace sensitive text in a stored trace.

### `opentraces discard`

```bash
opentraces discard <trace-id> --yes
```

Permanently deletes the local trace.

### `opentraces web`

```bash
opentraces web
opentraces web --port 6060 --no-open
```

| Flag | Description |
|------|-------------|
| `--port INTEGER` | Port for the local web inbox |
| `--no-open` | Do not open the browser automatically |

### `opentraces tui`

```bash
opentraces tui
opentraces tui --fullscreen
opentraces tui --limit 0
```

| Flag | Description |
|------|-------------|
| `--fullscreen` | Open directly into fullscreen inspect mode |
| `--limit INTEGER` | Maximum traces to load, `0` for all |

## Push And Import

### `opentraces push`

```bash
opentraces push
opentraces push --private
opentraces push --llm-review
opentraces push --repo owner/team-traces
opentraces push --no-assess
```

Uploads staged traces to Hugging Face Hub as a new shard.

| Flag | Description |
|------|-------------|
| `--private` | Force private visibility |
| `--public` | Force public visibility |
| `--publish` | Change an existing private dataset to public without uploading |
| `--gated` | Enable gated access on the dataset |
| `--repo TEXT` | Destination repo, defaulting to `username/opentraces` |
| `--assess / --no-assess` | Run quality scoring and include dataset-card badges |
| `--llm-review` | Require a clean Tier 2 verdict on every staged trace |
| `--no-trufflehog` | Skip Tier 1.5 TruffleHog for this push only |
| `--migrate-remote / --no-migrate-remote` | Auto-migrate older-schema remote shards |
| `-y, --yes` | Skip interactive prompts |

### `opentraces pull`

```bash
opentraces pull owner/dataset --parser hermes
opentraces pull owner/dataset --parser hermes --limit 10 --dry-run
opentraces pull owner/dataset --parser hermes --auto
```

Imports traces from a Hugging Face dataset.

| Flag | Description |
|------|-------------|
| `--parser TEXT` | Import format parser, currently `hermes` |
| `--subset TEXT` | Dataset subset or config |
| `--split TEXT` | Dataset split, default `train` |
| `--limit INTEGER` | Max rows to import, `0` for all |
| `--auto` | Auto-commit imported traces |
| `--dry-run` | Parse and report without writing |

### `opentraces export`

```bash
opentraces export --format agent-trace
opentraces export --format atif
```

Exports staged traces to another format.

## Quality And Security

### `opentraces assess`

```bash
opentraces assess
opentraces assess --judge --judge-model sonnet
opentraces assess --dataset owner/team-traces
opentraces assess --explain
```

Local mode assesses staged traces, falling back to all local traces if nothing is staged yet.

| Flag | Description |
|------|-------------|
| `--limit INTEGER` | Max traces to assess |
| `--dataset TEXT` | Assess a remote Hugging Face dataset |
| `--judge / --no-judge` | Enable the LLM judge |
| `--judge-model [haiku&#124;sonnet&#124;opus]` | Judge model |
| `--dry-run` | Print the assessment only |
| `--explain` | Show the rubric glossary and exit |

### `opentraces llm-review`

```bash
opentraces llm-review
opentraces llm-review --scope staged
opentraces llm-review --trace 8a3f1c
opentraces llm-review --dry-run
```

Runs Tier 2 semantic review using the provider configured by `opentraces setup llm-review`, unless you override it on the command line.

| Flag | Description |
|------|-------------|
| `--api-format [openai-compat&#124;ollama&#124;anthropic&#124;fake]` | Override the wire protocol |
| `--model TEXT` | Override the model |
| `--base-url TEXT` | Override the OpenAI-compatible base URL |
| `--api-key-env TEXT` | Override the env var containing the API key |
| `--scope [all&#124;inbox&#124;staged]` | Choose which traces to review |
| `--trace TEXT` | Review specific trace IDs, repeatable |
| `--limit INTEGER` | Cap the batch size |
| `--dry-run` | Estimate token usage only |
| `--force` | Re-review traces that already have a cached verdict |
| `--context-file FILE` | Pass project context such as `README.md` or `AGENTS.md` |

### `opentraces doctor`

```bash
opentraces doctor
opentraces doctor --security
```

Checks configured integrations, versions, and security tiers. It exits non-zero when a required integration is broken.

| Flag | Description |
|------|-------------|
| `--security` | Show only the security pipeline view |

## Remote Management

### `opentraces remote`

```bash
opentraces remote list
opentraces remote add owner/dataset
opentraces remote create owner/team-traces --private
opentraces remote visibility owner/dataset --public
opentraces remote remove owner/dataset
opentraces remote delete owner/dataset
```

Subcommands:

- `add` connects an existing dataset
- `create` creates a new dataset and connects it
- `list` shows connected remotes
- `remove` disconnects a remote locally
- `delete` deletes the remote dataset and disconnects it
- `visibility` flips a remote between private and public

## Configuration And Setup

### `opentraces config show`

```bash
opentraces config show
opentraces --json config show
```

Shows the effective config with secrets masked.

### `opentraces config set`

```bash
opentraces config set classifier_sensitivity high
opentraces config set custom_redact_strings ACME_INTERNAL_TOKEN --append
opentraces config set excluded_projects /path/to/repo --append
opentraces config set review_policy auto --project
```

| Flag | Description |
|------|-------------|
| `--project` | Write to `<repo>/.opentraces.json` |
| `--global` | Write to `~/.opentraces/config.json` |
| `--append` | Append to a list-typed key |

Default scope is global.

### `opentraces setup`

```bash
opentraces setup
opentraces setup claude-code
opentraces setup git
opentraces setup trufflehog
opentraces setup llm-review
opentraces setup review-policy --auto
opentraces setup upgrade
```

Current setup subcommands:

- `claude-code` installs the capture hooks
- `entity-parser` downloads and verifies the `ot-entities` binary
- `git` installs the post-commit correlator hook
- `llm-review` configures the Tier 2 reviewer
- `review-policy` changes the repo's review policy
- `skill` installs the opentraces skill globally
- `trufflehog` enables Tier 1.5 TruffleHog
- `upgrade` upgrades the CLI and refreshes project files
- `watcher` installs or removes the background attribution watcher

### `opentraces setup trufflehog`

```bash
opentraces setup trufflehog
opentraces setup trufflehog --enable
opentraces setup trufflehog --disable
```

| Flag | Description |
|------|-------------|
| `--enable` | Turn Tier 1.5 on, failing if the binary is not present |
| `--disable` | Turn Tier 1.5 off |
| `--project` | Scope the setting to the project marker instead of global config |

Tier 1.5 findings are redacted in place and force human review before push.

### `opentraces setup llm-review`

```bash
opentraces setup llm-review
opentraces setup llm-review --api-format openai-compat --base-url http://localhost:11434/v1 --model gemma3n:e4b
opentraces setup llm-review --disable
opentraces setup llm-review --print
```

| Flag | Description |
|------|-------------|
| `--api-format [openai-compat&#124;ollama&#124;anthropic&#124;fake]` | Reviewer transport |
| `--base-url TEXT` | Base URL for OpenAI-compatible backends |
| `--model TEXT` | Model name |
| `--api-key-env TEXT` | Env var holding the API key |
| `--timeout FLOAT` | Request timeout |
| `--disable` | Turn llm-review off |
| `--enable` | Turn llm-review on using the current config |
| `--test` | Ping the endpoint without writing config |
| `--print` | Print the effective config |
| `--no-interactive` | Skip the preset picker |
| `--project` | Scope the change to the project marker |

### `opentraces setup review-policy`

```bash
opentraces setup review-policy --review
opentraces setup review-policy --auto
opentraces setup review-policy --print
```

`--auto` auto-approves safe traces into `staged`. Push remains explicit.

## Other Commands

### `opentraces blame`

```bash
opentraces blame abc1234
opentraces blame abc1234 src/main.py
opentraces blame abc1234 --lines
opentraces blame abc1234 --json
```

Shows per-commit attribution for a commit SHA. Accepts a bare SHA or the `c:<sha>` prefixed form. Pass a path as the second positional argument to scope output to one file. Requires a populated attribution cache (run `opentraces backfill` if empty).

| Flag | Description |
|------|-------------|
| `--lines` | Per-line output (git-blame-style) |
| `--entities` | Expand entity changes (functions, classes) under each trace |
| `--project DIRECTORY` | Project directory, default CWD |
| `--json` | Emit structured JSON instead of text |
| `--no-color` | Disable ANSI colors |

### `opentraces graph`

```bash
opentraces graph
opentraces graph --limit 50
opentraces graph --trace abc12
opentraces graph --since HEAD~20 --until HEAD
```

Renders commit + trace history. Commit-primary by default: the git log is the spine and each commit shows the traces that touched it. Pass `--trace <id>` to pivot to trace-primary mode. Requires a populated attribution cache.

| Flag | Description |
|------|-------------|
| `--limit INTEGER` | Commits per page. Default `20`. |
| `--page INTEGER` | Page number (1-indexed). |
| `--all` | Disable pagination (large `--limit`). |
| `--trace TEXT` | Pivot to trace-primary mode for the given trace id. |
| `--since TEXT` | Show commits after this ref. |
| `--until TEXT` | Show commits up to this ref. |
| `--project DIRECTORY` | Project directory, default CWD. |
| `--entities` | Include entity-change suffixes (requires entity cache). |
| `--no-color` | Disable ANSI colors. |

### `opentraces backfill`

```bash
opentraces backfill
opentraces backfill --rebuild
opentraces backfill --dry-run
```

Backfills per-commit attribution into the local cache. Walks new commits since the last bookmark, correlates them to traces, and populates entity data when the `ot-entities` binary is available.

| Flag | Description |
|------|-------------|
| `--dry-run` | Compute coverage without writing cache files. |
| `--rebuild` | Clear the cache and re-attribute from HEAD. |
| `--since TEXT` | Start from this ref instead of the bookmark (currently forces `--rebuild`). |
| `--project DIRECTORY` | Project directory, default CWD. |
| `--max-commits INTEGER` | Cap on commits to walk when rebuilding. Default `500`. |
| `--json` | Emit a JSON payload instead of the human summary. |
| `-v, --verbose` | Forward verbose logging to the audit builder. |
| `--no-entities` | Skip the entity-parser pass (attribution only). |

### `opentraces watcher`

```bash
opentraces watcher start
opentraces watcher status
opentraces watcher tick
opentraces watcher stop
opentraces watcher uninstall
```

Manages the background attribution watcher service (installed by `opentraces setup watcher`). The watcher polls enlisted projects and runs incremental `backfill` when new commits or Claude Code sessions appear.

Subcommands: `start`, `stop`, `restart`, `status`, `tick` (one diagnostic pass), `uninstall`.

### `opentraces resume`

```bash
opentraces resume <trace-id>
opentraces resume <trace-id> --dry-run
opentraces resume <trace-id> --at-step s42
```

Resumes the upstream agent session behind a trace. Accepts a full `trace_id` or a `t:XX` / `XX` prefix (2+ chars). For claude-code the command execs `claude --resume <session_id>`; other agents print the native resume command instead.

| Flag | Description |
|------|-------------|
| `--at-step TEXT` | Fork a new Claude Code session from a specific step id (e.g. `s42`). |
| `--dry-run` | Print the resume command instead of exec'ing it. |

### `opentraces stats`

```bash
opentraces stats
```

Rolls up every local trace into counts, token totals, cost estimates, and a model breakdown.

### `opentraces log`

```bash
opentraces log
opentraces log --limit 0
```

Lists uploaded traces grouped by date. Walks the `pushed` stage only.

| Flag | Description |
|------|-------------|
| `--limit INTEGER` | Max days of history to show. `0` for no limit. Default `30`. |

### `opentraces completions`

```bash
opentraces completions install
opentraces completions install zsh --alias otd
opentraces completions uninstall
```

Prints or installs shell completion scripts.
