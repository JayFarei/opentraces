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
| `remote` | Manage dataset remotes |
| `config` | Show or set config values |
| `setup` | Install integrations like hooks, TruffleHog, and llm-review |
| `doctor` | Check security pipeline and integration health |
| `completions` | Print or install shell completions |

Three more commands are available but intentionally omitted from the default `--help` listing because they are advanced or typically driven by other surfaces: `backfill` (called by `watcher` and `init --import-existing`), `git-backfill` (retroactively correlates inbox traces to past commits after first install of the post-commit hook), and `watcher` (managed by `setup watcher`). All three are documented under [Advanced Commands](#advanced-commands) below.

## Authentication

### `opentraces auth`

```bash
opentraces auth whoami
opentraces auth login
opentraces auth logout
```

Subcommands:

- `login` starts the browser device flow by default
- `login --token` switches to the CI / headless flow and prompts for a PAT (the flag is a boolean, not a value)
- `whoami` reports the active HuggingFace identity
- `logout` clears the stored Hugging Face credential

### `opentraces auth login`

```bash
opentraces auth login
opentraces auth login --token
```

| Flag | Description |
|------|-------------|
| `--token` | Boolean. Switches to the headless flow and prompts for a PAT instead of opening the browser device flow |

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
opentraces remove --all
```

Uninstalls the capture hook, deletes the `.opentraces.json` marker, unregisters the repo from the global registry, and removes the machine-local `~/.opentraces/projects/<slug>/` directory. Pushed datasets on Hugging Face are left untouched.

| Flag | Description |
|------|-------------|
| `--all` | Also delete the audit ref (`refs/opentraces/audit/*`) and the trace-to-commit notes (`refs/notes/opentraces`) from this repository |

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
opentraces redact <trace-id> <pattern>
opentraces redact <trace-id> "ACME_INTERNAL_TOKEN"
opentraces redact <trace-id> "sk-[A-Za-z0-9]+" --regex
opentraces redact <trace-id> "secret" --field observations --step 3
```

Find and replace text in a stored trace. `PATTERN` is a required positional argument; without `--regex` it is treated as a literal string.

| Flag | Description |
|------|-------------|
| `--regex` | Treat `PATTERN` as a regular expression instead of a literal string |
| `--field TEXT` | Limit the rewrite to one field (e.g. `prompt`, `observations`, `outcome`) |
| `--step INTEGER` | Limit the rewrite to a specific step index |

### `opentraces discard`

```bash
opentraces discard <trace-id>
opentraces discard <trace-id> --yes
```

Permanently deletes the local trace.

| Flag | Description |
|------|-------------|
| `--yes` | Skip the interactive confirmation prompt |

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
| `--parser TEXT` | **Required.** Import format parser, currently `hermes` |
| `--subset TEXT` | Dataset subset or config |
| `--split TEXT` | Dataset split, default `train` |
| `--limit INTEGER` | Max rows to import, `0` for all |
| `--auto` | Auto-commit imported traces |
| `--dry-run` | Parse and report without writing |

### `opentraces export`

```bash
opentraces export --format agent-trace
opentraces export --format atif
opentraces export --format atif --output /tmp/traces.jsonl
```

Exports staged traces to another format. If no traces are staged the command exits 0 with a notice and does not create the output file.

| Flag | Description |
|------|-------------|
| `--format [atif&#124;agent-trace]` | **Required.** Target format |
| `--output PATH` | Output file path. Default `./opentraces-export.jsonl` |

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

For the LLM trace review tier, `doctor` also surfaces the active setup: backend and model, endpoint URL, API format, whether the configured `api_key_env` variable is set, and the probe result (e.g. model count, unreachable reason, or `not found` when the configured model is missing from the endpoint's catalog). Use this to confirm that `opentraces setup llm-review` wrote the expected values before running `opentraces llm-review`.

## Remote Management

### `opentraces remote`

```bash
opentraces remote list
opentraces remote list -v
opentraces remote add owner/dataset
opentraces remote create owner/team-traces --private
opentraces remote visibility owner/dataset --public
opentraces remote remove owner/dataset
opentraces remote remove owner/dataset --delete-remote --yes
opentraces remote delete owner/dataset --yes
```

Subcommands:

- `add` connects an existing dataset
- `create` creates a new dataset and connects it
- `list` shows connected remotes
- `remove` disconnects a remote locally
- `delete` deletes the remote dataset and disconnects it
- `visibility` flips a remote between private and public

Positional `REPO` is optional on `remove` and `delete` when exactly one remote is connected.

#### `opentraces remote list`

| Flag | Description |
|------|-------------|
| `-v` | Show full dataset URLs instead of the short `owner/name` form |

#### `opentraces remote create`

| Flag | Description |
|------|-------------|
| `--private / --public` | Visibility of the new dataset (default `--private`) |
| `--gated` | Enable gated access on the new dataset |

#### `opentraces remote visibility`

| Flag | Description |
|------|-------------|
| `--private / --public` | Target visibility for the remote |

#### `opentraces remote remove`

| Flag | Description |
|------|-------------|
| `--delete-remote` | Also delete the upstream Hugging Face dataset, not just the local connection |
| `--yes` | Skip the interactive confirmation prompt |

#### `opentraces remote delete`

| Flag | Description |
|------|-------------|
| `--yes` | Skip the interactive confirmation prompt |

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

| Flag | Description |
|------|-------------|
| `--review` | Set policy to `review` (manual approval required before push) |
| `--auto` | Set policy to `auto` (safe traces are auto-staged; push remains explicit) |
| `--print` | Print the current policy and exit without writing |
| `--project` | Write to the project marker. Default for this command |

### `opentraces setup claude-code`

```bash
opentraces setup claude-code
opentraces setup claude-code --dry-run
opentraces setup claude-code --remove
```

Installs (or removes) the Claude Code capture hooks into your Claude Code settings file.

| Flag | Description |
|------|-------------|
| `--hooks-dir TEXT` | Directory to drop hook scripts into. Default `~/.claude/hooks/` |
| `--settings-file TEXT` | Path to the Claude Code settings file. Default `~/.claude/settings.json` |
| `--dry-run` | Print the planned hook changes without writing |
| `--remove` | Uninstall previously-installed hooks |

### `opentraces setup entity-parser`

```bash
opentraces setup entity-parser
opentraces setup entity-parser --force
```

Downloads and verifies the `ot-entities` binary used to expand function/class changes in `blame` and `graph`.

| Flag | Description |
|------|-------------|
| `--force` | Re-download even if the binary is already installed |

### `opentraces setup git`

```bash
opentraces setup git
opentraces setup git --remove
```

Installs the post-commit correlator hook that attributes commits to traces.

| Flag | Description |
|------|-------------|
| `--remove` | Uninstall the hook |

### `opentraces setup skill`

```bash
opentraces setup skill
opentraces setup skill --harness claude-code
opentraces setup skill --remove
```

Installs the `opentraces` skill so Claude Code (and compatible harnesses) can drive the CLI.

| Flag | Description |
|------|-------------|
| `--harness TEXT` | Target harness, repeatable (e.g. `claude-code`). Defaults to every supported harness |
| `--remove` | Uninstall the skill |

### `opentraces setup watcher`

```bash
opentraces setup watcher
opentraces setup watcher --interval 600
opentraces setup watcher --no-install
opentraces setup watcher --uninstall
```

Installs (or removes) the background attribution watcher service. The watcher polls enlisted projects and runs `backfill` when new commits or Claude Code sessions appear.

| Flag | Description |
|------|-------------|
| `--interval INTEGER` | Poll interval in seconds. Default `300` |
| `--no-install` | Update config only; don't install the system service |
| `--uninstall` | Remove the installed service |

### `opentraces setup upgrade`

```bash
opentraces setup upgrade
opentraces setup upgrade --skill-only
```

Upgrades the opentraces CLI and refreshes project-side files (skill, hooks) where relevant.

| Flag | Description |
|------|-------------|
| `--skill-only` | Refresh only the installed skill, skip the CLI upgrade step |

## Advanced Commands

The first commands in this section (`blame`, `graph`, `resume`, `stats`, `log`, `completions`) are part of the default `--help` listing. `backfill`, `git-backfill`, and `watcher` are not: they are real commands but are intentionally hidden from the default listing because they are usually driven by `watcher`, `setup git`, and `setup watcher` respectively.

### `opentraces blame`

```bash
opentraces blame abc1234                          # Commit-mode (bare SHA)
opentraces blame c:abc1234 src/main.py            # Commit-mode, single file
opentraces blame abc1234 --lines                  # Per-line view
opentraces blame t:4dccb032                       # Trace-mode (canonical)
opentraces blame s:92437382 --include-overlapping # Trace-mode (upstream session)
opentraces blame abc1234 --json                   # Structured output
```

Two modes, one argument:

- **Commit-mode** (`c:<sha>` or bare SHA): which traces contributed to this commit. Uses the attribution cache for per-line detail and merges `refs/notes/opentraces` so hook-linked traces surface even when the attribution cache has no per-line data for that commit.
- **Trace-mode** (`t:<trace-id>`, `s:<session-id>`, or a bare hyphenated UUID): which commits carry this trace's output. Merges attribution-cache rows (fine-grained) with the trace's `git_links` (hook-linked). Hook-linked rows carry only a tier badge, not per-line counts.

Commit-mode requires a populated attribution cache — run `opentraces backfill` if empty. Trace-mode works from `git_links` alone, so it surfaces hook-linked commits even when per-line attribution hasn't been computed yet.

| Flag | Description |
|------|-------------|
| `--lines` | Per-line output (git-blame-style). Commit-mode only. |
| `--entities` | Expand entity changes (functions, classes) under each trace. Commit-mode only. |
| `--include-overlapping` | Trace-mode: include commits where files and timestamps overlap without direct tool-emit evidence. Off by default. |
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

### `opentraces git-backfill`

```bash
opentraces git-backfill
opentraces git-backfill --max-commits 2000 --window-hours 48
opentraces git-backfill --json
```

Retroactively correlates inbox traces to past commits. Useful after a first-time install of the post-commit hook (the hook only sees commits after install) or after a period where the hook failed silently. Walks first-parent history, re-runs the live correlator, writes `refs/notes/opentraces`, and persists `git_links` onto each trace's JSONL file. Safe to re-run: notes dedupe on append and `git_links` dedupe before rewrite.

| Flag | Description |
|------|-------------|
| `--project DIRECTORY` | Project directory, default CWD |
| `--max-commits INTEGER` | Cap on first-parent commits to walk. Default `500`. |
| `--window-hours FLOAT` | Match a trace to a commit if `timestamp_end` is within this many hours of the commit's date (either side). Default `24.0`. |
| `--json` | Emit a JSON payload instead of the human summary |

### `opentraces watcher`

```bash
opentraces watcher start
opentraces watcher start --interval 600 --no-install
opentraces watcher status
opentraces watcher status --json
opentraces watcher tick
opentraces watcher tick --project /path/to/repo --json
opentraces watcher stop
opentraces watcher restart
opentraces watcher uninstall
```

Manages the background attribution watcher service (installed by `opentraces setup watcher`). The watcher polls enlisted projects and runs incremental `backfill` when new commits or Claude Code sessions appear.

Subcommands: `start`, `stop`, `restart`, `status`, `tick` (one diagnostic pass), `uninstall`.

#### `opentraces watcher start`

| Flag | Description |
|------|-------------|
| `--interval INTEGER` | Poll interval in seconds. Default `300` |
| `--no-install` | Start in foreground only; don't register the system service |

#### `opentraces watcher status`

| Flag | Description |
|------|-------------|
| `--json` | Emit structured JSON instead of the human summary |

#### `opentraces watcher tick`

| Flag | Description |
|------|-------------|
| `--project DIRECTORY` | Tick only the given project. Default: every enlisted project |
| `--json` | Emit structured JSON instead of the human summary |

`stop`, `restart`, and `uninstall` take no flags beyond `--help`.

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
opentraces log --verbose
opentraces log --limit 0
```

Lists the recent traces that have been pushed, grouped by date. Only the `pushed` stage is walked, so in-progress Inbox or staged work is ignored.

Default output is one row per day with the push count, the destination remote(s), and the local time range of pushes:

```
2026-04-16  6 pushed   → origin   10:36–17:44
2026-04-15  1 pushed   → origin   11:42
```

`--verbose` / `-v` expands each day into per-trace rows with a short trace id, push time, model, and the first line of the task description, with total tokens and an estimated cost per day. The verbose view reads each trace file so it is slower on large inboxes:

```
2026-04-16  6 pushed   → origin   10:36–17:44   (430.7k tokens, ~$225.90)
  785ddc93  10:36  opus-4-6            fix(cli): restore opentraces log…   [62.6k tokens]
  2cfe7e14  10:36  opus-4-6            docs: audit commands reference      [203.0k tokens]
  …
```

| Flag | Description |
|------|-------------|
| `--limit INTEGER` | Max days of history to show. `0` for no limit. Default `30`. |
| `-v, --verbose` | Expand each day into per-trace rows with model, token totals, and task description |

### `opentraces completions`

```bash
opentraces completions install
opentraces completions install zsh --alias otd
opentraces completions install zsh --alias otd --alias ot --quiet
opentraces completions uninstall
opentraces completions uninstall zsh --quiet
```

Prints or installs shell completion scripts. Both `install` and `uninstall` take an optional positional shell name (`bash`, `zsh`, or `fish`); if omitted, the current shell is detected automatically.

#### `opentraces completions install`

| Flag | Description |
|------|-------------|
| `--alias NAME` | Also bind completion to `NAME`. Repeatable (e.g. `--alias otd --alias ot`) |
| `-q, --quiet` | Suppress the confirmation output |

#### `opentraces completions uninstall`

| Flag | Description |
|------|-------------|
| `-q, --quiet` | Suppress the confirmation output |
