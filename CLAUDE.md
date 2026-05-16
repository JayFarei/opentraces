# opentraces.ai

## Project Overview

Open schema + CLI for crowdsourcing agent traces to HuggingFace Hub. Parses coding agent traces, applies security scanning and redaction, enriches with attribution/git signals, and publishes as structured JSONL datasets.

## Stack

- **Language**: Python 3.10+
- **Schema**: `opentraces-schema` (standalone Pydantic v2 package in `packages/`)
- **CLI**: Click-based (`src/opentraces/cli/`)
- **Web review**: Flask (`src/opentraces/clients/web/`) + React SPA (`web/viewer/`)
- **Marketing site**: Next.js (`web/site/`)
- **Coming soon page**: Static HTML (`web/coming-soon/`)
- **Optional security extras** (opt-in via `opentraces setup <tool>`): `trufflehog` (binary), `transformers` + `torch` (for the `openai/privacy-filter` PII NER), and an OpenAI-compat LLM endpoint (for `llm_pii` and `llm_review`).

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
  - `cli/` - Command surface split by workflow area. Visible top-level groups: `trace` (query/index/map/slice/get/teleport), `trail` (blame/graph/track), `bucket` (status/manifest/replay/remote), `dataset` (list/new/run/review/publish/remote/schedule/status/remove), `workflow` (create/list/templates/remove), `setup`, `auth`, `config`, `doctor`, `completions`, plus `init`, `status`, `remove`. Substrate-level trail commands (`explain`, `sync`, `timeline`, `teleport`, `resolve`, `attach`, `rebuild`, `diff`, `resume`, `follow`, `snapshots`, `snapshot checkout`) remain callable but are hidden from `--help` after the CLI spine simplification.
  - `core/` - Domain glue: `config.py`, `paths.py`, `state.py`, `inbox.py`, `pipeline.py`, `processors.py`, `review.py`, `publish_flow.py`, `workflow.py`/`workflows.py`/`workflow_runner.py` (dataset workflows), `datasets.py` (local HF-shaped datasets), `bucket_store.py`/`bucket_remote.py` (private bucket + remote sync), `bursts.py`/`intent.py` (change-burst clustering and intent extraction), `schedules.py` (dataset schedule registry), `search_projection.py`/`semantic.py`/`trace_index.py` (Trace Index BM25 + semantic), `trace_map.py`/`trace_slices.py` (Trace Map and Slice projections), `agent_resume.py`, `entity_join.py`, `inverse_blame.py`, `migrate_trace_ids.py`, `repo_identity.py`, `theme.py`, `trace_meta.py`, `trace_summary.py`, `text_redaction.py`, `default_inbox.py`, `cache.py`, `backfill.py`, `doctor.py`, `ingest.py`. `core/trails/` holds the Trace Trails substrate: append-only `event_log.py` (canonical `refs/opentraces/local/events/v1`), `snapshots.py`, `anchors.py` (exact + structural identity), `sync.py` (survival state sync against current Git), `explain.py`, `resources.py` (`ot://` scheme), `reconciler.py` (watcher backstop), `supersede.py` (rewrite handling), `slices.py`, `attach.py`, `rebuild.py`, `exact.py`, `capture_limitations.py`, `maturation.py`, `query.py`, `workspace.py`, `contract.py`, `ids.py`, `models.py`.
  - `capture/` - Inbound boundary: parsers + hooks + installers per external system. `capture/claude_code/` (parse + hooks), `capture/hermes.py`, `capture/git/` (post-commit correlator + install), `capture/fs_watcher/` (filesystem mutation observation), `capture/tool_boundary.py` (tool-boundary event shaping), `capture/skill/` (skill invocation detection).
  - `publish/` - Outbound boundary: format serializers (`atif.py`, `agent_trace.py`) and destination publishers (`huggingface/` — sharded upload, dataset card, HF schema).
  - `enrichment/` - Read-only enrichers: git signals, attribution, dependencies, metrics. `enrichment/git/` holds the plan-041 commit-correlation stack — `correlator.py`, `notes_store.py` (`refs/notes/opentraces` read/write), `blame.py` (file:line to trace), `liveness.py` (lazy `commit_reachable` / `content_alive`), `jj_support.py` (Jujutsu change-id fallback). Note: post-commit tier assignment lives in `capture/git/post_commit.py`.
  - `quality/` - Trace quality assessment, persona rubrics, upload gates, parse gate
  - `security/` - Secret scanning, anonymization, classification (independently versioned via `SECURITY_VERSION`)
  - `clients/` - Presentation layers (text, TUI, web backend), business logic lives in `core/`
  - `workflow_templates/` - Bundled dataset workflow skill packages (e.g. `skill-command-trajectory-eval-v1/`) that `opentraces workflow create --template <name>` materializes into a project.
- `web/` - Web frontends
  - `viewer/` - React SPA trace review UI
  - `site/` - Next.js marketing site
  - `coming-soon/` - Static coming-soon page (Vercel)
- `tests/` - Test suite
  - `tests/otbox/` - otbox: snapshottable full test environment (dev/CI tool, not a shipped surface). Repo-root `otbox` shim (mirrors `otd`). Seeds a full opentraces world, snapshots it, runs declarative TOML journeys across CLI/TUI/web. Tier 1 lease over SSH/Tailscale (plan 061). Journey × checkpoint matrix with content-addressed snapshot cache + Click-registry coverage map (plan 062 — `tests/otbox/checkpoints/`, `tests/otbox/matrix.py`, `tests/otbox/inventory.py`). The captured-session checkpoint family (`c-captured-real-session`, `c-captured-with-revert`, `c-captured-with-secrets`, `c-captured-multi-skill`, `c-captured-with-pr-branch`) runs the real opentraces `_capture` + post-commit + watcher-tick chain end-to-end on deterministic fake-agent session JSONLs so consumer-API journeys assert on real TrailEvents, Git anchors, and Trace Index entries rather than empty-state envelopes (plans 064, 068). Journey TOMLs declare a `[preconditions]` block (declarative quantitative + qualitative world-state requirements) and a `tier_label` of `bronze` / `silver` / `gold` driving the tiered coverage gate (plan 069), with plan 070 backfilling four gold-tier agent-facing trajectories (`trail blame pr`, `dataset new`/`run`/`schedule`, `trail track` + `trail search --survival reverted`, `security sanitize`) on the credible-state checkpoints. The plan 071 simulated-user PTY runner drives real agent binaries (claude, codex, hermes) against scripted TOML scenarios and snapshots box state under `tests/otbox/captures/<name>/` via `make capture-refresh SCENARIO=<name>` (default-CI safe via the in-tree `echo-meta` synthetic binary). Plan 072 makes the captured-session checkpoints artifact-preferred / synthetic-fallback: if a committed `tests/otbox/captures/<name>/snapshot.tar.gz` exists the checkpoint restores it in-place (higher fidelity, real-agent-driven), otherwise the synthetic fake-claude harness chain runs (audit shape and journey templating identical across both, with a `capture_metadata.source` field for provenance). `make otbox-slice` / `make otbox-journeys` / `make otbox-tier1` / `make otbox-matrix` / `make otbox-inventory` / `make otbox-agent-session` / `make capture-refresh SCENARIO=<name>`; see `tests/otbox/README.md`, `.agents/skills/otbox/SKILL.md`, and `kb/plans/060` / `061` / `062` / `063` / `064` / `068` / `069` / `070` / `071` / `072`.
- `kb/` - Research and discussion logs (gitignored in OSS)

## Key Decisions

- Claude Code and Hermes (runtime agents) for v0.2, adapter contract ready for additional parsers
- Own schema (superset of ATIF), export to ATIF via `opentraces export --format atif`
- Sharded JSONL upload (one file per push, never append to existing)
- Attribution derived from Edit tool calls, not unified diff
- Context-aware security scanning (different rules per field type)
- Per-project review policy (auto/review) controlling whether traces need manual approval
- Zero required annotation, all enrichment is deterministic
- Security pipeline is a flat tool registry (`security/tools/_registry.py`). Each tool implements one of three protocols: `Detector` (emits redactable spans — regex, entropy, TruffleHog, LLM PII, privacy-filter), `Judge` (verdict without mutation — classifier), or `Transformer` (record rewrite without spans — path anonymizer). The orchestrator (`security/pipeline.py`) runs tools in canonical order: regex → entropy → trufflehog → llm_pii → path_anonymizer → classifier. Each tool exposes `enabled(cfg)`, `apply(record, ctx)`, and `describe(cfg)`. Tools are opted in via per-tool `cfg.security.<tool>.enabled` flags (set by `opentraces setup <tool>` commands); there is no top-level privacy-tier knob. The core capture pipeline (`core/pipeline.py::process_trace`) and workflow scripts both call `security.sanitize_record(record, cfg=cfg)` (or pass an explicit `tools=` list) — that's the single public surface for inline sanitization. Records emerge with `metadata.security.tools_applied: list[str]` and `metadata.security.tools.<name>` for per-tool result patches. `SECURITY_VERSION` (currently `0.4.0`) bumps when any tool's detection logic changes or the metadata shape evolves. Session-level LLM review (`opentraces dataset review`) is intentionally NOT in the tool registry — it's an expensive on-demand workflow, not part of the per-record sanitize step. Pipeline health and tool enable state are surfaced by `opentraces doctor` (under `security.tools`) and `opentraces security tools list`. Workflow scripts can also pipe JSON through `opentraces security sanitize --tools <names>` or `--use-config` for language-agnostic sanitization.
- Post-processors are declared per-project as an ordered list (`post_processors: [{name, command, args, env}]`). They run before dataset publication, after security redaction. Contract: stdin = trace JSON, stdout = trace JSON, exit 0. Non-zero exit / missing binary / invalid output are non-fatal by default, promoted to hard errors under `--strict`. Byte-identical output = no-op. `opentraces doctor` probes configured processors.
- VCS-anchored Trace Trails are stored as an append-only `TrailEvent` batch log under the `refs/opentraces/local/events/v1` Git ref. Snapshot refs under `refs/opentraces/local/traces/...` are advisory projections, rebuildable from the canonical log by internal maintenance paths. Anchor identity has two tiers: exact range hash first, structural match fallback (line-similarity ≥ 0.85), identity survives format-then-commit but firmness drops `firm` → `provisional`. Survival states: `alive_on_path`, `alive_transformed`, `reverted`, `lost`, `unknown`, `alive_moved`, `partially_preserved`, `repaired`. Stable resource scheme: `ot://trace/<id>/patches/<id>/trail`, `ot://git-anchor/<id>`, `ot://file/<path>/line/<n>/origin`. Watcher reconciliation only attributes mutations whose interval is fully inside exactly one writer's firm step window. Visible public surface: `opentraces trail blame commit <sha>` (commit-mode blame; the original `trail blame <sha>` is gone — `blame` is now a group), `opentraces trail blame pr render | create | update` (PR-shaped projection of commit blame for a branch range), `opentraces trail graph`, `opentraces trail track`. Substrate commands (`explain`, `sync`, `timeline`, `teleport`, `resolve`, `attach`, `rebuild`, `diff`, `resume`, `follow`, `snapshots`, `snapshot checkout`) remain callable for scripting and debugging but are hidden from `--help`.
- Bucket subsystem (private trace bucket). Every captured trace lands in a project-local private bucket under `~/.opentraces/projects/<slug>/bucket/`. The bucket is local-only by default; `opentraces setup bucket` opts into remote-by-default sync against a HuggingFace remote. `opentraces bucket status` reports local bucket health, sync eligibility, and trail freshness; `opentraces bucket manifest` materializes the manifest; `opentraces bucket remote push/pull/diff/status` runs sync with conflict handling; `opentraces bucket replay` replays bucket-exported Trace Trails into a Git repository. Buckets are distinct from datasets: a bucket holds raw captured traces, a dataset holds workflow-projected rows.
- Dataset subsystem (local HF-shaped datasets). A dataset is a workflow-driven row projection over one or more traces. `opentraces dataset new <name> --workflow <path>` creates the manifest; `opentraces dataset run` executes the workflow (dry-run, current-agent, or headless modes); `opentraces dataset review/approve/reject` controls per-row publication state; `opentraces dataset remote create` binds a HuggingFace dataset remote; `opentraces dataset publish` ships approved rows; `opentraces dataset schedule` controls recurring runs; `opentraces dataset status/list/remove` round out the surface. Workflows are skill-format packages (`opentraces workflow create/list/templates/remove`); the bundled template `skill-command-trajectory-eval-v1` lives in `src/opentraces/workflow_templates/` and is materialized into a project on `workflow create --template skill-command-trajectory-eval-v1`.
- Trace-side public surface. `opentraces trace query` returns bounded `CandidatePacket`s over a local BM25 + semantic Trace Index (`core/trace_index.py`, `core/search_projection.py`, `core/semantic.py`). `opentraces trace index` rebuilds and inspects the projection. `opentraces trace map` returns a deterministic `TraceMap`. `opentraces trace slice` materialises bounded `TraceSlice` packets (templates: `bursts`, `--around-step`, `--around-patch`, manual `--from-step/--to-step`). `opentraces trace get` resolves a trace, trace unit, map node, or `ot://` Trail resource. `opentraces trace teleport` moves a trace plus retained Git evidence between workspaces.
- HF dataset schema is model-driven. `publish/huggingface/schema.py` derives the `dataset_infos.json` features map from `TraceRecord` on every push (never hand-maintained). `HFUploader.ensure_repo_exists` fetches the remote `dataset_infos.json` and compares versions before uploading: remote-newer raises `RemoteSchemaAheadError` (CLI exits 3 with an `ot setup upgrade` hint, no overwrite), remote-equal skips the re-upload, remote-older/missing/malformed uploads the local schema. `migrate_outdated_shards` only rewrites rows strictly older than the target — newer rows are preserved byte-identically. Auto-migration is safe only under the additive-evolution contract in `packages/opentraces-schema/VERSION-POLICY.md`: MINOR/PATCH bumps must be additive, breaking changes require MAJOR plus a registered migration in `opentraces_schema.migrations`.
- Bursts and patch terminology. A *trace patch* is one Edit/Write tool call — roughly one hunk on one file, NOT one file. A *change burst* clusters file_edit / patch_created nodes by step proximity (default `--burst-gap 35`). `burst.unique_files` is a per-file aggregation (deduped: foreign-agent absolute prefixes and the repo root are stripped so abs/rel variants collapse) keyed by relative path. `burst.patches` is per-hunk (one entry per Edit). The burst's commit lives in `burst_commit_sha` (modal across `patches[*].commit_sha`, falling back to the first git commit observed in the burst's step range via the trace's hook trail) — NOT the trace's `outcome.commit_sha`, which is the LAST commit in the session and is unrelated to the burst. `trace map --bursts` emits a structured `intent` object on the burst node's metadata: `{trigger, most_substantive_spec, spec_chain, burst_commit_sha, commit_subject, commit_body}` (Cluster E). Legacy `intent_text` / `intent_user_step` remain as aliases for `intent.most_substantive_spec.{text, step}` (or trigger when no spec exists). Use `trace map --bursts --no-commit-lookup` (or `trace get --bursts --no-commit-lookup`) to skip the per-burst `git log` lookup on hot/offline paths.
- Workflow→consumer pattern for "trace data at place/time". Workflows produce typed structured row streams from bucket traces; *consumers* read those streams and render to one destination. `core.workflow_runner.execute_workflow(workflow_name, scope, output_path)` is the dataset-free primitive (sibling of `run_dataset_workflow`); it uses a `script` executor that subprocess-runs `<workflow.path>/scripts/build_rows.py` with `OT_RUN_PACKET` + `OT_DATASET_OUTPUT` env vars. The first non-dataset consumer is `core.branch_context.run_branch_workflow` (branch context: base, head, commits, scope_digest), driving the bundled `pr-intent-summary-v1` workflow that emits one row per branch commit with full trace lineage. The PR consumer (`opentraces trail blame pr render | create | update` in `cli/trail_pr.py`, attached to the `blame` group because the rows ARE blame projected across a branch range) renders that JSONL as a GitHub PR body (using `entity_join` + `Burst.intent` + `trace_summary` for deterministic synthesis — no LLM) and wraps `gh` for idempotent create-or-update. Future consumers (Slack, dashboards, CI) are new workflows + new renderers under `trail blame <destination>`, not bespoke modules. Outputs are cached at `<project>/.opentraces/branch_runs/<workflow>/<scope_digest>.jsonl`. The `run_dataset_workflow` function is intentionally unrefactored in v1; it should be collapsed into `execute_workflow` only when a third consumer arrives.

## Testing

Use the repo virtualenv for test commands; system Python may not have the
editable packages and CLI dependencies installed.

```bash
source .venv/bin/activate
pytest tests/ -v
```

Focused Trace Trails validation:

```bash
.venv/bin/python -m pytest \
  tests/integration/test_trace_trails_full_stack_demo.py \
  tests/integration/test_trace_trails_installed_runtime_uat.py \
  tests/integration/test_trace_trails_portrayal.py \
  tests/integration/test_trace_trails_corpus.py -q
```

Trace Trails test capabilities:

- `tests/integration/test_trace_trails_full_stack_demo.py` runs the deterministic full-stack mock project: hook-boundary mutation observation, ingest, delayed Git Anchor maturation, Trace Workspace export/open, and user-facing `trail`/`blame`/`graph` projections.
- `tests/integration/test_trace_trails_installed_runtime_uat.py` exercises installed runtime surfaces: `opentraces --json setup git`, a real Git post-commit hook invocation, and `opentraces setup watcher tick --project ... --json` driving session ingest, watcher reconciliation, and anchor maturation.
- `tests/integration/test_trace_trails_portrayal.py` builds the reviewer-facing portrayal packet and checks UAT judgement points for usefulness/usability across watcher, attribution, maturation, and projection surfaces. Human-readable criteria live in `tests/integration/trail_scenarios/reports/trace_trails_portrayal_uat.md`.
- `tests/integration/test_trace_trails_corpus.py` verifies the versioned synthetic corpus at `tests/fixtures/trace_trails_corpus/v1`, including normalized command/API outputs, TrailEvents, Trace Workspace rows, and HF-style dataset artifacts.
- `tests/integration/harness/trace_trails_corpus.py --check` confirms the committed corpus is current; `--update` intentionally regenerates it after accepted scenario changes.

Corpus commands:

```bash
.venv/bin/python tests/integration/harness/trace_trails_corpus.py --check
.venv/bin/python tests/integration/harness/trace_trails_corpus.py --update
```

Opt-in live Claude/tmux UAT is available but must not run in default CI:

```bash
OT_REAL_REPL=1 OT_TRAIL_REAL_REPL=1 \
  .venv/bin/python -m pytest tests/integration/test_trail_real_repl_scenarios.py -q
```

Use the focused Trace Trails suites when validating lineage behavior. Full
`pytest tests/ -v` is still the broad regression command, but unrelated perf
budgets or UI snapshot drift should be triaged separately from Trace Trails
functional evidence.
