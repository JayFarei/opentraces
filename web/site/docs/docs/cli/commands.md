# Commands

Complete reference for the current opentraces CLI surface.

## Public Commands

| Command | Description |
|---------|-------------|
| `opentraces auth login` | Authenticate with Hugging Face Hub |
| `opentraces auth logout` | Clear stored credentials |
| `opentraces auth` | Show the active Hugging Face identity |
| `opentraces init` | Initialize the current project inbox |
| `opentraces remove` | Remove the local inbox from the current project |
| `opentraces status` | Show inbox status and counts |
| `opentraces remote` | Manage the configured dataset remote |
| `opentraces trace` | Inspect and edit staged traces |
| `opentraces add` | Commit inbox traces for upload |
| `opentraces push` | Upload committed traces to Hugging Face Hub |
| `opentraces assess` | Run quality assessment on committed traces or a remote dataset |
| `opentraces web` | Open the browser inbox UI |
| `opentraces tui` | Open the terminal inbox UI |
| `opentraces stats` | Show aggregate inbox statistics |
| `opentraces config show` | Display current config |
| `opentraces config set` | Update config values |
| `opentraces pull` | Import traces from a HuggingFace dataset |
| `opentraces log` | List uploaded traces grouped by date |
| `opentraces setup upgrade` | Upgrade CLI and refresh project skill file |
| `opentraces setup` | Interactive wizard: walks every integration (Claude Code, git, trufflehog, review-llm) |
| `opentraces setup claude-code` | Install Claude Code capture hooks |
| `opentraces setup git` | Install or remove the opentraces post-commit hook for commit linking |
| `opentraces setup trufflehog` | Install or toggle the optional Tier 1.5 TruffleHog scanner |
| `opentraces setup review-llm` | Configure the third-party LLM used by `review-llm` (global config) |
| `opentraces doctor` | Report the health of the security pipeline (tiers, versions, auth) and any configured post-processors |
| `opentraces llm-review` | Run optional Tier 2 LLM semantic review over staged traces |
| `opentraces blame` | Resolve a commit to the opentraces trace(s) behind it |
| `opentraces export` | Export staged traces to another format (`atif` stub, `agent-trace`) |

## Authentication

### `opentraces auth login`

Authenticate with Hugging Face Hub.

```bash
opentraces auth login --token
opentraces auth login
```

| Flag | Default | Description |
|------|---------|-------------|
| `--token` | off | Paste a personal access token (required for pushing) |

> **Recommended:** Use `opentraces auth login --token` with a write-access PAT from [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens). The browser OAuth flow (`opentraces auth login` without `--token`) authenticates your identity but cannot create or push to dataset repos.

### `opentraces auth logout`

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
If Claude Code already has trace logs for this repo, the interactive flow can import that backlog into the inbox immediately.

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
| `--import-existing / --start-fresh` | prompt when backlog exists | Whether to import existing Claude Code traces for this repo during init |
| `--remote` | unset | HF dataset repo (`owner/name`) |
| `--no-hook` | off | Skip Claude Code hook installation |
| `--private / --public` | private | Dataset visibility when creating the remote repo |

`--mode` is a legacy alias kept for compatibility.

`init` also installs the opentraces skill into `.agents/skills/opentraces/` and symlinks it into the selected agent's skill directory (e.g., `.claude/commands/opentraces/` for Claude Code).

### `opentraces remove`

Remove the local `.opentraces/` inbox and Claude Code hook from the current project.

### `opentraces setup upgrade`

Upgrade the CLI and refresh the skill file and capture hook in the current project.

```bash
opentraces setup upgrade              # upgrade CLI + refresh skill and hook
opentraces setup upgrade --skill-only # just refresh the skill file and hook
```

| Flag | Default | Description |
|------|---------|-------------|
| `--skill-only` | off | Skip CLI upgrade, only refresh the skill file and hook |

Detects the install method (pipx, brew, pip, source) and runs the appropriate upgrade command. Then re-copies the latest skill file into `.agents/skills/opentraces/` and updates the capture hook.

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

### `opentraces trace`

Fine-grained review commands for staged traces.

```bash
opentraces list
opentraces list --by-commit
opentraces show <trace-id>
opentraces show <trace-id> --verbose
opentraces show <trace-id> --markdown
opentraces add <trace-id>
opentraces reject <trace-id>
opentraces reset <trace-id>
opentraces redact <trace-id> --step 3
opentraces discard <trace-id> --yes
```

`trace list` accepts `--stage inbox|committed|pushed|rejected`, `--model`, `--agent`, `--limit`, and `--by-commit`.

| `trace list` flag | Default | Description |
|---------------------|---------|-------------|
| `--stage` | all | Filter by `inbox`, `committed`, `pushed`, or `rejected` |
| `--model` | all | Substring filter over `agent.model` |
| `--agent` | all | Filter by agent name |
| `--limit` | `50` | Max traces returned |
| `--by-commit` | off | Group traces by `git_links[].revision` (plan 041 R29). Useful for finding every trace that contributed to a given commit. |

`trace show` truncates step content to 500 chars in human output by default to protect context windows. Pass `--verbose` to see full content, or use `opentraces --json trace show <id>` to get the complete record as JSON (never truncated).

| `trace show` flag | Default | Description |
|---------------------|---------|-------------|
| `--verbose` | off | Print full step content instead of the 500-char truncation |
| `--markdown` | off | Render the trace as injection-safe Markdown (fenced code, escaped prompts). Safer for piping into another agent. |

## Upload

### `opentraces add`

Commit inbox traces into a commit group for upload.

```bash
opentraces add --all
opentraces add -m "Fix parser and update schema"
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
| `--repo` | `{username}/opentraces` | Target HF dataset repo |

`--approved-only` is not part of the current CLI. The public path is `commit -> push`.

When `--llm-review` aborts, the hint points you at `opentraces llm-review` (see below) to produce verdicts.

### `opentraces doctor`

Report the end-to-end health of the pipeline: security tiers, schema version, HuggingFace auth, and any configured post-processors (probed against `PATH`).

```bash
opentraces doctor
opentraces --json doctor
```

The machine-readable JSON output includes:

- `security_version`, `schema_version`
- `trufflehog.{enabled,binary_version,status}`
- `hf_auth` — `ok` or `missing`
- `post_processors` — array of `{name, command, resolved_path, status}`. `status` is `detected` when the binary resolves on `PATH`, `missing` otherwise.

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

### opentraces pull

Import traces from a HuggingFace dataset into your local inbox. The inverse of `push`.

```bash
opentraces pull DATASET_ID [OPTIONS]
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

### `opentraces setup`

Wire opentraces into external tools — one subcommand per integration, or run bare `opentraces setup` for an interactive wizard that walks all of them.

```bash
opentraces setup                       # interactive wizard
opentraces setup claude-code           # Claude Code capture hooks (~/.claude/settings.json)
opentraces setup claude-code --remove  # uninstall
opentraces setup claude-code --dry-run # preview without writing
opentraces setup git                   # git post-commit hook (trace ↔ commit correlation)
opentraces setup git --remove
opentraces setup trufflehog            # Tier 1.5 TruffleHog scanning (interactive install wizard)
opentraces setup trufflehog --enable   # agent/CI: flip on; fails TRUFFLEHOG_MISSING if binary absent
opentraces setup trufflehog --disable  # turn the tier off without uninstalling
opentraces setup review-llm            # Tier 2 LLM review (interactive preset picker)
opentraces setup review-llm --disable  # turn the Tier 2 LLM review off
```

Claude Code hooks run at trace end (`Stop`) and after context compaction (`PostCompact`) to enrich traces with trace metadata. They write into `~/.claude/settings.json` using the matcher-envelope shape Claude Code expects.

The git integration writes an owned `opentraces-post-commit` script plus a fenced chain block into `.git/hooks/post-commit`, so existing hooks are preserved. It also adds a `refs/notes/opentraces` refspec so commit notes travel with `git fetch`.

Use `opentraces doctor` to check integration status at any time.

### `opentraces remote`

Manage the configured dataset remote.

```bash
opentraces remote
opentraces remote add origin owner/dataset
opentraces remote add origin owner/dataset --private
opentraces remote add origin owner/dataset --public
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
opentraces --json trace list --stage inbox
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

`HF_TOKEN` is also respected as the highest-priority credential source, so CI pipelines can authenticate without running `opentraces auth login`.

## Security Pipeline

### `opentraces setup trufflehog`

Install or toggle the optional Tier 1.5 TruffleHog scanner. TruffleHog is **off by default**; once you opt in, a missing binary becomes a hard error on subsequent scans and pushes, not a silent skip.

```bash
opentraces setup trufflehog            # interactive wizard; offers to install via brew or go if missing
opentraces setup trufflehog --enable   # agent/CI: flip the tier on. Fails TRUFFLEHOG_MISSING if binary not installed.
opentraces setup trufflehog --disable  # disable the tier, leave binary in place
```

| Flag | Default | Description |
|------|---------|-------------|
| `--enable` | off | Flip the Tier 1.5 switch on. Never installs anything — binary must already be on PATH, otherwise exits `3` with `TRUFFLEHOG_MISSING`. |
| `--disable` | off | Turn the Tier 1.5 tier off without uninstalling the binary |

The bare `opentraces setup trufflehog` is the **human flow**: when the binary is missing it shows a picker over the installers it can find (`brew`, `go`) plus a `skip` option, then installs via the chosen method. Agents should use `--enable` instead and never shell out to install binaries — if that fails, surface the `TRUFFLEHOG_MISSING` error to the user and let them run the interactive wizard or install manually.

TruffleHog runs locally in `--verify_secrets=false` mode, so no secrets are probed against third-party APIs.

### `opentraces setup git`

Install (or remove) the opentraces post-commit hook in the current repo. The hook runs after every `git commit`, correlates the new revision against provisional traces in the local staging, promotes matched traces to `lifecycle = "final"`, and attaches `opentraces://` notes to the commit via `git notes`.

```bash
opentraces setup git              # install the hook
opentraces setup git --remove     # remove the hook
```

| Flag | Default | Description |
|------|---------|-------------|
| `--uninstall` | off | Remove the hook instead of installing it |

The hook is a thin shim that calls `opentraces _run-post-commit-hook` (hidden). Failures in the hook never block the commit.

## Commit Linking and Attribution

### `opentraces blame`

Resolve a commit to the opentraces trace(s) behind it. Traces are linked via `refs/notes/opentraces` written by the post-commit hook (install with `setup git`); each hit joins with local staging records to show the task label, originating Claude Code session, and the command to resume it.

```bash
opentraces blame                  # defaults to HEAD
opentraces blame abc1234
opentraces blame HEAD~3 --json
```

| Flag | Default | Description |
|------|---------|-------------|
| `COMMIT` | `HEAD` | Commit-ish (sha, branch, `HEAD~N`) |
| `--json` | off | Emit machine-readable JSON (`{commit, traces: [{trace_id, session_id, url}]}`) |

Old commits can't be backfilled — only commits made after `setup git` ran carry opentraces notes.

## Export

### `opentraces export`

Export staged traces into another interchange format.

```bash
opentraces export --format agent-trace
opentraces export --format agent-trace --output ./my-export.jsonl
opentraces export --format atif
```

| Flag | Default | Description |
|------|---------|-------------|
| `--format` | required | `atif` or `agent-trace`. `atif` is still a stub; `agent-trace` emits Agent Trace v0.1.0 JSONL. |
| `--output` | `./opentraces-export.jsonl` | JSONL output path |

`agent-trace` is the spec published by the Cursor/community Agent Trace RFCs. See [Export](/docs/workflow/export) for the full mapping.

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
| `review_llm.enabled` | Whether the Tier 2 LLM review is configured |
| `review_llm.backend` | Inferred backend name from `base_url` (`ollama`, `lm-studio`, `llama.cpp`, `vllm`, `groq`, `openrouter`, `together`, `openai`, `anthropic`, …) |
| `review_llm.model` | Configured model identifier |
| `review_llm.reachable` | `true` when a cheap `/v1/models` ping succeeds (or the `anthropic` SDK is importable); `false` otherwise |
| `review_llm.status` | Human-readable status (`disabled …`, `UNREACHABLE …`, or `enabled (<backend> / <model>) — N models available`) |
| `hf_auth` | `"ok"` when a token is loaded, `"missing"` otherwise |
| `post_processors[]` | Configured post-processors with their resolved path and status |

Exits `3` when either Tier 1.5 or the review-LLM tier is enabled in config but unreachable (missing binary / unreachable endpoint / missing API-key env var).

### `opentraces setup review-llm`

Configure the third-party LLM that `opentraces llm-review` uses to independently review staged traces. Config is **global** (one LLM per machine, shared across projects) and lives under `security.review_llm` in `~/.opentraces/config.json`.

```bash
opentraces setup review-llm            # interactive preset picker (9 options, incl. Ollama model picker + pull)

# Agent / non-interactive:
opentraces setup review-llm --provider openai \
    --base-url http://localhost:11434/v1 --model gemma3n:e4b
opentraces setup review-llm --provider openai \
    --base-url https://api.groq.com/openai/v1 \
    --model llama-3.3-70b-versatile --api-key-env GROQ_API_KEY
opentraces setup review-llm --provider anthropic \
    --model claude-haiku-4-5-20251001 --api-key-env ANTHROPIC_API_KEY

opentraces setup review-llm --test     # ping the endpoint; do not write
opentraces setup review-llm --print    # dump current config as JSON
opentraces setup review-llm --disable  # turn off
```

| Flag | Default | Description |
|------|---------|-------------|
| `--provider` | (from config) | `openai` (default, covers OpenAI-compat servers incl. Ollama at `/v1`, LM Studio, vLLM, llama.cpp, OpenAI, Groq, OpenRouter, Together), `ollama` (native `/api/generate`), `anthropic`, `fake` |
| `--base-url` | (from config) | Base URL including `/v1` for OpenAI-compat servers. Ignored for `anthropic`. |
| `--model` | (from config) | Model identifier. |
| `--api-key-env` | `""` | Env var holding the API key. Empty for local servers that don't require auth. |
| `--timeout` | `120` | Request timeout in seconds. |
| `--enable` | off | Turn review-llm on using current config. |
| `--disable` | off | Turn review-llm off without changing other fields. |
| `--test` | off | Ping `{base_url}/models` and report reachability without writing. |
| `--print` | off | Print effective config as JSON and exit. |
| `--no-interactive` | off | Skip the preset picker when no flags are given. |

Built-in presets (shown in the picker): `ollama`, `lm-studio`, `llama-cpp`, `vllm`, `openai`, `groq`, `openrouter`, `together`, `anthropic-direct`, and a free-form `custom` option. When a local preset is chosen and the endpoint is reachable, the picker lists available models and offers `custom` to enter a tag manually; for Ollama specifically, an unknown tag triggers an offer to run `ollama pull <tag>` right there.

### `opentraces llm-review`

Run the Tier 2 LLM semantic review over the staged traces using the LLM configured via `opentraces setup review-llm` (overridable per-invocation with `--provider` / `--model` / `--base-url` / `--api-key-env`). Each trace's transcript is chunked (400k chars per chunk) and sent to the chosen backend; per-chunk verdicts are aggregated pessimistically (`shareable`: `no` > `manual_review` > `yes`; `missed_sensitive_data`: `yes` > `maybe` > `no`). Results are cached on `sha256(content + provider + base_url + model + prompt_version + context)`.

If Tier 1 / TruffleHog already blocked a trace, the LLM call is skipped and a synthetic `shareable="no"` verdict is recorded with `denied_before_llm: true` — no tokens spent on confirmed-bad traces.

```bash
opentraces llm-review                                # every trace in staging (current default)
opentraces llm-review --scope staged                 # STAGED status only (pre-commit)
opentraces llm-review --scope committed              # COMMITTED only — second line of defence before push
opentraces llm-review --trace 8a3f1c                 # one trace (short prefix ok)
opentraces llm-review --trace 8a3f --trace b4c9 --force
opentraces llm-review --limit 5 --dry-run            # cost estimate for the next 5
opentraces llm-review --provider fake                # offline stub, for tests
opentraces llm-review --context-file AGENTS.md
```

review-llm is slow. Narrow what you run with `--scope` or `--trace`, and cap with `--limit`. The typical "second line of defence" flow is `review-llm --scope committed` right before `push --llm-review`.

| Flag | Default | Description |
|------|---------|-------------|
| `--provider` | (from config) | Override provider for this run. |
| `--model` | (from config) | Override model for this run. |
| `--base-url` | (from config) | Override base URL for this run. |
| `--api-key-env` | (from config) | Override env var holding the API key for this run. |
| `--scope` | `all` | `all` / `staged` / `committed`. `staged` = only pre-commit traces; `committed` = only post-commit (ready-to-push) traces. |
| `--trace` | (none) | Target trace by id (full or short prefix). Repeatable. Overrides `--scope`. |
| `--dry-run` | off | Estimate token usage and cost without calling the provider. |
| `--limit` | `0` (no cap) | Cap the final batch at N traces, applied after `--scope` / `--trace` filtering. |
| `--force` | off | Re-review traces that already have a cached verdict. |
| `--context-file` | unset | Path to a README/AGENTS.md passed as project context (first 10k chars used). |

Each result carries a verdict shaped like:

```json
{
  "status": "complete",
  "shareable": "yes",
  "missed_sensitive_data": "no",
  "flagged_parts": [{"reason": "...", "evidence": "..."}],
  "summary": "...",
  "provider": "openai",
  "model": "gemma3n:e4b",
  "base_url": "http://localhost:11434/v1",
  "reviewed_at": "2026-04-13T12:34:56+00:00",
  "prompt_version": "1",
  "review_key": "sha256:…"
}
```

Verdicts are written back to the staged trace's `metadata.llm_review` so `opentraces push --llm-review` can gate on them. The provenance fields (`provider`, `model`, `base_url`, `reviewed_at`, `prompt_version`) surface in the TUI/web reviewer so you can see *which* LLM issued each verdict; they also feed `review_key` so the cache invalidates on any backend change.

Deny-before-LLM verdicts additionally carry `denied_before_llm: true`.

`--dry-run` emits a `traces / chars / estimate {tokens, cost_usd} / model / provider / base_url` summary and does not contact any provider.

## Hidden and Internal Commands

These commands exist for automation, compatibility, or diagnostics and are hidden from normal help output:

| Command | Purpose |
|---------|---------|
| `opentraces discover` | List available agent traces across all projects |
| `opentraces parse` | Parse raw agent logs into enriched JSONL traces (global mode) |
| `opentraces migrate` | Check schema version and run migrations |
| `opentraces capabilities --json` | Machine-discoverable feature list, supported agents, versions |
| `opentraces introspect` | Full API schema and TraceRecord JSON schema for automation |
| `opentraces _capture` | Invoked by the Claude Code SessionEnd hook to auto-capture traces |
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
