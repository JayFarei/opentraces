# Search Evaluation (plan 088)

Performance **and** outcome metrics for Trace Spotlight's progressive-discovery loop (`trace query -> map -> slice -> get`), measured over a deterministic, real-bucket-sized planted corpus. Regenerate with `make search-eval` (dev tier) or `make search-eval-real` (real-scale).

> **RED is expected in Phase A.** The harness is the executable spec: S2 (semantic latency), S3/S4 (identifier-needle recall + time order), and S5 (recency) are designed to fail until the ranking capabilities (U4 `--sort`, U5 recency weighting, U6 index-bounded scorer + URL/identifier tokenization) land red -> green against this report. `OK` checks that observed RED/GREEN matches each row's documented expectation.

## Tier: `dev` (seed 1)

- corpus: **150** traces, **24** query rows (24 green / 0 red)
- snapshot key: `sha256:35eec81a621dc0805afcac81b984d8c5d6e1fa1b9e4711f06f8772c1013d8846`
- outcome digest (deterministic): `sha256:aac617fa3624d69103715ad99702c59343ea66254c9404f90cc0b525b080ba80`
- profile digest: `sha256:754dc8669c68dbc82b5f9cf39f31ab2c7dd4fd9efce933f33d87f0fb3800111e`
- discovery-loop smoke: ok (stages: query, map, slice, get)
- **invariants_ok: yes**

### Seed Evaluation Dataset (S1–S8)

| Seed | Query | Total | Outcome | p95 ms | Status | Expected | OK |
|------|-------|------:|---------|-------:|--------|----------|----|
| S1 | `--lex do we know how to slice a trajectory per intent` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 188.30 | GREEN | green | ok |
| S1 | `--lex trajectory intent slices` | 10 | recall@10=1.00, mrr=0.11, rank=9 | 194.58 | GREEN | green | ok |
| S2 | `--semantic break a trajectory into per-intent slices` | 48 | recall@5=1.00, mrr=1.00, rank=1 | 201.90 | GREEN | green | ok |
| S3 | `--lex nico100` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 188.73 | GREEN | green | ok |
| S4 | `--lex chronotopic100` | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 193.44 | GREEN | green | ok |
| S5 | `--semantic latest work on the recencytopic100 stack` | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 212.04 | GREEN | green | ok |
| S6 | `--lex does our cli allow fast search over previous traces` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 199.77 | GREEN | green | ok |
| S7 | `--lex how many lines of code in this project` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 188.67 | GREEN | green | ok |
| S8 | `--files *src/core/intent-1-00.py` | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 204.61 | GREEN | green | ok |

### By archetype

| Archetype | n | mean recall | mean p95 ms | green | red |
|-----------|--:|------------:|------------:|------:|----:|
| boundedness_cliff | 1 | 1.00 | 373.77 | 1 | 0 |
| chronological | 3 | 1.00 | 192.75 | 3 | 0 |
| descriptive | 4 | 1.00 | 192.16 | 4 | 0 |
| facet | 2 | 1.00 | 202.69 | 2 | 0 |
| recency | 3 | 1.00 | 210.92 | 3 | 0 |
| reference_bare | 3 | 1.00 | 190.97 | 3 | 0 |
| reference_id | 3 | 1.00 | 192.66 | 3 | 0 |
| semantic_precedent | 3 | 1.00 | 201.56 | 3 | 0 |
| superseded | 2 | 1.00 | 188.60 | 2 | 0 |

### Boundedness (qmd invariant, R3)

The qmd invariant (R3): a query may scan ~its matches, not the whole corpus. `rows_scanned ~ corpus` with few matches = the O(corpus) cliff (U6 target). Deterministic, so it gates at any tier regardless of ms.

| Row | Path | matched | rows_scanned | corpus | bounded | expected | OK |
|-----|------|--------:|-------------:|-------:|---------|----------|----|
| chrono-00 | index_fts | 4 | 4 | 150 | bounded | bounded | ok |
| chrono-01 | index_fts | 4 | 4 | 150 | bounded | bounded | ok |
| chrono-02 | index_fts | 4 | 4 | 150 | bounded | bounded | ok |
| recency-00 | projection_fts | 112 | 112 | 20279 | bounded | bounded | ok |
| recency-01 | projection_fts | 112 | 112 | 20279 | bounded | bounded | ok |
| recency-02 | projection_fts | 112 | 112 | 20279 | bounded | bounded | ok |
| refbare-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refbare-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refbare-02 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refid-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refid-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| refid-02 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| facet-00 | index_scan | 4 | 150 | 150 | O(corpus) | O(corpus) | ok |
| facet-01 | index_scan | 4 | 150 | 150 | O(corpus) | O(corpus) | ok |
| desc-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| desc-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| desc-02 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| desc-03 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| supersede-00 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| supersede-01 | index_fts | 1 | 1 | 150 | bounded | bounded | ok |
| precedent-sem-00 | projection_fts | 48 | 48 | 20279 | bounded | bounded | ok |
| precedent-lex-00 | index_fts | 10 | 10 | 150 | bounded | bounded | ok |
| precedent-sem-01 | projection_fts | 48 | 48 | 20279 | bounded | bounded | ok |
| cliff-00 | projection_concept | 12 | 20279 | 20279 | O(corpus) | O(corpus) | ok |

### All query rows

| Row | Archetype | Mode | Total | Outcome | p95 | Status | Bounded | OK |
|-----|-----------|------|------:|---------|----:|--------|---------|----|
| chrono-00 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 193.44 | GREEN/green | bounded (4/150) | ok |
| chrono-01 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 191.60 | GREEN/green | bounded (4/150) | ok |
| chrono-02 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 193.20 | GREEN/green | bounded (4/150) | ok |
| recency-00 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 212.04 | GREEN/green | bounded (112/20279) | ok |
| recency-01 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 209.27 | GREEN/green | bounded (112/20279) | ok |
| recency-02 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 211.45 | GREEN/green | bounded (112/20279) | ok |
| refbare-00 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 189.30 | GREEN/green | bounded (1/150) | ok |
| refbare-01 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 187.46 | GREEN/green | bounded (1/150) | ok |
| refbare-02 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 196.15 | GREEN/green | bounded (1/150) | ok |
| refid-00 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 188.73 | GREEN/green | bounded (1/150) | ok |
| refid-01 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 197.13 | GREEN/green | bounded (1/150) | ok |
| refid-02 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 192.11 | GREEN/green | bounded (1/150) | ok |
| facet-00 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 204.61 | GREEN/green | O(corpus) (150/150) | ok |
| facet-01 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 200.78 | GREEN/green | O(corpus) (150/150) | ok |
| desc-00 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 188.67 | GREEN/green | bounded (1/150) | ok |
| desc-01 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 199.77 | GREEN/green | bounded (1/150) | ok |
| desc-02 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 188.30 | GREEN/green | bounded (1/150) | ok |
| desc-03 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 191.90 | GREEN/green | bounded (1/150) | ok |
| supersede-00 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 187.48 | GREEN/green | bounded (1/150) | ok |
| supersede-01 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 189.72 | GREEN/green | bounded (1/150) | ok |
| precedent-sem-00 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=1.00, rank=1 | 201.90 | GREEN/green | bounded (48/20279) | ok |
| precedent-lex-00 | semantic_precedent | lex | 10 | recall@10=1.00, mrr=0.11, rank=9 | 194.58 | GREEN/green | bounded (10/150) | ok |
| precedent-sem-01 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=0.50, rank=2 | 208.20 | GREEN/green | bounded (48/20279) | ok |
| cliff-00 | boundedness_cliff | semantic | 12 | recall@10=1.00, mrr=1.00, rank=1 | 373.77 | GREEN/green | O(corpus) (20279/20279) | ok |

---
_Outcome metrics are deterministic (recall/MRR/NDCG/tau/recency-hit over distinct traces); perf metrics are wall-clock (gate on scaling slope + counters, not absolute ms — see budgets). Generated by `tests/search_eval/report.py`._
