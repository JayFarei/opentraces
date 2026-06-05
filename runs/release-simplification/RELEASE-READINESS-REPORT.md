# OpenTraces Release-Readiness Simplification — Before/After Report

**Date:** 2026-06-03 · **Base:** `d5d6bfd9b7` (main) · **Integration branch:** `simplify/release-readiness-v04`
**Net change:** 49 files, **+383 / −5,240 (≈ −4,857 LOC)**, 17 files removed, 2 small modules added. Zero merge conflicts.

---

## 1. Workflow used

A dynamic, multi-agent orchestration with isolated subagent contexts, explicitly designed against the three failure modes:

| Failure mode | Defense applied |
|---|---|
| **Agentic laziness** | Every theme driven to a terminal state (verified-and-merged here; none abandoned). Per-theme implement→verify loop (≤3 rounds); a theme only "passes" with `status=pass` **and** zero blocker/major findings. |
| **Self-preferential bias** | No implementer judged its own work. Each worktree got a **separate Opus adversarial verifier in a clean context** that independently re-ran tests and re-read the diff. |
| **Goal drift** | A feature map (non-regression contract) was captured once (`FEATURE-MAP.json`) and passed **verbatim** into every discovery, implementation, and verification agent. A durable `GOAL.md` ledger survived context compaction. |

**Phases (model routing: Sonnet = mechanical, Opus = judgment):**
1. **Phase 0–2** (workflow `wvf3oema7`, 9 agents): Opus built the feature map → 6 Sonnet discovery agents (structure, CLI, core, docs, tests, naming) → Opus synthesized 4 themes from the merged candidates, dropping 13 with written reasons. Key claims independently spot-checked by the orchestrator before approval.
2. **Phase 3–4** (workflow `w92e8bt0f`, 11 agents): 4 themes implemented in parallel isolated git worktrees (Sonnet) + adversarially verified (Opus, clean context, re-running tests). All 4 passed; t1 needed 2 rounds.
3. **Polish** (workflow `w5nzpmtrc`): completed the t4 `core/` consolidation correctly + adversarial re-verify. t2 doc-accuracy seam fixed directly.
4. **Phase 5–6** (orchestrator): sequential `--no-ff` merges with per-theme guards, full-suite + wheel-build integration gate, this report, PR.

Worktree test isolation used `PYTHONPATH` pinning (the repo `.venv` editable install points at the `pi-support` worktree, so pinning is required to exercise branch code; proven before any implementation began).

---

## 2. Themes selected (all verified-and-merged)

| id | Theme | Files | Δ LOC | Risk | Verifier |
|----|-------|------:|------:|------|----------|
| **t1** | Fix CI red-bar `SECURITY_VERSION` test + correct stale version/tool/command docs | 6 | +78 / −11 | low | pass (2 rounds) |
| **t2** | Remove dead modules & dev cruft (`http_proxy/`, `scripts/archive/`, `otc`, noop `hatch_build.py`, orphan Makefile target, badge tool) | 20 | +8 / −3,657 | low | pass (1 round) |
| **t3** | Delete dead CLI commands + parallel `_cli` forwarding shims + stale drop-list | 10 | +148 / −1,370 | medium | pass (1 round) |
| **t4** | Consolidate duplicated core query/time helpers; remove no-op stub loops | 14 | +149 / −202 | low | pass (1 round + polish) |

---

## 3. Before/after structure notes

**Files removed (17):**
- `src/opentraces/capture/http_proxy/` — entire deferred prototype (6 files, ~1,625 LOC). Frozen `capture_method='proxy'` vocab constant retained; prototype evidence retained under `tests/otbox/captures/http-proxy-prototype/` + git history.
- `src/opentraces/cli/publish.py` (687 LOC) + `src/opentraces/cli/import_hf.py` (306 LOC) — each registered exactly one already-dropped command; the 3 reusable config helpers were **relocated** to `core/publish_flow.py` first.
- `scripts/archive/` — 5 spike scripts, zero callers (~1,730 LOC).
- `hatch_build.py` + `tests/test_hatch_build.py` — a gutted noop build hook and its noop-asserting tests; the load-bearing declarative skill force-include block is untouched.
- `otc` — broken dev shim (hardcoded `PYTHONPATH` to a deleted worktree). `otd` (canonical) retained.
- `tools/badge-generator.html`, Makefile `build-viewer` orphan target.

**Files added (2, both small single-source modules):**
- `src/opentraces/core/_time.py` — `utc_now_str()`, single source for the formerly-duplicated UTC timestamp helper (consolidated across 7 `core/` files).
- `src/opentraces/core/query_helpers.py` — single source for `_fts_query` / `_page_offset` / `_recency_weighted_sort` / `_terms`, fixing a **silent regex divergence** between the two search backends.

**Dead code removed in place:** 4 never-registered `trace_*` CLI commands, the duplicate `_emit_json`, ~7 stale drop-list no-ops, ~34 `return _cli.*` forwarding shims, two no-op map-insert stub loops, and 4 zero-caller `workflow.py` functions.

---

## 4. Feature-preservation checklist (vs `FEATURE-MAP.json`)

| Contract item | Status | Evidence |
|---|---|---|
| Visible CLI surface (all groups + commands) | ✅ preserved | every subgroup `--help` resolves; `trail blame` still a GROUP (`commit`/`pr`) |
| Hidden-but-callable commands (`_capture`, `_run-post-commit-hook`, `_ingest-session`, `_scan`, hidden `trail …`) | ✅ preserved | all 35 top-level commands registered; `_capture --help` renders identically to base |
| `SCHEMA_VERSION = 0.6.0` (additive) | ✅ unchanged | no schema file touched |
| `SECURITY_VERSION = 0.6.0` + 9-tool order | ✅ unchanged | registry untouched; red-bar test now asserts 0.6.0 |
| CLI version `0.4.0` | ✅ unchanged | `--version` → 0.4.0; wheel builds `opentraces-0.4.0` |
| `CAPTURE_METHOD_VALUES` keeps `'proxy'` | ✅ preserved | import check: `{hardcoded_template, live_capture, otel, proxy, transcript_reconstruction}` |
| Event-log ref / anchor tiers / gzip mtime=0 | ✅ untouched | no substrate code changed |
| Wheel bundles skill assets | ✅ preserved | real wheel build inspected: skill assets present, `http_proxy` absent |
| No tests/docs deleted to shrink | ✅ honored | only `test_hatch_build.py` removed (it tested deleted code); doc edits are accuracy fixes |

---

## 5. Test / check results

**Full `pytest tests/` sweep on the integration branch: 3,436 passed, 182 skipped, 2 xfailed, 35 failed (31m25s).**

All 35 failures were triaged to **environment/harness artifacts, none caused by the simplification changes** (each proven, not assumed):

| Cluster | Count | Root cause | Proof |
|---|---:|---|---|
| `tests/perf/*` | 9 | Git worktrees don't carry `.venv`; the worktree `otd` shim falls back to a `python3` without opentraces → command `exit=1` before any timing. | With a functional worktree `otd`, the **entire perf suite passes (24 passed, 8 skipped, 0 failed)**. Perf has its own CI lane (`perf.yml --perf-lane smoke`), not a wholesale `pytest tests/`. |
| `tests/otbox/*` | 25 | Ran against **stale machine-local cached checkpoint boxes** (86 boxes under gitignored `.otbox/`; 0.37s cache hits, no rebuild). | The live integration CLI emits the asserted help string ("Search local retained traces"); that string is **unchanged by any theme** (empty `trace.py` diff for it). otbox is a dev tool run via `make otbox-*`, "not a shipped surface" (CLAUDE.md). |
| `test_migration…s7` | 1 | Drives a separate isolated v0.3.3 venv at `/tmp/ot-v033-worktree` that is missing `pydantic`. | **Fails identically on base** (`ModuleNotFoundError: No module named 'pydantic'`), source-independent. |

`35 = otbox(25) + perf(9) + s7(1)` exactly, so the **3,436 passing tests cover the entire shippable surface**, including every directly-affected area (cli, publish, e2e, core, security, integration/trails, search_eval).

Per-merge guards (all green):
- **t1:** red-bar suite `tests/e2e/test_plan32_integration.py` → 2 passed; `--version` 0.4.0.
- **t2:** `'proxy'` in `CAPTURE_METHOD_VALUES`; force-include intact; collect-only 3645, no import breaks.
- **t3:** all subgroup `--help` ok; `trail blame` still a group; `tests/publish/test_publish_flow.py` + `tests/cli/test_cli_commands.py` → 92 passed; no `cli.publish`/`cli.import_hf` imports remain.
- **t4 (+polish):** `tests/integration/test_trace_query_parity.py` 193 passed; `test_trace_trails_corpus.py` 3 passed; search-eval baseline 18 passed/1 skip (incl. outcome-digest stability); core dataset/bucket/workflow 35 passed.
- **Wheel build:** `python -m build --wheel` → `opentraces-0.4.0-py3-none-any.whl`, skill assets bundled, `http_proxy` absent.

---

## 6. Risks & recommended follow-ups

**Accepted minor (one deliberate behavior change):**
- **t4 `_terms` regex convergence.** The two search backends had *already silently diverged* at base (`search_projection` was `@`-aware, `trace_index` was not). Unification necessarily changes one side; the implementer kept the **primary** backend (`search_projection`) byte-identical to base and brought `trace_index` into agreement (now `@`-aware). Net observable change is limited to `@`-containing text in the secondary index; **all eval-baseline + 193 parity tests stay green** (the corpora contain no ranking-affecting `@` tokens). This is the fix the theme existed for, not a regression.

**Accepted as-is (cosmetic, recorded):**
- t1: published well-known `SKILL.md` quick-start block isn't byte-identical to source `skill/SKILL.md` — but the Trace-Intelligence commands are already documented in the published skill's dedicated section.
- t3: one harmless `"export"` drop-list entry remains (a swallowed no-op; no root `export` command exists).

**Deferred (recorded in `THEMES.json` "dropped" — intentionally out of an overnight PR):**
- Large-file splits (`trace_index.py` 4,263 LOC; `bucket_store.py` 3,205; `trails/sync.py`) — structural-only, high-coordination, no behavior win; a dedicated session.
- `core/workflow.py` ↔ `workflows.py` rename (ripples across ~10 import sites + docs).
- CLI shared-option decorator / `json.dumps` helper consolidation (fans across ~10 CLI files).
- Remaining `_utc_now()` copies in `capture/fs_watcher` + `consumers/**` (out of the `core/` theme scope) and the `search_projection` `datetime` variant (different return type — correctly left alone).
- Context-tree test-tree reorganization.

**Residual risk:** low. Changes are additive-safe, behavior-preserving (one documented convergence), and reviewable as four labeled `--no-ff` merges.
