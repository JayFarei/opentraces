# Progressive-Discovery Search — Run Log

Goal: implement plan 089 (U0-U5) so Trace Spotlight supports progressive
discovery: non-boilerplate summaries, reliable repeated queries, bounded cards,
trail/git color, and deterministic `trace discover`.

## 2026-06-01 — U0: boilerplate registry + CandidatePacket summaries

- **Diff:** added `src/opentraces/core/boilerplate.py` with injected-wrapper
  stripping for `IMPORTANT: Do NOT read SKILL.md...`, leading `$skill`/`/skill`
  triggers, `<goal_context><objective>...`, and `<system-reminder>` blocks.
  Added optional `headline` + `summary` fields to `CandidatePacket`. Threaded
  summary/headline metadata through Trace Index unit construction, and made
  packet construction re-read the bounded trace record when an older warm index
  lacks summary metadata, so users do not need a rebuild to stop seeing wrapper
  text. Extended `tests/search_eval` with planted boilerplate prefixes and a
  `summary_non_boilerplate_rate` gate.
- **Harness numbers:** `make search-eval` (dev tier) green: 24/24 outcome rows,
  `invariants_ok=True`, `summary_non_boilerplate_rate=1.00`, S1-S8 unchanged
  semantically, O(corpus)=2 documented facet rows.
- **Focused verification:** `.venv/bin/python -m py_compile ...` passed for the
  touched modules; `.venv/bin/python -m pytest tests/search_eval/test_search_eval.py -q`
  passed (`14 passed, 1 skipped`).
- **Next:** U1. Root-cause repeated/parallel query stalls around
  `cheap_sync_query_state()` and the index/projection cheap-signal markers, then
  add a 20 sequential + 8 parallel regression with a sync-count gate.

## 2026-06-01 — U1: repeated/parallel query reliability

- **Root cause:** concurrent callers could all observe a stale/missing
  cheap-sync marker before the first caller recorded the delta result, so they
  could pile into the expensive digest/refresh branch and contend on the
  projection writer. Sequential steady-state was already covered for single
  probes, but not as a discovery-loop invariant.
- **Diff:** added an advisory `.cheap-sync.lock` around the non-steady-state
  branch of `cheap_sync_query_state()`, with a marker re-check after acquiring
  the lock. Steady-state readers still take no lock. Added opt-in CLI
  diagnostics (`OT_SEARCH_DIAG=1`) exposing the `cheap_sync.synced` flag. Added
  cheap-sync regressions for 20 sequential projection probes and 8 concurrent
  stale projection probes; extended `tests/search_eval` with a repeated/parallel
  CLI reliability probe.
- **Harness numbers:** `make search-eval` (dev tier) green: 24/24 outcome rows,
  `invariants_ok=True`, `summary_non_boilerplate_rate=1.00`,
  repeated/parallel reliability `20 sequential + 8 parallel`, `sync_count=0`,
  elapsed `3774.71ms` under the 60s cliff. S1-S8 stayed green.
- **Focused verification:** `.venv/bin/python -m pytest
  tests/core/test_trace_index_cheap_sync.py tests/core/test_index_keep_warm_g2.py
  tests/search_eval/test_search_eval.py -q` passed (`27 passed, 1 skipped`);
  the cheap-sync focused subset passed again after removing the temporary
  unreachable body (`13 passed`).
- **Next:** U2. Add a bounded per-trace `CandidateCard` programmatic builder
  and `trace get --card`, reusing the summary helper plus existing Trace Map /
  burst summary data.

## 2026-06-01 — U2: bounded CandidateCard + `trace get --card`

- **Diff:** added `src/opentraces/core/discovery.py` with a read-only
  `CandidateCard` model and `candidate_card(ref)` builder. The card includes
  headline, summary, agent, day, bounded key files, bounded key steps, outcome,
  and stable refs. Added `trace get --card <ref> --json`; it resolves trace,
  unit, map-node, `t:` and `ot://trace/...` refs through the existing index.
  Extended the search-eval discovery-loop smoke to run
  `query -> map -> slice -> get --card -> get`.
- **Harness numbers:** `make search-eval` (dev tier) green: 24/24 outcome rows,
  `invariants_ok=True`, discovery-loop stages `query,map,slice,card,get`,
  `summary_non_boilerplate_rate=1.00`, repeated/parallel reliability still
  `20 sequential + 8 parallel`, `sync_count=0`, elapsed `3977.45ms`.
- **Focused verification:** `.venv/bin/python -m pytest
  tests/search_eval/test_search_eval.py -q` passed (`14 passed, 1 skipped`);
  py_compile passed for `core/discovery.py`, `cli/trace.py`, and the touched
  harness modules.
- **Next:** U3. Derive and surface deterministic provenance color
  (`committed|uncommitted|reverted|unknown` plus commit sha/subject) on cards and
  query results from existing outcome/git-link/trail facets, then cache/bound it.

## 2026-06-01 — U3: deterministic provenance color on query packets and cards

- **Diff:** added `src/opentraces/core/provenance.py` with deterministic
  provenance derivation from existing record outcome/git-link/trail metadata and
  indexed unit facets. Extended `CandidatePacket` and `CandidateCard` with
  `provenance_color`, `committed`, `commit_sha`, and `commit_subject`. The query
  path first uses indexed facets and falls back to the bounded trace record only
  when needed for older indexes. The card path resolves the record and surfaces
  the same color/commit fields.
- **Harness numbers:** `make search-eval` (dev tier) green: 24/24 outcome rows,
  `invariants_ok=True`, discovery-loop stages `query,map,slice,card,get`,
  `summary_non_boilerplate_rate=1.00`, repeated/parallel reliability
  `20 sequential + 8 parallel`, `sync_count=0`, elapsed `3972.01ms`. The loop
  smoke now fails if `trace get --card --json` omits either `headline` or
  `provenance_color`.
- **Focused verification:** `.venv/bin/python -m py_compile ...` passed for the
  touched provenance/discovery/index/CLI/harness modules;
  `.venv/bin/python -m pytest tests/search_eval/test_search_eval.py -q` passed
  (`14 passed, 1 skipped`); `.venv/bin/python -m pytest
  tests/core/test_trace_index_cheap_sync.py tests/core/test_index_keep_warm_g2.py
  -q` passed (`13 passed`).
- **Next:** U4. Add deterministic `trace discover <topic> --by day --json`
  using bounded query packets plus candidate cards, grouped by day with
  forward-links and no LLM/embedding dependency.

## 2026-06-01 — U4/U5: `trace discover` + day-grouped eval target

- **Diff:** extended `src/opentraces/core/discovery.py` with `DiscoveryPacket`
  and `DiscoveryGroup`, plus deterministic `discover(topic, by="day")`. It
  reuses bounded lexical `query_index_page`, dedupes by trace id, builds
  `CandidateCard`s, groups by day newest-first, and includes query/card/map
  forward refs. Added `opentraces trace discover <topic> --by day --json` with
  cheap-sync-first behavior matching `trace query`. Extended the planted eval
  corpus with a 6-trace `trace capsule` target spread over 3 days and added a
  discovery-loop smoke that checks day grouping, card refs, headlines, and
  provenance colors.
- **Harness numbers:** `make search-eval` (dev tier) green: 25/25 outcome rows,
  `invariants_ok=True`, discovery-loop stages
  `query,map,slice,card,get,discover`, `trace discover` grouping `3` day groups
  / `6` cards, `summary_non_boilerplate_rate=1.00`, repeated/parallel
  reliability `20 sequential + 8 parallel`, `sync_count=0`, elapsed
  `3818.38ms`. New `discover-00` row is bounded (`rows_scanned=6`,
  `corpus_docs=150`) with recall@10=1.00.
- **Focused verification:** `.venv/bin/python -m py_compile ...` passed for
  discovery/CLI/search-eval modules; `.venv/bin/python -m pytest
  tests/search_eval/test_search_eval.py -q` passed (`14 passed, 1 skipped`);
  `.venv/bin/python -m pytest
  tests/core/test_trace_index_cheap_sync.py tests/core/test_index_keep_warm_g2.py
  -q` passed (`13 passed`).
- **Next:** final hardening pass: inspect diff for accidental unrelated edits,
  run one combined focused test lane, and verify the public CLI surfaces.

## 2026-06-01 — Final hardening

- **Diff audit:** confirmed implementation edits are scoped to the schema
  `CandidatePacket`, trace CLI/index/discovery/provenance helpers, cheap-sync
  tests, and the search-eval harness/report. The pre-existing dirty
  `tests/perf/artifacts/latest/*.json` files remain unrelated.
- **CLI smoke:** `./otd trace --help` lists `discover`; `./otd trace discover
  --help` exposes `--by day`, card limits, project scoping, rebuild, and JSON.
  Real-checkout smoke `./otd trace discover trace capsule --by day --json
  --limit 3` returned `status=ok`, `topic=trace capsule`, `by=day`,
  `total_cards=3`, `group_count=3`, and limitations
  `lexical_topic_discovery,bounded_cards,no_llm`.
- **Real-bucket timing note:** two real-checkout `trace discover` runs were
  byte-deterministic but each took ~104s before returning. Follow-up marker
  inspection showed the index cheap-signal marker differed from the current
  stat-only signal immediately after the run, so this checkout is still paying
  the real-bucket freshness path under active trace churn. `trace get --card`
  on a returned real trace was fast: five runs p95 `205.30ms` with headline and
  provenance color present. The synthetic gate still proves 20 sequential + 8
  parallel query calls stay under the cliff with `sync_count=0`.
- **Final focused verification:** `.venv/bin/python -m py_compile ...` passed
  for all touched Python modules. Combined lane
  `.venv/bin/python -m pytest tests/search_eval/test_search_eval.py
  tests/core/test_trace_index_cheap_sync.py tests/core/test_index_keep_warm_g2.py
  -q` passed (`27 passed, 1 skipped`). Final `make search-eval` report is at
  `tests/search_eval/SEARCH-EVAL.md` with 25/25 green rows.

## 2026-06-01 — Final hardening update after full-suite pass/fail triage

- **Full-suite evidence:** `.venv/bin/python -m pytest tests/ -q` completed in
  `3123.29s` with `3126 passed, 178 skipped, 2 xfailed, 12 failed`. In-scope
  failures from that run were fixed:
  `tests/core/test_trace_index_plan056.py::test_trace_index_redacts_secret_shapes_from_packets_units_and_map_previews`,
  `tests/otbox/test_jtbd_ssot.py::test_jtbd_drift_check_passes_strict`, and
  `tests/integration/test_trace_trails_corpus.py::test_trace_trails_corpus_fixture_is_current`.
  Remaining failures were load-sensitive perf/watch timers plus
  `tests/test_migration_0_3_3_to_0_4.py::test_s7_real_v033_client_refuses_0_6_0_remote`,
  which is caused by a present but incomplete `/tmp/ot-v033-worktree/.venv-v033`
  missing `opentraces_schema`; the test skips only when that ephemeral venv is
  absent.
- **Diff after triage:** added fast `redact_preview_text()` for display
  summaries so cards/headlines redact assignment-shaped/provider secrets
  without running the entropy detector over the whole real bucket during cheap
  sync. Added `trace discover` to plan 063's JTBD table and to the existing
  `agent-session-to-published-dataset` journey. Regenerated the Trace Trails
  corpus fixture with the repo harness; the fixture drift is normalized
  content-hash placeholder renumbering after the projection digest changed.
- **Real-bucket root cause and final timing:** the earlier ~104s / timeout
  runs were cold freshness repairs against the live bucket, not the warm query
  path. A single explicit cheap-sync run advanced the markers for one changed
  trace (`d35eea89-ba41-4f0d-8033-bf82b6cc204f`): index sync
  `261141.35ms`, projection sync `91515.77ms`. After that, real
  `./otd trace discover trace capsule --by day --json` was deterministic across
  two runs: `1887.51ms` and `1855.22ms`, `7` day groups, `17` cards, days
  `2026-06-01`, `2026-05-30`, `2026-05-29`, `2026-05-21`, `2026-05-20`,
  `2026-04-24`, `2026-04-23`. `./otd trace get --card --json
  7e024a87-c014-4652-bb72-b4931ba9acdf` p95 was `202.94ms` over five runs,
  headline present, provenance color `uncommitted`.
- **Final harness numbers:** `make search-eval` is green: `25/25` rows,
  `invariants_ok=True`, `summary_non_boilerplate_rate=1.00`, discovery-loop
  smoke `query,map,slice,card,get,discover`, `3` eval day groups / `6` cards,
  repeated/parallel reliability `20 sequential + 8 parallel`, `sync_count=0`,
  `elapsed_ms=4038.37` under the 60s cliff.
- **Final focused verification:** `.venv/bin/python -m pytest
  tests/search_eval/test_search_eval.py tests/core/test_trace_index_cheap_sync.py
  tests/core/test_trace_index_perf.py tests/core/test_index_keep_warm_g2.py
  tests/core/test_trace_index_plan056.py::test_trace_index_redacts_secret_shapes_from_packets_units_and_map_previews
  tests/otbox/test_jtbd_ssot.py::test_jtbd_drift_check_passes_strict
  tests/integration/test_trace_trails_corpus.py::test_trace_trails_corpus_fixture_is_current
  -q` passed (`45 passed, 1 skipped`). `git diff --check -- .
  ':(exclude)tests/perf/artifacts/latest/**'` passed.
