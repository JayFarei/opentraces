# Commands

Complete reference for the current opentraces CLI surface.

## Public Commands

| Command | Description |
|---------|-------------|
| `opentraces login` | Authenticate with Hugging Face Hub |
| `opentraces logout` | Clear stored credentials |
| `opentraces whoami` | Print the active Hugging Face identity |
| `opentraces auth` | Authentication subcommands (`login`, `logout`, `status`) |
| `opentraces init` | Initialize the current project inbox |
| `opentraces remove` | Remove the local inbox from the current project |
| `opentraces status` | Show inbox status and counts |
| `opentraces remote` | Manage the configured dataset remote |
| `opentraces session` | Inspect and edit staged traces |
| `opentraces commit` | Commit inbox traces for upload |
| `opentraces push` | Upload committed traces to Hugging Face Hub |
| `opentraces web` | Open the browser inbox UI |
| `opentraces tui` | Open the terminal inbox UI |
| `opentraces stats` | Show aggregate inbox statistics |
| `opentraces context` | Return machine-readable project context |
| `opentraces config show` | Display current config |
| `opentraces config set` | Update config values |

## Authentication

### `opentraces login`

Authenticate with Hugging Face Hub.

```bash
opentraces login --token
opentraces login
```

| Flag | Default | Description |
|------|---------|-------------|
| `--token` | off | Paste a personal access token (required for pushing) |

> **Recommended:** Use `opentraces login --token` with a write-access PAT from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). The browser OAuth flow (`opentraces login` without `--token`) authenticates your identity but cannot create or push to dataset repos.

### `opentraces logout`

Clear stored Hugging Face credentials.

### `opentraces auth`

Authentication subcommands:

```bash
opentraces auth status
opentraces auth login
opentraces auth logout
```

## Project Setup

### `opentraces init`

Initialize opentraces in the current project directory. Creates `.opentraces/config.json`, `.opentraces/staging/`, and the Claude Code hook.
If Claude Code already has session files for this repo, the interactive flow can import that backlog into the inbox immediately.

```bash
opentraces init
opentraces init --review-policy review --start-fresh
opentraces init --review-policy auto --import-existing
opentraces init --review-policy review --remote your-name/opentraces --start-fresh
```

| Flag | Default | Description |
|------|---------|-------------|
| `--agent` | detected interactively | Agent runtime to connect |
| `--review-policy` | prompt | `review` or `auto` |
| `--import-existing / --start-fresh` | prompt when backlog exists | Whether to import existing Claude Code sessions for this repo during init |
| `--remote` | unset | HF dataset repo (`owner/name`) |
| `--no-hook` | off | Skip Claude Code hook installation |
| `--private / --public` | private | Dataset visibility when creating the remote repo |

`--mode` is a legacy alias kept for compatibility.

`init` also installs the opentraces skill into `.agents/skills/opentraces/` and symlinks it into the selected agent's skill directory (e.g., `.claude/commands/opentraces/` for Claude Code).

### `opentraces remove`

Remove the local `.opentraces/` inbox and Claude Code hook from the current project.

### `opentraces config show`

Display the current user config with secrets masked.

### `opentraces config set`

Update configuration values.

```bash
opentraces config set --exclude /path/to/client-project
opentraces config set --redact "INTERNAL_API_KEY"
```

| Flag | Description |
|------|-------------|
| `--exclude` | Append a project path to the exclusion list |
| `--redact` | Append a literal custom redaction string |
| `--pricing-file` | Override token pricing table |
| `--classifier-sensitivity` | `low`, `medium`, or `high` |

## Inbox and Review

### `opentraces web`

Open the browser inbox UI. This serves the React viewer from `web/viewer/` through the local Flask app.

```bash
opentraces web
opentraces web --port 8080
opentraces web --no-open
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `5050` | Local port |
| `--no-open` | off | Do not auto-open the browser |

### `opentraces tui`

Open the terminal inbox UI.

```bash
opentraces tui
opentraces tui --fullscreen
```

### `opentraces session`

Fine-grained review commands for staged traces.

```bash
opentraces session list
opentraces session show <trace-id>
opentraces session commit <trace-id>
opentraces session reject <trace-id>
opentraces session reset <trace-id>
opentraces session redact <trace-id> --step 3
opentraces session discard <trace-id> --yes
```

`session list` accepts `--stage inbox|committed|pushed|rejected`, `--model`, `--agent`, and `--limit`.

## Upload

### `opentraces commit`

Commit inbox traces into a commit group for upload.

```bash
opentraces commit --all
opentraces commit -m "Fix parser and update schema"
```

### `opentraces push`

Upload committed traces to Hugging Face Hub as sharded JSONL files.

```bash
opentraces push
opentraces push --private
opentraces push --public
opentraces push --publish
opentraces push --gated
opentraces push --repo user/custom-dataset
```

| Flag | Default | Description |
|------|---------|-------------|
| `--private` | off | Force private visibility |
| `--public` | off | Force public visibility |
| `--publish` | off | Publish an existing private dataset |
| `--gated` | off | Enable gated access on the dataset |
| `--repo` | `{username}/opentraces` | Target HF dataset repo |

`--approved-only` is not part of the current CLI. The public path is `commit -> push`.

### `opentraces remote`

Manage the configured dataset remote.

```bash
opentraces remote
opentraces remote set owner/dataset
opentraces remote set owner/dataset --private
opentraces remote set owner/dataset --public
opentraces remote remove
```

### `opentraces status`

Show the current project inbox, counts, review policy, agents, and remote.

### `opentraces log`

List uploaded traces grouped by date. Shows trace IDs, timestamps, models used, and step counts for traces that have been pushed to the remote.

```bash
opentraces log
```

### `opentraces stats`

Show aggregate counts, token totals, estimated cost, model distribution, and stage counts for the current inbox. Useful for understanding your contribution volume and cost breakdown.

```bash
opentraces stats
```

### `opentraces context`

The agent's "what should I do next?" command. Returns project config, auth status, counts per stage, and a `suggested_next` command. Start here when resuming work or when uncertain about state.

```bash
opentraces context
opentraces --json context
```

## Machine-Readable Output

Add `--json` to any command to suppress human-readable text and get structured JSON only:

```bash
opentraces --json context
opentraces --json session list --stage inbox
opentraces --json push
```

JSON is emitted after the sentinel line `---OPENTRACES_JSON---`. When parsing programmatically, split on this sentinel and parse the text that follows.

Every JSON response includes:

| Field | Description |
|-------|-------------|
| `status` | `"ok"`, `"error"`, or `"needs_action"` |
| `next_steps` | Array of suggested next actions (human-readable) |
| `next_command` | The single most likely next command to run |

## Hidden and Internal Commands

These commands exist for automation, compatibility, or diagnostics and are hidden from normal help output:

| Command | Purpose |
|---------|---------|
| `opentraces discover` | List available agent sessions across all projects |
| `opentraces parse` | Parse agent sessions into enriched JSONL traces (global mode) |
| `opentraces review` | Legacy alias for `web`/`tui`/`session` |
| `opentraces export` | Export traces to other formats (e.g., `--format atif`) |
| `opentraces migrate` | Check schema version and run migrations |
| `opentraces capabilities --json` | Machine-discoverable feature list, supported agents, versions |
| `opentraces introspect` | Full API schema and TraceRecord JSON schema for automation |
| `opentraces assess` | Run quality assessment on staged traces |
| `opentraces _capture` | Invoked by the Claude Code SessionEnd hook to auto-capture sessions |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `2` | Usage or validation error |
| `3` | Missing config, auth, or not found |
| `4` | Network or upload error |
| `5` | Data corruption / invalid state |
| `7` | Lock contention / busy state |
