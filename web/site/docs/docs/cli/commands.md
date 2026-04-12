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
| `opentraces enrich` | Enrich a trace file with an Intent summary and any configured post-processors |
| `opentraces push` | Upload committed traces to Hugging Face Hub |
| `opentraces assess` | Run quality assessment on committed traces or a remote dataset |
| `opentraces web` | Open the browser inbox UI |
| `opentraces tui` | Open the terminal inbox UI |
| `opentraces stats` | Show aggregate inbox statistics |
| `opentraces context` | Return machine-readable project context |
| `opentraces config show` | Display current config |
| `opentraces config set` | Update config values |
| `opentraces import-hf` | Import traces from a HuggingFace dataset |
| `opentraces hooks install` | Install Claude Code session capture hooks |
| `opentraces log` | List uploaded traces grouped by date |
| `opentraces upgrade` | Upgrade CLI and refresh project skill file |
| `opentraces setup trufflehog` | Install or toggle the optional Tier 1.5 TruffleHog scanner |
| `opentraces doctor` | Report the health of the security pipeline (tiers, versions, auth), the current `intent.mode`, and any configured post-processors |
| `opentraces review-llm` | Run optional Tier 2 LLM semantic review over staged traces |

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

### `opentraces upgrade`

Upgrade the CLI and refresh the skill file and session hook in the current project.

```bash
opentraces upgrade              # upgrade CLI + refresh skill and hook
opentraces upgrade --skill-only # just refresh the skill file and hook
```

| Flag | Default | Description |
|------|---------|-------------|
| `--skill-only` | off | Skip CLI upgrade, only refresh the skill file and hook |

Detects the install method (pipx, brew, pip, source) and runs the appropriate upgrade command. Then re-copies the latest skill file into `.agents/skills/opentraces/` and updates the session hook.

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
opentraces session show <trace-id> --verbose
opentraces session commit <trace-id>
opentraces session reject <trace-id>
opentraces session reset <trace-id>
opentraces session redact <trace-id> --step 3
opentraces session discard <trace-id> --yes
```

`session list` accepts `--stage inbox|committed|pushed|rejected`, `--model`, `--agent`, and `--limit`.

`session show` truncates step content to 500 chars in human output by default to protect context windows. Pass `--verbose` to see full content, or use `opentraces --json session show <id>` to get the complete record as JSON (never truncated).

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
opentraces push --assess
opentraces push --llm-review
opentraces push --no-trufflehog
opentraces push --repo user/custom-dataset
```

| Flag | Default | Description |
|------|---------|-------------|
| `--private` | off | Force private visibility |
| `--public` | off | Force public visibility |
| `--publish` | off | Publish an existing private dataset |
| `--gated` | off | Enable gated access on the dataset |
| `--assess` | off | Run quality assessment after upload and embed scores in dataset card |
| `--llm-review` | off | Require every committed trace to carry a clean Tier 2 LLM verdict before uploading. Aborts with exit code 3 if any trace lacks a `"status":"complete"` verdict, or has `shareable == "no"`, or has `missed_sensitive_data == "yes"`. |
| `--no-trufflehog` | off | One-shot override: skip Tier 1.5 TruffleHog scanning for this push only. Does not change the config. |
| `--no-intent` | off | One-shot override: skip Intent enrichment for this push only. |
| `--repo` | `{username}/opentraces` | Target HF dataset repo |

`--approved-only` is not part of the current CLI. The public path is `commit -> push`.

When `--llm-review` aborts, the hint points you at `opentraces review-llm` (see below) to produce verdicts.

### `opentraces assess`

Run quality assessment on committed traces or a full remote dataset.

```bash
opentraces assess
opentraces assess --judge
opentraces assess --judge --judge-model sonnet
opentraces assess --limit 50
opentraces assess --all-staged
opentraces assess --compare-remote
opentraces assess --dataset user/my-traces
```

| Flag | Default | Description |
|------|---------|-------------|
| `--judge / --no-judge` | off | Enable LLM judge for qualitative scoring |
| `--judge-model` | `haiku` | Model for LLM judge: `haiku`, `sonnet`, or `opus` |
| `--limit` | `0` (all) | Maximum number of traces to assess |
| `--compare-remote` | off | Fetch the remote dataset's `quality.json` and show score deltas |
| `--all-staged` | off | Assess all staged traces instead of COMMITTED-only |
| `--dataset TEXT` | unset | Assess a full remote HF dataset (e.g. `user/my-traces`). Downloads all shards, runs assessment, and updates `README.md` and `quality.json` on the dataset repo. Does not require hf-mount. |

By default, `assess` targets only **committed** traces, matching the population that `push` would upload. Use `--all-staged` to include traces that are staged but not yet committed.

`--dataset` is independent of the local inbox. It downloads shards from the specified HF dataset repo and updates that repo's dataset card and `quality.json` sidecar in place, without requiring a new push.

### opentraces import-hf

Import traces from a HuggingFace dataset into your local inbox.

```bash
opentraces import-hf DATASET_ID [OPTIONS]
```

| Flag | Description |
|------|-------------|
| `DATASET_ID` | HuggingFace dataset ID (e.g. `user/my-traces`) |
| `--parser` | Parser to use: `hermes` or `generic` (default: `hermes`) |
| `--subset` | Dataset subset/config name |
| `--split` | Dataset split (default: `train`) |
| `--limit` | Maximum number of traces to import |
| `--auto` | Commit imported traces immediately, skip inbox |
| `--dry-run` | Preview import without writing any files |

Exit codes: `0` success, `1` partial failure (some traces rejected by quality gate).

### opentraces hooks install

Install Claude Code session capture hooks into the current project. Hooks run automatically at session end (`on_stop`) and after context compaction (`on_compact`) to enrich traces with session metadata.

```bash
opentraces hooks install
```

Run this once per project after `opentraces init`.

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

### CI / headless / agent mode

When `stdout` is not a TTY, bare `opentraces` prints help text instead of launching the TUI. You can also force this explicitly:

```bash
OPENTRACES_NO_TUI=1 opentraces    # always prints help, never opens TUI
```

`HF_TOKEN` is also respected as the highest-priority credential source, so CI pipelines can authenticate without running `opentraces login`.

## Security Pipeline

### `opentraces setup trufflehog`

Install or toggle the optional Tier 1.5 TruffleHog scanner. TruffleHog is **off by default**; once you opt in, a missing binary becomes a hard error on subsequent scans and pushes, not a silent skip.

```bash
opentraces setup trufflehog            # install (or detect) and enable
opentraces setup trufflehog --verify   # skip install, verify binary, enable
opentraces setup trufflehog --disable  # disable the tier, leave binary in place
```

| Flag | Default | Description |
|------|---------|-------------|
| `--disable` | off | Turn the Tier 1.5 tier off without uninstalling the binary |
| `--verify` | off | Skip install; verify the binary is present and enable the tier |

Installation tries `brew` then `go install`. If both fail, the command prints the upstream install URL and exits `4` so you can install manually and re-run with `--verify`.

TruffleHog runs locally in `--verify_secrets=false` mode, so no secrets are probed against third-party APIs.

### `opentraces doctor`

Report the health of the opentraces security pipeline. Exits `3` when Tier 1.5 is enabled in config but the binary is missing.

```bash
opentraces doctor
opentraces --json doctor
```

The JSON payload under `doctor` contains:

| Field | Description |
|-------|-------------|
| `security_version` | Current `SECURITY_VERSION` (for example `0.4.0`) |
| `schema_version` | `opentraces_schema.SCHEMA_VERSION` if installed |
| `trufflehog.enabled` | Whether Tier 1.5 is enabled in config |
| `trufflehog.binary_version` | Output of `trufflehog --version`, or `null` |
| `trufflehog.status` | Human-readable status (`disabled ...`, `ENABLED-BUT-MISSING ...`, or `enabled (<version>)`) |
| `hf_auth` | `"ok"` when a token is loaded, `"missing"` otherwise |
| `intent.mode` | Intent enrichment mode (`on`/`off`) |
| `post_processors[]` | Configured post-processors with their resolved path and status |

### `opentraces review-llm`

Run the optional Tier 2 LLM semantic review over the staged traces. Each session's transcript is chunked (400k chars per chunk) and sent to the chosen provider; per-chunk verdicts are aggregated pessimistically (`shareable`: `no` > `manual_review` > `yes`; `missed_sensitive_data`: `yes` > `maybe` > `no`). Results are cached on `sha256(content + model + prompt_version + context)`.

```bash
opentraces review-llm
opentraces review-llm --provider anthropic --model claude-haiku-4-5-20251001
opentraces review-llm --dry-run
opentraces review-llm --context-file AGENTS.md --limit 10
opentraces review-llm --force
```

| Flag | Default | Description |
|------|---------|-------------|
| `--provider` | `ollama` | LLM provider: `ollama`, `anthropic`, or `fake` |
| `--model` | `gemma4:e4b` | Model name. `claude-haiku-4-5-20251001` recommended for `anthropic` |
| `--dry-run` | off | Estimate token usage and cost without calling the provider |
| `--limit` | `0` (all) | Max staged sessions to review this invocation |
| `--force` | off | Re-review sessions that already have a cached verdict |
| `--context-file` | unset | Path to a README/AGENTS.md passed as project context (first 10k chars used) |

Each result carries a verdict shaped like:

```json
{
  "shareable": "yes",
  "missed_sensitive_data": "no",
  "flagged_parts": [{"reason": "...", "evidence": "..."}],
  "summary": "..."
}
```

Verdicts are written back to the staged trace's `metadata.llm_review` so `opentraces push --llm-review` can gate on them.

`--dry-run` emits a `sessions / chars / estimate {tokens, cost_usd} / model / provider` summary and does not contact any provider.

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
| `opentraces _capture` | Invoked by the Claude Code SessionEnd hook to auto-capture sessions |
| `opentraces _assess-remote` | Force quality assessment on a remote dataset via hf-mount (automation only) |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `2` | Usage error (bad flags, conflicting options) |
| `3` | Auth/config error (not authenticated, not initialized) |
| `4` | Network or upload error |
| `5` | Data corruption / invalid state |
| `6` | Not found (trace ID, project, or resource) |
| `7` | Lock contention / busy state |
