---
title: "ops: opentraces CLI — job-to-be-done × action-trajectory map"
summary: "Single source of truth for the JTBD × action-trajectory map. One row per CLI command (107 rows across 6 buckets) with a pragmatic JTBD one-liner, the action trajectory it belongs to, its position in that trajectory, the otbox journey that owns it today, and the persona it serves. Plan 063 is now SSoT: every Click command must be mapped here, every journey TOML must name a 063 trajectory, every core-lane command must be owned by ≥1 journey — drift fails CI under `--strict`. Per-trajectory user-facing sections in §10 (rendered for the two strategic ★ trajectories + one routine, expand as ownership lands)."
type: ops
status: delivered
date: 2026-05-15
delivered: 2026-05-15
plan: kb/plans/062-otbox-checkpoint-journey-matrix.md
---

# opentraces CLI — JTBD × action-trajectory map

## Method

Six Explore agents fanned out across the CLI surface, one bucket each.
Each agent emitted a JTBD one-liner per command + the action trajectory
it belongs to + its position in that trajectory + the owning otbox
journey (per `tests/otbox/catalogue/journey-inventory.md`) + the
persona. Each agent also flagged uncertainty.

This document is the synthesis. Cross-bucket trajectory clashes were
reconciled. The "Provisional resolutions" section lists the
reconciliation decisions I made unilaterally; the "Open questions"
section lists the four that need a call from the team.

## Headline numbers

- **123 commands mapped** (97 public + 26 documented hidden, ignoring
  pure-help group entry-points where ambiguous).
- **~20 named action trajectories** (listed in §1 below). Six are
  cross-bucket.
- **18 commands owned by ≥1 journey** today (per
  `journey-inventory.md`); **79 unowned**. Matches plan 062's analysis.

## §1 — Action trajectory inventory

The full vocabulary of trajectories surfaced across the six buckets,
deduped and reconciled. Cross-bucket trajectories are marked **★**.

| Trajectory | Span | Spans buckets | What it accomplishes |
|---|---|---|---|
| **onboard-repo** | 1 → 5 | 1 (Setup) | A developer enrolls a new git repo + verifies status |
| **onboard-integrations** | 1 → N | 1 (Setup) | The bare `setup` wizard walks every integration step |
| **offboard-repo** | 1/1 | 1 | A developer unenrolls + wipes local state |
| **verify-install** | 1/1 | 1 | `doctor` reports pipeline health after any setup step |
| **connect-hf-identity** | 1 → 3 | 1 | `auth login` / `whoami` / `logout` lifecycle |
| **configure-settings** | 1 → 3 | 1 | `config set` + `config show` + `config get` |
| **configure-tracking-mode** | 1/1 | 1 | `config tracking-mode` |
| **enable-shell-completions** | 1 → 3 | 1 | Print → install → uninstall |
| **connect-agent-runtime** | 1 → 2 | 1 | `setup claude-code` + `setup skill` + `setup entity-parser` |
| **configure-codex-runtime** | 1/1 | 1 | `setup codex-cli` |
| **configure-otel-capture** | 1 → 6 | 1 + 2 | `setup capture-otlp` + `capture-otlp start/status/flush/restart/stop` |
| **configure-bucket** | 1/1 | 1 | `setup bucket` (toggle local-only vs HF-synced) |
| **configure-security-detectors** | 1 → 2 | 1 | `setup trufflehog` + `setup privacy-filter` |
| **configure-security-reviewer** | 1/1 | 1 | `setup llm-review` (also cross-refs `configure-publishing-gates`) |
| **maintain-install** | 1/1 | 1 | `setup upgrade` |
| **reconcile-runtime** | 1 → 4 | 1 | `setup runtime status/use/use-dev/remove-duplicates` — select / reconcile the active runtime on a multi-install machine (issue #99) |
| **inspect-security-pipeline** | 1 → 3 | 1 | `security sanitize` + `security tools list` + `security tools info` (workflow-author + agent inspection surface, not setup) |
| **session-ingest** | auto | 2 (Capture) | Hook-driven trace ingest (`_capture`, `_ingest-session`) |
| **commit-correlation** | auto | 2 | Hook-driven commit→trace link (`_run-post-commit-hook`) |
| **manual-inbox-recovery** | 1/1 | 2 | `_scan` after missed hooks |
| **schema-migration** | 1/1 | 2 | `migrate` + `_migrate-trace-ids` |
| **attribution-backfill** | 1 → 2 | 2 | `backfill` (cache) + `git-backfill` (notes refs) |
| **enable-live-attribution** | 1 → 3 + lifecycle | 2 | `setup watcher install/start/stop/restart/status/tick/sweep/uninstall` |
| **retrieve-relevant-traces** ★ | 1 → 3 | 3 (Trace) + 6 (Dataset) | `trace query` → result handling → downstream consumer |
| **inspect-trace-context** | 1 → 2 | 3 | `trace map` for structural inspection |
| **inspect-context-tree** | 1 → 9 | 3 | `ctx tree/show/step/reads/writes/diff/compactions/list/info` for what the model saw |
| **resume-from-context** | 1 → 4 | 3 | `ctx prune/resume/resolve/anchor-for-step` for replay and handoff packets |
| **extract-bounded-evidence** ★ | 1 → 2 | 3 + 6 | `trace slice` for dataset workflows |
| **skill-intelligence** ★ | 1 → N | 3 + 6 | `trace skills` → skill episodes / rollouts / eval-tasks projected into a dataset (the skill-intelligence consumer) |
| **trace-index-rebuild-progress** | 1/1 | 3 | `trace index rebuild` under the `--progress`/heartbeat contract (plan 088) |
| **resolve-trace-artifact** | 1/1 | 3 | `trace get` (incl. `--resume`) |
| **maintain-index** | 1 → 2 | 4 | `trace index rebuild` + `status` + `compact` |
| **recreate-trace-environment** ★ | 1 → 2 | 3 + 6 | `trace teleport export` + `open` — reconstitutes the environment that produced a trace, for perturbation analysis, RL training, evaluation harnesses, or "rewind" features in OSS repos |
| **commit-attribution-audit** | 1 → 3 | 4 (Trail) | `trail blame commit` + `trail graph` |
| **pr-lineage-publish** | 1 → 3 | 4 | `trail pr render` → `create` → `update` |
| **survival-walk** | 2/3 | 4 | `trail track` |
| **build-dataset-from-lineage** ★ | (utility) | 4 + 6 | Hidden `trail *` commands — load-bearing utilities for downstream dataset / consumer apps that need lineage primitives (`explain` for evidence chains, `resolve` for `ot://` URI deref, `sync` / `attach` / `mature` / `rebuild` for survival-state maintenance, `snapshots` / `snapshot checkout` for rewind, `timeline` / `search` / `diff` for structured lineage lookup). Hidden from end-user `--help` but **not power-user debugging only** — consumer apps depend on these. |
| **inspect-private-storage** | 1 → 2 | 5 (Bucket) | `bucket status` + `manifest` |
| **compare-bucket-digests** | 1/1 | 5 | `bucket remote status` / `diff` |
| **backup-bucket-to-remote** | 1/1 | 5 | `bucket remote push` |
| **restore-bucket-on-new-machine** | 1/1 | 5 | `bucket remote pull` |
| **restore-trail-lineage-to-repo** | 1/1 | 5 | `bucket replay` (post-pull restore) |
| **bucket-spine-and-self-sufficient** | maintenance | 5 | Bucket v2 manifest, repair, verify, replay, blob, and remote-symmetry guarantees |
| **survey-local-datasets** | 1/1 | 6 (Dataset) | `dataset list` |
| **build-publishable-dataset** ★ | 1 → 5 | 3 + 6 | `trace index rebuild` → `trace query` → `dataset new` → `dataset run` → `dataset status` → `dataset publish` |
| **review-rows-cli** | 1/1 | 6 | `dataset review <approve|reject|reset>` + hidden `dataset approve` / `dataset reject` agent aliases |
| **review-rows-tui** | 1/1 | 6 | `dataset review --tui` — the Textual two-column triage UI |
| **review-rows-web** | 1/1 | 6 | `dataset review --web` — the Flask-backed browser triage UI |
| **decommission-dataset** | 1/1 | 6 | `dataset remove` |
| **bind-hf-remote** | 1 → 3 | 6 | `dataset remote create/add` → `list` → `remove` |
| **manage-hf-visibility** | 1/1 | 6 | `dataset remote visibility` |
| **automate-dataset-runs** | 1 → 4 | 6 | `dataset schedule add/list/show/logs/pause/resume/remove` |
| **productionize-trace-workflow** ★ | 1 → 4 | 6 (touches 3 via slice) | `workflow templates` → `workflow create` → `dataset new --workflow` → `workflow list` |
| **agent-protocol-orient** | 1 → 3 | 6 | `context` + `capabilities` + `introspect` + `discover` (hidden agent surface) |
| **shell-completion-protocol** | 1/1 | 6 | `__complete` (hidden) |
| **dev-quality-maintenance** | 1 → 2 | 6 | `_audit-spec` + `_audit-run` (release-eng tools) |
| **context-tree-branching** | validation | 3 | Context Tree branch/fork shape validation |
| **context-tree-capture** | validation | 3 | Context Tree capture fidelity validation |
| **context-tree-compaction** | validation | 3 | Context Tree compaction/loss validation |
| **context-tree-cli** | validation | 3 | Context Tree `ctx` CLI contract validation |
| **context-tree-demo** | validation | 3 | Context Tree end-to-end demo acceptance |
| **context-tree-determinism** | validation | 3 | Context Tree deterministic rebuild validation |
| **context-tree-fork** | validation | 3 | Context Tree subagent fork validation |
| **context-tree-otel-lifecycle** | validation | 2 + 3 | OTLP receiver setup, lifecycle, and doctor validation |
| **context-tree-otel-capture** | validation | 2 + 3 | OTLP Context Tree capture fidelity validation |
| **context-tree-otel-outcome** | validation | 2 + 3 | OTLP outcome journeys for replay, inspection, resume, and experiments |
| **context-tree-temporal-anchor** | validation | 3 | Context Tree temporal anchor precision validation |
| **trace-spine** | validation | 3 + 4 + 5 | TraceRecord spine and cross-substrate resolution validation |
| **mature-bucket-perf-guard** | validation | 3 + 4 + 5 + 6 | Perf recurrence guard (issue #213): the four seal-family hot commands (`dataset run`, `capsule create`, `bucket status`, `trail track`) stay under catastrophic-regression duration + peak-RSS ceilings on a ~600-trace / ~50K-event mature bucket — the #87/#121/#137/#208 O(corpus) class caught in the nightly `scale` lane |

| **capsule-dependency-unblock** | 1 → 9 | 3 + 6 | Seal a failing episode into a capsule, share/file it, watch the verdict flip when a dependency unblocks |
| **trace-intelligence-compare** | 1/1 | 3 | Derive-on-demand A/B compare of two traces (`opentraces.trace_compare.v1`) |
| **skill-verifier-calibration** | 1 → 4 | 6 | Mine skill episodes into a rubric, score against evidence, walk the trust ladder (blocked → provisional → calibrated) |
| **skill-intelligence-mining** | 1 → 3 | 6 | Project bucket traces into skill episode/rollout/eval-task datasets and optimization loops |

★ = trajectory crosses bucket boundaries. Six trajectories total.

## §2 — Bucket 1: Setup & install

| Command | JTBD one-liner | Action trajectory (n/N) | Owning journey | Persona |
|---|---|---|---|---|
| `init` | Developer enrolls a git repo so opentraces can capture traces from it | onboard-repo (1/5) | `capture-safety-import-existing` | human |
| `status` | Developer checks inbox counts + remote binding after init to confirm the project is live | onboard-repo (5/5) | `cli-lifecycle`, `cli-publish-happy-path` | human |
| `remove` | Developer unenrolls a project + wipes local state after stopping capture | offboard-repo (1/1) | `cli-lifecycle` | human |
| `doctor` | Developer or agent verifies pipeline health after setup to catch broken tools or missing hooks | verify-install (1/1) | `doctor-health`, `install-smoke-tier1` | both |
| `setup doctor` | Same `doctor` command object mounted a second time under `setup`, so the install-health read-twin is discoverable right beside the verbs it reports on (issue #160) | verify-install (1/1) — alias | unowned | both |
| `auth login` | Developer authenticates with HuggingFace via browser OAuth or token so dataset remotes + bucket sync are unblocked | connect-hf-identity (1/3) | unowned | both |
| `auth whoami` | Agent or developer confirms which HF identity is active before running publish commands | connect-hf-identity (2/3) | unowned | both |
| `auth logout` | Developer removes stored HF credentials to rotate or decommission an identity | connect-hf-identity (3/3) | unowned | human |
| `config set` | Developer or agent writes a config key to global or project scope so behaviour changes take effect without editing JSON | configure-settings (1/2) | `capture-safety-excluded-marker` | both |
| `config show` | Developer inspects current config with secrets masked to confirm active settings | configure-settings (2/2) | `cli-lifecycle` | human |
| `config tracking-mode` | Developer switches between global and manual tracking behavior so auto-enrollment matches the repo's privacy posture | configure-tracking-mode (1/1) | `capture-safety-tracking-mode`, `capture-safety-excluded-marker` | human |
| `config get` | Developer or agent reads one resolved config key without a whole-config dump (the `set [KEY] [VALUE]` shape implies a matching key-addressing getter, issue #160) | configure-settings (3/3 — new) | unowned | both |
| `completions` | Developer prints a shell completion script to evaluate before installing | enable-shell-completions (1/3) | unowned | human |
| `completions install` | Developer installs shell completions for bash/zsh/fish so `ot <TAB>` works | enable-shell-completions (2/3) | unowned | human |
| `completions uninstall` | Developer removes installed completions after uninstalling or switching shells | enable-shell-completions (3/3) | unowned | human |
| `setup` (bare) | Developer walks an interactive wizard that covers every integration after init | onboard-integrations (1/1) | unowned | human |
| `setup auth` (hidden) | Backwards-compatible alias for the canonical `auth login` command | connect-hf-identity (1/3) — alias | unowned | human |
| `setup bucket` (hidden) | Developer configures whether captured traces sync to a private HF remote or stay local-only — renamed to `bucket connect` (issue #162); stays callable hidden | configure-bucket (1/1) | unowned | both |
| `setup claude-code` | Developer installs the four Claude Code hooks (PreToolUse / PostToolUse / Stop / PostCompact) so sessions are captured | connect-agent-runtime (1/2) | `capture-safety-tracking-mode` | both |
| `setup codex-cli` | Developer installs Codex CLI capture hooks so Codex sessions enter the same trace/bucket substrates as Claude Code | configure-codex-runtime (1/1) | `codex-full-parity-latest` | both |
| `setup pi` | Developer installs the Pi extension package entry so Pi sessions auto-enroll into capture | connect-agent-runtime (2/2) | `pi-setup-dry-run` | both |
| `setup git` | Developer installs the post-commit hook that correlates commits to traces, powering `trail blame` | connect-agent-runtime (2/2) | unowned | both |
| `setup skill` | Developer installs the opentraces skill globally + links it into each agent harness so agents can invoke opentraces commands | connect-agent-runtime (1/2) | unowned | human |
| `setup entity-parser` (hidden) | Developer downloads + verifies the `ot-entities` binary for richer commit-diff attribution in `trail blame` | connect-agent-runtime (2/2) | unowned | both |
| `setup capture-otlp` | Developer patches Claude Code telemetry settings and installs the local OTLP receiver so Context Tree layers can be captured from wire events | configure-otel-capture (1/6) | unowned | human |
| `setup trufflehog` | Developer configures the optional TruffleHog secret detector for redaction and publication safety checks | configure-security-detectors (1/2) | unowned | both |
| `setup privacy-filter` | Developer configures the optional `openai/privacy-filter` PII detector for dataset-row scanning | configure-security-detectors (2/2) | unowned | both |
| `setup llm-review` (hidden) | Developer configures the optional LLM reviewer that gates dataset publication — a session-level publication gate, not a per-record sanitize/install step, so it's off `setup --help` (issue #160); still callable, unchanged internals | configure-security-reviewer (1/1) | unowned | both |
| `setup upgrade` (hidden) | Backwards-compatible alias for the root `opentraces upgrade` peer verb (issue #160) | maintain-install (1/2) — alias | unowned | both |
| `setup uninstall` (hidden) | Backwards-compatible alias for the root `opentraces uninstall` peer verb (issue #160) | maintain-install (2/2) — alias | unowned | both |
| `upgrade` | Developer upgrades the CLI + refreshes the project skill file after a new release — a root peer verb beside `setup` (the in / update / out triad), not a subcommand of it (issue #160) | maintain-install (1/2) | unowned | both |
| `uninstall` | Developer reverses the opentraces install — the symmetric inverse of `setup` — removing hooks/daemons/env while preserving captured traces, datasets, and buckets (`--purge` to also delete the corpus); a root peer verb beside `setup` (issue #160) | maintain-install (2/2) | `setup-uninstall-dry-run` | both |
| `security sanitize` | Developer or workflow author pipes JSON through the security pipeline tool registry to sanitize a record in a language-agnostic way | inspect-security-pipeline (1/3) | unowned | both |
| `security tools list` | Developer or agent inspects the registered security tools + their enable state before deciding which to opt into | inspect-security-pipeline (2/3) | unowned | both |
| `security tools info` | Developer inspects one tool's descriptor (config keys, runtime requirements) before opt-in | inspect-security-pipeline (3/3) | unowned | both |

## §3 — Bucket 2: Capture pipeline + watcher

| Command | JTBD one-liner | Action trajectory (n/N) | Owning journey | Persona |
|---|---|---|---|---|
| `_capture` (hidden) | Claude Code's SessionEnd hook calls this to ingest a finished session into the project inbox so traces appear automatically | session-ingest (auto-invoked) | unowned | agent |
| `_ingest-session` (hidden) | The Stop hook calls this fire-and-forget to ingest a single JSONL immediately after session end so fast ingestion doesn't block the agent | session-ingest (auto-invoked) | unowned | agent |
| `_run-post-commit-hook` (hidden) | `.git/hooks/opentraces-post-commit` calls this after each commit to correlate the commit to the trace that produced it | commit-correlation (auto-invoked) | unowned | agent |
| `_scan` (hidden) | Developer manually re-syncs the inbox from the JSONL corpus after a missed hook fire or schema bump | manual-inbox-recovery (1/1) | unowned | human |
| `parse` (hidden, deprecated) | Was the manual parse entry point; now exits with an error pointing to `_scan` | deprecated | unowned | human |
| `migrate` (hidden) | Developer or CI script runs this to check current schema/config version + apply pending migrations | schema-migration (1/1) | unowned | human |
| `_migrate-trace-ids` (hidden) | Operator rewrites legacy `<agent>_<session>` trace IDs to canonical UUIDv4 after upgrading to the new ID scheme | schema-migration (1/1) | unowned | human |
| `backfill` (hidden) | Developer re-attributes commits to traces — incrementally by default, from-scratch with `--rebuild` — so `trail blame` has full coverage | attribution-backfill (1/2) | unowned | human |
| `git-backfill` (hidden) | Developer retro-correlates inbox traces to past commits the post-commit hook missed (first install, broken hook) | attribution-backfill (2/2) | unowned | human |
| `setup watcher install` | Developer installs the launchd/systemd watcher unit so commits + JSONL activity are processed automatically without manual ticks | enable-live-attribution (1/3) | unowned | human |
| `setup watcher start` | Developer loads an installed-but-stopped watcher unit so attribution resumes after a deliberate stop | enable-live-attribution (lifecycle: start) | unowned | human |
| `setup watcher stop` | Developer unloads the running watcher service while keeping the unit installed so attribution pauses without losing config | enable-live-attribution (lifecycle: stop) | unowned | human |
| `setup watcher restart` | Developer stops then immediately starts the watcher service so config changes or a hung process take effect | enable-live-attribution (lifecycle: restart) | unowned | human |
| `setup watcher status` | Developer checks whether the watcher unit is installed + running before debugging a missing `trail blame` entry | enable-live-attribution (check) | unowned | human |
| `setup watcher uninstall` | Developer removes the watcher unit + shim entirely so the daemon no longer runs | enable-live-attribution (3/3) | unowned | human |
| `setup watcher tick` | Developer triggers a single synchronous watcher tick + prints the report so they can diagnose attribution coverage without waiting for the scheduled poll | enable-live-attribution (diagnostic) | unowned | human |
| `setup watcher sweep` | Production watcher entrypoint (#65): runs one bounded sweep over all enlisted projects then exits, so the launchd/systemd unit ticks every project without a long-lived daemon pinning RSS | enable-live-attribution (lifecycle: sweep) | unowned | human |
| `setup runtime status` | Developer or agent sees which install root each integration runner executes on a multi-install (pipx + brew + source) machine, reusing #93 detection | reconcile-runtime (1/4) | `setup-runtime-status-mixed` | both |
| `setup runtime use` | Developer re-renders the integration glue to a chosen installed runtime (pipx/homebrew/source) so hooks + watcher stop running stale code; integrations-only, dry-run-able | reconcile-runtime (2/4) | `setup-runtime-use-installed-rewrites-integrations`, `setup-runtime-dry-run-no-mutation` | both |
| `setup runtime use-dev` | Developer points the integration glue at the editable checkout (dev mode) and records it so doctor reports deliberate dev runtime, not drift | reconcile-runtime (3/4) | `setup-runtime-use-dev-checkout` | both |
| `setup runtime remove-duplicates` | Developer PRINTS (never executes) the data-safe package-removal commands for duplicate installs, keeping the chosen runtime | reconcile-runtime (4/4) | `setup-runtime-remove-duplicates-prints` | both |
| `capture-otlp start` | Developer starts the local OTLP receiver so Claude Code telemetry can be collected for Context Tree capture | configure-otel-capture (2/6) | unowned | human |
| `capture-otlp status` | Developer checks receiver health, capture counts, uptime, and raw-body footprint before trusting OTel-backed Context Tree evidence | configure-otel-capture (3/6) | unowned | both |
| `capture-otlp flush` | Developer flushes receiver snapshots into a project's canonical event log so captured Context Tree layers become queryable by `ctx` | configure-otel-capture (4/6) | unowned | both |
| `capture-otlp restart` | Developer restarts the receiver after settings or port changes without uninstalling the capture source | configure-otel-capture (5/6) | unowned | human |
| `capture-otlp stop` | Developer stops the receiver when capture should pause while preserving installed settings | configure-otel-capture (6/6) | unowned | human |

## §4 — Bucket 3: Trace retrieval

| Command | JTBD one-liner | Action trajectory (n/N) | Owning journey | Persona |
|---|---|---|---|---|
| `trace query` | Agent filters local retained traces by lexical/semantic/facet criteria so it can retrieve bounded CandidatePackets without loading full transcripts | retrieve-relevant-traces (1/3) ★ | `trace-map-and-slice`, `cli-publish-happy-path`, `tier1-cold-publish`, `tier1-warm-reuse` | both |
| `trace discover` | Agent groups retained traces into topic timeline cards so it can orient across prior work without loading full transcripts | retrieve-relevant-traces (2/3) ★ | `agent-session-to-published-dataset` | both |
| `trace skills` | Agent or developer lists the skills observed across retained traces ranked by usage so it can see which skills sessions actually invoked before scoping a dataset | retrieve-relevant-traces (3/3) ★ | `skill-usage-to-dataset` | both |
| `trace map` | Agent expands a deterministic TraceMap around a known trace or unit to inspect structural context without reading the full transcript | inspect-trace-context (1/2) | `trace-map-and-slice` | both |
| `trace slice` | Agent extracts deterministic TraceSlice packets via template or manual step range so dataset workflows get bounded reproducible input | extract-bounded-evidence (1/2) ★ | `trace-map-and-slice` | agent |
| `trace partition` | Agent decomposes a captured session into a tiling array of Trajectories (`opentraces.slicing.v1`) via one of four slicers, resolving the cheap-LLM step through the rc=10 agent-loop judge | extract-bounded-evidence (1/2) ★ | `slicer-conformance` | agent |
| `trace get` | Human or agent resolves a trace, trace unit, map node, or `ot://` resource by ref to inspect full content (or `--resume` to hand control back to an upstream agent) | resolve-trace-artifact (1/1) | `cli-lifecycle` | both |
| `trace compare` | Human or agent A/B-compares two traces (metrics, quality personas, bursts, signals) via the frozen trace_compare.v1 envelope | trace-intelligence-compare (1/1) | `trace-compare-smoke` | both |
| `trace index rebuild` | Human or agent explicitly rebuilds the local Trace Index + bucket-shaped search projection after new traces are captured | maintain-index (1/4) | `trace-map-and-slice`, `cli-publish-happy-path`, `tier1-cold-publish` | both |
| `trace index refresh` | Human or agent incrementally refreshes the local Trace Index + search projection for newly-captured traces without a full rebuild (the cheap keep-warm maintenance path; plan 087) | maintain-index (2/4) | `migration-s12-end-to-end-upgrade` | both |
| `trace index status` | Checks whether the local Trace Index + search projection are current before querying | maintain-index (3/4) | `trace-map-and-slice` | both |
| `trace index compact` | Reclaims index.db space after legacy body bloat accumulation (issue #40 remedy verb) | maintain-index (4/4) | `trace-map-and-slice` | both |
| `trace teleport export` | Bundles a trace + its retained Git evidence into a portable workspace so a downstream consumer (RL trainer, perturbation harness, evaluation rig, OSS-repo rewind) can reproduce the environment that produced the trace | recreate-trace-environment (1/2) ★ | unowned | both |
| `trace teleport open` | Reconstitutes the trace environment in a target project directory so the downstream consumer can run perturbations / RL rollouts / replay against it | recreate-trace-environment (2/2) ★ | unowned | both |
| `ctx tree` | Agent or developer lists the Context Tree for a trace to inspect the active path of what the model saw | inspect-context-tree (1/9) | `context-tree-linear` | both |
| `ctx show` | Agent fetches one ContextNode and its layer summary or content to inspect prompt/runtime state at that point | inspect-context-tree (2/9) | `context-tree-ctx-show` | both |
| `ctx step` | Agent resolves a trace step to the ContextNode that represents the model view at that step | inspect-context-tree (3/9) | `context-tree-ctx-step` | both |
| `ctx reads` | Agent lists context reads for a trace so downstream consumers can identify what evidence was available | inspect-context-tree (4/9) | `context-tree-ctx-reads` | agent |
| `ctx writes` | Agent lists context writes for a trace so downstream consumers can identify what changed in the model-visible state | inspect-context-tree (5/9) | `context-tree-ctx-writes` | agent |
| `ctx diff` | Agent compares two ContextNodes to see what layers changed between model steps | inspect-context-tree (6/9) | `context-tree-ctx-diff` | agent |
| `ctx compactions` | Developer inspects compaction boundaries and loss summaries in a captured session | inspect-context-tree (7/9) | `context-tree-compaction` | both |
| `ctx list` (hidden) | Agent lists bucket-manifest Context Tree heads without loading every blob — cut from `ctx --help` per #164 (discovery is the trace spine; `bucket list` is the row surface); still callable | inspect-context-tree (8/9) | unowned | agent |
| `ctx info` (hidden) | Agent inspects a single trace's Context Tree head and blob availability from the bucket manifest — cut from `ctx --help` per #164 (folds into the bare `ctx <trace>` overview); still callable | inspect-context-tree (9/9) | unowned | agent |
| `ctx prune` | Agent materializes a pruned session up to a ContextNode for replay or resume-from-step workflows | resume-from-context (1/4) | `context-tree-ctx-prune` | agent |
| `ctx resume` | Agent emits a compact resume packet for one ContextNode so another agent can continue with bounded context | resume-from-context (2/4) | `context-tree-ctx-resume` | agent |
| `ctx resolve` | Agent resolves Context Tree resource identifiers to canonical payloads for replay, dashboards, or audits | resume-from-context (3/4) | `context-tree-ctx-resolve` | agent |
| `ctx anchor-for-step` | Developer maps a trace step to the Git anchor evidence associated with its context node | resume-from-context (4/4) | unowned | both |

## §5 — Bucket 4: Trail / VCS-anchored lineage

| Command | JTBD one-liner | Action trajectory (n/N) | Owning journey | Persona |
|---|---|---|---|---|
| `trail blame commit` | Reviewer asks which traces contributed to a given commit so they can audit attribution | commit-attribution-audit (2/3) | `trail-blame-and-graph` | both |
| `trail graph` | Developer scans commit + trace history as a GitButler-style ASCII graph to navigate which sessions touched which commits | commit-attribution-audit (1/3) | `trail-blame-and-graph` | both |
| `trail pr render` | Developer previews the trace-lineage PR body for the current branch before pushing | pr-lineage-publish (1/3) | unowned | human |
| `trail pr create` | Developer opens a GitHub PR whose body is sourced from trace lineage, so reviewers see what agent sessions produced each commit | pr-lineage-publish (2/3) | unowned | human |
| `trail pr update` | Developer refreshes an existing PR body after new commits land so the lineage stays current | pr-lineage-publish (3/3) | unowned | human |
| `trail track` | Agent or developer walks survival state for one trace or a batch of patches to confirm which edits survived into git | survival-walk (2/3) | unowned | both |
| `trail snapshot checkout` (hidden) | Developer materializes a snapshot rewind point to replay or inspect a prior workspace state | build-dataset-from-lineage | unowned | human |
| `trail snapshots` (hidden) | Developer lists rewind candidates for a trace before picking one to check out | build-dataset-from-lineage | unowned | human |
| `trail explain` (hidden) | Maintainer inspects the evidence chain for a specific trace step or commit to debug anchor quality | build-dataset-from-lineage | unowned | agent |
| `trail resolve` (hidden) | Maintainer dereferences a stable `ot://` resource URI to its canonical payload | build-dataset-from-lineage | unowned | agent |
| `trail sync` (hidden) | Maintainer syncs a single Trace Patch or Git Anchor against current git history to update survival state | build-dataset-from-lineage | unowned | agent |
| `trail timeline` (hidden) | Maintainer inspects the full ordered event timeline for a trace to diagnose capture or maturation gaps | build-dataset-from-lineage | unowned | agent |
| `trail search` (hidden) | Agent queries the Trail Query projection by trace, commit, path, or survival state for structured lineage lookup | build-dataset-from-lineage | unowned | agent |
| `trail diff` (hidden) | Developer computes the Trace Patch between two captured step snapshots to inspect what changed between steps | build-dataset-from-lineage | unowned | human |
| `trail attach` (hidden) | Maintainer manually connects a trace's patches to a commit after a hook failure so blame becomes available | build-dataset-from-lineage | unowned | agent |
| `trail mature` (hidden) | Maintainer force-matures pending patches into Git Anchors over recent commits so blame is available without waiting for the watcher | build-dataset-from-lineage | unowned | agent |
| `trail verify` (hidden) | Maintainer verifies or summarizes the canonical Trace Trails event log after doctor reports skipped or invalid verification | verify-install | `trail-verify-large-log-bounded` | agent |
| `trail rebuild` (hidden) | Maintainer re-derives advisory snapshot projections from the canonical event log after branch surgery or ref corruption | build-dataset-from-lineage | unowned | agent |
| `trail teleport export` | Deprecated visible alias for `trace teleport export`. Still in the Click registry pending a future removal; do NOT exercise in new journeys | deprecated | unowned | — |
| `trail teleport open` | Deprecated visible alias for `trace teleport open`. Still in the Click registry pending a future removal; do NOT exercise in new journeys | deprecated | unowned | — |

## §6 — Bucket 5: Bucket subsystem

| Command | JTBD one-liner | Action trajectory (n/N) | Owning journey | Persona |
|---|---|---|---|---|
| `bucket status` (hidden) | Lower-level bucket-health readout — the fleet dashboard moved to the top-level `status` (issue #162); stays callable hidden | inspect-private-storage (1/2) | `bucket-inspect` | both |
| `bucket manifest` (hidden) | Prints the bucket manifest — read side moved to `bucket list`, `--heal` folded into `bucket repair` (issue #162); stays callable hidden | inspect-private-storage (2/2) | `bucket-inspect` | both |
| `bucket list` | Agent or developer enumerates the per-trace bucket inventory — bounded, paginated, filterable (`--unsynced` / `--unscanned` / `--security-stale` / `--project` / `--since`), the hang-proof read the old `manifest` never was | inspect-private-storage (1/2) | `bucket-list-and-sync-withhold` | both |
| `bucket connect` | Developer configures the private bucket remote target (the rename of `setup bucket`) — binds the HF remote before syncing | configure-bucket (1/1) | `bucket-list-and-sync-withhold` | both |
| `bucket security policy` | Developer inspects or sets which security tools the bucket egress filter runs (off/basic/recommended/strict or a custom per-tool set) before private sync | configure-bucket (1/3) | `bucket-security-policy-basic` | both |
| `bucket security run` | Developer applies the configured security filter over already-captured records so they become remote-sync eligible | configure-bucket (2/3) | `bucket-security-policy-basic` | both |
| `bucket security status` | Developer or agent checks the bucket security posture + the exact remediation before a remote sync | configure-bucket (3/3) | `bucket-remote-status-filtered-eligible` | both |
| `bucket sync status` | Developer compares the local bucket digest with the configured remote before deciding whether a push or pull is safe (was `bucket remote status`) | compare-bucket-digests (1/1) | `bucket-list-and-sync-withhold` | both |
| `bucket sync diff` | Developer sees which objects diverge between the local and remote manifests before committing to a push or pull (was `bucket remote diff`) | compare-bucket-digests (1/1) | `bucket-list-and-sync-withhold` | both |
| `bucket sync push` | Developer mirrors the local bucket to the configured remote — withholds every trace not yet cleared for sync; `--dry-run` computes the pushed/withheld partition without egressing (was `bucket remote push`) | backup-bucket-to-remote (1/1) | `bucket-list-and-sync-withhold` | both |
| `bucket sync pull` | Developer restores the local bucket from the configured remote on a new or wiped machine (was `bucket remote pull`) | restore-bucket-on-new-machine (1/1) | `bucket-list-and-sync-withhold` | human |
| `bucket remote status` (hidden) | Pre-#162 alias of `bucket sync status`; stays callable hidden | compare-bucket-digests (1/1) | unowned | both |
| `bucket remote diff` (hidden) | Pre-#162 alias of `bucket sync diff`; stays callable hidden | compare-bucket-digests (1/1) | unowned | both |
| `bucket remote push` (hidden) | Pre-#162 alias of `bucket sync push`; stays callable hidden | backup-bucket-to-remote (1/1) | unowned | both |
| `bucket remote pull` (hidden) | Pre-#162 alias of `bucket sync pull`; stays callable hidden | restore-bucket-on-new-machine (1/1) | unowned | human |
| `bucket replay` | Developer replays bucket-exported Trace Trails into a target Git repository after pulling to a new machine so trail lineage is reconstructed | restore-trail-lineage-to-repo (1/1) | unowned | both |
| `bucket verify` | Developer checks bucket manifest, blob integrity, and dangling references before trusting or syncing the bucket | inspect-private-storage (2/2) | unowned | both |
| `bucket reclaim` | Developer reclaims leaked Trace Trails cruft under `.git/**/opentraces/` (leaked tmp files + orphan accelerator pickles) — dry-run by default, `--apply` to remove | inspect-private-storage (maintenance) | `bucket-list-and-sync-withhold` | both |
| `bucket repair` | Developer rebuilds the bucket from canonical event logs and blobs after detecting manifest drift | inspect-private-storage (maintenance) | unowned | human |
| `bucket rebuild` | Developer rebuilds bucket projections after substrate changes or fixture refreshes | inspect-private-storage (maintenance) | unowned | human |
| `bucket prune` | Developer removes unreachable bucket blobs in dry-run or confirmed mode without deleting trace/event history | inspect-private-storage (maintenance) | unowned | human |
| `bucket prefetch` | Developer eagerly pulls one trace's bucket blobs from remote before offline inspection or replay | restore-bucket-on-new-machine (prefetch) | unowned | both |

## §7 — Bucket 6: Datasets / workflows / publish / review / introspect

| Command | JTBD one-liner | Action trajectory (n/N) | Owning journey | Persona |
|---|---|---|---|---|
| `dataset list` | Curator lists all local HF-shaped datasets to know what exists before running or publishing | survey-local-datasets (1/1) | unowned | both |
| `dataset new` | Curator creates a workflow-driven (or ad-hoc) dataset shell so workflow output can land in a typed structure | build-publishable-dataset (3/6) ★ | `cli-publish-happy-path`, `tier1-cold-publish` | both |
| `dataset security` | Curator inspects or edits one dataset's resolved security policy (seeded from the workflow contract): enabling optional tools or recording an unsafe override for a required tool. Owned by the dataset manifest, never a global config toggle | build-publishable-dataset (3/6) ★ | `dataset-security-workflow-seeding` | both |
| `dataset run` | Curator executes the workflow against matching bucket traces so new rows land in the dataset | build-publishable-dataset (4/6) ★ | unowned | both |
| `dataset status` | Curator inspects row counts by publication state to decide whether the dataset is ready for review | build-publishable-dataset (5/6) ★ | unowned | both |
| `dataset verify` | Curator replays the bound workflow side-effect-free and byte-compares against the stored rows to grade the seal's explainability (reproduces / bucket-advanced / integrity-failure) before publishing | build-publishable-dataset (5/6) ★ | `build-publishable-dataset-shape` | both |
| `dataset review` | Curator opens the row triage surface. Positional verbs (`approve` / `reject` / `reset`) drive the CLI persona; `--tui` and `--web` open alternate review-rows-tui / review-rows-web fronts | review-rows-cli (1/1) | `cli-publish-happy-path`, `tier1-cold-publish` | both |
| `dataset approve` (hidden alias) | Agent or script bulk-approves rows programmatically, bypassing interactive review | review-rows-cli (1/1) | unowned | agent |
| `dataset reject` (hidden alias) | Agent or script bulk-rejects rows programmatically | review-rows-cli (1/1) | unowned | agent |
| `dataset publish` | Curator ships approved rows + contract files to the active HF remote after review gates pass | build-publishable-dataset (6/6) ★ | `cli-publish-happy-path`, `tier1-cold-publish` | both |
| `dataset remove` | Curator tears down a local dataset after confirming, reclaiming state and removing stale config | decommission-dataset (1/1) | unowned | human |
| `dataset remote create` | Curator provisions a new private HF dataset repo + binds it in one step so publish has a destination | bind-hf-remote (1/3) | `cli-publish-happy-path`, `tier1-cold-publish` | both |
| `dataset remote add` (hidden) | Backwards-compatible bind-only alias; canonical create-or-bind lives at `dataset remote create` | bind-hf-remote (1/3) — alias | unowned | both |
| `dataset remote list` | Curator inspects which HF remotes are bound to a given dataset | bind-hf-remote (2/3) | unowned | both |
| `dataset remote remove` | Curator disconnects (and optionally deletes) a remote binding when decommissioning or rotating | bind-hf-remote (3/3) | unowned | human |
| `dataset remote visibility` | Curator flips a bound HF dataset between private + public so community access can be opened after internal review | manage-hf-visibility (1/1) | unowned | human |
| `dataset schedule add` | Curator registers a local recurring interval so the dataset workflow runs automatically | automate-dataset-runs (1/4) | unowned | human |
| `dataset schedule list` | Curator lists schedules to see which datasets run automatically and when | automate-dataset-runs (2/4) | unowned | both |
| `dataset schedule show` | Curator inspects one schedule entry to verify interval + run state | automate-dataset-runs (2/4) | unowned | both |
| `dataset schedule logs` | Curator inspects scheduler log lines to diagnose missed or failed automated runs | automate-dataset-runs (3/4) | unowned | both |
| `dataset schedule pause` | Curator pauses a schedule during refactor or outage without deleting it | automate-dataset-runs (3/4) | unowned | human |
| `dataset schedule resume` | Curator re-enables a previously paused schedule to resume automated runs | automate-dataset-runs (4/4) | unowned | human |
| `dataset schedule remove` | Curator deletes a schedule permanently when no longer needed | automate-dataset-runs (4/4) | unowned | human |
| `workflow templates` | Curator lists bundled workflow templates to pick a starting point before scaffolding | productionize-trace-workflow (1/4) ★ | unowned | both |
| `workflow create` | Curator scaffolds a new workflow skill package (optionally from a template) so a dataset can point at it | productionize-trace-workflow (2/4) ★ | unowned | both |
| `workflow list` | Curator inspects installed workflows + their dataset bindings to audit dependencies before editing or removing | productionize-trace-workflow (3/4) ★ | unowned | both |
| `workflow remove` | Curator removes an installed workflow skill package after unbinding it from all datasets | productionize-trace-workflow (4/4) ★ | unowned | human |
| `workflow optimize` | Operator runs the SkillOpt optimization loop over a skill using bucket episodes as the data source | skill-intelligence-mining (3/3) | `skillopt-online-loop-echo` | agent |
| `workflow skill-intelligence` | Operator projects bucket traces into skill episode/rollout/eval-task datasets via the bundled templates | skill-intelligence-mining (1/3) | unowned | both |
| `workflow verifier-factory` | Operator mines skill episodes into a per-skill verifier rubric draft for calibration | skill-intelligence-mining (2/3) | `skill-verifier-factory-echo` | agent |
| `skill-verifier status` | Operator inspects a skill verifier's trust-ladder status (blocked_<reason> / provisional_weak_only / calibrated) | skill-verifier-calibration (1/4) | `skill-verifier-command-smoke` | both |
| `skill-verifier autoverify` | Operator runs the factory's evidence-scored verification pass over a skill's episodes | skill-verifier-calibration (2/4) | `skill-verifier-factory-echo` | agent |
| `skill-verifier align` | Operator aligns rubric criteria against human gold labels (calibration step) | skill-verifier-calibration (3/4) | `skill-verifier-command-smoke` | human |
| `skill-verifier score` | Operator or loop scores a rollout against a calibrated rubric as a reward source | skill-verifier-calibration (4/4) | `skill-verifier-command-smoke` | agent |
| `capsule create` | Developer seals a bounded, redacted, self-contained capsule from an agent session (v7 trace / trace:step / trace:A-B address) — the visible seal verb superseding `export` | capsule-dependency-unblock (1/9) | `capsule-command-smoke` | both |
| `capsule get` | Consumer resolves a capsule (file / https / hf:// ref) and prints its envelope read-only, writing no bucket or project state | capsule-dependency-unblock (5/9) | `capsule-command-smoke` | both |
| `capsule import` | Consumer resolves a capsule and writes it into the local bucket as a first-class trace so it projects natively via map / slice / trace get | capsule-dependency-unblock (5/9) | `capsule-command-smoke` | both |
| `capsule export` | Developer seals a failing/usage episode (intent + context packet + slice + repo pin) into a capsule under .opentraces/ | capsule-dependency-unblock (1/9) | `capsule-dependency-unblock` | both |
| `capsule preview` | Developer dry-runs the full redaction pipeline and reviews the counts-only manifest before anything leaves the machine | capsule-dependency-unblock (2/9) | `capsule-command-smoke` | human |
| `capsule share` | Developer publishes the redacted capsule to HF (sha-pinned immutable URL) after the consent gate | capsule-dependency-unblock (3/9) | `capsule-dependency-unblock` | human |
| `capsule issue` | Developer files/updates the idempotent GitHub issue carrying the capsule render | capsule-dependency-unblock (4/9) | `capsule-dependency-unblock` | both |
| `capsule open` | Consumer resolves a capsule from file / https / hf:// ref and prints the envelope or summary | capsule-dependency-unblock (5/9) | `capsule-command-smoke` | both |
| `capsule replay` | Consumer re-poses the capsule's intent as a structured packet against the current checkout | capsule-dependency-unblock (6/9) | `capsule-command-smoke` | agent |
| `capsule test` | Consumer runs the capsule's repro command (optionally --with/--matrix dependency sweeps) to compute a verdict | capsule-dependency-unblock (7/9) | `capsule-dependency-unblock` | both |
| `capsule verdict` | Consumer posts the reproduces/fixed/inconclusive verdict back to the issue | capsule-dependency-unblock (8/9) | `capsule-dependency-unblock` | both |
| `capsule watch` | Blocked client watches an issue for the verdict flip and receives one actionable payload | capsule-dependency-unblock (9/9) | `capsule-command-smoke` | agent |
| `context` (hidden) | Agent reads the full project context JSON — trace counts, config, state — to decide what to do next | agent-protocol-orient (1/3) | unowned | agent |
| `capabilities` (hidden) | Agent reads the feature flag envelope + schema version to gate-check its own skill invocations | agent-protocol-orient (2/3) | unowned | agent |
| `introspect` (hidden) | Agent reads the full API schema JSON including `TraceRecord` field definitions + exit codes so it can generate or validate calls | agent-protocol-orient (3/3) | unowned | agent |
| `discover` (hidden) | Agent or setup script locates known agent session directories across projects to determine which corpora are available | agent-protocol-orient (1/3) | unowned | agent |
| `__complete` (hidden) | Shell delegates every tab-completion query here so completions stay in sync with the Click tree without regenerating shell scripts | shell-completion-protocol (1/1) | unowned | agent |
| `_assess-remote` (hidden) | Release engineer runs quality assessment on a published HF dataset via hf-mount, writing a `quality.json` sidecar + optionally regenerating the README | build-publishable-dataset (post-publish) ★ | unowned | agent |
| `_audit-spec` (hidden) | Developer fills missing entries in `field_intent.yaml` interactively or in CI to keep the quality field-intent spec current | dev-quality-maintenance (1/2) | unowned | agent |
| `_audit-run` (hidden) | Developer runs a field-intent audit over sampled traces to generate `audit_report.md` + verify quality gate calibration | dev-quality-maintenance (2/2) | unowned | agent |

## §10 — Per-trajectory usage guide

Three reference trajectories rendered as full user-facing sections.
Two strategic ★ trajectories (the load-bearing consumer-API surfaces
named in §9) plus one routine trajectory to prove the doc shape
generalizes. The remaining trajectories follow the same shape; expand
as ownership lands.

### 10.1 `recreate-trace-environment` ★ (strategic)

**JTBD.** Rebuild the exact workspace state that produced a trace so a
downstream consumer — perturbation harness, RL trainer, evaluation
rig, or an OSS-repo "rewind" feature — can replay or branch off it
deterministically.

**Commands.** `trace teleport export TRACE_ID --output DIR` then
`trace teleport open WORKSPACE --project DIR`. Bundles a trace + its
retained Git evidence into a portable workspace, then opens that
workspace into a fresh project directory.

**Example — consumer-flow (perturbation analysis).**

```bash
# 1. Inspect what's available
opentraces trace query "auth refactor"
# pick trace tr-1a2b...

# 2. Bundle the trace + git evidence (workspace dir must not exist)
opentraces trace teleport export tr-1a2b3c --output ./perturb.workspace

# 3. Reconstitute into a fresh project where the experiment runs
opentraces trace teleport open ./perturb.workspace --project ./perturb-run
cd ./perturb-run

# 4. Now apply the perturbation, replay the agent, diff outcomes.
```

**Key flags + JSON contract.**

- `trace teleport export TRACE_ID --output DIR --json` — `--json`
  emits the bundle manifest (paths written, commit shas referenced,
  retained Git evidence summary). Consumer apps parse the manifest to
  decide whether the bundle is complete enough for their experiment.
- `trace teleport open WORKSPACE --project DIR --json` — `--json`
  emits the opened-project envelope (project dir, restored HEAD,
  applied Git anchors). Required for headless automation.
- **Failure contract.** When no TrailEvents exist for a trace,
  `export` refuses with `rc=3` and the documented error
  `no TrailEvents found for trace <id>`. Downstream consumers branch
  on `rc=3` to fall back to a degraded mode rather than corrupt a
  bundle. Asserted by the `recreate-trace-environment` journey.

**Persona guidance.**

- `agent` (default for this trajectory): always pair with `--json`.
  Parse the export manifest; never assume the bundle layout from the
  filename.
- `human`: drop `--json` for a pretty summary. The two commands also
  work in a Conductor-style "make changes, rewind, retry" loop —
  `trace teleport open` into a blank project gives you a clean slate
  matching the moment captured by the trace.
- Surface modes: `--json` only; no `--tui` or `--web` mode (the
  consumer flow is always headless).

**Agent chaining.**

```bash
# Materialize, run, diff in one shell pipeline
opentraces trace teleport export "$TRACE" --output /tmp/ws.bundle --json \
  | jq -r '.workspace_root' \
  | xargs -I{} opentraces trace teleport open {} --project /tmp/replay --json \
  | jq -r '.project_root' \
  | xargs -I{} sh -c 'cd "{}" && pytest -x'
```

**Owning journey.** `recreate-trace-environment.toml` (Tier 0, core
lane). Today asserts the export error contract; the happy-path
companion that seeds real TrailEvents is a tracked follow-up.

---

### 10.2 `build-dataset-from-lineage` ★ (strategic)

**JTBD.** Build a dataset or downstream consumer app on top of trace
lineage primitives — query Patch Trails by trace/commit/path,
materialize rewind snapshots, dereference `ot://` resource URIs, walk
the structured evidence chain for any trace step.

**Commands (hidden but first-class consumer APIs per §9).**

| Command | Purpose |
|---|---|
| `trail explain --trace X --step N --json` | Evidence chain JSON for one step |
| `trail explain --commit SHA --json` | Evidence chain for a commit |
| `trail search --trace X --json` | All Patch Trails for a trace |
| `trail search --commit SHA --json` | Anchors at a commit |
| `trail search --path FILE --json` | Patches touching a file |
| `trail search --survival reverted --json` | Patches with a given survival state |
| `trail snapshots --trace X --json` | List rewind candidates for a trace |
| `trail snapshot checkout SNAP_REF` | Materialize a rewind snapshot |
| `trail resolve "ot://trace/X/patches/Y/trail"` | Dereference a stable URI |
| `trail timeline --trace X --json` | Full ordered event timeline |
| `trail diff --trace X --from N --to M` | Trace Patch between two step snapshots |
| `trail attach --trace X --commit SHA` | Manually connect a trace to a commit |
| `trail mature` / `trail rebuild` | Maintenance — re-derive projections |
| `trail sync --trace X` | Sync survival state against current Git |

These are HIDDEN from `opentraces trail --help` (the end-user surface
is `blame` / `graph` / `track`) but **callable as consumer APIs**.
Dataset workflows, RL trainers, dashboards, and CI consumer apps
depend on them.

**Example — building a "patches that survived into main" dataset.**

```bash
# Get every Patch Trail for a trace, JSON-shaped
opentraces trail search --trace tr-1a2b3c --json \
  | jq '.patches[] | select(.survival_state == "alive_on_path")' \
  > survived.jsonl

# For each survivor, fetch its evidence chain
jq -r '.trace_patch_id' survived.jsonl | while read pid; do
  opentraces trail resolve "ot://trace/tr-1a2b3c/patches/$pid/trail" --json
done > evidence.jsonl
```

**Key flags + JSON contract.**

- All commands accept `--json` and emit a stable schema-versioned
  envelope (`schema_version: "opentraces.trail_X.vN"`).
- **Empty-state contract.** When the TrailEvent log is empty,
  `--json` returns a structured envelope describing
  `limitations: ["trail_event_log_unavailable"]` with a
  `recommended_action` — never `rc != 0`, never a traceback.
  Consumer apps branch on the limitations vocabulary.
- `trail explain` returns the canonical `step_id`, `trace_id`,
  `step_index`, `event_log_ref`, `evidence_tier`, plus per-step
  `limitations`. The full envelope is the consumer-API contract.

**Persona guidance.**

- `agent` (the only sensible persona for these commands): always
  `--json`. The text/rich-table output is for maintainers, not
  consumers.
- No `--tui` or `--web` modes (consumer-API only).
- For dataset workflows, wire `trail search` results through
  `trace teleport export` when you need the workspace state too —
  the two trajectories compose.

**Agent chaining.**

```bash
opentraces trail search --commit "$COMMIT" --json \
  | jq -r '.anchors[].trace_patch_id' \
  | xargs -I{} opentraces trail resolve "ot://trace-patch/{}" --json
```

**Owning journeys (3, one per consumer-API primitive).**

- `build-dataset-lineage-explain.toml` — `trail explain` envelope
  contract (`event_log_ref`, `step_index`, `limitations`).
- `build-dataset-lineage-search.toml` — `trail search` empty-state
  envelope (`limitations`, `recommended_action`).
- `build-dataset-lineage-snapshot.toml` — `trail snapshots` envelope
  contract (`schema_version`, `snapshot_count`, `trace_id`) + the
  `trail snapshot checkout` surface is documented.

---

### 10.3 `connect-hf-identity` (routine — proves the doc shape generalizes)

**JTBD.** Log in to HuggingFace Hub so dataset remotes + bucket sync
can authenticate. Confirm the active identity before publishing.
Rotate or decommission credentials when done.

**Commands.** `auth login` → `auth whoami` → `auth logout` (lifecycle
1 → 3).

**Example — first-time login.**

```bash
# Browser OAuth (recommended for humans)
opentraces auth login

# Or pass a token directly (agent-friendly)
opentraces auth login --token "$HF_TOKEN"

# Confirm the active identity before publishing
opentraces auth whoami --json
# {"username": "...", "name": "...", "orgs": [...]}

# Rotate / decommission
opentraces auth logout
```

**Key flags.**

- `auth login --token TOKEN` — non-interactive form for agents + CI.
  Without `--token`, opens a browser OAuth flow.
- `auth whoami --json` — gates `dataset publish` and `bucket remote
  push`. Always run this in an agent pre-flight before any HF write.

**Persona guidance.**

- `agent`: always `--json` on `whoami`. Use `--token` for `login`;
  never trigger a browser flow.
- `human`: omit `--json` on `whoami` for a pretty summary; browser
  OAuth on `login` is fine.
- No `--tui` or `--web` surface (these are credentials commands —
  intentionally CLI-only).

**Owning journey.** `connect-hf-identity.toml` (Tier 0, core lane —
ships in the routine-22 batch). Exercises the `whoami` → no-identity
contract (rc=0 with `logged_in: false`) against an unauthenticated
box, then validates `login --help` and `logout --help` surfaces.

---

## §8 — Provisional resolutions (no user input needed)

These cross-bucket reconciliations were applied unilaterally during
synthesis. Document them here for review; flip any of them by editing
this section.

1. **`build-publishable-dataset` is the canonical 6-step trajectory**
   spanning Bucket 3 + Bucket 6: `trace index rebuild` → `trace query`
   → `dataset new` → `dataset run` → `dataset status` → `dataset
   publish`. `_assess-remote` is an optional post-publish step.
2. **`extract-bounded-evidence` and `retrieve-relevant-traces`** stay
   distinct: the first is dataset-workflow-oriented (`trace slice`),
   the second is general-purpose retrieval (`trace query`). They share
   no commands.
3. **`connect-agent-runtime` has two parallel step-1s**:
   `setup claude-code` (capture hooks) and `setup skill` (skill
   registry). Both are entry points into the same trajectory; pick
   either based on what the developer is wiring first.
4. **`setup auth` is a hidden compatibility alias** of `auth login`. No
   separate trajectory or public ownership journey.
5. **`backfill` and `git-backfill` are both in the same
   `attribution-backfill` trajectory.** `backfill` writes the per-line
   attribution cache (commit→file:line); `git-backfill` writes
   `refs/notes/opentraces` + `git_links` (commit→trace). Complementary,
   not redundant.
6. **Hidden `trail *` substrate is a load-bearing utility, not
   debugging-only.** Collapse them into one `build-dataset-from-lineage`
   trajectory. Per the user (2026-05-15): "Trail are important
   utilities to create datasets or consumer application that need
   trace data." Treat them as **unowned**, not N/A — the coverage gate
   should drive journey authoring against these commands, because
   downstream consumers (RL trainers, evaluation rigs, dashboard apps)
   call them as the dataset-construction primitives. Plan 060's
   original framing of these as "hidden power-user surfaces" was
   incomplete; they are hidden from end-user `--help` but they are
   first-class consumer-facing APIs.
7. **`trail teleport export/open` is dropped** as a deprecated alias of
   `trace teleport`. Only the `trace` form is in the map.
8. **Pure-help group entry-points** (`trace`, `trail`, `trail blame`,
   `bucket`, `bucket remote`, `dataset`, `dataset remote`, `dataset
   schedule`, `workflow`, `auth`, `config`, `setup` group, `setup
   watcher` group) are **omitted** from the per-bucket tables — they
   have no JTBD beyond "show help" and dilute the coverage gate. They
   remain in `journey-inventory.md` for completeness.
9. **`setup llm-review`** is in `configure-security-reviewer`, with a
   cross-reference to `configure-publishing-gates` (which is just an
   informal label for "the set of gates `dataset publish` runs through"
   — not a true trajectory of its own since there's only one command).
10. **`_capture` and `_ingest-session`** both belong to the same
    `session-ingest` trajectory. The distinction (SessionEnd hook vs
    Stop hook) is implementation detail; document but don't split.
11. **`trace teleport` is the environment-reconstitution primitive.**
    Per the user (2026-05-15): the JTBD is to "recreate the
    environment that created the trace for perturbation analysis,
    reinforcement learning, or simply rewind features in OSS repos."
    This is a strategic capability for downstream consumers, not a
    developer-only hand-off tool. The trajectory was renamed from
    `move-trace-workspace` to `recreate-trace-environment` and marked
    ★ (cross-bucket) because dataset workflows + consumer apps call
    into it.
12. **`dataset review` splits into three trajectories.** Per the user
    (2026-05-15): keep `review-rows-cli` (the programmatic
    approve/reject/reset path), `review-rows-tui` (the Textual UI),
    `review-rows-web` (the Flask UI) as distinct trajectories. Each
    deserves its own owning journey because the surfaces and personas
    diverge enough to need separate coverage.
13. **`automate-dataset-runs` stays as one 4-step trajectory** (add →
    list/show → pause/logs → resume/remove). Reads as the natural
    schedule lifecycle.

## §9 — Resolved decisions (user, 2026-05-15)

All four open questions were resolved in a single review pass. The
substantive shifts are captured in §8 (#6, #11, #12, #13). Two of them
materially change how the project should think about parts of the CLI:

- **`trace teleport` is a strategic surface**, not developer plumbing.
  It enables perturbation analysis, RL training pipelines, evaluation
  harnesses, and "rewind" features. Future plans (064+ cloud / 065+
  VNC) should treat environment reconstitution as a first-class
  consumer-facing capability, and the matrix should grow at least one
  journey that exercises `trace teleport export → trace teleport open`
  in a downstream-consumer flow.

- **Hidden `trail` substrate commands are first-class consumer APIs**,
  not debugging surfaces. The coverage gate should treat them as
  unowned (i.e. drive journey authoring against them), not N/A. The
  next wave of journey TOMLs should include at least: a `trail
  explain` evidence-chain journey, a `trail snapshot checkout` rewind
  journey, and a `trail search` lineage-query journey, because dataset
  + consumer apps consume those exact primitives.

## Delivery Summary (`/goal` session, 2026-05-15)

Plan 063 shipped as the single source of truth for the JTBD × action-
trajectory map. The two interlocking deliverables landed:

**A. 063 promoted to SSoT.**
- Per-trajectory user-facing sections live in §10 — example
  invocations + key flags + persona guidance (`--json` vs `--tui` vs
  `--web`, agent-chaining patterns). Rendered for the two strategic ★
  trajectories (`recreate-trace-environment`, `build-dataset-from-lineage`)
  plus one routine (`connect-hf-identity`) to prove the doc shape.
  Remaining trajectories follow the same shape; expand as ownership
  lands.
- `tests/otbox/jtbd.py` parses 063 directly (no duplicated machine-
  readable copy). The parser extracts 41 trajectories from §1 and 112
  commands from §2..§7.
- Matrix runner enforces 063 through `tests/otbox/inventory.py`:
  - **Gate 1** — Click command not in 063 → FAIL (visible non-group
    only; per §8.8 pure-help groups are exempt).
  - **Gate 1b** — 063 row not in the live Click registry → FAIL.
    Catches stale entries.
  - **Gate 2** — Journey TOML's `trajectories = [...]` names an
    unknown trajectory → FAIL at matrix start, before any boxes
    are provisioned.
  - **Gate 3** — Visible, non-deprecated, non-auto-invoked command
    with no owning journey → FAIL under `--strict`.
- `./otbox matrix --inventory --strict` now exits 1 on any drift.
  `make otbox-inventory` runs in strict mode by default.
- `tests/otbox/test_jtbd_ssot.py` is the millisecond-scale pytest
  gate that locks the SSoT contract into CI.

**B. Coverage gap closed.**
- Routine 22 + strategic 4 + cleanup 3 journey TOMLs shipped.
- Owned count: **18 / 97 → 78 / 97** (target was 60+). The 19
  remaining unowned are all pure-help Click groups (exempted by
  design per §8.8).
- Strategic ★ trajectories now have PASSing Tier 0 journeys:
  - `recreate-trace-environment.toml` — asserts the
    `trace teleport export` error contract (`rc=3`, documented
    error message) + the `trace teleport open --help` surface.
  - `build-dataset-lineage-explain.toml` — `trail explain --json`
    envelope contract (`event_log_ref`, `step_index`, `limitations`).
  - `build-dataset-lineage-search.toml` — `trail search --json`
    empty-state contract (`limitations: trail_event_log_unavailable`,
    `recommended_action`).
  - `build-dataset-lineage-snapshot.toml` — `trail snapshots --json`
    envelope contract (`schema_version`, `snapshot_count`,
    `trace_id`) + `trail snapshot checkout --help` surface.
- Routine 22 + partials shipped: `connect-hf-identity`,
  `enable-shell-completions`, `verify-install`,
  `attribution-backfill`, `enable-live-attribution`,
  `bucket-remote-digests`, `bucket-remote-push`, `bucket-remote-pull`,
  `bucket-replay`, `bind-hf-remote`, `manage-hf-visibility`,
  `automate-dataset-runs`, `productionize-trace-workflow`,
  `pr-lineage-publish`, `agent-protocol-orient`,
  `survey-local-datasets`, `decommission-dataset`, `survival-walk`,
  `dev-quality-maintenance`, `inspect-security-pipeline`,
  `configure-settings`, `connect-agent-runtime`, `configure-bucket`,
  `enable-security-tools`, `maintain-install`, `onboard-integrations`,
  `build-publishable-dataset-shape`, `onboard-repo`.

**Edge cases shaken out during the slice.**
- 063 originally collapsed `dataset review approve/reject/reset`
  into one row, but Click models them as a single command with a
  positional arg; restored to one `dataset review` row covering all
  behaviors.
- `security tools list` / `info` and `security sanitize` were
  missing from 063 entirely — added with new trajectory
  `inspect-security-pipeline`.
- `trail teleport export/open` is deprecated but still visible in
  Click; 063 now carries explicit deprecated rows so the gate
  doesn't flag them as missing.
- Parser had to tolerate `**slug** ★` in §1 inventory rows (cross-
  bucket trajectories append the star outside the bold close).
- `discover` returns rc=6 ("no Claude session dirs in isolated
  box") inside otbox — that's the documented contract, asserted as
  the trajectory's expected behavior.

**CI guard.** `make otbox-slice` (1 test) + `make otbox-journeys`
(45 tests, was 13) + `make otbox-tier1` (8 tests) + `make
otbox-matrix` (PASS on 2 declared-checkpoint pairs; SKIPs the
legacy-seed journeys cleanly) + `make otbox-inventory` (strict gate,
exits 0). `tests/otbox/test_jtbd_ssot.py` ties the SSoT contract to
the default test sweep.

**Follow-ups (non-blocking).**
- Real-TrailEvent seed (`c-traces-with-trails` checkpoint) so the
  `recreate-trace-environment` happy-path can exercise the full
  `export → open` round trip on actual captured trace data — not
  just the no-data contract.
- Render per-trajectory §10 sections for the remaining 38
  trajectories as ownership matures.
- Auto-generated coverage badge in journey-inventory.md (per-row
  trajectory link to the owning journey TOML).
- Promote the Click ↔ 063 walker output into a pyproject-installed
  console script (today only callable via `./otbox`).

## Appendix — Reconciled trajectory ownership (post-delivery, 2026-05-15)

| Trajectory | Owning journeys |
|---|---|
| onboard-repo | `onboard-repo` + `cli-publish-happy-path` + `cli-lifecycle` |
| offboard-repo | `cli-lifecycle` |
| verify-install | `doctor-health` + `verify-install` + `install-smoke-tier1` + `tier1-cross-os-install` + `tier1-warm-reuse` |
| connect-hf-identity | `connect-hf-identity` |
| configure-settings | `configure-settings` + `cli-lifecycle` |
| enable-shell-completions | `enable-shell-completions` |
| connect-agent-runtime | `connect-agent-runtime` (via `setup claude-code/git/skill --help`) |
| configure-bucket | `configure-bucket` |
| configure-security-detectors, configure-security-reviewer | `enable-security-tools` |
| inspect-security-pipeline | `inspect-security-pipeline` |
| maintain-install | `maintain-install` |
| onboard-integrations | `onboard-integrations` |
| attribution-backfill | `attribution-backfill` + `trail-blame-and-graph` |
| enable-live-attribution | `enable-live-attribution` |
| retrieve-relevant-traces, inspect-trace-context, extract-bounded-evidence, maintain-index | `trace-map-and-slice` (+ `cli-publish-happy-path`, `tier1-cold-publish`, `tier1-warm-reuse`) |
| resolve-trace-artifact | `cli-lifecycle` |
| recreate-trace-environment ★ | `recreate-trace-environment` |
| commit-attribution-audit, pr-lineage-publish | `trail-blame-and-graph`, `pr-lineage-publish` |
| survival-walk | `survival-walk` |
| build-dataset-from-lineage ★ | `build-dataset-lineage-explain`, `build-dataset-lineage-search`, `build-dataset-lineage-snapshot` |
| inspect-private-storage | `bucket-inspect` |
| compare-bucket-digests, backup-bucket-to-remote, restore-bucket-on-new-machine, restore-trail-lineage-to-repo | `bucket-remote-digests`, `bucket-remote-push`, `bucket-remote-pull`, `bucket-replay` |
| survey-local-datasets | `survey-local-datasets` |
| build-publishable-dataset ★ | `cli-publish-happy-path`, `tier1-cold-publish`, `build-publishable-dataset-shape` |
| review-rows-cli | `cli-publish-happy-path` + `tier1-cold-publish` |
| bind-hf-remote | `bind-hf-remote` + `cli-publish-happy-path` |
| manage-hf-visibility | `manage-hf-visibility` |
| automate-dataset-runs | `automate-dataset-runs` |
| productionize-trace-workflow ★ | `productionize-trace-workflow` |
| agent-protocol-orient | `agent-protocol-orient` |
| dev-quality-maintenance | `dev-quality-maintenance` |
| decommission-dataset | `decommission-dataset` |
| session-ingest, commit-correlation | partial (auto-invoked; proven indirectly by `tier1-cold-publish` + `world` seed) |
| review-rows-tui, review-rows-web | follow-up (the legacy TUI and Flask web review surfaces are decommissioned — `tui-review-smoke` (a `sys.exit(2)` stub tombstone) and `web-viewer-smoke` (only green by bypassing the missing `opentraces web` command) were deleted in #43/#42; new journeys will target the future dataset-scoped review UI) |
| manual-inbox-recovery, schema-migration, shell-completion-protocol | follow-up (hidden surfaces not in the strict gate, but candidates for the next coverage wave) |

## Pre-delivery — Original trajectory ownership snapshot (2026-05-15)

The table below captured ownership *before* the slice closed the gap.
Preserved for historical reference.

| Trajectory | Has a journey today? |
|---|---|
| onboard-repo, offboard-repo, verify-install | partial (`cli-lifecycle`, `doctor-health`) |
| connect-hf-identity | **no** |
| configure-settings | partial (`config show` via `cli-lifecycle`) |
| enable-shell-completions | **no** |
| connect-agent-runtime | partial (`world` seed installs `setup git`) |
| configure-bucket, configure-security-detectors, configure-security-reviewer, maintain-install | **no** |
| session-ingest, commit-correlation | partial (proven indirectly by `tier1-cold-publish` flow) |
| manual-inbox-recovery, schema-migration | **no** |
| attribution-backfill | partial (`trail-blame-and-graph` runs `backfill`) |
| enable-live-attribution | **no** (only `test_watcher_installer.py` covers golden files) |
| retrieve-relevant-traces, inspect-trace-context, extract-bounded-evidence, maintain-index | **yes** (`trace-map-and-slice`) |
| resolve-trace-artifact | **yes** (`cli-lifecycle`) |
| recreate-trace-environment ★ | **no** (strategic surface — needs at least one consumer-flow journey) |
| commit-attribution-audit | **yes** (`trail-blame-and-graph`) |
| pr-lineage-publish | **no** |
| survival-walk | **no** |
| build-dataset-from-lineage ★ | **no** (load-bearing consumer-API; needs explicit journeys per user decision §9) |
| inspect-private-storage | **yes** (`bucket-inspect`) |
| compare-bucket-digests, backup-bucket-to-remote, restore-bucket-on-new-machine, restore-trail-lineage-to-repo | **no** |
| survey-local-datasets, decommission-dataset | **no** |
| build-publishable-dataset | **yes** (`cli-publish-happy-path`, `tier1-cold-publish`) |
| review-rows-cli | **yes** (covered by `cli-publish-happy-path`) |
| review-rows-tui | **no** |
| review-rows-web | **no** |
| bind-hf-remote, manage-hf-visibility | partial (`dataset remote create` covered; rest unowned) |
| automate-dataset-runs | **no** |
| productionize-trace-workflow | **no** |
| agent-protocol-orient, shell-completion-protocol, dev-quality-maintenance | **no** |

**Bottom line**: 8 trajectories fully owned, 6 partial, **24 trajectories with zero journey coverage** (now 24 after splitting `review-rows-interactive` into TUI + web). Two of the 24 are *strategic* surfaces that the user flagged as load-bearing for downstream consumer apps:

- `recreate-trace-environment` (★) — perturbation, RL, rewind. Needs a consumer-flow journey.
- `build-dataset-from-lineage` (★) — every hidden `trail *` substrate command. Each consumer-API primitive needs its own journey (start with `trail explain`, `trail snapshot checkout`, `trail search`).

The remaining 22 are the routine coverage debt: `setup`/`auth`/`completions`/bucket-remote/dataset-schedule/PR-lineage/agent-protocol clusters. Plan 062's matrix (M62-4..M62-7) is the vehicle for closing them.
