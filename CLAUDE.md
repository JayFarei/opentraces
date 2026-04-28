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
- `src/opentraces/` - Main CLI package (7 top-level folders after the phase-6 reorg):
  - `cli/` - Command surface (split by workflow area)
  - `core/` - Domain glue: config, paths, state, workflow, inbox, pipeline, processors, review, publish_flow. `core/trails/` holds the Phase-5 Trace Trails substrate — append-only `event_log.py` (canonical `refs/opentraces/local/events/v1`), `snapshots.py`, `anchors.py` (exact + structural identity), `follow.py` (survival states), `explain.py`, `resources.py` (`ot://` scheme), `reconciler.py` (watcher backstop), `supersede.py` (rewrite handling), `slices.py`, `attach.py`, `rebuild.py`, `exact.py`, `capture_limitations.py`, `models.py`.
  - `capture/` - Inbound boundary: parsers + hooks + installers per external system. `capture/claude_code/` (parse + hooks), `capture/hermes.py`, `capture/git/` (post-commit correlator + install).
  - `publish/` - Outbound boundary: format serializers (`atif.py`, `agent_trace.py`) and destination publishers (`huggingface/` — sharded upload, dataset card, HF schema).
  - `enrichment/` - Read-only enrichers: git signals, attribution, dependencies, metrics. `enrichment/git/` holds the plan-041 commit-correlation stack — `correlator.py`, `notes_store.py` (`refs/notes/opentraces` read/write), `blame.py` (file:line to trace), `liveness.py` (lazy `commit_reachable` / `content_alive`), `jj_support.py` (Jujutsu change-id fallback). Note: post-commit tier assignment lives in `capture/git/post_commit.py`.
  - `quality/` - Trace quality assessment, persona rubrics, upload gates, parse gate
  - `security/` - Secret scanning, anonymization, classification (independently versioned via `SECURITY_VERSION`)
  - `clients/` - Presentation layers (TUI, web backend) — business logic lives in `core/`
- `web/` - Web frontends
  - `viewer/` - React SPA trace review UI
  - `site/` - Next.js marketing site
  - `coming-soon/` - Static coming-soon page (Vercel)
- `tests/` - Test suite
- `kb/` - Research and discussion logs (gitignored in OSS)

## Key Decisions

- Claude Code and Hermes (runtime agents) for v0.2, adapter contract ready for additional parsers
- Own schema (superset of ATIF), export to ATIF via `opentraces export --format atif`
- Sharded JSONL upload (one file per push, never append to existing)
- Attribution derived from Edit tool calls, not unified diff
- Context-aware security scanning (different rules per field type)
- Per-project review policy (auto/review) controlling whether traces need manual approval
- Zero required annotation, all enrichment is deterministic
- Security pipeline has its own `SECURITY_VERSION` in `security/version.py` (currently `0.3.0`), bump it when changing detection logic (regex patterns, entropy thresholds, classifier heuristics, anonymization rules). Tiers: 1a regex, 1b entropy (always on); 1.5 TruffleHog, 1.8 LLM PII, 2 LLM semantic review (opt-in); 3 human inbox. Tier 1.5 findings are redacted in place and force review; parse errors and Tier 2 denials can move traces to `TraceStatus.BLOCKED`. Opt-in commands: `opentraces setup trufflehog`, `opentraces llm-review`, `opentraces push --llm-review`, and `opentraces doctor` for pipeline health.
- Post-processors are declared per-project as an ordered list (`post_processors: [{name, command, args, env}]`). They run pre-upload during `opentraces push`, after security redaction. Contract: stdin = trace JSON, stdout = trace JSON, exit 0. Non-zero exit / missing binary / invalid output are non-fatal by default, promoted to hard errors under `--strict`. Byte-identical output = no-op. `opentraces doctor` probes configured processors.
- VCS-anchored Trace Trails are stored as an append-only `TrailEvent` batch log under the `refs/opentraces/local/events/v1` Git ref. Snapshot refs under `refs/opentraces/local/traces/...` are advisory projections, rebuildable from the canonical log via `opentraces trail rebuild`. Anchor identity has two tiers: exact range hash first, structural match fallback (line-similarity ≥ 0.85) — identity survives format-then-commit but firmness drops `firm` → `provisional`. Survival states: `alive_on_path`, `alive_transformed`, `reverted`, `lost`, `unknown`, `alive_moved`, `partially_preserved`, `repaired`. Stable resource scheme: `ot://trace/<id>/patches/<id>/trail`, `ot://git-anchor/<id>`, `ot://file/<path>/line/<n>/origin`. Watcher reconciliation only attributes mutations whose interval is fully inside exactly one writer's firm step window. Surface: the `opentraces trail` command group (`explain`, `diff`, `follow`, `rebuild`, `attach`, `resolve`).
- HF dataset schema is model-driven. `publish/huggingface/schema.py` derives the `dataset_infos.json` features map from `TraceRecord` on every push (never hand-maintained). `HFUploader.ensure_repo_exists` fetches the remote `dataset_infos.json` and compares versions before uploading: remote-newer raises `RemoteSchemaAheadError` (CLI exits 3 with an `ot setup upgrade` hint, no overwrite), remote-equal skips the re-upload, remote-older/missing/malformed uploads the local schema. `migrate_outdated_shards` only rewrites rows strictly older than the target — newer rows are preserved byte-identically. Auto-migration is safe only under the additive-evolution contract in `packages/opentraces-schema/VERSION-POLICY.md`: MINOR/PATCH bumps must be additive, breaking changes require MAJOR plus a registered migration in `opentraces_schema.migrations`.

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
- `tests/integration/test_trace_trails_installed_runtime_uat.py` exercises installed runtime surfaces: `opentraces --json setup git`, a real Git post-commit hook invocation, and `opentraces watcher tick --project ... --json` driving session ingest, watcher reconciliation, and anchor maturation.
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
