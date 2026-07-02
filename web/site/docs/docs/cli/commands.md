# Commands

Reference for the current `opentraces` CLI. Use `--json` on commands that
offer it when another agent or script needs structured output; under an
explicit `--json`, stdout is pure JSON, with no `---OPENTRACES_JSON---`
sentinel line, and interactive commands refuse to prompt (a structured
`INTERACTIVE_REQUIRED` error, exit code 2, zero prompt bytes) rather than
hang under `--json` or a non-TTY.

```bash
opentraces [--json] <command> ...
```

A bare `opentraces <ref>[:<step>|:last|:<A>-<B>]` root-dispatches to
`opentraces trace get <ref>` when `<ref>` is not an existing command name and
is address-shaped: a full trace-id UUID, a `t:`/`tu:`/`tmn:`/`ot://` typed id,
or a bare hex prefix (8+ chars) that uniquely resolves to one trace, the same
trace address grammar `ctx` and `trail` resolve, so one token names the
"resume triple" (action / context / world) across all three. Known commands
always win, so no verb can ever be shadowed.

## Public Root Surface

### Global setup

| Command | What it does |
|---------|---------------|
| `setup` | Wire opentraces into your system |
| `upgrade` | Upgrade the CLI, re-render installed integration glue, and refresh the project skill (root peer of `setup`, not a subcommand) |
| `uninstall` | Reverse the opentraces install, the symmetric inverse of `setup` (root peer of `setup`, not a subcommand) |
| `auth` | HuggingFace identity (`login`, `logout`, `whoami`) |
| `config` | Show or set configuration |
| `completions` | Print or install shell completion scripts |

### Project setup

| Command | What it does |
|---------|---------------|
| `init` | Enroll the current project and connect selected agent hooks |
| `status` | Fleet bucket safety dashboard: is my private trace bucket safe to sync? |
| `doctor` | Report integration and security tool health |
| `remove` | Remove opentraces from the current project |

### Trace, Trail, Context, Bucket

| Command | What it does |
|---------|---------------|
| `trace query` | Search retained traces and return bounded candidate packets |
| `trace get` | Resolve a trace, trace unit, map node, or `ot://` resource; resolves `<trace>:<step>` / `:last` / `:A-B` |
| `trace map` | Show a deterministic Trace Map or burst projection |
| `trace slice` | Extract bounded Trace Slices, or tile the whole trace into Trajectories with `--by` |
| `trail blame commit` | Resolve commit-to-trace or trace-to-commit attribution |
| `trail pr render\|create\|update` | Render/create/update PR bodies from branch lineage (lifted from `trail blame pr`) |
| `trail graph` | Render commit + trace history |
| `trail track` | Walk trace patch survival through Git history |
| `ctx` | Navigate the Context Tree: what the LLM saw at each step, via the bare-noun `ctx <trace>[:<step>\|:last]` |
| `bucket` | Inspect, list, verify, repair, reclaim, connect, and sync the private trace bucket |

The CLI collapsed from 8 visible `trace` verbs to 4 (`query` / `get` / `map` / `slice`) and lifted `trail blame pr` to top-level `trail pr`; every demoted verb (`trace skills`, `trace index`, `trace partition`, `trace compare`, `trace teleport`, `trace discover`, `bucket status`, `bucket manifest`, `bucket remote`, `bucket rebuild`, `bucket replay`, `bucket prune`, `bucket prefetch`, `setup bucket`, `setup upgrade`, `setup uninstall`, `setup llm-review`, `ctx tree`/`show`/`step`/`reads`/`writes`/`list`/`info`) stays callable and `--json`-scriptable, hidden from `--help`, never removed.

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
opentraces bucket connect
opentraces auth login
opentraces setup capture-otlp
opentraces setup trufflehog
opentraces setup privacy-filter
opentraces setup skill
opentraces upgrade
opentraces uninstall
```

`upgrade` and `uninstall` are root peer verbs beside `setup` (the in / update /
out triad), not subcommands of it: `opentraces upgrade` / `opentraces
uninstall`. `opentraces bucket connect` (a thin wrapper over the same
configure logic) is the rename of `setup bucket`; the old `setup bucket` /
`setup upgrade` / `setup uninstall` spellings stay callable, hidden.
`setup doctor` aliases the root `opentraces doctor`. The former `setup
llm-review` is hidden; the canonical LLM row-review surface is `opentraces
dataset review` / `opentraces dataset publish`, not a setup step.

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

A bare `opentraces upgrade` does the full stack: it upgrades the CLI via
the detected install method (`pipx`, Homebrew, or pip; a `source`/editable
install skips the package upgrade), re-renders every installed integration glue
file (watcher shim, git post-commit hook, Claude Code and Codex CLI hooks, OTLP
settings and autostart) re-stamped to the new version, and refreshes the project
skill and hook if opted in. Two mutually exclusive flags narrow that scope:

| Flag | Meaning |
|------|---------|
| `--integrations-only` | Re-render only already-installed integration glue to the current CLI version, with no CLI bump and without enabling new integrations |
| `--skill-only` | Refresh only the skill file and hook, skip the CLI upgrade |

`opentraces uninstall` is the symmetric inverse of `setup`: one command that reverses the whole multi-surface install (Claude Code / Codex / Pi capture hooks, the OTLP receiver + its `~/.claude/settings.json` env keys, the watcher daemon, the skill, shell completions, per-repo git post-commit hooks, security-tool flags) and stops every opentraces process. It is **data-safe by default** and emits the frozen `opentraces.setup_uninstall.v1` JSON envelope (`removed_names`, `skipped_names`, `keys_removed`, `refs_preserved`, `refs_purged`, `daemon_stopped`, `unit_unloaded`, `package_uninstall_command`, `residue`).

| Flag | Meaning |
|------|---------|
| `--integrations-only` | Default. Reverse every install-time patch + daemon; PRESERVE all captured data (bucket, datasets, projects, staging, and every `refs/opentraces/*` + `refs/notes/opentraces`) |
| `--purge` | Also DELETE the captured corpus + opentraces git refs. UNRECOVERABLE (the canonical Trail event log and its only local replay source, the bucket, both die); requires a typed confirmation |
| `--project PATH` | Scope per-repo reversal (and, under `--purge`, ref-purge) to one repository instead of every registered repo |
| `--prune-unflushed` | Also delete un-flushed raw bodies + OTel staging (`raw-bodies/`, `staging/otel/`); destructive opt-in under the default tier |
| `--dry-run` | Resolve and print the plan; change nothing. The recommended first run |
| `--yes` | Skip the `--purge` confirmation (required for `--purge` in non-interactive use); does not bypass `--dry-run` |

It quiesces first (sets each repo's `excluded` marker, resets `tracking_mode` to `manual`, stops the daemons) so a mid-uninstall agent touch cannot re-enroll the repo, then reverses the surfaces, reusing every existing per-surface reverser. The package itself is never self-uninstalled; the correct command for the detected install method is printed, and `~/.cache/huggingface/token` is never deleted. A configured remote bucket survives a local `--purge` and is named in `residue`. Unlike `opentraces remove` (one cwd repo), `uninstall` fans out across every registered repo.

## Project Commands

```bash
opentraces init
opentraces init --agent claude-code --import-existing
opentraces init --agent codex-cli
opentraces init --agent pi
opentraces status
opentraces status --short
opentraces status --project my-repo
opentraces doctor
opentraces doctor --security
opentraces remove
opentraces remove --all
```

`opentraces status` is the fleet bucket safety dashboard (`opentraces.bucket.status.v1`):
an O(1) read of the local bucket, never a full scan, reporting how many
traces are captured, how many are cleared for sync, and, the load-bearing
signal, how many are not yet cleared. The green "safe to sync" verdict is
**structurally impossible** while any trace is unscanned; the wording is a
PROCESS state ("not cleared for sync"), never a content claim. Flags:
`--short` (one-line porcelain summary), `--full` (adds debugger detail the
default render drops), `--project SLUG` (scope every count and the verdict
to one project), `--json`. The former per-project capture inbox (counts by
stage, active remote, most recent traces) moved to the hidden
`opentraces status-inbox`; the lower-level per-field bucket-health readout
`status`'s verdict points at for deeper debugging is the hidden
`opentraces bucket status`.

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
`stale` / `missing` / `error`, mapped from the snapshot state: ok→ok, stale→stale,
missing→missing, wrong_schema→stale, unreadable/error/unknown→error;
`rebuild_advice` is always `opentraces trace index rebuild`). The deprecated legacy `index.db` compatibility cache is reported
separately as `doctor.trace_index.legacy_index_state`, so its absence is not a
current failure when the snapshot is healthy. Both `opentraces --json doctor`
and the command-local `opentraces doctor --json` emit this payload.

Under `--json`, `doctor.runtime_provenance` reports install **provenance** —
not just versions — so a machine with multiple opentraces runtimes (editable
checkout, pipx, Homebrew) that all report the same version is still
distinguishable. It carries `current` (the foreground command's python +
`module_file` + `source_kind` + git root/commit), `discovered_installs` (the
distinct code roots behind the configured integration runners, with real
`module_file`/`dist_version` from a bounded probe), `integration_runners`
(codex/claude/git/watcher/otlp, each with `matches_current`), and a
`state` of `single_runtime` or `mixed_runtimes`. Mixed runtimes are a
`warning` (never an error — they never change doctor's exit code); the advice
is informational and never deletes or rewrites anything.

`init --agent` accepts `claude`, `claude-code`, `codex`, `codex-cli`, or `pi`.
`--import-existing` currently imports existing Claude Code traces for the
current repo. Codex CLI and Pi capture start with future sessions after their
runtime setup and project enrollment are in place. Pi project enrollment is an
explicit consent gate even when tracking mode is global.

## Trace Discovery

```bash
opentraces trace query --lex "bug fix failing test" --cwd --limit 20 --json
opentraces trace query --skill opentraces --include-slice intent
opentraces trace map <trace-id> --bursts --json
opentraces trace slice <trace-id> --template bursts --json
opentraces trace slice <trace-id> --by user-turn --json
opentraces trace slice <trace-id> --by milestone --judge agent --answers answers.json --json
opentraces trace get <trace-id> --bursts --json
opentraces trace get <trace-id>:<step-index> --json
opentraces trace get <trace-id>:last --json
opentraces trace get <trace-id> --remote owner/private-bucket
```

The visible `trace` surface is four verbs: `query` (search) -> `get` (pull
up) -> `map` (dissect) -> `slice` (extract). Two verbs from the previous CLI
folded into that loop and stay callable, hidden from `--help`: `trace skills`
is now `trace query --skill <name>` (same compact snapshot's
`skill_invocations` table, same `telemetry.duration_ms` + `search_diagnostics`
so agents can tell whether a raw scan happened); `trace partition <ref> --by
s1|s2|s3|s4` is now `trace slice <target> --by user-turn|change-burst|
milestone|subgoal` (see below). `trace index`, `trace compare`, `trace
discover`, and `trace teleport` are likewise hidden-but-callable; the search
index now self-maintains invisibly behind `trace query` rather than needing an
explicit rebuild step.

Common `trace query` filters include `--lex`, `--semantic`, `--skill`,
`--tool`, `--files`, `--signal`, `--survival`, `--since`, `--candidate-kind`,
`--project`, and `--cwd`. `trace query` is read-only against the local search
snapshot: it neither rebuilds the index nor pulls remote data. Under active
capture the snapshot can be marked stale by a concurrent write; rather than
dead-ending with `maintenance_needed`, the default query serves the
last-known-good snapshot and attaches a `freshness` object (`stale`,
`stale_reason`, `built_at`, `source_hash`, `rebuild_recommended`). Pass
`--fresh` for strict freshness: it returns `maintenance_needed` instead of
serving a stale snapshot when freshness cannot be proven. To search remote
traces, first `opentraces bucket sync pull` and then refresh the snapshot with
the hidden `opentraces trace index --json`. `trace get --remote owner/repo` is
the per-trace remote-read path, a uniform `--remote <hf-repo>` flag that
`trace query`, `trace get`, and `ctx` all take to read from a HuggingFace
bucket ad hoc; the old `--remote-bucket` / `--force-remote-bucket` flags on
`trace query` / `trace get` are hidden deprecated aliases for it. `bucket
sync` moves the whole bucket against the one remote configured by `bucket
connect`, so its verbs don't take a per-call `--remote`.

### `trace slice --by` — the Trace Slicer Library

`opentraces trace slice <trace> --by <user-turn|change-burst|milestone|subgoal> --json`
runs one operation, `slice(trace, slicer) -> Trajectory[]`, that decomposes a
captured session into an array of trajectories which **tiles** the whole trace
(every step in exactly one trajectory). It absorbs the former `trace partition
<trace> --by s1|s2|s3|s4`, which stays callable, hidden from `--help`, sharing
the exact same slicing engine byte-for-byte (its `s1`-`s4` ids map onto the
`--by` names below). It emits the frozen `opentraces.slicing.v1` envelope
(`trace_id`, `slicer`, `tier`, `total_steps`, `trajectories[]`, `tiling`); a
`Trajectory` is `{start, end, kind, label}` with an end-inclusive step-index
span. It is derive-on-demand (no captured-schema change, no `SCHEMA_VERSION`
bump), like `--waste` / `--run-intel` / `trace compare`.

| Slicer | `--by` | former `--by` (`trace partition`) | Tier | What it isolates |
|---|---|---|---|---|
| user-turn | `user-turn` | `s1` | deterministic | one genuine human ask plus the work serving it |
| change-burst | `change-burst` | `s2` | deterministic | a coherent code-change burst (reuses the burst gap, 35) with its read/verify |
| milestone | `milestone` | `s3` | cheap-LLM | a span ending on a verified outcome (no over-segmenting same-artifact successes) |
| subgoal | `subgoal` | `s4` | cheap-LLM | one coherent sub-goal with one deliverable |

The cheap-LLM slicers (`milestone`, `subgoal`) resolve their judgment through a
pluggable `--judge deterministic|agent|provider|human` (default `agent`). With
the agent judge, if judgments are needed and none are supplied the command
computes the `JudgmentRequest`s, prints them with an instruction, and exits
`rc=10` (`opentraces.slicing.needs_judgment.v1`). Answer each request, write
`{"answers": [{"id": "...", "decision": "...", "confidence": 0.9}]}` to a file,
and re-run with `--answers <file>` for the final tiling at `rc=0`. `slice` is a
pure function of `(trace, answers)` — same inputs, byte-identical output. The
`provider` judge resolves the same requests unattended via the detected LLM
backend. Reading the trace from a HuggingFace bucket ad hoc (`--remote <repo>`)
is available today only on the hidden `trace partition <ref> --by s3 --remote
<repo>` form; `trace slice --by` does not yet expose that flag.

Each milestone / subgoal trajectory is given a short, outcome-first
**label** so a sliced session reads as a legible map. When a model is reachable
(the detected `claude` CLI / API) the labels are model-authored in one batched
call per trace; with no model they fall back to the agent's own narration. Labels
never affect the boundaries — tiling stays deterministic.

Explicit maintenance commands that can run for minutes accept a shared
`--progress auto|plain|json|never` flag (starting with the hidden `opentraces
trace index rebuild`; the search index now self-maintains behind `trace
query`, so this is callable plumbing rather than a step you run by hand).
Progress and heartbeat events go to **stderr** so the final `--json`
payload on stdout stays clean; `--progress json` emits a stable JSONL event
stream (`{"event":"progress","command":...,"stage":...,"elapsed_ms":...}`) that
lets an agent tell "working" from "hung". Default is `auto` (quiet on a
non-TTY/CI, human-readable on a terminal); read commands like `trace query`
stay quiet and never gain hidden rebuild/progress behavior.

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
- **Run compare** — `trace compare <a> <b>` (hidden from `--help`, still callable and `--json`-scriptable; it isn't one of the four job verbs) emits `opentraces.trace_compare.v1` with top-level `{schema_version, status, trace_a, trace_b, fidelity: {a, b}, burst_gap, quality_included, delta}`. `delta` holds `{a, b, delta}` triples over `metrics`, deterministic quality persona scores under `quality` (skip with `--no-quality`, which sets `quality_included: false`), burst/signal counts under `bursts` and `signals`, and `security`. Both traces are pinned to the same burst gap (`--burst-gap`, default 35).

`--waste` and `--run-intel` are mutually exclusive with `--bursts` and with each other; on `trace get` they are also mutually exclusive with `--resume`. The `trace get` and `trace map` surfaces emit byte-identical payloads for matching flags. Each detector reports a `fidelity` of `record` or `otel`, preferring full wire fidelity when the trace was captured via the OTLP receiver. An unresolved trace ref exits with code 6.

## Trace Trails

```bash
opentraces trail <trace-id>
opentraces trail <trace-id>:<step-index>
opentraces trail blame commit <sha>
opentraces trail blame commit c:<sha> src/main.py --lines
opentraces trail blame commit t:<trace-id> --include-overlapping
opentraces trail pr render --base main
opentraces trail pr create --base main
opentraces trail pr update --base main
opentraces trail graph --limit 50
opentraces trail graph --trace <trace-id>
opentraces trail track <trace-id>
opentraces trail track --patch <trace-patch-id>
opentraces trail track --anchor <git-anchor-id>
opentraces trail track --since 12h --json
opentraces trail track --all --json --limit 50
```

The bare-noun `trail <trace>` returns a bounded, survival-free lineage
overview and `trail <trace>:<step>` returns the world (outcome + hunk) at
that step, the same `trace:step` address `trace get` and `ctx` resolve.
`trail blame` is a group scoped to commit/trace attribution: use `trail
blame commit` for that. PR-body rendering is lifted to the top-level `trail
pr render|create|update` (the old `trail blame pr` path stays callable,
hidden). `trail teleport` is dropped from `--help` (hidden but callable).

## Context Tree

```bash
opentraces ctx <trace-id>
opentraces ctx <trace-id>:<step-index>
opentraces ctx <trace-id>:last
opentraces ctx <trace-id>:<step-index> --layer system
opentraces ctx <trace-id>:<step-index> --layer messages
opentraces ctx <trace-id>:<step-index> --full --json
opentraces ctx diff <node-a> <node-b> --json
opentraces ctx compactions <trace-id> --json
opentraces ctx resume <context-node-id> --json
opentraces ctx prune <context-node-id> --source-jsonl <session.jsonl>
opentraces ctx resolve ot://context-node/<id> --json
opentraces ctx anchor-for-step <trace-id> <step-index>
```

`ctx` is bare-noun addressable: `ctx <trace>` renders the context overview
(shape + capture method), `ctx <trace>:<step>` the model input at that step,
and `ctx <trace>:last` the final/active step. `--layer system|messages|
tools|runtime` renders one layer readably (default is the bounded four-layer
card); `--full` inlines the full hydrated model input (system + messages +
tools + runtime), the fork/eval-row packet; `--with-dropped` includes
compaction-dropped content; `--remote <hf-repo>` reads from a remote bucket
and `--offline` fails rc=4 instead of fetching a blob not already cached.
`<trace>:<step>` is the same universal address `trace get` and `trail`
resolve, so a `trace:step` ref piped in on stdin (`trace query --json | ctx
--json`) is hydrated too. The former `ctx tree` / `ctx show` / `ctx step`
subcommands are superseded by the bare noun and stay callable, hidden;
`ctx reads` / `ctx writes` (per-step decomposition, now `ctx <trace>:<step>
--json | jq`), `ctx list` / `ctx info` (manifest-only reads, superseded by
`bucket list`), and `ctx diff` / `ctx compactions` / `ctx resume` / `ctx
prune` / `ctx resolve` / `ctx anchor-for-step` (Context Tree plumbing) remain
hidden-but-callable and resolve Context Tree nodes and layers from local
retained evidence.

## Bucket

```bash
opentraces bucket list --json
opentraces bucket list --unscanned --json
opentraces bucket list --project my-repo --count
opentraces bucket verify --sample 100 --json
opentraces bucket verify --full --json
opentraces bucket repair --json
opentraces bucket reclaim --json
opentraces bucket reclaim --apply --json
opentraces bucket connect --repo me/opentraces-bucket
opentraces bucket connect --local-only
opentraces bucket sync status --json
opentraces bucket sync diff --json
opentraces bucket sync push --dry-run --json
opentraces bucket sync push --json
opentraces bucket sync pull --json
opentraces bucket security status
opentraces bucket security policy --policy recommended
opentraces bucket security policy --tool regex --enable
opentraces bucket security policy --tool entropy --disable
opentraces bucket security status --json
```

`bucket status`, `bucket manifest`, `bucket remote` (`status`/`diff`/`push`/
`pull`), `bucket rebuild`, `bucket replay`, `bucket prune`, `bucket prefetch`,
and `bucket security` are hidden from `--help` in v7 (hidden != removed): the
visible bucket surface is `bucket list` / `bucket connect` / `bucket sync` /
`bucket verify` / `bucket repair` / `bucket reclaim`. `bucket manifest` folded
into `bucket list` (the read side) and `bucket repair` (the `--heal` write
side, plus what `bucket rebuild` used to cover for per-trace envelopes and
anchors); the hidden `bucket rebuild --substrate context-tree|trail|traces|
all` remains for substrate-targeted rebuilds beyond what `bucket repair`
covers. `bucket prune` / `bucket prefetch` stay callable for that narrower
maintenance need, but `verify` / `repair` / `reclaim` are the documented path.
`opentraces bucket connect` (the rename of `setup bucket`, which stays
callable, hidden) configures the private bucket remote target: `--remote` /
`--local-only`, `--provider huggingface|fake`, `--repo`, `--sync-policy
daemon|manual`.

`bucket list` is the bounded, paginated per-trace inventory (issue #162) that
supersedes the hang-prone `bucket manifest`: it reads the plan-087 per-row
status accelerator once and applies `--unsynced` / `--unfiltered` /
`--security-stale` / `--unscanned` / `--project` / `--since` before
projecting rows, so a real fleet bucket returns a page in an interactive
budget. `--count` returns only the matching count; `--limit` / `--cursor`
page through the rest (default 50, hard cap 1000).

`bucket sync {push,pull,diff,status}` is the bidirectional mirror of the
whole raw bucket (traces, blobs, events, manifest) against the one remote
`bucket connect` configured, distinct from `dataset publish`, the gated
one-way egress of reviewed rows. `bucket sync push` is the gated egress
SEAL: it computes an auditable `pushed[]` / `withheld[]` partition and
**refuses** (zero bytes egressed, non-zero exit) if any trace is not yet
cleared for sync; `--dry-run` previews that partition without egressing.
`bucket sync status --json` carries an additive `security_gate` block so a
single command answers "why can't this bucket sync, and what makes it
eligible?". It reports `configured`, `unfiltered_count`, `security_stale_count`,
`eligible`, `blocking_reasons` (in order: `setup bucket` first when the remote is
unconfigured, then `bucket security policy --policy basic` → `bucket security run
--all`), and `remediation`. `doctor` and `bucket sync status` derive this from
the same persisted-manifest counts via one shared helper, so they agree on counts
and remediation; `bucket security status` shares the remediation. The gate reads
the persisted bucket manifest (no corpus scan); a missing manifest yields
`security_gate` state `unknown`.

`bucket list` and `bucket verify` are side-effect-free reads: they never
write under the bucket. Self-heal (regenerating `manifest.json` and every
per-trace envelope from the canonical event log + blob store) is `bucket
repair`, the idempotent crash-recovery primitive; `bucket reclaim` separately
finds (and, with `--apply`, removes) leaked Trace Trails cruft under
`.git/**/opentraces/`, dry-run by default, never touching the live event
log or accelerator.

The bucket is the private capture-time store. It is separate from datasets:
`bucket sync` moves raw retained evidence, while `dataset publish` moves
approved workflow rows.

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
opentraces capture-otlp flush --from-raw-bodies --session <session-id> --project <repo> --trace-id <trace-id>
opentraces capture-otlp restart
opentraces capture-otlp stop
```

The receiver feeds Claude Code OTel events and raw API bodies into Context
Tree events. If the receiver is down, agent traffic is not blocked.

`flush` lands the captured context in the bucket and joins it to the trace
spine, so `ctx show` / `ctx step` can read it. You usually do not need to run it
by hand: the watcher tick auto-flushes a project's active OTel sessions once
each goes idle (zero-touch). `--from-raw-bodies` reconstructs an
already-captured session per-step straight from the raw request/response bodies
with no live receiver running — the full-fidelity, retroactive path.
`capture-otlp status` lists the captured session ids you can flush.

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
