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

- Global setup: `opentraces setup`, `opentraces auth login`, `opentraces setup bucket`, `opentraces setup skill`, `opentraces setup upgrade`, `opentraces auth`
- Project setup: `opentraces init`, `opentraces status`, `opentraces doctor`, `opentraces remove`
- Trace retrieval and search: `opentraces trace query`, `opentraces trace skills`, `opentraces trace index`, `opentraces trace map`, `opentraces trace slice`, `opentraces trace get`, `opentraces trace teleport`
- Trace Intelligence: `opentraces trace map|get --waste`, `opentraces trace map|get --run-intel`, `opentraces trace compare`
- Trace Trails (visible surface): `opentraces trail blame commit <sha>`, `opentraces trail blame pr render|create|update`, `opentraces trail graph`, `opentraces trail track`
- Context Tree: `opentraces ctx tree/show/step/reads/writes/diff/compactions/prune/resume/resolve/anchor-for-step`, plus `ctx list/info`
- Bucket (portable capture store): `opentraces bucket status`, `opentraces bucket manifest`, `opentraces bucket verify`, `opentraces bucket repair`, `opentraces bucket rebuild`, `opentraces bucket prune`, `opentraces bucket prefetch`, `opentraces bucket remote push/pull/diff/status`, `opentraces bucket replay`
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

## Setup

```bash
opentraces setup
opentraces auth login
opentraces setup bucket          # configure remote-by-default private bucket sync
opentraces setup codex-cli       # install terminal Codex CLI hooks in ~/.codex/hooks.json
opentraces setup pi              # check/install the Pi package entry
opentraces setup skill           # install the opentraces skill into agent harnesses
opentraces setup skill --harness codex-cli
opentraces setup skill --harness pi
opentraces setup upgrade         # upgrade CLI + re-render installed integration glue + refresh project skill file
opentraces setup upgrade --integrations-only  # re-render installed hooks/watchers without a CLI bump
opentraces setup uninstall --dry-run  # reverse-of-install plan (recommended first); --integrations-only preserves data, --purge deletes it
opentraces config tracking-mode  # show; pass global|manual to set
opentraces auth whoami
opentraces init
opentraces init --agent codex-cli
opentraces init --agent pi
opentraces status
opentraces doctor
```

`opentraces --json doctor` exposes the agent-readable CLI freshness fields at
`doctor.cli`: `{installed_version, latest_version, upgrade_available}`. When
`upgrade_available` is true, run `opentraces setup upgrade`; when doctor reports
integration drift, run `opentraces setup upgrade --integrations-only` to
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
policy belong under `opentraces dataset ...`. Private bucket configuration belongs under `opentraces setup
bucket` and `opentraces bucket remote`.

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
opentraces trace query --cwd --json  # remote traces: opentraces bucket remote pull first
opentraces trace query --skill grill-me --json
opentraces trace skills --json
opentraces trace skills --skill grill-me --json
opentraces trace index --json
opentraces trace map <trace_id> --candidate <unit_id> --json
opentraces trace slice <trace_id> --template bursts --json
opentraces trace get <trace_id> --json
opentraces trace get <trace_id> --remote-bucket --json
opentraces trace map <trace_id> --waste --json
opentraces trace get <trace_id> --run-intel --json
opentraces trace compare <trace_a> <trace_b> --json
opentraces trace teleport export <trace_id> --output <dir>
```

`trace query` returns bounded candidate packets over the local lexical +
concept Trace Index (BM25 plus a bounded concept join, not embeddings).
`trace skills` lists observed skills ranked by
snapshot-backed invocation usage. `trace index --json` refreshes and reports
the local search snapshot with stage telemetry.
`trace map` returns a workflow-neutral evidence map or candidate slice.
`trace slice` materialises deterministic Trace Slice packets for dataset
workflows. `trace get` is the explicit full retrieval step. `trace
teleport` moves a trace and its retained Git evidence between workspaces.

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
opentraces trace compare <trace_a> <trace_b> --json  # add --no-quality to skip persona scores
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
(now a group with `commit` and `pr` subcommands), `trail graph`, and
`trail track`.

```bash
# Visible surface
opentraces trail blame commit <sha>             # which traces authored this commit
opentraces trail blame commit t:<trace_id>      # which commits carry this trace
opentraces trail blame pr render --base main    # PR body for the current branch
opentraces trail blame pr create --base main    # gh pr create with the body
opentraces trail blame pr update --base main    # idempotent update of existing PR
opentraces trail graph
opentraces trail graph --trace <trace_id>
opentraces trail track <trace_id>
opentraces trail track --patch <trace_patch_id>
opentraces trail track --anchor <git_anchor_id>
opentraces trail track --since 12h --json
opentraces trail track --all --json --limit 50

# Hidden substrate commands (still callable from scripts and JSON automation)
opentraces trail explain --trace <id> --step <n>
opentraces trail explain <path>:<line>
opentraces trail sync --patch <trace_patch_id>
opentraces trail sync --anchor <git_anchor_id>
opentraces trail timeline <trace_id>
opentraces trail resume <trace_id>
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
`trail teleport` moves a trace plus the retained Git evidence needed to
inspect or resume it in a blank workspace.

## Bucket

The bucket is the private store of every captured trace. It keeps raw
capture-time evidence under `~/.opentraces/bucket/`: per-trace envelopes,
patch history, `trail.jsonl.gz`, `context.jsonl.gz`, `sources.jsonl.gz`,
content-addressed blobs, an event-log mirror, and `manifest.json`. It is
local-only until `opentraces setup bucket` configures a private HuggingFace
bucket remote. Bucket sync is separate from dataset publication.

```bash
opentraces bucket status --json
opentraces bucket manifest --json
opentraces bucket verify --json
opentraces bucket repair --json
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
dataset holds workflow-projected rows. `bucket rebuild` refreshes derived
bucket projections from canonical state. `bucket replay` replays
bucket-exported Trace Trails into a Git repository (useful when a teammate
hands you a bucket and you need to materialise its evidence locally).

## Context Tree

The Context Tree answers "what did the agent see at this step?" It rides on
the same canonical event log as Trace Trails and is addressed by
`Step.context_node_id` in schema `0.5.0`.

```bash
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

`ctx list`, `ctx info`, and `ctx show` accept `--remote <hf-repo>` (`user/repo`)
to read a remote bucket manifest (and lazy-fetch missing layer blobs for `ctx
show`) with output bytewise-equal to the local read.

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
agents can turn a ranked skill from `trace skills` into reviewable episode rows.

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
opentraces dataset run <name> --facet model=anthropic/claude-sonnet-4 --json
opentraces dataset run <name> --model anthropic/claude-sonnet-4 --json  # convenience alias for --facet model=...
opentraces dataset run <name> --agent claude-code --facet agent.version=2.1.143 --json
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

`dataset run --facet name=value` (repeatable; `--model`/`--agent` are
convenience aliases for `--facet model=...`/`--facet agent.name=...`) scopes a
run to traces whose model / harness name / harness version match, composing
with `--scope`/`--project`/`--trace`. It reuses the `trace query --facet`
name vocabulary and resolves entirely against the persisted bucket manifest
(O(manifest), never a per-trace file open), so it costs the same regardless
of corpus size. The run record's `facet_resolution` block reports the
matched trace count and rows. A trace whose harness never captured a version
is honestly excluded from a version scope (not a migration).

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
opentraces setup llm-review
```

Registered inline tools are `regex`, `entropy`, `trufflehog`,
`privacy_filter`, `llm_pii`, `business_logic`, `path_anonymizer`,
`capsule_scope`, and `classifier`. Session-level LLM review is configured by
`setup llm-review` but is a dataset publication reviewer, not part of the
per-record sanitize registry.

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

Prefer `--json` for agent automation:

```bash
opentraces --json status
opentraces --json trace query --skill grill-me
opentraces --json trace skills --limit 20
opentraces --json trace map <trace_id>
opentraces --json trail track <trace_id>
opentraces --json bucket status
opentraces --json ctx tree <trace_id>
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
| Bucket not syncing | Run `opentraces setup bucket` to configure a remote, then `opentraces bucket remote status` |
| Publish blocked | Run `opentraces dataset status <name> --json` and `opentraces dataset publish <name> --check-only` |
