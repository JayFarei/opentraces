# opentraces.ai

## Project Overview

Open schema + CLI for capturing agent traces into a private local bucket, applying security scanning and redaction, enriching with attribution/git signals, and optionally publishing as structured JSONL datasets to HuggingFace Hub.

Full-detail architecture and decision notes live in `docs/agents/architecture-notes.md` (private, gitignored). This file carries the short version; read the notes file before working on any subsystem it covers.

## Stack

- **Language**: Python 3.10+
- **Schema**: `opentraces-schema` (standalone Pydantic v2 package in `packages/`)
- **CLI**: Click-based (`src/opentraces/cli/`)
- **Web review**: removed in v0.4.8 (Flask server + Textual TUI deleted; React SPA under `web/viewer/` unmaintained)
- **Marketing site**: Next.js (`web/site/`); **coming soon page**: static HTML (`web/coming-soon/`)
- **Optional security extras** (opt-in via `opentraces setup <tool>`): `trufflehog`, `transformers` + `torch` (privacy-filter PII NER), OpenAI-compat LLM endpoint (`llm_pii`, `llm_review`)

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e packages/opentraces-schema
pip install -e ".[dev]"
pytest tests/ -v
```

## Structure

- `skill/` - Claude Code skill definition (skills.sh convention)
- `packages/opentraces-schema/` - Standalone schema package (Pydantic models)
- `packages/opentraces-ui/` - Design system (tokens, base, components, React wrappers, logo assets, DESIGN.md)
- `src/opentraces/` - Main CLI package:
  - `cli/` - Command surface split by workflow area. v7 spine (PR #174): visible trace verbs are `query` / `get` / `map` / `slice`; `ctx <trace>[:step]` bare-noun address; `trail blame|graph|track|pr`; `bucket list|repair|reclaim|verify|sync|connect`; `dataset`, `workflow`, `setup`, `auth`, `config`, `doctor`, root `status`/`upgrade`/`uninstall`. Many pre-v7 verbs are hidden-but-callable (hidden != removed; all stay `--json`-scriptable). Under explicit `--json` stdout is pure JSON; `opentraces <trace-id>[:step|:last|:A-B]` root-dispatches to `trace get`. Full verb map: architecture-notes "CLI surface".
  - `core/` - Domain glue: config/paths/state/pipeline/review/publish flow, `capsule/` (seal-family capsule subsystem), `bucket_store.py`/`bucket_remote.py`, `egress_clearance.py` (the one shared egress predicate), `isolation.py`, `datasets.py` + workflow runner, trace index/map/slices, `trails/` (Trace Trails substrate: append-only event log under `refs/opentraces/local/events/v1`, anchors, sync, reconciler), `context_tree/` (Context Tree substrate: what the LLM saw at each step). Full module map: architecture-notes "core/ module map".
  - `capture/` - Inbound boundary: parsers + hooks + installers per external system (`claude_code/`, `hermes.py`, `git/`, `fs_watcher/`, `otlp/`, `tool_boundary.py`, `skill/`).
  - `publish/` - Outbound boundary: format serializers (`atif.py`, `agent_trace.py`) and destination publishers (`huggingface/`).
  - `enrichment/` - Read-only enrichers: git signals, attribution, dependencies, metrics. `enrichment/git/` holds the commit-correlation stack; post-commit tier assignment lives in `capture/git/post_commit.py`.
  - `quality/` - Trace quality assessment, persona rubrics, upload gates, parse gate
  - `security/` - Secret scanning, anonymization, classification (independently versioned via `SECURITY_VERSION`)
  - `clients/` - Presentation layers; only `clients/text/` remains (terminal renderers). Business logic lives in `core/`.
  - `workflow_templates/` - Bundled dataset workflow skill packages, materialized via `opentraces workflow create <name> --template <template>`.
- `web/` - `viewer/` (unmaintained React SPA), `site/` (Next.js marketing site), `coming-soon/` (static, Vercel)
- `tests/` - Test suite. `tests/otbox/` is otbox, a snapshottable full test environment (dev/CI tool, not shipped): seeds an opentraces world, snapshots it, runs declarative TOML journeys with a journey × checkpoint matrix, captured-session checkpoints, simulated-user runner, and footage recording. Entry points: `make otbox-slice|otbox-journeys|otbox-tier1|otbox-matrix|otbox-inventory|capture-refresh SCENARIO=<name>|otbox-footage-all`. See `tests/otbox/README.md`, `.agents/skills/otbox/SKILL.md`, and architecture-notes "otbox test environment".
- `kb/` - Private research, designs, experiments, goal-run evidence (gitignored in OSS). Private execution artifacts go to `kb/runs/opentraces/`, active design work to numbered `kb/wip/` workspaces, prototype outputs to `kb/wip/007-experiments/`; keep public `examples/` sanitized.

## Key Decisions

Summaries only — the full text of each is in `docs/agents/architecture-notes.md` under the matching heading.

- Claude Code and Hermes (runtime agents) for v0.2; adapter contract ready for additional parsers
- Own schema (superset of ATIF), export via `opentraces export --format atif`
- Sharded JSONL upload (one file per push, never append to existing)
- Attribution derived from Edit tool calls, not unified diff
- Context-aware security scanning (different rules per field type); per-project review policy (auto/review); zero required annotation, all enrichment deterministic
- **Security pipeline** is a flat tool registry (`security/tools/_registry.py`) of Detector/Judge/Transformer tools run in canonical order by `security/pipeline.py`; `security.sanitize_record(record, cfg=cfg)` is the single public sanitize surface; `SECURITY_VERSION` (currently `0.8.0`) bumps on any detection-logic or metadata-shape change; session-level LLM review is deliberately NOT in the registry.
- **Post-processors**: per-project ordered list, stdin/stdout trace JSON contract, non-fatal by default (`--strict` promotes), byte-identical output = no-op, probed by `doctor`.
- **Runtime selection** (`setup runtime`, issue #99): re-renders integration runners (codex/claude/git/watcher) to a chosen install's interpreter; data-safe (never installs/removes packages, never touches bucket or git refs); watcher re-render is shim-only.
- **Context Tree** (plans 077/078): third substrate alongside Trace and Trail, capturing what the LLM saw at each step; rides Trail's event log with 4 new event types + 4 content-addressed layer types; `ctx <trace>[:step]` is the surface, frozen `opentraces.context_*.v1` envelopes. v1 honest scope: JSONL path is session-level approximation; OTLP receiver (`opentraces capture-otlp`, `setup capture-otlp`) closes the system-prompt/tool-schema/sampling-params gaps with `capture_method=otel`; bypass-safe (receiver down = emission dropped, agent traffic never blocked). #158 lands OTel context in the bucket via raw-body reconstruction, content-addressed message blobs, watcher auto-flush, and `doctor` coverage reporting.
- **Trace Trails**: VCS-anchored, append-only `TrailEvent` log under `refs/opentraces/local/events/v1` (snapshot refs are advisory projections); two-tier anchor identity (exact hash, then structural ≥ 0.85); survival states (`alive_on_path` … `repaired`); `ot://` resource scheme; anchor search recorded as ONE summary event per commit/reconcile-run (plan 090); since #358 the live shape is v3 — anchored-only `results[]` plus an O(1) coverage claim (hot hook/attach) or exact `unanchored_trace_patch_ids` (maturation flush, compaction) — read via tri-shape `iter_search_records`. Historical fat v2/legacy events can be compacted by the experimental, opt-in `bucket reclaim --anchor-search` (default off — O(corpus) and slow on large real buckets, issue #362). Visible surface: `trail blame commit <sha>`, `trail pr render|create|update`, `trail graph`, `trail track`.
- **Bucket** (layout v2, plan 080): every trace lands in `~/.opentraces/bucket/` — per-trace envelope + gzip-deterministic companions, content-addressed blobs, canonical event-log mirror, manifest. Local-only by default; `bucket connect` opts into HF remote sync; `bucket sync push` is the gated egress seal (auditable `pushed[]`/`withheld[]`, refuses on uncleared traces). Read verbs accept `--remote`. Gzip `mtime=0` everywhere. Buckets (raw traces) are distinct from datasets (workflow-projected rows).
- **Size-independent bucket reads** (plan 087): `bucket status` is O(1) off the persisted manifest + per-row `status` accelerator (digest-excluded, so `bucket_digest` is unchanged); honest `freshness` block; `trail track --all` bounded via `read_events_scoped`. HONEST BOUNDARY: `bucket sync push` / `bucket repair` stay O(N) by the sync protocol (full-corpus digests; Merkle change deliberately out of scope).
- **Datasets**: a dataset is a workflow-driven row projection over traces (`dataset new/run/review/publish/remote/schedule/verify/...`); workflows are skill-format packages. Seal-family M2: workflow resolved before binding (rc=2 on failure), first-class row lineage (`scope_ref`/`transform@digest`/`bucket_state@digest`/`answers` per ADR-0008), `dataset run --sync` is watermark-delta, `dataset verify` classifies `reproduces` / `bucket-advanced` / `integrity-failure`.
- **Trace surface** (v7): `query` → `get` → `map` → `slice` is the loop. `slice --by user-turn|change-burst|milestone|subgoal` tiles the whole trace (frozen `opentraces.slicing.v1`; milestone/subgoal via pluggable judge, `rc=10 needs-judgment` handshake + `--answers`). Trace Intelligence (plan 086): `--waste`, `--run-intel`, hidden `trace compare` — all derive-on-demand frozen envelopes reporting `record`/`otel` fidelity.
- **HF schema is model-driven**: `dataset_infos.json` derives from `TraceRecord` on every push; remote-newer raises `RemoteSchemaAheadError` (exit 3, no overwrite); auto-migration only under the additive-evolution contract in `packages/opentraces-schema/VERSION-POLICY.md`.
- **Bursts/patches**: a *trace patch* is one Edit/Write call (one hunk, NOT one file); a *change burst* clusters by step proximity (`--burst-gap 35`); the burst's commit is `burst_commit_sha`, NOT the trace's `outcome.commit_sha` (last commit of session, unrelated). `--no-commit-lookup` skips per-burst `git log` on hot paths.
- **Workflow → consumer pattern**: workflows produce typed row streams from bucket traces; consumers render one destination. `execute_workflow` is the single execution primitive (versioned run packet on `OT_RUN_PACKET`); executor set is `{current-agent, script}`. First consumer: branch context → `trail pr` (deterministic PR body, no LLM). Prototype second consumer: daily standup at `examples/standup/`. New destinations = new workflows + renderers, not bespoke modules.
- **Workflow judgment + integrity** (#186/#187): judgment is a recorded input (`rc=10` + `JudgmentRequest`s, re-run with `--answers` for byte-identical results); install-time dotfile/symlink rejection; run-time digest re-verification (`--strict` fails on mismatch).
- **Seal-family contract** (ADR-0008): a projection is explainable iff a pure function of `scope_ref` + `transform@digest` + `bucket_state@digest` + `answers`; exactly two seals — dataset and capsule. ONE shared egress predicate (`core/egress_clearance.py`, `unknown` conservatively withheld) behind all three egress doors. `core/isolation.py` reports `env_scrubbed`/`home_isolated`/`network_denied` honestly and stamps `sandbox_tier` from the S0-S3 lattice — tier rises only with real OS containment, never by relabelling.
- **Capsule**: immutable, URL-addressed seal — a redacted mini-bucket of one usage episode. Surface: `capsule create <ref>` (v7 address selects scope) / `get` (read-only, zero setup) / `import` (explicit opt-in write) / `preview` / `share [--publish]` (same egress predicate). `web/capsule-worker/` renders published capsules statelessly (reviewed prototype, deploy excluded).
- **Capsule replay honesty** (M3): four properties (`reproducible`/`gradable`/`scoped`/`sandboxed`) from lattice ordinals; `verdict_trust` is the weakest-link min. Today every honest capsule reports `floor` — the honesty contract working as designed (env resolver is follow-up #202). `capsule test` runs isolated with foreign-command block-by-default; bundle secret scan is a publish GATE (blocks bytes, never a trust factor).
- **Schema 0.8.0** (#200): purely additive `Environment` fields for the future dependency-pin resolver; presence never raises `env_tier`. **Schema 0.9.0** (#212): additive dataset-lifecycle fields for `dataset run --facet`; never touches the published HF row schema.
- **Skill-intelligence → verifier → SkillOpt**: skill detection feeds the trace index; verifier factory mines episodes into per-skill rubrics with a mechanically derived trust ladder (agent PROPOSES → factory SCORES → human APPROVES); statuses `blocked_<reason>` / `provisional_weak_only` / `calibrated`; BLOCKED-by-design on the current near-one-class bucket is the honest answer.

## Testing

Use the repo virtualenv for test commands; system Python may not have the editable packages and CLI dependencies installed.

```bash
source .venv/bin/activate
pytest tests/ -v
```

Focused Trace Trails validation (use these when validating lineage behavior; per-file capabilities described in architecture-notes "Trace Trails test capabilities"):

```bash
.venv/bin/python -m pytest \
  tests/integration/test_trace_trails_full_stack_demo.py \
  tests/integration/test_trace_trails_installed_runtime_uat.py \
  tests/integration/test_trace_trails_portrayal.py \
  tests/integration/test_trace_trails_corpus.py -q
```

Corpus commands:

```bash
.venv/bin/python tests/integration/harness/trace_trails_corpus.py --check
.venv/bin/python tests/integration/harness/trace_trails_corpus.py --update
```

Opt-in live Claude/tmux UAT (must not run in default CI):

```bash
OT_REAL_REPL=1 OT_TRAIL_REAL_REPL=1 \
  .venv/bin/python -m pytest tests/integration/test_trail_real_repl_scenarios.py -q
```

Full `pytest tests/ -v` is the broad regression command, but unrelated perf budgets or UI snapshot drift should be triaged separately from Trace Trails functional evidence.

## Agent skills

### Issue tracker

Issues live in the `JayFarei/opentraces` GitHub repo, managed via the `gh` CLI. External PRs are NOT a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage-state roles using their default strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`), orthogonal to the existing topic labels (`bug`, `dev`, `architecture`, etc.). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
