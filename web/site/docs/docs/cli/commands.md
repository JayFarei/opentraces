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
| `trace skills` | Rank observed skills by snapshot-backed invocation usage |
| `trace index` | Refresh and inspect local search projections |
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
opentraces setup pi
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
opentraces setup uninstall
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

`opentraces setup pi --dry-run --json` reports the Pi package/checklist plan
without writing. Use `pi install npm:opentraces-pi` for the primary package
install path. Under global tracking (the default) Pi auto-enrolls each repo on
first capture like Claude/Codex; opt out with `opentraces config tracking-mode
manual` or a per-project `excluded` marker, or run `opentraces init --agent pi`
to enroll a repo explicitly.

`opentraces setup pi` supports:

| Flag | Meaning |
|------|---------|
| `--dry-run` | Report the package/checklist plan without writing |
| `--project` | Write/check project-local `.pi/settings.json` instead of global Pi settings |
| `--settings-file TEXT` | Use an explicit Pi `settings.json` path |
| `--local` | Use the repo-local `packages/opentraces-pi` package path when present |
| `--remove` | Remove the OpenTraces package entry instead of installing |
| `--json` | Emit machine-readable JSON, accepted for Pi tool callers |

Inside Pi, the package provides `/ot-capture-status`, `/ot-setup`, `/ot-search
<query>`, `/ot-trace <trace-id>`, `/ot-standup`, `/ot-capsule [trace-id]`, and
`/ot-dataset`. The matching model tools are `ot_capture_status`, `ot_search`,
`ot_trace`, `ot_standup`, `ot_capsule`, and `ot_dataset`.

`opentraces setup skill` installs the shared opentraces skill into
`~/.agents/skills/opentraces/` and links it into supported harness skill
directories:

```bash
opentraces setup skill --harness claude-code
opentraces setup skill --harness codex-cli
opentraces setup skill --harness pi
```

Omit `--harness` to refresh every supported harness link. Use
`opentraces doctor` to verify the canonical skill copy and per-harness symlinks.

A bare `opentraces setup upgrade` does the full stack: it upgrades the CLI via
the detected install method (`pipx`, Homebrew, or pip; a `source`/editable
install skips the package upgrade), re-renders every installed integration glue
file (watcher shim, git post-commit hook, Claude Code and Codex CLI hooks, OTLP
settings and autostart) re-stamped to the new version, and refreshes the project
skill and hook if opted in. Two mutually exclusive flags narrow that scope:

| Flag | Meaning |
|------|---------|
| `--integrations-only` | Re-render only already-installed integration glue to the current CLI version, with no CLI bump and without enabling new integrations |
| `--skill-only` | Refresh only the skill file and hook, skip the CLI upgrade |

`opentraces setup uninstall` is the symmetric inverse of `setup`: one command that reverses the whole multi-surface install (Claude Code / Codex / Pi capture hooks, the OTLP receiver + its `~/.claude/settings.json` env keys, the watcher daemon, the skill, shell completions, per-repo git post-commit hooks, security-tool flags) and stops every opentraces process. It is **data-safe by default** and emits the frozen `opentraces.setup_uninstall.v1` JSON envelope (`removed_names`, `skipped_names`, `keys_removed`, `refs_preserved`, `refs_purged`, `daemon_stopped`, `unit_unloaded`, `package_uninstall_command`, `residue`).

| Flag | Meaning |
|------|---------|
| `--integrations-only` | Default. Reverse every install-time patch + daemon; PRESERVE all captured data (bucket, datasets, projects, staging, and every `refs/opentraces/*` + `refs/notes/opentraces`) |
| `--purge` | Also DELETE the captured corpus + opentraces git refs. UNRECOVERABLE (the canonical Trail event log and its only local replay source, the bucket, both die); requires a typed confirmation |
| `--project PATH` | Scope per-repo reversal (and, under `--purge`, ref-purge) to one repository instead of every registered repo |
| `--prune-unflushed` | Also delete un-flushed raw bodies + OTel staging (`raw-bodies/`, `staging/otel/`); destructive opt-in under the default tier |
| `--dry-run` | Resolve and print the plan; change nothing. The recommended first run |
| `--yes` | Skip the `--purge` confirmation (required for `--purge` in non-interactive use); does not bypass `--dry-run` |

It quiesces first (sets each repo's `excluded` marker, resets `tracking_mode` to `manual`, stops the daemons) so a mid-uninstall agent touch cannot re-enroll the repo, then reverses the surfaces, reusing every existing per-surface reverser. The package itself is never self-uninstalled; the correct command for the detected install method is printed, and `~/.cache/huggingface/token` is never deleted. A configured remote bucket survives a local `--purge` and is named in `residue`. Unlike `opentraces remove` (one cwd repo), `setup uninstall` fans out across every registered repo.

## Project Commands

```bash
opentraces init
opentraces init --agent claude-code --import-existing
opentraces init --agent codex-cli
opentraces init --agent pi
opentraces status
opentraces doctor
opentraces doctor --security
opentraces remove
opentraces remove --all
```

`opentraces doctor` also reports CLI freshness and integration version drift.
Under `--json`, `doctor.cli` carries `installed_version`, `latest_version`,
`upgrade_available`, `check_state` (`fresh`, `cached`, `stale-cache`,
`unavailable`, `disabled`, or `unknown`), `source`, and `cache_path`. The
latest-version lookup is an offline-safe, 24h-cached PyPI check; set
`OPENTRACES_DISABLE_VERSION_CHECK=1` to disable it. `doctor.integrations`
(`cli_version`, `items`, `drift`) reports each deployed integration glue file
against the running CLI. Doctor emits a top-level `next_command` / `next_steps`
directive for agents: `opentraces setup upgrade` when a newer CLI is available,
or `opentraces setup upgrade --integrations-only` when only deployed glue has
drifted. An available upgrade alone stays exit 0, but `doctor` exits 3 when an
installed integration has version drift (alongside the existing broken-hook and
invalid-trail exit-3 conditions). The human view shows a warn row
"v\<latest\> available; run 'opentraces setup upgrade'" and per-integration
"drift: ...; run 'opentraces setup upgrade --integrations-only'".

Under `--json`, `doctor.trace_index.state` and `rebuild_advice` describe the
**live search snapshot** that `trace query` actually serves (`state` is `ok` /
`stale` / `missing`; `rebuild_advice` is always `opentraces trace index
rebuild`). The deprecated legacy `index.db` compatibility cache is reported
separately as `doctor.trace_index.legacy_index_state`, so its absence is not a
current failure when the snapshot is healthy. Both `opentraces --json doctor`
and the command-local `opentraces doctor --json` emit this payload.

`init --agent` accepts `claude`, `claude-code`, `codex`, `codex-cli`, or `pi`.
`--import-existing` currently imports existing Claude Code traces for the
current repo. Codex CLI and Pi capture start with future sessions after their
runtime setup and project enrollment are in place. Pi project enrollment is an
explicit consent gate even when tracking mode is global.

## Trace Discovery

```bash
opentraces trace query --lex "bug fix failing test" --cwd --limit 20 --json
opentraces trace query --skill opentraces --include-slice intent
opentraces trace skills --json
opentraces trace skills --skill opentraces --json
opentraces trace index --json
opentraces trace map <trace-id> --bursts --json
opentraces trace slice <trace-id> --template bursts --json
opentraces trace get <trace-id> --bursts --json
opentraces trace get <trace-id> --remote-bucket
opentraces trace get <trace-id> --remote owner/private-bucket
opentraces trace teleport export <trace-id> --output <dir>
```

Common `trace query` filters include `--lex`, `--semantic`, `--skill`,
`--tool`, `--files`, `--signal`, `--survival`, `--since`, `--candidate-kind`,
`--project`, and `--cwd`. `trace query` is read-only against the local search
snapshot: it neither rebuilds the index nor pulls remote data. `trace skills`
uses the same compact snapshot's `skill_invocations` table and emits
`telemetry.duration_ms` plus `search_diagnostics` so agents can tell whether a
raw scan happened. To search remote traces, first `opentraces bucket remote
pull` and then refresh the snapshot with `opentraces trace index --json`.
(`trace get --remote-bucket` / `--remote
owner/repo` remain the per-trace remote-read path.)

`--semantic` expands a small static dictionary of service/library concepts
observed in trace evidence (for example Hugging Face or MongoDB); it is not an
embedding search. A query that matches no concept falls back to lexical
matching where every token must match, so natural-language paraphrases can
miss traces that concrete keywords would find.

## Trace Intelligence

Deterministic, derive-on-demand signals about how a run went, layered on top of the Trace surface. No LLM, no schema change, nothing persisted; each is a frozen JSON envelope. Three capabilities: context waste, run signals, run compare.

```bash
opentraces trace map <trace-id> --waste --json       # also: trace get <id> --waste
opentraces trace get <trace-id> --run-intel --json   # also: trace map <id> --run-intel
opentraces trace compare <trace-a> <trace-b> --json
opentraces trace compare <trace-a> <trace-b> --no-quality --json
```

- **Context waste** — `--waste` emits `opentraces.context_waste.v2`: `large_output` (>= 12000 chars), `repeated_file_read` (same file 3+ times in 20 min), and `repeated_search` (`rg|grep|find|ag|ack` 5+ times in 10 min) findings plus a `summary` count block. Override thresholds with `--large-output-chars`, `--file-read-window-min`, `--search-window-min`.
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
opentraces bucket manifest --heal --json
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
opentraces bucket security status
opentraces bucket security policy --policy recommended
opentraces bucket security policy --tool regex --enable
opentraces bucket security policy --tool entropy --disable
opentraces bucket security status --json
```

`bucket security` is a command group, the scoped bucket policy front-end.
`bucket security status` is a read-only posture inspector. `bucket security policy
--policy` applies a named bundle and accepts only `off|basic|recommended|strict`.
`bucket security policy --tool ... --enable` / `--tool ... --disable` edits one
tool at a time. `bucket security run [--all | --trace <id>]` applies the configured
filter to existing records so they become remote-sync eligible. The policy
commands flip the same `cfg.security.<tool>.enabled` flags as `security tools` and
`config set`, scoped to the bucket.

`bucket status` and `bucket manifest` are side-effect-free reads: they never
write under the bucket. Self-heal (materializing the top-level manifest from the
per-trace envelopes on disk) is explicit via `bucket manifest --heal`, or do a
full rebuild with `bucket repair`.

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
opentraces dataset new opentraces-episodes --from-skill opentraces
opentraces dataset new my-import --rows-file rows.jsonl --schema schema.json
opentraces dataset run my-dataset --dry-run --limit 5 --json
opentraces dataset run opentraces-episodes --executor script --json
opentraces dataset run my-dataset --scope trace --trace <trace-id>
opentraces dataset status my-dataset --json
opentraces dataset security my-dataset --json
opentraces dataset security my-dataset --tool business_logic --enable
opentraces dataset security my-dataset --tool regex --disable --unsafe-override --reason "synthetic fixtures"
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

`dataset new --from-skill <skill>` creates a `skill-episodes-v1` dataset and
stores the selected skill in the dataset's `candidate_query`. Pair it with
`dataset run <name> --executor script --json` for deterministic local row
materialization from the snapshot-backed skill invocation surface.

`dataset security <name>` inspects or edits that dataset's resolved security
policy, which is seeded from the workflow's front-matter security contract at
`dataset new --workflow` and stored in the dataset manifest. It is scoped to one
dataset, not a global config toggle, and there is no `--policy` form. Optional
tools toggle with `--tool <name> --enable|--disable`; a required tool can only be
disabled with `--unsafe-override` (and an optional `--reason`) when the workflow
contract permits it. `dataset publish --check-only` blocks rows when a dataset's
required security tools are not satisfied.

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

`security tools list|info` and `security sanitize` are the generic registry
surface for introspection and explicit sanitization. `bucket security` is the
scoped bucket policy front-end over the same `cfg.security.<tool>.enabled`
flags, where `--policy` accepts only `off|basic|recommended|strict`.

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
