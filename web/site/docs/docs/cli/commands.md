# Commands

Reference for the current 0.4 `opentraces` CLI.

## Root Command

```bash
opentraces [--json] <command> ...
```

Use `--json` on any command when you want machine-readable output instead of the TTY view.

The 0.4 command surface is grouped into six top-level command groups (`auth`, `bucket`, `config`, `dataset`, `setup`, `trace`, `trail`, `workflow`) plus a handful of project-level verbs.

### Global setup

| Command | What it does |
|---------|---------------|
| `setup` | Wire opentraces into your system (claude-code, git, watcher, auth, bucket, trufflehog, llm-review, skill, upgrade) |
| `auth` | HuggingFace identity (`login`, `logout`, `whoami`) |
| `config` | Show or set configuration (`show`, `set`) |
| `completions` | Print or install shell completion scripts |

### Project setup

| Command | What it does |
|---------|---------------|
| `init` | Initialize opentraces in the current project |
| `status` | Show project status, inbox stage counts, and recent traces |
| `doctor` | Report security pipeline and integration health |
| `remove` | Remove opentraces from the current project |

### Trace commands

| Command | What it does |
|---------|---------------|
| `trace` | Search, map, slice, and retrieve retained traces |
| `trace get` | Resolve a trace, trace unit, map node, or `ot://` Trail resource |
| `trace index` | Rebuild and inspect local trace search projections |
| `trace map` | Show a deterministic Trace Map or bounded candidate slice |
| `trace query` | Search local retained traces and return bounded candidate packets |
| `trace slice` | Extract deterministic Trace Slices for dataset workflows |
| `trace teleport` | Move a trace and its retained Git evidence between workspaces |

### Trail commands

| Command | What it does |
|---------|---------------|
| `trail` | Inspect and sync VCS-anchored Trace Trails |
| `trail blame` | Attribution between traces and commits |
| `trail graph` | Render commit + trace history |
| `trail track` | Walk and render trace lineage through Git history (subsumes the former `trail timeline`, `trail explain`, `trail sync`, and `trail search` surfaces) |

### Bucket commands

| Command | What it does |
|---------|---------------|
| `bucket` | Inspect and troubleshoot the local trace bucket |
| `bucket manifest` | Materialize and print the local bucket manifest |
| `bucket remote` | Manage the configured private bucket remote (`status`, `diff`, `push`, `pull`) |
| `bucket replay` | Replay bucket-exported Trace Trails into a Git repository |
| `bucket status` | Show local bucket health, sync eligibility, and trail freshness |

### Dataset commands

| Command | What it does |
|---------|---------------|
| `dataset` | Manage local executable datasets |
| `dataset list` | List local HF-shaped datasets |
| `dataset new` | Create a local HF-shaped dataset with an OpenTraces sidecar |
| `dataset publish` | Publish reviewed dataset rows and contract files to the active remote |
| `dataset remote` | Manage dataset-scoped HuggingFace remotes (`add`, `create`, `list`, `remove`, `visibility`) |
| `dataset remove` | Remove a local dataset after explicit confirmation |
| `dataset review` | Review, approve, reject, or reset dataset rows (TUI and web review entrypoints) |
| `dataset run` | Run the dataset workflow in dry-run, current-agent, or headless mode |
| `dataset schedule` | Manage local dataset schedules (`add`, `list`, `logs`, `pause`, `remove`, `resume`, `show`) |
| `dataset status` | Show row count and publication-state breakdown for a dataset |

### Workflow commands

| Command | What it does |
|---------|---------------|
| `workflow` | Manage local dataset workflow skills |
| `workflow create` | Scaffold a new local dataset workflow skill |
| `workflow list` | List installed workflows with their path and bound datasets |
| `workflow templates` | List built-in workflow templates available to `workflow create` |
| `workflow remove` | Remove an installed workflow skill package |

A few advanced commands are real but intentionally omitted from the default `--help` listing because they are typically driven by other surfaces: `backfill`, `git-backfill`, `parse`, `discover`, `context`, `migrate`, `introspect`, and `capabilities`. The active ones are documented under [Advanced Commands](#advanced-commands) below.

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
opentraces init --agent claude-code
opentraces init --start-fresh
opentraces init --import-existing
```

Initializes the current repo, writes `.opentraces.json`, registers machine-local state under `~/.opentraces/projects/<slug>/`, and installs the capture hook for the selected agent. Dataset remotes and review policy are configured separately via `opentraces dataset remote ...` and `opentraces config set review_policy review --project`.

| Flag | Description |
|------|-------------|
| `--agent [claude-code]` | Agent runtime to connect |
| `--import-existing / --start-fresh` | Backfill existing Claude Code traces for this repo, or start from the next run |

### `opentraces remove`

```bash
opentraces remove
opentraces remove --all
```

Uninstalls the capture hook, deletes the `.opentraces.json` marker, and unregisters the repo from the global registry. Pushed datasets on Hugging Face are left untouched.

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

## Trace Commands

The `trace` group is the search and retrieval surface over locally retained traces. It replaces the old flat `list` / `show` verbs.

### `opentraces trace query`

```bash
opentraces trace query
opentraces trace query "redact secrets"
opentraces trace query --skill opentraces --since 7d
opentraces trace query --candidate-kind bug_fix --limit 20
opentraces trace query --files "src/**/*.py" --signal failing-test
```

Searches local retained traces and returns bounded candidate packets. Supports lexical, semantic, faceted, and survival-state filters.

Highlights:

| Flag | Description |
|------|-------------|
| `--lex TEXT` | Lexical query text |
| `--semantic TEXT` | Semantic service/library query text |
| `--skill TEXT` | Exact `skill.name` facet |
| `--tool TEXT` | Exact `tool.name` facet |
| `--files TEXT` | File glob filter over indexed paths |
| `--candidate-kind` | Closed-vocabulary candidate type (`bug_fix`, `trace`, `trace_map_node`, `trace_slice`, `trace_intent_candidate`, `patch`, `skill_invocation`, `tool_sequence`, `test_or_error_signal`, `git_anchor`) |
| `--since TEXT` | ISO date/time or duration such as `7d` |
| `--success / --no-success` | Filter `outcome.success` (explicit True/False) |
| `--committed / --uncommitted` | Filter `outcome.committed` (explicit True/False) |
| `--project TEXT` / `--cwd` | Scope to one project or only the current opted-in project |
| `--limit INTEGER` | Maximum candidates. Default `20`. |
| `--include-slice [intent\|evidence]` | Embed a bounded Trace Map slice in each candidate |
| `--source [index\|projection]` | Local query source. Default `index`. |

### `opentraces trace get`

```bash
opentraces trace get tr_abc123
opentraces trace get tr_abc123 --bursts
opentraces trace get tr_abc123 --resume
opentraces trace get tr_abc123 --resume --at-step s42 --dry-run
```

Resolves a trace, trace unit, map node, or `ot://` Trail resource. With `--resume`, hands control back to the upstream agent (Claude Code) instead of printing trace details.

| Flag | Description |
|------|-------------|
| `--resume` | Hand control back to the upstream agent for this trace |
| `--at-step TEXT` | With `--resume`: fork a new session from a specific step id (e.g. `s42`) |
| `--dry-run` | With `--resume`: print the resume command instead of exec'ing it |
| `--bursts` | Return only the change-burst summary list for this trace |
| `--burst-gap INTEGER` | Step-index gap between adjacent edits within a burst (default `35`) |
| `--no-commit-lookup` | With `--bursts`: skip the per-burst `git log` lookup |
| `--json` | Emit structured JSON |

### `opentraces trace index`

Rebuild and inspect local trace search projections.

```bash
opentraces trace index rebuild
opentraces trace index status
```

### `opentraces trace map`

```bash
opentraces trace map tr_abc123
opentraces trace map tr_abc123 --bursts
opentraces trace map tr_abc123 --around s42 --depth 2
opentraces trace map tr_abc123 --from-node s10 --walk forward
```

Shows a deterministic Trace Map or a bounded candidate slice.

| Flag | Description |
|------|-------------|
| `--candidate TEXT` | Candidate unit or map node to expand around |
| `--around TEXT` | Map node or unit to show a local neighborhood around |
| `--depth INTEGER` | Neighborhood depth for `--around`. Default `2` |
| `--from-node TEXT` | Map node or unit where a directional walk starts |
| `--walk [back\|forward]` | Walk direction for `--from-node` |
| `--until TEXT` | Action type that stops `--walk` |
| `--max-steps INTEGER` | Maximum nodes in the candidate slice. Default `40` |
| `--actions TEXT` | Comma-separated action types to keep |
| `--bursts` | Project the map as `change_burst` aggregate nodes (one per cluster) |
| `--burst-gap INTEGER` | Step-index gap between adjacent edits within a burst (default `35`) |
| `--no-commit-lookup` | Skip the per-burst `git log` lookup |

### `opentraces trace slice`

```bash
opentraces trace slice tr_abc123 --from-step 5 --to-step 12
opentraces trace slice tr_abc123 --around-step 7 --radius 3
opentraces trace slice tr_abc123 --template bursts
```

Extracts deterministic Trace Slices for dataset workflows.

| Flag | Description |
|------|-------------|
| `--from-step INTEGER` / `--to-step INTEGER` | First and last step indices in a manual slice |
| `--around-step INTEGER` | Create a slice around one step |
| `--around-patch TEXT` | Create a slice around a patch id, map node, or trace-patch id |
| `--radius INTEGER` | Step radius for `--around-step` / `--around-patch`. Default `3` |
| `--template [bursts]` | Built-in deterministic slicing strategy |

### `opentraces trace teleport`

```bash
opentraces trace teleport export tr_abc123
opentraces trace teleport open ./workspace.tar.gz
```

Moves a trace and its retained Git evidence between workspaces. Useful for handing a single trace to a collaborator with the full lineage intact.

## Trail Commands

The `trail` group is the VCS-anchored evidence surface. In 0.4 it has been collapsed to three visible subcommands: `blame`, `graph`, and `track`. The older verbs (`timeline`, `explain`, `sync`, `search`, `resolve`, `follow`, `attach`, `rebuild`, `diff`) now live as scopes of `trail track`, or have moved into `trace get` (for `ot://` resolution) and `trace teleport` (for cross-workspace evidence).

### `opentraces trail blame`

```bash
opentraces trail blame abc1234                          # Commit-mode (bare SHA)
opentraces trail blame c:abc1234 src/main.py            # Commit-mode, single file
opentraces trail blame abc1234 --lines                  # Per-line view
opentraces trail blame t:4dccb032                       # Trace-mode (canonical)
opentraces trail blame s:92437382 --include-overlapping # Trace-mode (upstream session)
opentraces trail blame abc1234 --json                   # Structured output
```

Two modes, one argument:

- **Commit-mode** (`c:<sha>` or bare SHA): which traces contributed to this commit. Uses the attribution cache for per-line detail and merges `refs/notes/opentraces` so hook-linked traces surface even when the attribution cache has no per-line data for that commit.
- **Trace-mode** (`t:<trace-id>`, `s:<session-id>`, or a bare hyphenated UUID): which commits carry this trace's output. Merges attribution-cache rows (fine-grained) with the trace's `git_links` (hook-linked).

| Flag | Description |
|------|-------------|
| `--lines` | Per-line output (git-blame-style). Commit-mode only. |
| `--entities` | Expand entity changes (functions, classes) under each trace. Commit-mode only. |
| `--include-overlapping` | Trace-mode: include commits where files and timestamps overlap without direct tool-emit evidence. Off by default. |
| `--project DIRECTORY` | Project directory (default CWD) |
| `--json` | Emit structured JSON instead of text |
| `--no-color` | Disable ANSI colors |

### `opentraces trail graph`

```bash
opentraces trail graph
opentraces trail graph --limit 50
opentraces trail graph --trace abc12
opentraces trail graph --since HEAD~20 --until HEAD
```

Renders commit + trace history. Commit-primary by default: the git log is the spine and each commit shows the traces that touched it. Pass `--trace <id>` to pivot to trace-primary mode. Requires a populated attribution cache.

| Flag | Description |
|------|-------------|
| `--limit INTEGER` | Commits per page. Default `20` |
| `--page INTEGER` | Page number (1-indexed) |
| `--all` | Disable pagination |
| `--trace TEXT` | Pivot to trace-primary mode for the given trace id |
| `--since TEXT` / `--until TEXT` | Show commits within a ref range |
| `--project DIRECTORY` | Project directory (default CWD) |
| `--entities` | Include entity-change suffixes (requires entity cache) |
| `--json` / `--no-color` | Output controls |

### `opentraces trail track`

```bash
opentraces trail track tr_abc123                     # Full trace lineage
opentraces trail track tr_abc123 --step 4            # One step's evidence
opentraces trail track --patch tracepatch-sha256:abc # One Trace Patch's survival
opentraces trail track --anchor gitanchor-sha256:def # One Git Anchor's survival
opentraces trail track --since 12h                   # Every patch in a time window
opentraces trail track --all                         # Every Trace Patch in the trail
opentraces trail track --patches-from patches.txt    # Ids from a file
```

Walks and renders trace lineage through Git history. Subsumes the former `trail timeline`, `trail explain`, `trail sync`, and `trail search` commands. Batch modes emit one JSON line per patch, so stream them through `jq -s '.'` when collecting.

| Flag | Description |
|------|-------------|
| `--patch TEXT` | Track a single Trace Patch (no TRACE_ID needed) |
| `--anchor TEXT` | Track a single Git Anchor (no TRACE_ID needed) |
| `--step INTEGER` | With TRACE_ID: focus on a single trace step's evidence |
| `--since TEXT` | Batch: track every Trace Patch whose `event_time` falls within a duration window (e.g. `12h`, `30m`, `2d`) or after an ISO timestamp |
| `--patches-from FILE` | Batch: read patch ids from FILE (one id per line, or JSONL with `patch_id` / `trace_patch_id`) |
| `--all` | Batch: track every Trace Patch in the project's trail |
| `--limit INTEGER` | Cap the number of patches emitted in batch mode |
| `--silent` | Run the walk without printing rendered output |
| `--json` | Emit structured JSON instead of text |
| `--project DIRECTORY` | Project directory (default CWD) |

Survival states reported: `alive_on_path`, `alive_transformed`, `reverted`, `lost`, `unknown`, `alive_moved`, `partially_preserved`, `repaired`.

For resolving an `ot://` resource (Trace Patch, Git Anchor, or file line origin) directly, use `opentraces trace get` with the resource ref.

## Bucket Commands

The local trace bucket is the private workspace state that backs the trace index, Trace Trails, and dataset workflows. The `bucket` group inspects it and (via `bucket remote`) syncs it with a HuggingFace private remote.

### `opentraces bucket status`

```bash
opentraces bucket status
opentraces bucket status --json
```

Shows local bucket health, sync eligibility, and trail freshness.

### `opentraces bucket manifest`

```bash
opentraces bucket manifest
opentraces bucket manifest --json
```

Materializes and prints the local bucket manifest. Useful when comparing against a remote out-of-band.

### `opentraces bucket replay`

```bash
opentraces bucket replay --repo /path/to/git-clone
opentraces bucket replay --repo /path/to/git-clone --repo-id my-other-clone
opentraces bucket replay --repo /path/to/git-clone --force --json
```

Replays bucket-exported Trace Trails into a Git repository (e.g. on a different machine after a bucket pull).

| Flag | Description |
|------|-------------|
| `--repo DIRECTORY` | **Required.** Git repository to receive the Trace Trails ref |
| `--repo-id TEXT` | Bucket TrailEvents repo id (required when the bucket has multiple exports) |
| `--force` | Replace an existing differing Trace Trails ref |

### `opentraces bucket remote`

Manages the configured private bucket remote.

```bash
opentraces bucket remote status
opentraces bucket remote diff
opentraces bucket remote push
opentraces bucket remote push --force
opentraces bucket remote pull
opentraces bucket remote pull --force
```

| Subcommand | What it does |
|------------|--------------|
| `status` | Compare the local bucket digest with the configured private remote |
| `diff` | Compare local and remote bucket manifests in detail |
| `push` | Mirror the local bucket into the configured private remote |
| `pull` | Restore the local bucket from the configured private remote |

Shared flags on every `bucket remote` subcommand:

| Flag | Description |
|------|-------------|
| `--root DIRECTORY` | Fake remote root override (testing). Defaults to the configured bucket remote |
| `--force` | (`push` / `pull` only) overwrite a remote-ahead, local-ahead, or diverged bucket |
| `--json` | Emit structured JSON |

Configure the remote up front with `opentraces setup bucket`. Without a configured remote, the bucket runs in local-only mode.

## Dataset Commands

The `dataset` group is the workflow surface that produces HF-shaped JSONL datasets from your retained traces. It replaces the older flat `push` / `pull` / `assess` verbs (push now lives under `dataset publish`, import is handled per-dataset workflow, and assessment runs inside the workflow itself).

### `opentraces dataset list`

```bash
opentraces dataset list
opentraces dataset list --json
```

Lists local HF-shaped datasets and their bound workflows.

### `opentraces dataset new`

```bash
opentraces dataset new my-dataset --workflow my-workflow
opentraces dataset new my-dataset --rows-file rows.jsonl --schema schema.json
opentraces dataset new my-dataset --workflow my-workflow --query-name "fix bugs" --query-scope project
```

Creates a local HF-shaped dataset with an OpenTraces sidecar. Two modes:

- **Workflow mode** (default): synthesizes a workflow-driven dataset that is filled by `dataset run`. Use `--schema` to define the row contract.
- **Ad-hoc mode** (`--rows-file` + `--schema`): seeds a manual dataset directly from a JSONL file. `dataset run` is a no-op for manual datasets; review/approve/publish still work.

| Flag | Description |
|------|-------------|
| `--description TEXT` | Dataset description |
| `--workflow TEXT` | Workflow skill name or path to a Markdown workflow file/package |
| `--workflow-digest TEXT` | Workflow digest for legacy skill-name workflows |
| `--query-name TEXT` | Remembered trace query name for workflow runs |
| `--query-scope [all-projects\|project\|cwd\|trace]` | Remembered trace query scope. Default `all-projects` |
| `--query-lex TEXT` / `--query-semantic TEXT` | Remembered query strings |
| `--query-source [index\|projection]` | Remembered local trace query source |
| `--query-project TEXT` | Remembered trace query project slug |
| `--query-candidate-kind TEXT` | Remembered trace query candidate kind |
| `--query-arg TEXT` | Extra remembered trace query arg as `key=value` |
| `--rows-file FILE` | Ad-hoc mode: JSONL file of rows to seed the dataset with. Requires `--schema` |
| `--schema FILE` | JSON Schema file describing dataset rows |

### `opentraces dataset run`

```bash
opentraces dataset run my-dataset
opentraces dataset run my-dataset --dry-run
opentraces dataset run my-dataset --executor claude-code-headless --limit 20
opentraces dataset run my-dataset --since-last-run --json
```

Runs the dataset workflow.

| Flag | Description |
|------|-------------|
| `--dry-run` | Execute without appending rows or advancing cursors |
| `--executor [current-agent\|claude-code-headless]` | Workflow executor |
| `--scope [all-projects\|project\|cwd\|trace]` | Candidate query scope. Default `all-projects` |
| `--project TEXT` | Project slug for `--scope project` |
| `--trace TEXT` | Trace ID for `--scope trace` |
| `--limit INTEGER` | Candidate limit |
| `--privacy-tier [off\|low\|medium\|high]` | Privacy tier to apply while appending workflow rows |
| `--trail-freshness [warn\|fail\|ignore]` | How to handle stale Trace Trail projections. Default `warn` |
| `--since-last-run` | Use the dataset cursor |
| `--reconcile` | Run a full reconciliation scan |
| `--scheduled` | Mark this run as scheduler-initiated |
| `--verbose` | Include run artefact paths |
| `--json` | Emit structured JSON |

### `opentraces dataset review`

```bash
opentraces dataset review my-dataset
opentraces dataset review my-dataset --tui
opentraces dataset review my-dataset --web
opentraces dataset review my-dataset approve <row-id>
opentraces dataset review my-dataset reject <row-id>
opentraces dataset review my-dataset reset <row-id>
opentraces dataset review my-dataset approve --all
```

Review, approve, reject, or reset dataset rows. The `--tui` and `--web` flags open the interactive inbox surfaces (these replace the standalone `opentraces tui` and `opentraces web` commands from 0.3).

| Flag | Description |
|------|-------------|
| `--tui` | Open TUI review |
| `--web` | Open web review |
| `--all` | With `approve`, `reject`, or `reset`, apply to every eligible row |
| `--json` | Emit structured JSON |

### `opentraces dataset publish`

```bash
opentraces dataset publish my-dataset
opentraces dataset publish my-dataset --to my-org/my-dataset
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset --min-retention 0.5 --exclude-state lost
```

Publishes reviewed dataset rows and contract files to the active remote. Replaces the 0.3 `opentraces push` flow.

| Flag | Description |
|------|-------------|
| `--to TEXT` | Remote name or `owner/name` override |
| `--check-only` | Run all gates and stage without uploading |
| `--resume TEXT` | Resume a previous publication run id |
| `--min-retention FLOAT` | Drop rows whose mean `retention_fraction` across `patches_with_survival` is below this threshold (0.0-1.0) |
| `--exclude-state TEXT` | Drop rows that have any patch with this `survival_state`. Repeatable (e.g. `--exclude-state lost --exclude-state never_committed`) |
| `--json` | Emit structured JSON |

Under `--check-only` the drop counts are reported in the JSON `publish.filter` block without uploading.

### `opentraces dataset remove`

```bash
opentraces dataset remove my-dataset --yes
```

Removes a local dataset after explicit confirmation.

### `opentraces dataset schedule`

Manages local dataset workflow schedules.

```bash
opentraces dataset schedule add my-dataset --cron "0 * * * *"
opentraces dataset schedule list
opentraces dataset schedule logs my-dataset
opentraces dataset schedule pause my-dataset
opentraces dataset schedule resume my-dataset
opentraces dataset schedule show my-dataset
opentraces dataset schedule remove my-dataset
```

### `opentraces dataset status`

```bash
opentraces dataset status my-dataset
opentraces dataset status my-dataset --json
```

Shows row count and publication-state breakdown for a dataset.

### `opentraces dataset remote`

Manages dataset-scoped HuggingFace remotes. Replaces the old project-wide `opentraces remote` group: every dataset now carries its own remotes.

```bash
opentraces dataset remote add my-dataset owner/dataset
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset remote list my-dataset
opentraces dataset remote list my-dataset --verbose
opentraces dataset remote remove my-dataset owner/dataset
opentraces dataset remote remove my-dataset owner/dataset --delete-remote --yes
opentraces dataset remote visibility my-dataset owner/dataset --public
```

| Subcommand | Flags |
|------------|-------|
| `add NAME REPO` | `--json` |
| `create NAME REPO` | `--private / --public` (default `--private`), `--json` |
| `list NAME` | `-v, --verbose` (show full URLs), `--json` |
| `remove NAME [REMOTE]` | `--delete-remote`, `--yes`, `--json` |
| `visibility NAME [REMOTE]` | `--private`, `--public`, `--json` |

## Workflow Commands

The `workflow` group manages local dataset workflow skills, the per-dataset Markdown skill packages that `dataset run` invokes.

### `opentraces workflow create`

```bash
opentraces workflow create my-workflow
opentraces workflow create my-workflow --template default --description "Annotate bug fixes"
```

Scaffolds a new local dataset workflow skill.

| Flag | Description |
|------|-------------|
| `--template TEXT` | Workflow template. Default `default` |
| `--description TEXT` | Workflow description for `SKILL.md` |
| `--json` | Emit structured JSON |

### `opentraces workflow list`

```bash
opentraces workflow list
opentraces workflow list --digest
```

Lists installed workflows with their path and bound datasets.

### `opentraces workflow templates`

```bash
opentraces workflow templates
opentraces workflow templates --json
```

Lists built-in workflow templates available to `workflow create`.

### `opentraces workflow remove`

```bash
opentraces workflow remove my-workflow --yes
```

Removes an installed workflow skill package.

## Doctor

### `opentraces doctor`

```bash
opentraces doctor
opentraces doctor --security
```

Checks configured integrations, versions, and security tiers. It exits non-zero when a required integration is broken.

| Flag | Description |
|------|-------------|
| `--security` | Show only the security pipeline view |

For the LLM trace review tier, `doctor` surfaces the active setup: backend and model, endpoint URL, API format, whether the configured `api_key_env` variable is set, and the probe result (e.g. model count, unreachable reason, or `not found` when the configured model is missing from the endpoint's catalog).

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
opentraces setup watcher
opentraces setup bucket
opentraces setup auth
opentraces setup trufflehog
opentraces setup llm-review
opentraces setup skill
opentraces setup upgrade
```

Current setup subcommands:

- `auth` runs the HuggingFace login flow used by dataset remotes
- `bucket` configures the private bucket sync target (remote-by-default or local-only)
- `claude-code` installs the Claude Code capture hooks
- `git` installs the post-commit correlator hook
- `llm-review` configures the Tier 2 reviewer
- `skill` installs the opentraces skill globally and links it into each agent harness
- `trufflehog` enables Tier 1.5 TruffleHog
- `upgrade` upgrades the CLI and refreshes project files
- `watcher` installs and controls the background attribution watcher (subcommands: `install`, `uninstall`, `start`, `stop`, `restart`, `status`, `tick`)

Run bare `opentraces setup` for an interactive wizard that walks every integration.

### `opentraces setup bucket`

```bash
opentraces setup bucket
opentraces setup bucket --local-only
opentraces setup bucket --repo me/opentraces-bucket
opentraces setup bucket --push-now
opentraces setup bucket --pull-now
```

Configures the private bucket sync target. The bucket is private workspace state; dataset publication remotes are configured separately via `opentraces dataset remote ...`.

| Flag | Description |
|------|-------------|
| `--remote / --local-only` | Configure private remote bucket sync, or opt out to local-only. Default `--remote` |
| `--provider [huggingface\|fake]` | Remote bucket provider. Default `huggingface` |
| `--repo TEXT` | HuggingFace bucket repo id. Defaults to `<authenticated-user>/opentraces-bucket` |
| `--fake-root DIRECTORY` | Local directory used by the fake bucket remote harness |
| `--sync-policy [daemon\|manual]` | How the private remote bucket should be kept current. Default `daemon` |
| `--push-now` | Upload the existing local bucket after setup |
| `--pull-now` | Restore the local bucket from the remote after setup |

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

Tier 1.5 findings are redacted in place and force human review before publication.

### `opentraces setup llm-review`

```bash
opentraces setup llm-review
opentraces setup llm-review --api-format openai-compat --base-url http://localhost:11434/v1 --model gemma3n:e4b
opentraces setup llm-review --disable
opentraces setup llm-review --print
```

| Flag | Description |
|------|-------------|
| `--api-format [openai-compat\|ollama\|anthropic\|fake]` | Reviewer transport |
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

### `opentraces setup claude-code`

```bash
opentraces setup claude-code
opentraces setup claude-code --dry-run
opentraces setup claude-code --remove
```

Installs (or removes) the Claude Code capture hooks (`PreToolUse`, `PostToolUse`, `Stop`, `PostCompact`) into your Claude Code settings file.

| Flag | Description |
|------|-------------|
| `--hooks-dir TEXT` | Directory to drop hook scripts into. Default `~/.claude/hooks/` |
| `--settings-file TEXT` | Path to the Claude Code settings file. Default `~/.claude/settings.json` |
| `--dry-run` | Print the planned hook changes without writing |
| `--remove` | Uninstall previously-installed hooks |

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

Installs the `opentraces` skill so Claude Code (and compatible harnesses) can drive the CLI. The canonical copy lives at `~/.agents/skills/opentraces/`; each supported harness gets a symlink, e.g. `~/.claude/skills/opentraces -> ~/.agents/skills/opentraces`.

| Flag | Description |
|------|-------------|
| `--harness TEXT` | Target harness, repeatable (e.g. `claude-code`). Defaults to every supported harness |
| `--remove` | Uninstall the skill |

### `opentraces setup watcher`

```bash
opentraces setup watcher install
opentraces setup watcher start
opentraces setup watcher stop
opentraces setup watcher restart
opentraces setup watcher status
opentraces setup watcher tick
opentraces setup watcher uninstall
```

The watcher is a launchd agent (macOS) or systemd user timer (Linux) that wakes every poll interval, walks enlisted projects, and runs incremental backfill when new commits or Claude Code JSONL activity appears. It powers `opentraces trail blame` and the lazy Trace Trails maturation pipeline.

| Subcommand | What it does |
|------------|--------------|
| `install` | Render and load the unit + shim |
| `start` | Install (if needed) and start the watcher service |
| `stop` | Stop the watcher service (unit remains installed) |
| `restart` | Stop then start |
| `status` | Show install + running state |
| `tick` | Run one diagnostic tick now |
| `uninstall` | Unload and remove the unit file |

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

The `backfill` and `git-backfill` commands are real but only `git-backfill` is shown in the default `--help` listing. The watcher manages `backfill` automatically.

### `opentraces backfill`

```bash
opentraces backfill
opentraces backfill --rebuild
opentraces backfill --dry-run
```

Backfills per-commit attribution into the local cache. Walks new commits since the last bookmark, correlates them to traces, and populates entity data when the `ot-entities` binary is available.

| Flag | Description |
|------|-------------|
| `--dry-run` | Compute coverage without writing cache files |
| `--rebuild` | Clear the cache and re-attribute from HEAD |
| `--since TEXT` | Start from this ref instead of the bookmark (currently forces `--rebuild`) |
| `--project DIRECTORY` | Project directory (default CWD) |
| `--max-commits INTEGER` | Cap on commits to walk when rebuilding. Default `500` |
| `--json` | Emit a JSON payload instead of the human summary |
| `-v, --verbose` | Forward verbose logging to the audit builder |
| `--no-entities` | Skip the entity-parser pass (attribution only) |

### `opentraces git-backfill`

```bash
opentraces git-backfill
opentraces git-backfill --max-commits 2000 --window-hours 48
opentraces git-backfill --json
```

Retroactively correlates inbox traces to past commits. Useful after a first-time install of the post-commit hook (the hook only sees commits after install) or after a period where the hook failed silently. Walks first-parent history, re-runs the live correlator, writes `refs/notes/opentraces`, and persists `git_links` onto each trace's JSONL file. Safe to re-run.

| Flag | Description |
|------|-------------|
| `--project DIRECTORY` | Project directory (default CWD) |
| `--max-commits INTEGER` | Cap on first-parent commits to walk. Default `500` |
| `--window-hours FLOAT` | Match a trace to a commit if `timestamp_end` is within this many hours of the commit's date (either side). Default `24.0` |
| `--json` | Emit a JSON payload instead of the human summary |

### `opentraces completions`

```bash
opentraces completions install
opentraces completions install zsh --alias otd
opentraces completions install zsh --alias otd --alias ot --quiet
opentraces completions uninstall
opentraces completions uninstall zsh --quiet
```

Prints or installs shell completion scripts. Both `install` and `uninstall` take an optional positional shell name (`bash`, `zsh`, or `fish`); if omitted, the current shell is detected automatically.

| Flag | Description |
|------|-------------|
| `--alias NAME` | (`install`) Also bind completion to `NAME`. Repeatable |
| `-q, --quiet` | Suppress the confirmation output |
