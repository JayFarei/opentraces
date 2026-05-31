# Search Evaluation (plan 088)

Performance **and** outcome metrics for Trace Spotlight's progressive-discovery loop (`trace query -> map -> slice -> get`), measured over a deterministic, real-bucket-sized planted corpus. Regenerate with `make search-eval` (dev tier) or `make search-eval-real` (real-scale).

> **RED is expected in Phase A.** The harness is the executable spec: S2 (semantic latency), S3/S4 (identifier-needle recall + time order), and S5 (recency) are designed to fail until the ranking capabilities (U4 `--sort`, U5 recency weighting, U6 index-bounded scorer + URL/identifier tokenization) land red -> green against this report. `OK` checks that observed RED/GREEN matches each row's documented expectation.

## Tier: `dev` (seed 1)

- corpus: **150** traces, **23** query rows (14 green / 9 red)
- snapshot key: `sha256:05a5c19cf1688ebc0978111a049d699f56c918176d3fada9f32732df6dddcc99`
- outcome digest (deterministic): `sha256:b6a724cc97e31b55e72dc68c306dd786d6d8f0ca285081d4982c418b082e1ac5`
- profile digest: `sha256:754dc8669c68dbc82b5f9cf39f31ab2c7dd4fd9efce933f33d87f0fb3800111e`
- discovery-loop smoke: ok (stages: query, map, slice, get)
- **invariants_ok: yes**

### Seed Evaluation Dataset (S1–S8)

| Seed | Query | Total | Outcome | p95 ms | Status | Expected | OK |
|------|-------|------:|---------|-------:|--------|----------|----|
| S1 | `--lex do we know how to slice a trajectory per intent` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 186.62 | GREEN | green | ok |
| S1 | `--lex trajectory intent slices` | 10 | recall@10=1.00, mrr=0.11, rank=9 | 198.64 | GREEN | green | ok |
| S2 | `--semantic break a trajectory into per-intent slices` | 48 | recall@5=1.00, mrr=1.00, rank=1 | 203.13 | GREEN | green | ok |
| S3 | `--lex nico100` | 0 | recall@10=0.00, mrr=0.00 | 192.04 | RED | red | ok |
| S4 | `--lex chronotopic100` | 4 | recall@10=1.00, tau=-1.00, mrr=1.00, rank=1 | 190.69 | RED | red | ok |
| S5 | `--semantic latest work on the recencytopic100 stack` | 112 | recall@10=1.00, rec@1=0.00, mrr=1.00, rank=1 | 200.63 | RED | red | ok |
| S6 | `--lex does our cli allow fast search over previous traces` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 184.31 | GREEN | green | ok |
| S7 | `--lex how many lines of code in this project` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 181.34 | GREEN | green | ok |
| S8 | `--files *src/core/intent-1-00.py` | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 196.94 | GREEN | green | ok |

### By archetype

| Archetype | n | mean recall | mean p95 ms | green | red |
|-----------|--:|------------:|------------:|------:|----:|
| chronological | 3 | 1.00 | 185.52 | 0 | 3 |
| descriptive | 4 | 1.00 | 184.39 | 4 | 0 |
| facet | 2 | 1.00 | 195.72 | 2 | 0 |
| recency | 3 | 1.00 | 202.43 | 0 | 3 |
| reference_bare | 3 | 1.00 | 182.82 | 3 | 0 |
| reference_id | 3 | 0.00 | 189.05 | 0 | 3 |
| semantic_precedent | 3 | 1.00 | 198.51 | 3 | 0 |
| superseded | 2 | 1.00 | 184.51 | 2 | 0 |

### All query rows

| Row | Archetype | Mode | Total | Outcome | p50 | p95 | cold | Status | Exp | OK |
|-----|-----------|------|------:|---------|----:|----:|-----:|--------|-----|----|
| chrono-00 | chronological | lex | 4 | recall@10=1.00, tau=-1.00, mrr=1.00, rank=1 | 187.79 | 190.69 | 187.51 | RED | red | ok |
| chrono-01 | chronological | lex | 4 | recall@10=1.00, tau=-1.00, mrr=1.00, rank=1 | 179.63 | 182.08 | 179.63 | RED | red | ok |
| chrono-02 | chronological | lex | 4 | recall@10=1.00, tau=-1.00, mrr=1.00, rank=1 | 181.04 | 183.78 | 181.04 | RED | red | ok |
| recency-00 | recency | semantic | 112 | recall@10=1.00, rec@1=0.00, mrr=1.00, rank=1 | 199.30 | 200.63 | 196.87 | RED | red | ok |
| recency-01 | recency | semantic | 112 | recall@10=1.00, rec@1=0.00, mrr=1.00, rank=1 | 201.62 | 205.06 | 205.06 | RED | red | ok |
| recency-02 | recency | semantic | 112 | recall@10=1.00, rec@1=0.00, mrr=1.00, rank=1 | 201.16 | 201.59 | 199.50 | RED | red | ok |
| refbare-00 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 180.56 | 182.29 | 179.78 | GREEN | green | ok |
| refbare-01 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 184.21 | 184.72 | 184.72 | GREEN | green | ok |
| refbare-02 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 180.22 | 181.44 | 179.79 | GREEN | green | ok |
| refid-00 | reference_id | lex | 0 | recall@10=0.00, mrr=0.00 | 181.54 | 192.04 | 181.54 | RED | red | ok |
| refid-01 | reference_id | lex | 0 | recall@10=0.00, mrr=0.00 | 187.78 | 192.28 | 179.17 | RED | red | ok |
| refid-02 | reference_id | lex | 0 | recall@10=0.00, mrr=0.00 | 182.75 | 182.83 | 182.75 | RED | red | ok |
| facet-00 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 195.69 | 196.94 | 195.69 | GREEN | green | ok |
| facet-01 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 193.44 | 194.51 | 193.44 | GREEN | green | ok |
| desc-00 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 180.99 | 181.34 | 180.99 | GREEN | green | ok |
| desc-01 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 180.85 | 184.31 | 184.31 | GREEN | green | ok |
| desc-02 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 180.16 | 186.62 | 180.16 | GREEN | green | ok |
| desc-03 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 180.79 | 185.30 | 180.79 | GREEN | green | ok |
| supersede-00 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 185.84 | 187.93 | 185.84 | GREEN | green | ok |
| supersede-01 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 180.25 | 181.09 | 179.44 | GREEN | green | ok |
| precedent-sem-00 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=1.00, rank=1 | 190.70 | 203.13 | 190.70 | GREEN | green | ok |
| precedent-lex-00 | semantic_precedent | lex | 10 | recall@10=1.00, mrr=0.11, rank=9 | 187.70 | 198.64 | 184.76 | GREEN | green | ok |
| precedent-sem-01 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=0.50, rank=2 | 189.57 | 193.76 | 193.76 | GREEN | green | ok |

---
_Outcome metrics are deterministic (recall/MRR/NDCG/tau/recency-hit over distinct traces); perf metrics are wall-clock (gate on scaling slope + counters, not absolute ms — see budgets). Generated by `tests/search_eval/report.py`._
