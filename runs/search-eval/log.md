# Search Evaluation Harness — Run Log

Goal: Make Trace Spotlight a fast, relevant, large-scale progressive-discovery engine
(qmd-for-agentic-traces) and prove it with the Search Evaluation Harness of
`kb/plans/088-search-evaluation-harness.md`. Build order: U0 → U1 → U2 → U3 (harness),
then U4 → U5 → U6 (capabilities, red→green), then U7 → U9 (scale + otbox gate).

Each entry records: the unit, the diff summary, the seed-case / slope numbers observed,
and the next-step rationale. RED on S2/S3/S4/S5 in the first report is **expected and
correct** — those are the executable spec for U4/U5/U6.

Hard constraints (from plan 087 + the Goal):
- Python 3.10+, stdlib + existing deps only; no new third-party deps.
- Full determinism on the gated path: seeded, no wall-clock/`random` in fixtures,
  gzip `mtime=0`, snapshot key = hash(profile + seed + generator_version + code_version + tier).
- Gold is **planted** (synthetic) or **mined** (`--live`), never LLM-judged on the gated path.
- Never reintroduce a full `rebuild_index()` or O(corpus) refresh on the query hot path
  (preserve the 087 warm-query wins: `_cheap_bucket_signal` stat probe, in-place WAL refresh).
- No change to the bucket on-disk layout or the canonical event log; read/search path only
  (`core/search_projection.py`, `core/trace_index.py`, `cli/trace.py`) + `tests/search_eval`,
  `tests/perf`, `tests/otbox`.
- Gate on scaling SLOPE + counters, not absolute ms (absolute budgets flake across machines).

---

## 2026-05-31 — Session start: warm-up + recon

- Read: plan 088 (the spec: R1–R10, architecture, 8-case Seed Evaluation Dataset),
  plan 087 (warm-query lifecycle; U6 target = `query_search_projection_page`'s no-`WHERE`
  scan + Python concept filter over all docs, the qmd-invariant violation), memories
  `project-search-eval-harness` + `project-trace-spotlight-perf`.
- Confirmed scaffolding state: `tests/search_eval/` and `runs/search-eval/` empty (clean).
  Perf harness reuse spine present: `tests/perf/{measure,models,python,subprocess,fixtures}.py`,
  `scenarios/`, `budgets.toml`, `render_baseline_report.py` → `BASELINE.md`.
- Launched recon workflow (4 parallel Explore agents) to anchor U0–U3 reuse points with
  file:line: perf harness API, query/projection surface, bucket read APIs (profiler inputs),
  env/scaffolding + determinism helpers.
- Next: synthesize recon → build U0 (bucket profiler → committed `real-bucket-profile.json`).

## 2026-05-31 — U0: bucket profiler ✅

- **Diff:** new `tests/search_eval/__init__.py`, `tests/search_eval/profiler.py` (stdlib-only,
  decoupled object-store reader), committed `tests/search_eval/real-bucket-profile.json`.
- **Numbers (live bucket):** 1084 traces / 5 projects (1059 in main); steps_per_trace
  p50=24 / p90=252 / p99=972 / max=2529 / mean=99.1 / total=107,408; distinct_files
  p50=0 / p90=16 / p99=120 / max=159 (most traces touch 0 files); generation_index
  p50=2 / max=21, supersession 0.979; 363 sessions, 334 multi-generation; events 460,312.
  profile_digest `sha256:754dc866…`. Matches recon's independent measurement exactly.
- **Determinism:** two runs → byte-identical (4.5s). `provenance` (volatile) separated from
  the hashed `profile` block; `generated_at` omitted by default for byte-stability.
- **U1 grounding (empirical probe):** built a perf `stack` fixture in a temp HOME and confirmed
  `trace query --lex` (index, total=24) AND `--semantic` (projection, total=480) BOTH serve
  from the project-staging source (`get_project_traces_dir` + StateManager). So U1 reuses the
  proven perf-fixture corpus mechanism; both query paths work without writing the bucket object
  store. `--live` (U8) will exercise the real bucket.
- **Next:** U1 deterministic planting generator — plant chronological (τ→RED), recency
  (recency-hit→RED), reference-bare needle (recall→GREEN) + reference-id hyphenated needle
  (total=0→RED, the S3 tokenizer gap), facet (recall GREEN / order RED), descriptive (GREEN);
  record gold + `expected_phase_a` per row; snapshot key = hash(profile_digest+seed+gen_version+code_version+tier).

## 2026-05-31 — U1: deterministic planting generator ✅

- **Diff:** new `tests/search_eval/generator.py` — pure `plan_corpus(profile, seed, tier)` →
  `CorpusPlan` (TraceSpecs + 20 QueryRows w/ recorded gold + `expected_phase_a`) and
  `materialize_corpus()` (writes staging JSONL + StateManager, reusing the perf home-config).
  Tiers dev=150 / real-scale=1084 (from profile) / xl=10000. No `random`, no wall-clock —
  ordinal IDs + hash-derived sampling (`_u`), profile inverse-CDF for filler step counts.
- **Determinism:** plan manifest byte-identical on rerun; corpus staging JSONL byte-identical
  across two materializations (`2e1f164f…`); snapshot_key stable (`sha256:c58d32a8…`);
  seed 2 → different corpus + key. snapshot_key = hash(profile_digest+seed+tier+gen_version+
  code_version), code_version = sha256 of generator.py source.
- **Empirical validation (dev tier, all 20 queries via ./otd):** invariants hold —
  GREEN rows (refbare/facet/desc/supersede) all recall gold at rank 1 / recall 1.00;
  refid reproduces **total=0** (RED); chrono recall 1.00 (order=RED dimension);
  recency recall 0.75 @limit30 + latest_rank=None (RED).
- **Two findings the harness surfaced (drive U2):**
  1. **S3 root cause is NOT the FTS hyphen** (recon theory was wrong). Verified on live bucket:
     `--lex nicobailon`/`pi-subagents`/`github.com/nicobailon/pi-subagents` all → total=0, but
     `--lex install` → 321. The needle lives ONLY inside a URL; the `_terms` regex
     `[a-zA-Z0-9_./-]+` swallows the URL into an unreachable compound (leading `//`, `https:`
     split). Refid re-modeled to embed the needle only inside `https://github.com/<owner>/<repo>`
     and query the bare segment → faithfully total=0. The capability-phase fix is URL/identifier
     sub-tokenization (folds into U6), NOT a hyphen tokenizer swap.
  2. **CandidatePackets are per-UNIT, not per-trace.** Recall/MRR/rank must dedupe to distinct
     trace_ids (first-occurrence order); the outcome query needs a generous --limit so ≥k distinct
     traces surface (per-unit packets crowd out distinct traces, e.g. recency 0.75 @limit30).
- **Next:** U2 runner (discovery loop per row via `measure_command_factory`) + outcome scorers
  (recall@k/MRR/NDCG/τ/recency-hit over distinct traces) + `report.py` → SEARCH-EVAL.md.

## 2026-05-31 — U2: runner + outcome scorers + report ✅

- **Diff:** `score_outcome.py` (recall@k/MRR/NDCG/Kendall-τ/recency-hit, trace-deduped),
  `runner.py` (materialize-once → warm → per-row outcome[generous limit] + perf[constant plan via
  `measure_command_factory`] → loop smoke → EvalReport + deterministic outcome_digest),
  `report.py` (→ SEARCH-EVAL.md: seed-case table + per-row + archetype + invariants),
  `test_search_eval.py` (10 tests), Makefile targets, `.gitignore` artifacts.
- **`make search-eval` runs end-to-end on dev in ~32s.** SEARCH-EVAL.md committed.
  150 traces / 23 rows / 14 green / 9 red, **invariants_ok=True** (every row's observed
  RED/GREEN matches its documented `expected_phase_a`), discovery-loop smoke ok (query→map→slice→get).
- **Seed cases reproduced (dev):** S2 GREEN recall=1.0 rank=1 (latency is the future RED at scale);
  S3 total=0 recall=0 (RED, URL needle); S4 recall=1.0 **τ=−1.0** (RED, reverse order);
  S5 recall=1.0 **rec@1=0.0** (RED, latest not first); S1 recall=1.0 **rank=9** (weak-lex quality);
  S6/S7 rank=1, S8 recall=1.0 (GREEN).
- **Determinism (R7):** outcome_digest + snapshot_key byte-identical across two full runs.
- **Two scorer corrections the harness forced (logged in U1):** trace-level dedup; chrono time-order
  must be DECORRELATED from trace_id order (equal-score rows fall back to the trace_id tiebreaker, so
  identity order would false-GREEN chronological without `--sort time`) → reversed → τ=−1 RED.
- pytest tests/search_eval green (10 passed). No production code touched yet.
- **Next:** U3 — boundedness counter (docs_scored ∝ matches, instrument search_projection.py:751 +
  index path; env-gated, surfaced in --json) + cliff p95 budgets (catch 10×, exempt known S2 cliff).
