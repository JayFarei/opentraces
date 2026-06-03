# Commands

Reference for the current `opentraces` CLI. Use `--json` on commands that
offer it when another agent or script needs structured output.

```bash
opentraces [--json] <command> ...
```

## Public Root Surface

### Global setup

| Command | What it does |
|---------|---------------|
| `setup` | Wire opentraces into your system |
| `auth` | HuggingFace identity (`login`, `logout`, `whoami`) |
| `config` | Show or set configuration |
| `completions` | Print or install shell completion scripts |

### Project setup

| Command | What it does |
|---------|---------------|
| `init` | Enroll the current project and connect selected agent hooks |
| `status` | Show project snapshot and recent retained traces |
| `doctor` | Report integration and security tool health |
| `remove` | Remove opentraces from the current project |

### Trace, Trail, Context, Bucket

| Command | What it does |
|---------|---------------|
| `trace query` | Search retained traces and return bounded candidate packets |
| `trace index` | Rebuild and inspect local search projections |
| `trace map` | Show a deterministic Trace Map or burst projection |
| `trace slice` | Extract bounded Trace Slices for workflows |
| `trace get` | Resolve a trace, trace unit, map node, or `ot://` resource |
| `trace compare` | Compare two traces: metrics, quality, burst/error, and security deltas |
| `trace teleport` | Move a trace and retained Git evidence between workspaces |
| `trail blame commit` | Resolve commit-to-trace or trace-to-commit attribution |
| `trail blame pr` | Render/create/update PR bodies from branch lineage |
| `trail graph` | Render commit + trace history |
| `trail track` | Walk trace patch survival through Git history |
| `ctx` | Navigate the Context Tree: what the LLM saw at each step |
| `bucket` | Inspect, repair, verify, sync, and replay the private trace bucket |

### Dataset Workflow, Dataset, Security, Capture

| Command | What it does |
|---------|---------------|
| `workflow` | Manage local dataset workflow skill packages |
| `dataset` | Manage local executable datasets and row publication |
| `security` | Optional privacy/security utilities |
| `capture-otlp` | Run and control the OTLP receiver capture source |
| `git-backfill` | Retroactively correlate old commits to retained traces |

## Setup

```bash
opentraces setup
opentraces setup claude-code
opentraces setup codex-cli
opentraces setup git
opentraces setup watcher install
opentraces setup watcher status
opentraces setup bucket
opentraces setup auth
opentraces setup capture-otlp
opentraces setup trufflehog
opentraces setup privacy-filter
opentraces setup llm-review
opentraces setup skill
opentraces setup upgrade
```

Security setup commands only enable the tools you choose. Regex, entropy,
TruffleHog, privacy-filter, LLM PII, business-logic signals, path anonymization,
capsule scope, and classifier are not on by default for per-record sanitization.

`opentraces setup codex-cli` supports:

| Flag | Meaning |
|------|---------|
| `--dry-run` | Print the hook-copy and `hooks.json` update plan without writing files |
| `--remove` | Remove opentraces Codex hook commands and copied scripts |
| `--hooks-file TEXT` | Override the Codex hooks file, default `~/.codex/hooks.json` |
| `--hooks-dir TEXT` | Override the copied hook script directory, default `~/.codex/hooks/opentraces/` |

It registers native Codex hook commands for future Codex CLI sessions. Use
`opentraces doctor` for install health; there is no `setup codex-cli --status`
flag.

`opentraces setup skill` installs the shared opentraces skill into
`~/.agents/skills/opentraces/` and links it into supported harness skill
directories:

```bash
opentraces setup skill --harness claude-code
opentraces setup skill --harness codex-cli
```

Omit `--harness` to refresh every supported harness link. Use
`opentraces doctor` to verify the canonical skill copy and per-harness symlinks.

## Project Commands

```bash
opentraces init
opentraces init --agent claude-code --import-existing
opentraces init --agent codex-cli
opentraces status
opentraces doctor
opentraces doctor --security
opentraces remove
opentraces remove --all
```

`init --agent` accepts `claude`, `claude-code`, `codex`, or `codex-cli`.
`--import-existing` currently imports existing Claude Code traces for the
current repo. Codex CLI capture starts with future sessions after
`opentraces setup codex-cli` and `opentraces init --agent codex-cli`.

## Trace Discovery

```bash
opentraces trace query --lex "bug fix failing test" --cwd --limit 20 --json
opentraces trace query --skill opentraces --include-slice intent
opentraces trace index rebuild
opentraces trace index status
opentraces trace map <trace-id> --bursts --json
opentraces trace slice <trace-id> --template bursts --json
opentraces trace get <trace-id> --bursts --json
opentraces trace get <trace-id> --remote-bucket
opentraces trace get <trace-id> --remote owner/private-bucket
opentraces trace teleport export <trace-id> --output <dir>
```

Common `trace query` filters include `--lex`, `--semantic`, `--skill`,
`--tool`, `--files`, `--signal`, `--survival`, `--since`, `--candidate-kind`,
`--project`, `--cwd`, `--remote-bucket`, and `--source index|projection`.

## Trace Intelligence

Deterministic, derive-on-demand signals about how a run went, layered on top of the Trace surface. No LLM, no schema change, nothing persisted; each is a frozen JSON envelope. Three capabilities: context waste, run signals, run compare.

```bash
opentraces trace map <trace-id> --waste --json       # also: trace get <id> --waste
opentraces trace get <trace-id> --run-intel --json   # also: trace map <id> --run-intel
opentraces trace compare <trace-a> <trace-b> --json
opentraces trace compare <trace-a> <trace-b> --no-quality --json
```

- **Context waste** — `--waste` emits `opentraces.context_waste.v1`: `large_output` (>= 12000 chars), `repeated_file_read` (same file 3+ times in 20 min), and `repeated_search` (`rg|grep|find|ag|ack` 5+ times in 10 min) findings plus a `summary` count block. Override thresholds with `--large-output-chars`, `--file-read-window-min`, `--search-window-min`.
- **Run signals** — `--run-intel` emits `opentraces.run_intel.v1` with deterministic `resteer` / `recovery` / `loop` / `failure` annotations. Recovery only fires after an uncleared prior failure; failure prefers structured tool errors over substring matches; a repeated command is one `loop` signal carrying `evidence.repeat_count`.
- **Run compare** — `trace compare <a> <b>` emits `opentraces.trace_compare.v1` with top-level `{schema_version, status, trace_a, trace_b, fidelity: {a, b}, burst_gap, quality_included, delta}`. `delta` holds `{a, b, delta}` triples over `metrics`, deterministic quality persona scores under `quality` (skip with `--no-quality`, which sets `quality_included: false`), burst/signal counts under `bursts` and `signals`, and `security`. Both traces are pinned to the same burst gap (`--burst-gap`, default 35).

`--waste` and `--run-intel` are mutually exclusive with `--bursts` and with each other; on `trace get` they are also mutually exclusive with `--resume`. The `trace get` and `trace map` surfaces emit byte-identical payloads for matching flags. Each detector reports a `fidelity` of `record` or `otel`, preferring full wire fidelity when the trace was captured via the OTLP receiver. An unresolved trace ref exits with code 6.

## Trace Trails

```bash
opentraces trail blame commit <sha>
opentraces trail blame commit c:<sha> src/main.py --lines
opentraces trail blame commit t:<trace-id> --include-overlapping
opentraces trail blame pr render --base main
opentraces trail blame pr create --base main
opentraces trail blame pr update --base main
opentraces trail graph --limit 50
opentraces trail graph --trace <trace-id>
opentraces trail track <trace-id>
opentraces trail track --patch <trace-patch-id>
opentraces trail track --anchor <git-anchor-id>
opentraces trail track --since 12h --json
opentraces trail track --all --json --limit 50
```

`trail blame` is a group. Use `trail blame commit` for commit/trace
attribution and `trail blame pr` for PR-body consumers.

## Context Tree

```bash
opentraces ctx list --json
opentraces ctx info <trace-id> --json
opentraces ctx tree <trace-id> --json
opentraces ctx show <context-node-id> --json
opentraces ctx step <trace-id> <step-index> --json
opentraces ctx reads <trace-id> --json
opentraces ctx writes <trace-id> --json
opentraces ctx diff <node-a> <node-b> --json
opentraces ctx compactions <trace-id> --json
opentraces ctx resume <context-node-id> --json
opentraces ctx prune <context-node-id> --source-jsonl <session.jsonl>
opentraces ctx resolve ot://context-node/<id> --json
opentraces ctx anchor-for-step <trace-id> <step-index>
```

`ctx list` and `ctx info` read bucket manifests without loading layer blobs.
The other commands resolve Context Tree nodes and layers from local retained
evidence.

## Bucket

```bash
opentraces bucket status --json
opentraces bucket manifest --json
opentraces bucket verify --sample 100 --json
opentraces bucket verify --full --json
opentraces bucket repair --json
opentraces bucket rebuild --json
opentraces bucket rebuild --substrate context-tree --json
opentraces bucket prune --dry-run --json
opentraces bucket prefetch <trace-id> --json
opentraces bucket remote status --json
opentraces bucket remote diff --json
opentraces bucket remote push --json
opentraces bucket remote pull --json
opentraces bucket replay --repo /path/to/git-clone --json
```

The bucket is the private capture-time store. It is separate from datasets:
bucket sync moves raw retained evidence, while dataset publish moves approved
workflow rows.

## Dataset Workflows

```bash
opentraces workflow templates
opentraces workflow templates --json
opentraces workflow create my-workflow
opentraces workflow create my-workflow --template skill-command-trajectory-eval-v1
opentraces workflow list
opentraces workflow list --digest
opentraces workflow remove my-workflow --yes
```

Workflow packages are skill-format row builders. `dataset run` invokes them to
project bucket traces into dataset rows.

## Datasets

```bash
opentraces dataset list --json
opentraces dataset new my-dataset --workflow ./workflows/my-workflow/
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
opentraces dataset run my-dataset --dry-run --limit 5 --json
opentraces dataset run my-dataset --scope trace --trace <trace-id>
opentraces dataset status my-dataset --json
opentraces dataset review my-dataset --json
opentraces dataset review approve my-dataset <row-id>
opentraces dataset review reject my-dataset <row-id>
opentraces dataset review reset my-dataset <row-id>
opentraces dataset review approve my-dataset --all
opentraces dataset remote create my-dataset owner/team-traces --private
opentraces dataset remote list my-dataset --verbose
opentraces dataset publish my-dataset --check-only
opentraces dataset publish my-dataset
opentraces dataset schedule add my-dataset --every 1h --approve-new --publish-check-only
opentraces dataset schedule list
opentraces dataset remove my-dataset --yes
```

The legacy interactive `dataset review --web` and `--tui` flags are still
accepted but return decommission notices.

`dataset run --privacy-tier off|low|medium|high` remains a publication
compatibility field for dataset row envelopes. It is not the security tool
selection mechanism; use `opentraces security sanitize --tools ...` or
`--use-config` in a workflow when explicit sanitization is required.

## Security Tools

```bash
opentraces security tools list
opentraces security tools list --json
opentraces security tools info regex --json
printf '%s\n' '{"text":"OPENAI_API_KEY=sk-demo"}' | opentraces security sanitize --tools regex
printf '%s\n' '{"row":{"path":"/Users/alice/project"}}' | opentraces security sanitize --tools path_anonymizer
printf '%s\n' '{"record":{...}}' | opentraces security sanitize --use-config
```

`security sanitize` requires either `--tools` or `--use-config`.
`--tools` runs the named tools in canonical order. `--use-config` runs only
tools explicitly enabled in the loaded config.

Registered tools: `regex`, `entropy`, `trufflehog`, `privacy_filter`,
`llm_pii`, `business_logic`, `path_anonymizer`, `capsule_scope`, and `classifier`.

## OTLP Capture

```bash
opentraces setup capture-otlp
opentraces capture-otlp start
opentraces capture-otlp status --json
opentraces capture-otlp flush --session <session-id> --project <repo> --trace-id <trace-id>
opentraces capture-otlp restart
opentraces capture-otlp stop
```

The receiver feeds Claude Code OTel events and raw API bodies into Context
Tree events. If the receiver is down, agent traffic is not blocked.

## Advanced

```bash
opentraces git-backfill --max-commits 2000 --window-hours 48 --json
opentraces completions install zsh --alias ot
opentraces capabilities --json
opentraces introspect --json
```

`capabilities` and `introspect` are machine-readable metadata surfaces for
agents and tests. They are intentionally less prominent than the workflow
commands above.
