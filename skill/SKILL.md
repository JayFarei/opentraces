---
name: opentraces
description: >
  Share agent traces to open datasets on HuggingFace Hub. Use this skill when
  the user mentions OpenTraces, trace capture, Trace Trails, workflow-built
  datasets, dataset review, or publishing reviewed dataset rows.
---

# opentraces

OpenTraces captures local agent traces, links them to Git evidence with Trace
Trails, lets workflows turn one or more traces into local datasets, and then
publishes reviewed dataset rows to HuggingFace remotes.

## Current Command Model

v7 CLI spine (PR #174, issues #160-#165): one command surface, progressively disclosed. Hidden never means removed — every demoted verb below stays fully callable and `--json`-scriptable; `--help` just stops advertising it.

- Global setup: `opentraces setup`, `opentraces auth login`, `opentraces bucket connect` (was `setup bucket`), `opentraces setup skill`, `opentraces upgrade` (root peer verb, was `setup upgrade`), `opentraces uninstall` (root peer verb, the symmetric reverse-of-install), `opentraces auth`
- Project setup: `opentraces init`, `opentraces status` (now the fleet bucket-safety dashboard), `opentraces doctor`, `opentraces remove`
- Trace retrieval and search (8 -> 4 collapse): `opentraces trace query`, `opentraces trace get`, `opentraces trace map`, `opentraces trace slice`. Hidden-but-callable: `trace discover`, `trace skills` (folded into `trace query --skill`), `trace index` (folded into `query`/`map`/`get`'s self-maintaining snapshot), `trace partition` (folded into `trace slice --by`), `trace compare`, `trace teleport`.
- Trace Intelligence: `opentraces trace map|get --waste`, `opentraces trace map|get --run-intel`, `opentraces trace compare` (hidden-but-callable)
- Trace slicing: `opentraces trace slice <target> --by user-turn|change-burst|milestone|subgoal --json` decomposes a trace into a tiling array of trajectories (`opentraces.slicing.v1`); absorbs the former `trace partition --by s1|s2|s3|s4` (which stays hidden-but-callable under its old spelling). `user-turn` + `change-burst` are deterministic; `milestone` + `subgoal` are cheap-LLM. With `--by milestone|subgoal` and the default `--judge agent`, if judgments are needed the command exits `rc=10` printing `JudgmentRequest`s — answer them, write `{"answers": [{"id","decision","confidence"}]}` to a file, and re-run with `--answers <file>` for the final tiling at `rc=0`.
- Trace Trails (visible surface): `opentraces trail blame commit <sha>` (also bare `opentraces trail blame <sha>`), `opentraces trail pr render|create|update` (lifted to a top-level sibling of `blame`; `trail blame pr ...` stays hidden-but-callable), `opentraces trail graph`, `opentraces trail track`, plus the bare address `opentraces trail <trace_id>[:step]`
- Context Tree: the bare-noun read `opentraces ctx <trace_id>`, `opentraces ctx <trace_id>:<step>`, `opentraces ctx <trace_id>:last` (`--layer system|messages|tools|runtime`, `--full`, `--with-dropped`); the old subcommand surface (`tree/show/step/reads/writes/diff/compactions/prune/resume/resolve/anchor-for-step`, plus `list/info`) stays callable, hidden from `--help`
- Bucket (portable capture store): `opentraces bucket list`, `opentraces bucket verify`, `opentraces bucket repair`, `opentraces bucket reclaim`, `opentraces bucket sync push/pull/diff/status`, `opentraces bucket connect`. Hidden-but-callable: `bucket status`, `bucket manifest`, `bucket remote push/pull/diff/status`, `bucket replay`, `bucket rebuild`, `bucket prune`, `bucket prefetch`
- Dataset workflows: `opentraces workflow create`, `opentraces workflow list`, `opentraces workflow templates`, `opentraces workflow remove`, plus the internal `opentraces workflow skill-intelligence` eval over skill episodes
- Datasets: `opentraces dataset list/new/run/review/publish/remote/schedule/status/remove/security`. Review transitions are `opentraces dataset review approve|reject|reset <name> [row_id...]`. Per-dataset egress security is `opentraces dataset security <name> [--tool <t> --enable|--disable] [--unsafe-override --reason <text>]`.
- Skill verifier (trace-grounded reward for SkillOpt): `opentraces skill-verifier status/autoverify/align/score`
- Security tools: `opentraces security tools list/info`, `opentraces security sanitize --tools <names>` or `--use-config`
- OTLP capture source: `opentraces setup capture-otlp`, `opentraces capture-otlp start|stop|status|restart|flush`

Old flat inbox commands such as `opentraces list`, `add`, `reject`, `push`,
`pull`, `web`, and `tui` are not part of the public command tree. Several
Trace Trails substrate commands (`trail explain`, `sync`, `timeline`,
`teleport`, `resolve`, `attach`, `rebuild`, `diff`, `resume`,
`snapshots`, `snapshot checkout`) remain callable for scripting and
debugging but are hidden from `--help` after the CLI spine simplification.

### Addressing and the agent contract

`opentraces <trace-id>[:step | :last | :A-B]` root-dispatches to `trace get`;
the same `trace:step` join key resolves across `trace get`, `ctx`, and
`trail` — the "resume triple" of action, context, and world. Each substrate
honors as much of the grammar as it has materialized: `trace get` the full
`[:step | :last | :A-B]`, `ctx` `[:step | :last]`, and `trail` `[:step]`
only (its `:last`/`:A-B` slots are reserved for v1.1). A bare
token at the root only ever dispatches when no existing command name
matches it, so no verb, hidden or visible, can be shadowed by a trace id.

Under explicit `--json`, stdout is pure JSON — no `---OPENTRACES_JSON---`
sentinel and no leading human text — so `opentraces --json <cmd> | jq` is
always valid JSON (piped stdout without `--json` keeps the sentinel
preamble for legacy consumers). A command that would otherwise drop into
an interactive prompt refuses to do so under `--json` or on a
non-interactive terminal: it emits a structured `INTERACTIVE_REQUIRED`
error and exits `2`, with zero prompt bytes.

## Setup

```bash
opentraces setup
opentraces auth login
opentraces bucket connect        # configure remote-by-default private bucket sync (was `setup bucket`)
opentraces setup codex-cli       # install terminal Codex CLI hooks in ~/.codex/hooks.json
opentraces setup pi              # check/install the Pi package entry
opentraces setup skill           # install the opentraces skill into agent harnesses
opentraces setup skill --harness codex-cli
opentraces setup skill --harness pi
opentraces upgrade                # upgrade CLI + re-render installed integration glue + refresh project skill file (root peer verb, was `setup upgrade`)
opentraces upgrade --integrations-only  # re-render installed hooks/watchers without a CLI bump
opentraces upgrade --skill-only         # refresh only the skill file + hook, skip the CLI upgrade
opentraces uninstall --dry-run   # reverse-of-install plan (recommended first); root peer verb, was `setup uninstall`
opentraces uninstall --integrations-only  # default: reverse install-time patches + daemons, PRESERVE captured data
opentraces uninstall --purge --yes        # also DELETE captured data + git refs (unrecoverable)
opentraces config tracking-mode  # show; pass global|manual to set
opentraces config get <key>      # single-key config read (opentraces.config.get.v1)
opentraces auth whoami
opentraces init
opentraces init --agent codex-cli
opentraces init --agent pi
opentraces status
opentraces status --short
opentraces doctor
```

`opentraces status` is the O(1) fleet bucket-safety dashboard (envelope
`opentraces.bucket.status.v1`): scanned/unscanned trace counts across every
registered project, with a "safe to sync" verdict that is structurally
impossible while any trace is still unscanned. `--short` prints a stable
one-line porcelain summary (`status=... traces=... unscanned=...
not_cleared=... stale=...`), `--full` adds the per-project debugger detail,
`--project <slug>` scopes every count and the verdict to one project. The
former per-project capture inbox (stage counts, active remote, most recent
traces) is now the hidden `opentraces status-inbox`.

`opentraces --json doctor` exposes the agent-readable CLI freshness fields at
`doctor.cli`: `{installed_version, latest_version, upgrade_available}`. When
`upgrade_available` is true, run `opentraces upgrade`; when doctor reports
integration drift, run `opentraces upgrade --integrations-only` to
re-render already-installed glue without enabling new integrations. You do not
need to inspect `doctor.cli` yourself: when an upgrade or repair is warranted,
`opentraces --json doctor` also surfaces the action at the top-level
`next_command` / `next_steps` fields (the standard agent contract) — run that
`next_command`.

`setup` is machine-global: tracking mode, hooks, auth, watcher, TruffleHog,
LLM review, and supporting binaries. Tracking mode (`opentraces config
tracking-mode`) controls enrollment: `global` (default) auto-enrolls every
agent — Claude, Codex, and Pi — git or not, private + review-required the first
time a capture hook or the Pi extension fires there, so `init` is optional;
`manual` keeps the explicit per-project `opentraces init` opt-in. Capture is
opt-out: switch to `manual`, or set a per-project `excluded` marker /
`opentraces remove`, to turn it off (raw provider bodies stay default-off
regardless). `init` is project enrollment only; dataset remotes and review
policy belong under `opentraces dataset ...`. Private bucket configuration
belongs under `opentraces bucket connect` (the rename of `setup bucket`,
which stays callable, hidden) and `opentraces bucket sync` (the rename of
`bucket remote`).

`opentraces setup skill` writes one canonical skill copy under
`~/.agents/skills/opentraces/` and symlinks supported harnesses to it. Current
harness targets are `claude-code`, `codex-cli`, and `pi`; pass `--harness
<name>` to refresh only one link.

Codex support is for terminal Codex CLI, not Codex Desktop. Install and
authenticate Codex first, then run `opentraces setup codex-cli` once and
`opentraces init --agent codex-cli` in each repo. Hooks are passive observers:
they record sidecars under `.opentraces/codex-cli/hooks/` and must not approve
or deny permission prompts. Codex capture starts with future sessions;
`--import-existing` is a Claude Code backfill path.

Pi support is extension-backed. Install with `pi install npm:opentraces-pi`, use
`/ot-setup` or `opentraces setup pi --dry-run --json` for the local checklist;
under global tracking (default) capture is automatic once the `opentraces` CLI
is present, or run `opentraces init --agent pi` to enroll a repo explicitly. Pi
sidecars land under `.opentraces/pi/events/` and flow through the same
TraceRecord, Trace Trails, Context Tree, and bucket v2 pipeline. Raw provider
bodies stay default-off.

Inside Pi, use slash commands for quick private-bucket retrieval and setup:
`/ot-capture-status`, `/ot-setup`, `/ot-search <query>`, `/ot-trace <trace-id>`,
`/ot-standup`, `/ot-capsule [trace-id]`, and `/ot-dataset`. Model-facing tools
are `ot_capture_status`, `ot_search`, `ot_trace`, `ot_standup`, `ot_capsule`,
and `ot_dataset`. Prefer `/ot-search`/`ot_search` first, then `/ot-trace` or
`ot_trace` for a selected bucket trace. Direct slash commands are TUI actions;
model-invoked `ot_*` tools are captured as read-only `opentraces_retrieval`
tool calls.

## Trace Retrieval

Use trace commands when an agent needs compact evidence before loading full
transcripts.

```bash
opentraces trace query --lex "bug fix failing test" --json
opentraces trace query --cwd --json  # remote traces: opentraces bucket sync pull first
opentraces trace query --skill grill-me --json
opentraces trace map <trace_id> --candidate <unit_id> --json
opentraces trace slice <trace_id> --template bursts --json
opentraces trace slice <trace_id> --by user-turn --json       # tiling Trajectory[] (opentraces.slicing.v1)
opentraces trace slice <trace_id> --by milestone --json       # cheap-LLM: rc=10 -> answer -> --answers <file> -> rc=0
opentraces trace get <trace_id> --json
opentraces trace get <trace_id> --remote me/opentraces-bucket --json
opentraces trace get <trace_id>:last --json
opentraces trace map <trace_id> --waste --json
opentraces trace get <trace_id> --run-intel --json
```

`trace query` returns bounded candidate packets over the local lexical +
concept Trace Index (BM25 plus a bounded concept join, not embeddings);
skill filtering folds into `trace query --skill <name>` (the old `trace
skills` command stays callable, hidden). `trace map` returns a
workflow-neutral evidence map or candidate slice, and ends in a runnable
`trace slice` handoff. `trace slice` materialises deterministic Trace Slice
packets for dataset workflows and absorbs `trace partition --by` (now
`--by user-turn|change-burst|milestone|subgoal`). `trace get` is the
explicit full retrieval step and resolves the full `<id>:<step>` / `:last` /
`:A-B` address grammar; `ctx` shares `<id>:<step>` / `:last` and `trail`
shares `<id>:<step>` (their remaining selectors reserved for v1.1). The four visible
verbs form a loop: query (search) -> get (pull up) -> map (dissect) ->
slice (extract). `trace teleport` (hidden-but-callable) moves a trace and
its retained Git evidence between workspaces.

### Bursts and intent

`trace map --bursts` (or `trace get <ref> --bursts`) projects the trace's
file_edit / patch_created nodes into one virtual `change_burst` node per
cluster of nearby edits. Each burst exposes:

- `step_range` — `[min_step, max_step]` of the underlying nodes
- `unique_files` — repo-relative path → hunk count (deduped: absolute and
  relative variants of the same file collapse onto one entry)
- `patches` — one entry per Edit/Write tool call (NOT one per file)
- `burst_commit_sha` — modal commit across the burst's patches, fallback to
  the first git commit seen via the post-tool hook trail
- `intent` — structured object: `{trigger, most_substantive_spec, spec_chain,
  burst_commit_sha, commit_subject, commit_body}`. The trigger is the short
  imperative authorising the action ("ok", "let's go ahead and commit");
  the spec is the most recent substantive user instruction before the
  burst. `intent_text` / `intent_user_step` remain as legacy aliases for
  `intent.most_substantive_spec.{text, step}`.

Pass `--no-commit-lookup` to skip the per-burst `git log` lookup when running
offline or in a hot CLI path. The burst commit's SHA is a separate concept
from the trace's `outcome.commit_sha` (which is the *last* commit of the
session).

### Trace Intelligence

Deterministic, derive-on-demand signals about how a run went, layered on top
of the Trace surface. No LLM, no schema change, nothing persisted; each is a
frozen JSON envelope. Three capabilities: context waste, run signals, run compare.

```bash
opentraces trace map <trace_id> --waste --json       # also: trace get --waste
opentraces trace get <trace_id> --run-intel --json   # also: trace map --run-intel
opentraces trace compare <trace_a> <trace_b> --json  # hidden-but-callable; add --no-quality to skip persona scores
```

- **Context waste** — `--waste` emits `opentraces.context_waste.v2`: `large_output`
  (>= 12000 chars), `repeated_file_read` (same file 3+ times in 20 min), and
  `repeated_search` (rg|grep|find|ag|ack 5+ times in 10 min) findings, with a
  `summary` count block.
- **Run signals** — `--run-intel` emits `opentraces.run_intel.v1` with
  deterministic `resteer` / `recovery` / `loop` / `failure` annotations. Recovery
  only fires after an uncleared prior failure; failure prefers structured tool
  errors over substring matches; a repeated command is ONE `loop` signal carrying
  `evidence.repeat_count`; a one-word approval never reads as a resteer.
- **Run compare** — `trace compare <a> <b>` emits `opentraces.trace_compare.v1`:
  per-side fidelity plus `{a, b, delta}` triples over Metrics, deterministic
  quality persona scores, and burst/error/security signals (both traces pinned
  to the same burst gap).

`--waste` and `--run-intel` are mutually exclusive with `--bursts` (and with
each other); on `trace get` they are also mutually exclusive with `--resume`.
The `trace get` and `trace map` surfaces emit byte-identical payloads for
`--waste` and `--run-intel`. Each detector reports a `fidelity` of `record` or
`otel`, preferring full wire fidelity when the trace was captured via the OTLP
receiver.

## Trace Trails

Trace Trails are the Git-anchored evidence chain for what a trace changed and
where that change lives now. The visible top-level surface is `trail blame`
(a group; `commit <sha>` is the point-scope subcommand and bare `trail
blame <sha>` resolves the same way), `trail pr render|create|update`
(lifted to a top-level sibling of `blame` — the family's one gated GitHub
write, isolated from the read verbs; the old `trail blame pr ...` path
stays callable as a hidden compat alias), `trail graph`, and `trail
track`. A bare `trail <trace_id>[:step]` address projects the same
per-trace view as `trace get` / `ctx`.

```bash
# Visible surface
opentraces trail blame commit <sha>             # which traces authored this commit
opentraces trail blame <sha>                    # same, via the bare-address form
opentraces trail blame commit t:<trace_id>      # which commits carry this trace
opentraces trail pr render --base main          # PR body for the current branch
opentraces trail pr create --base main          # gh pr create with the body
opentraces trail pr update --base main          # idempotent update of existing PR
opentraces trail graph
opentraces trail graph --trace <trace_id>
opentraces trail track <trace_id>
opentraces trail track --patch <trace_patch_id>
opentraces trail track --anchor <git_anchor_id>
opentraces trail track --since 12h --json
opentraces trail track --all --json --limit 50
opentraces trail <trace_id>                     # bare address: same projection as `trace get`/`ctx`
opentraces trail <trace_id>:<step>

# Hidden substrate commands (still callable from scripts and JSON automation)
opentraces trail explain --trace <id> --step <n>
opentraces trail explain <path>:<line>
opentraces trail sync --patch <trace_patch_id>
opentraces trail sync --anchor <git_anchor_id>
opentraces trail timeline <trace_id>
opentraces trail resume <trace_id>
opentraces trail blame pr render --base main    # compat alias for `trail pr render`
opentraces trail teleport export <trace_id> --output <dir>
opentraces trail teleport open <bundle> --project <blank-dir>
opentraces trail resolve ot://trace/<id>/patches/<id>/trail --json
opentraces trail attach --trace <id> --commit <sha>
opentraces trail rebuild
opentraces trail search --commit <sha> --remote-bucket --json
```

`trail track` walks a trace's lineage through Git history and reports
current `HEAD` survival across all anchors, with batch JSONL output via
`--since`, `--all`, and `--patches-from`. The substrate `trail sync`
synchronizes OpenTraces' current understanding of a Trace Patch or Git
Anchor with the latest Git history. `trail timeline` shows the observed
timeline of snapshots, patches, anchors, and survival observations.
`trail teleport` (hidden-but-callable; superseded by `trace teleport`)
moves a trace plus the retained Git evidence needed to inspect or resume
it in a blank workspace.

## Bucket

The bucket is the private store of every captured trace. It keeps raw
capture-time evidence under `~/.opentraces/bucket/`: per-trace envelopes,
patch history, `trail.jsonl.gz`, `context.jsonl.gz`, `sources.jsonl.gz`,
content-addressed blobs, an event-log mirror, and `manifest.json`. It is
local-only until `opentraces bucket connect` configures a private
HuggingFace bucket remote. Bucket sync (`bucket sync push|pull|diff|status`)
mirrors the whole raw substrate and is separate from dataset publication.

```bash
opentraces bucket list --json
opentraces bucket list --unsynced --json
opentraces bucket verify --json
opentraces bucket repair --json
opentraces bucket reclaim --json                # dry-run by default
opentraces bucket reclaim --apply --json
opentraces bucket sync status --json
opentraces bucket sync diff --json
opentraces bucket sync push --dry-run --json    # preview the pushed[]/withheld[] partition; egresses nothing
opentraces bucket sync push --json              # REFUSES (rc!=0, zero bytes) while any trace is withheld
opentraces bucket sync pull --json
opentraces bucket connect --json
opentraces bucket connect --local-only

# Hidden-but-callable (superseded by the verbs above)
opentraces bucket status --json
opentraces bucket manifest --json
opentraces bucket rebuild --json
opentraces bucket rebuild --substrate context-tree --json
opentraces bucket prune --dry-run --json
opentraces bucket prefetch <trace_id> --json
opentraces bucket remote status --json
opentraces bucket remote push --json
opentraces bucket remote pull --json
opentraces bucket remote diff --json
opentraces bucket replay --repo <repo-dir>
```

Buckets are distinct from datasets. A bucket holds raw captured traces; a
dataset holds workflow-projected rows. `bucket list` is the bounded,
paginated per-trace inventory (envelope `opentraces.bucket.list.v1`;
`--count`/`--limit`/`--cursor` plus facet filters like `--unsynced`,
`--unfiltered`, `--unscanned`) that supersedes the old O(N) `bucket
manifest` hang. `bucket repair` re-projects the full bucket (envelopes +
`manifest.json`) from canonical state — the target for `bucket status`'s
freshness remediation, and where `bucket manifest --heal` / `bucket
rebuild` now fold. `bucket reclaim` removes leaked Trace Trails cruft under
`.git/**/opentraces/`; it is print-only until `--apply`. `bucket sync push`
is the gated egress seal: it computes an auditable `pushed[]`/`withheld[]`
partition and REFUSES — zero bytes egressed, non-zero exit — while any
trace is not cleared for sync; `--dry-run` previews the same partition
without egressing. `bucket connect` (the rename of `setup bucket`)
configures the remote target; `bucket sync` moves data — they stay
distinct verbs.

## Context Tree

The Context Tree answers "what did the agent see at this step?" It rides on
the same canonical event log as Trace Trails and is addressed by
`Step.context_node_id` in schema `0.5.0`.

```bash
opentraces ctx <trace_id> --json                                # overview card: shape + capture method
opentraces ctx <trace_id>:<step_index> --json                   # model input at that step
opentraces ctx <trace_id>:last --json                            # the final / active step
opentraces ctx <trace_id>:<step_index> --layer system --json     # one layer, readably
opentraces ctx <trace_id>:<step_index> --layer messages --json
opentraces ctx <trace_id>:<step_index> --layer tools --json
opentraces ctx <trace_id>:<step_index> --layer runtime --json
opentraces ctx <trace_id>:<step_index> --full --json             # full hydrated model input (the fork/eval-row packet)
opentraces ctx <trace_id> --with-dropped --json                  # include compaction-dropped content
opentraces ctx <trace_id> --remote me/opentraces-bucket --json   # read a remote bucket manifest

# Old subcommand surface (still callable from scripts; hidden from --help,
# including `list`/`info`)
opentraces ctx list --json
opentraces ctx info <trace_id> --json
opentraces ctx tree <trace_id> --json
opentraces ctx show <context_node_id> --json
opentraces ctx step <trace_id> <step_index> --json
opentraces ctx reads <trace_id> --json
opentraces ctx writes <trace_id> --json
opentraces ctx diff <node_a> <node_b> --json
opentraces ctx compactions <trace_id> --json
opentraces ctx resume <context_node_id> --json
opentraces ctx prune <context_node_id> --source-jsonl <session.jsonl>
opentraces ctx resolve ot://context-node/<id> --json
opentraces ctx anchor-for-step <trace_id> <step_index>
```

`trace:step` is the universal address — the same token resolves across
`ctx`, `trace get`, and `trail`. The bare `ctx <trace>` read and the
hidden `ctx list` / `ctx info` / `ctx show` accept `--remote <hf-repo>`
(`user/repo`) to read a remote bucket manifest (and lazy-fetch missing
layer blobs) with output bytewise-equal to the local read.

Claude/Codex JSONL capture gives a useful structural approximation. Codex uses
`capture_method=transcript_reconstruction`, does not decrypt encrypted
reasoning, and does not support snapshot-backed `--at-step` resume. For
higher-fidelity Claude Code context capture, set up the OTLP source:

```bash
opentraces setup capture-otlp
opentraces capture-otlp start
opentraces capture-otlp status --json
opentraces capture-otlp flush --session <session_id> --project <repo> --trace-id <trace_id>
opentraces capture-otlp flush --from-raw-bodies --session <session_id> --project <repo> --trace-id <trace_id>
```

`flush` lands the captured context in the bucket (one node per llm-request) and
joins it to the trace spine, so `ctx show` / `ctx step` can read it. You usually
do not need to run it by hand: the watcher tick auto-flushes a project's active
OTel sessions once each goes idle (zero-touch). `--from-raw-bodies` reconstructs
an already-captured session per-step from the raw request/response bodies with
no live receiver — the full-fidelity, retroactive path. `capture-otlp status`
lists the captured session ids you can flush.

## Dataset Workflows

Workflows are skill-format packages (or Markdown files) that know how to turn
trace evidence into dataset rows. They use trace discovery, Trace Trails, and
Context Tree evidence to emit purposeful row streams. The main path is to
scaffold one with `opentraces workflow create` and then bind it to a dataset:

```bash
opentraces workflow templates --json
opentraces workflow create <name> --template skill-command-trajectory-eval-v1
opentraces workflow list --json
opentraces workflow remove <name> --yes
opentraces dataset new <name> --workflow ./workflows/<workflow>/WORKFLOW.md
opentraces dataset new <name> --workflow ./workflows/<workflow>/
opentraces dataset new <name> --from-skill <skill>
opentraces dataset run <name> --executor script --json
```

The bundled `skill-command-trajectory-eval-v1` template materialises a ready
workflow that emits command-trajectory evaluation rows. `--from-skill` binds
the built-in `skill-episodes-v1` workflow to a snapshot-backed skill query so
agents can turn a ranked skill from `trace query --skill` into reviewable episode rows.

## Skill Verifier

The skill verifier turns "was this agent *skill* used *effectively*?" into a
reward signal SkillOpt can optimize against. It rests on the skill-intelligence
consumer (skill episodes / rollouts / eval-tasks mined from bucket traces) and
a per-skill **rubric** of weighted criteria, each judged against bounded,
read-only evidence.

```bash
opentraces skill-verifier status <skill>            # feasibility triage: status + episode count + blockers
opentraces skill-verifier autoverify <skill> --json # self-align a rubric to the skill goal + calibrate (fast path)
opentraces skill-verifier align <skill> --json      # scaffold a manual alignment session (human gold labels)
opentraces skill-verifier score <skill> --out <dir> # drive SkillOpt with the rubric; emit a package
```

The trust boundary is **the agent PROPOSES** a rubric, **the factory SCORES**
it mechanically against evidence + calibration, **a human APPROVES** promotion
(`manual_required_default_off`). Status is derived mechanically, never
author-set: `blocked_<reason>` (cannot feed reward; the reason names the
remedy), `provisional_weak_only` (a deterministic non-outcome signal separates
the weak git signal but no human gold), or `calibrated` (the only fully-trusted
status; always human-gated). Self-judgment can never exceed
`provisional_weak_only`. On the current near-one-class bucket every seed skill
honestly returns `blocked_*` — that is the correct answer, not an unfinished
feature; the bottleneck is trustworthy human/deterministic labels, not the
framework.

## Datasets

A dataset is built by running a workflow over one or more traces. It can stay
local, or it can be bound to a HuggingFace dataset remote and published after
review/security gates pass.

```bash
opentraces dataset list --json
opentraces dataset new <name> --workflow <workflow.md-or-package-dir>
opentraces dataset new <name> --from-skill <skill>
opentraces dataset status <name> --json
opentraces dataset run <name> --dry-run --limit 5 --verbose
opentraces dataset run <name>
opentraces dataset run <name> --executor script --json
opentraces dataset run <name> --approve-new --publish-check-only
opentraces dataset run <name> --approve-new --publish
opentraces dataset review <name>
opentraces dataset review approve <name> <row_id>
opentraces dataset review reject <name> <row_id>
opentraces dataset review reset <name> <row_id>
opentraces dataset remote create <name> <owner/name> --private  # idempotent: creates the HF dataset, or binds it if it already exists
opentraces dataset remote list <name>
opentraces dataset remote visibility <name> --public
opentraces dataset publish <name> --check-only
opentraces dataset publish <name>
opentraces dataset publish <name> --min-retention 0.5 --exclude-state lost
opentraces dataset schedule list
opentraces dataset schedule add <name> --every 1h --approve-new --publish-check-only
opentraces dataset remove <name> --yes
```

Manual review means rows remain local until approved. Automatic review policy
may mark rows publishable, but remote egress is still explicit: publish is a
separate user action. `dataset publish --min-retention` and `--exclude-state`
filter rows by survival quality before staging.

## Security Tools

Security tools are optional and default off. Workflows can run named tools
directly, or use the project/global config to select enabled tools.

```bash
opentraces security tools list --json
opentraces security tools info regex --json
printf '%s\n' '{"text":"OPENAI_API_KEY=sk-demo"}' | opentraces security sanitize --tools regex
printf '%s\n' '{"row":{"path":"/Users/alice/project"}}' | opentraces security sanitize --tools path_anonymizer
printf '%s\n' '{"record":{...}}' | opentraces security sanitize --use-config
opentraces setup trufflehog
opentraces setup privacy-filter
```

Registered inline tools are `regex`, `entropy`, `trufflehog`,
`privacy_filter`, `llm_pii`, `business_logic`, `path_anonymizer`,
`capsule_scope`, and `classifier`. Session-level LLM review used to be
configured by `setup llm-review`; that command is now hidden-but-callable
(it's a dataset publication gate, not a per-record sanitize tool) — the
canonical LLM row-review surface is `opentraces dataset review` /
`opentraces dataset publish`.

Security has two scopes. **Bucket security** (`opentraces bucket security`) is
machine-wide bucket egress over global tool flags, applied before private bucket
sync. **Dataset security** is per-dataset: each dataset's manifest carries a
resolved policy seeded from its workflow's front-matter `security:` contract
(`required_tools`, `optional_tools`, `default_enabled_tools`, `disallowed_tools`,
`allow_disable_required`) and pinned to the workflow digest. A dataset contract
may only reference row-applicable tools (`regex`, `entropy`, `privacy_filter`,
`business_logic`, `path_anonymizer`); `trufflehog`, `llm_pii`, `capsule_scope`,
and `classifier` run on full records, not row dicts, so a contract listing them
is rejected at `dataset new`. Manage it with
`opentraces dataset security <name>`: inspect the policy, toggle an optional tool
on that dataset only (`--tool <t> --enable|--disable`, repeatable), and disable a
required tool only when the contract sets `allow_disable_required: true` AND you
pass `--unsafe-override` (else the command exits 2). It edits only that dataset's
manifest; it is not a global config toggle and there is no `--policy` form on the
dataset command. The publish gate is keyed on execution evidence: `dataset
publish --check-only` blocks a row whose recorded `tools_applied` is missing a
required tool (`required_security_tools_missing`), so a row appended while a
required tool was off stays blocked even after the tool is re-enabled.
`security sanitize --tools ...` / `--use-config` stays available for inline
sanitization inside workflows and scripts.

```bash
opentraces dataset security <name> --json
```

## JSON Mode

Prefer `--json` for agent automation. Under explicit `--json`, stdout is
pure JSON — no `---OPENTRACES_JSON---` sentinel, no leading human text —
so `opentraces --json <cmd> | jq` is always valid JSON:

```bash
opentraces --json status
opentraces --json trace query --skill grill-me
opentraces --json trace map <trace_id>
opentraces --json trail track <trace_id>
opentraces --json bucket list
opentraces --json ctx <trace_id>
opentraces --json ctx <trace_id>:last
opentraces security tools list --json
opentraces --json dataset status <name>
opentraces dataset security <name> --json
```

## Troubleshooting

| Problem | Action |
|---|---|
| Not initialized | Run `opentraces init` |
| Auth missing | Run `opentraces auth login` |
| No traces visible | Check `opentraces setup claude-code` / `setup codex-cli`; for Pi run `/ot-capture-status` or `opentraces setup pi --dry-run --json` and confirm capture is enabled (`tracking-mode global` and the repo not `excluded`, or an explicit `opentraces init --agent pi`); then `opentraces status` |
| Trace Trail event log invalid | Run `opentraces doctor`; `opentraces trail rebuild` re-derives advisory projections |
| Bucket not syncing | Run `opentraces bucket connect` to configure a remote, then `opentraces bucket sync status` |
| Publish blocked | Run `opentraces dataset status <name> --json` and `opentraces dataset publish <name> --check-only` |
