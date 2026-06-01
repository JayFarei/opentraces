# Search Evaluation (plan 088)

Performance **and** outcome metrics for Trace Spotlight's progressive-discovery loop (`trace query -> map -> slice -> get`), measured over a deterministic, real-bucket-sized planted corpus. Regenerate with `make search-eval` (dev tier) or `make search-eval-real` (real-scale).

> **RED is expected in Phase A.** The harness is the executable spec: S2 (semantic latency), S3/S4 (identifier-needle recall + time order), and S5 (recency) are designed to fail until the ranking capabilities (U4 `--sort`, U5 recency weighting, U6 index-bounded scorer + URL/identifier tokenization) land red -> green against this report. `OK` checks that observed RED/GREEN matches each row's documented expectation.

## Tier: `dev` (seed 1)

- corpus: **150** traces, **24** query rows (24 green / 0 red)
- snapshot key: `sha256:c6fbae2ad4a488c778b575b01b4383e2c6bcc58eded676bf1495c5185a0f888c`
- outcome digest (deterministic): `sha256:ed84949a6e10ff806001d86aa5263cff392eaff8e6fb9a2cf8626ba4af69b317`
- profile digest: `sha256:754dc8669c68dbc82b5f9cf39f31ab2c7dd4fd9efce933f33d87f0fb3800111e`
- discovery-loop smoke: ok (stages: query, map, slice, get)
- **invariants_ok: yes**

### Seed Evaluation Dataset (S1–S8)

| Seed | Query | Total | Outcome | p95 ms | Status | Expected | OK |
|------|-------|------:|---------|-------:|--------|----------|----|
| S1 | `--lex do we know how to slice a trajectory per intent` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 187.34 | GREEN | green | ok |
| S1 | `--lex trajectory intent slices` | 10 | recall@10=1.00, mrr=0.11, rank=9 | 185.88 | GREEN | green | ok |
| S2 | `--semantic break a trajectory into per-intent slices` | 48 | recall@5=1.00, mrr=1.00, rank=1 | 196.21 | GREEN | green | ok |
| S3 | `--lex nico100` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 185.77 | GREEN | green | ok |
| S4 | `--lex chronotopic100` | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 185.20 | GREEN | green | ok |
| S5 | `--semantic latest work on the recencytopic100 stack` | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 203.25 | GREEN | green | ok |
| S6 | `--lex does our cli allow fast search over previous traces` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 184.79 | GREEN | green | ok |
| S7 | `--lex how many lines of code in this project` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 185.14 | GREEN | green | ok |
| S8 | `--files *src/core/intent-1-00.py` | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 194.28 | GREEN | green | ok |

### By archetype

| Archetype | n | mean recall | mean p95 ms | green | red |
|-----------|--:|------------:|------------:|------:|----:|
| boundedness_cliff | 1 | 1.00 | 205.14 | 1 | 0 |
| chronological | 3 | 1.00 | 187.58 | 3 | 0 |
| descriptive | 4 | 1.00 | 185.80 | 4 | 0 |
| facet | 2 | 1.00 | 197.03 | 2 | 0 |
| recency | 3 | 1.00 | 204.42 | 3 | 0 |
| reference_bare | 3 | 1.00 | 189.73 | 3 | 0 |
| reference_id | 3 | 1.00 | 184.26 | 3 | 0 |
| semantic_precedent | 3 | 1.00 | 191.84 | 3 | 0 |
| superseded | 2 | 1.00 | 192.73 | 2 | 0 |

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
| cliff-00 | projection_concept | 12 | 12 | 20279 | bounded | bounded | ok |

### All query rows

| Row | Archetype | Mode | Total | Outcome | p95 | Status | Bounded | OK |
|-----|-----------|------|------:|---------|----:|--------|---------|----|
| chrono-00 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 185.20 | GREEN/green | bounded (4/150) | ok |
| chrono-01 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 183.91 | GREEN/green | bounded (4/150) | ok |
| chrono-02 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 193.62 | GREEN/green | bounded (4/150) | ok |
| recency-00 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 203.25 | GREEN/green | bounded (112/20279) | ok |
| recency-01 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 203.83 | GREEN/green | bounded (112/20279) | ok |
| recency-02 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 206.18 | GREEN/green | bounded (112/20279) | ok |
| refbare-00 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 182.93 | GREEN/green | bounded (1/150) | ok |
| refbare-01 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 191.47 | GREEN/green | bounded (1/150) | ok |
| refbare-02 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 194.80 | GREEN/green | bounded (1/150) | ok |
| refid-00 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 185.77 | GREEN/green | bounded (1/150) | ok |
| refid-01 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 183.48 | GREEN/green | bounded (1/150) | ok |
| refid-02 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 183.52 | GREEN/green | bounded (1/150) | ok |
| facet-00 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 194.28 | GREEN/green | O(corpus) (150/150) | ok |
| facet-01 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 199.78 | GREEN/green | O(corpus) (150/150) | ok |
| desc-00 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 185.14 | GREEN/green | bounded (1/150) | ok |
| desc-01 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 184.79 | GREEN/green | bounded (1/150) | ok |
| desc-02 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 187.34 | GREEN/green | bounded (1/150) | ok |
| desc-03 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 185.94 | GREEN/green | bounded (1/150) | ok |
| supersede-00 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 196.89 | GREEN/green | bounded (1/150) | ok |
| supersede-01 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 188.57 | GREEN/green | bounded (1/150) | ok |
| precedent-sem-00 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=1.00, rank=1 | 196.21 | GREEN/green | bounded (48/20279) | ok |
| precedent-lex-00 | semantic_precedent | lex | 10 | recall@10=1.00, mrr=0.11, rank=9 | 185.88 | GREEN/green | bounded (10/150) | ok |
| precedent-sem-01 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=0.50, rank=2 | 193.42 | GREEN/green | bounded (48/20279) | ok |
| cliff-00 | boundedness_cliff | semantic | 12 | recall@10=1.00, mrr=1.00, rank=1 | 205.14 | GREEN/green | bounded (12/20279) | ok |

## Tier: `real-scale` (seed 1)

- corpus: **1084** traces, **24** query rows (24 green / 0 red)
- snapshot key: `sha256:65854ca6217a3128505c587f0037f97f6edf34c943039c9913fe11537f8bfc46`
- outcome digest (deterministic): `sha256:ed84949a6e10ff806001d86aa5263cff392eaff8e6fb9a2cf8626ba4af69b317`
- profile digest: `sha256:754dc8669c68dbc82b5f9cf39f31ab2c7dd4fd9efce933f33d87f0fb3800111e`
- discovery-loop smoke: ok (stages: query, map, slice, get)
- **invariants_ok: yes**

### Seed Evaluation Dataset (S1–S8)

| Seed | Query | Total | Outcome | p95 ms | Status | Expected | OK |
|------|-------|------:|---------|-------:|--------|----------|----|
| S1 | `--lex do we know how to slice a trajectory per intent` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 224.45 | GREEN | green | ok |
| S1 | `--lex trajectory intent slices` | 10 | recall@10=1.00, mrr=0.11, rank=9 | 209.95 | GREEN | green | ok |
| S2 | `--semantic break a trajectory into per-intent slices` | 48 | recall@5=1.00, mrr=1.00, rank=1 | 232.04 | GREEN | green | ok |
| S3 | `--lex nico100` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 216.05 | GREEN | green | ok |
| S4 | `--lex chronotopic100` | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 211.72 | GREEN | green | ok |
| S5 | `--semantic latest work on the recencytopic100 stack` | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 230.15 | GREEN | green | ok |
| S6 | `--lex does our cli allow fast search over previous traces` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 213.43 | GREEN | green | ok |
| S7 | `--lex how many lines of code in this project` | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.99 | GREEN | green | ok |
| S8 | `--files *src/core/intent-1-00.py` | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 297.79 | GREEN | green | ok |

### By archetype

| Archetype | n | mean recall | mean p95 ms | green | red |
|-----------|--:|------------:|------------:|------:|----:|
| boundedness_cliff | 1 | 1.00 | 221.04 | 1 | 0 |
| chronological | 3 | 1.00 | 209.28 | 3 | 0 |
| descriptive | 4 | 1.00 | 214.24 | 4 | 0 |
| facet | 2 | 1.00 | 297.53 | 2 | 0 |
| recency | 3 | 1.00 | 234.80 | 3 | 0 |
| reference_bare | 3 | 1.00 | 207.64 | 3 | 0 |
| reference_id | 3 | 1.00 | 215.54 | 3 | 0 |
| semantic_precedent | 3 | 1.00 | 225.32 | 3 | 0 |
| superseded | 2 | 1.00 | 209.07 | 2 | 0 |

### Boundedness (qmd invariant, R3)

The qmd invariant (R3): a query may scan ~its matches, not the whole corpus. `rows_scanned ~ corpus` with few matches = the O(corpus) cliff (U6 target). Deterministic, so it gates at any tier regardless of ms.

| Row | Path | matched | rows_scanned | corpus | bounded | expected | OK |
|-----|------|--------:|-------------:|-------:|---------|----------|----|
| chrono-00 | index_fts | 4 | 4 | 1084 | bounded | bounded | ok |
| chrono-01 | index_fts | 4 | 4 | 1084 | bounded | bounded | ok |
| chrono-02 | index_fts | 4 | 4 | 1084 | bounded | bounded | ok |
| recency-00 | projection_fts | 112 | 112 | 216206 | bounded | bounded | ok |
| recency-01 | projection_fts | 112 | 112 | 216206 | bounded | bounded | ok |
| recency-02 | projection_fts | 112 | 112 | 216206 | bounded | bounded | ok |
| refbare-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refbare-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refbare-02 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refid-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refid-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| refid-02 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| facet-00 | index_scan | 4 | 1084 | 1084 | O(corpus) | O(corpus) | ok |
| facet-01 | index_scan | 4 | 1084 | 1084 | O(corpus) | O(corpus) | ok |
| desc-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| desc-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| desc-02 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| desc-03 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| supersede-00 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| supersede-01 | index_fts | 1 | 1 | 1084 | bounded | bounded | ok |
| precedent-sem-00 | projection_fts | 48 | 48 | 216206 | bounded | bounded | ok |
| precedent-lex-00 | index_fts | 10 | 10 | 1084 | bounded | bounded | ok |
| precedent-sem-01 | projection_fts | 48 | 48 | 216206 | bounded | bounded | ok |
| cliff-00 | projection_concept | 12 | 12 | 216206 | bounded | bounded | ok |

### All query rows

| Row | Archetype | Mode | Total | Outcome | p95 | Status | Bounded | OK |
|-----|-----------|------|------:|---------|----:|--------|---------|----|
| chrono-00 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 211.72 | GREEN/green | bounded (4/1084) | ok |
| chrono-01 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 207.86 | GREEN/green | bounded (4/1084) | ok |
| chrono-02 | chronological | lex | 4 | recall@10=1.00, tau=1.00, mrr=1.00, rank=1 | 208.27 | GREEN/green | bounded (4/1084) | ok |
| recency-00 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 230.15 | GREEN/green | bounded (112/216206) | ok |
| recency-01 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 233.09 | GREEN/green | bounded (112/216206) | ok |
| recency-02 | recency | semantic | 112 | recall@10=1.00, rec@1=1.00, mrr=1.00, rank=1 | 241.16 | GREEN/green | bounded (112/216206) | ok |
| refbare-00 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 207.08 | GREEN/green | bounded (1/1084) | ok |
| refbare-01 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.21 | GREEN/green | bounded (1/1084) | ok |
| refbare-02 | reference_bare | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 206.62 | GREEN/green | bounded (1/1084) | ok |
| refid-00 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 216.05 | GREEN/green | bounded (1/1084) | ok |
| refid-01 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.36 | GREEN/green | bounded (1/1084) | ok |
| refid-02 | reference_id | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 221.21 | GREEN/green | bounded (1/1084) | ok |
| facet-00 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 297.79 | GREEN/green | O(corpus) (1084/1084) | ok |
| facet-01 | facet | files | 4 | recall@20=1.00, rec@1=0.00, mrr=1.00, rank=1 | 297.27 | GREEN/green | O(corpus) (1084/1084) | ok |
| desc-00 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.99 | GREEN/green | bounded (1/1084) | ok |
| desc-01 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 213.43 | GREEN/green | bounded (1/1084) | ok |
| desc-02 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 224.45 | GREEN/green | bounded (1/1084) | ok |
| desc-03 | descriptive | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.09 | GREEN/green | bounded (1/1084) | ok |
| supersede-00 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 209.90 | GREEN/green | bounded (1/1084) | ok |
| supersede-01 | superseded | lex | 1 | recall@10=1.00, mrr=1.00, rank=1 | 208.24 | GREEN/green | bounded (1/1084) | ok |
| precedent-sem-00 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=1.00, rank=1 | 232.04 | GREEN/green | bounded (48/216206) | ok |
| precedent-lex-00 | semantic_precedent | lex | 10 | recall@10=1.00, mrr=0.11, rank=9 | 209.95 | GREEN/green | bounded (10/1084) | ok |
| precedent-sem-01 | semantic_precedent | semantic | 48 | recall@5=1.00, mrr=0.50, rank=2 | 233.98 | GREEN/green | bounded (48/216206) | ok |
| cliff-00 | boundedness_cliff | semantic | 12 | recall@10=1.00, mrr=1.00, rank=1 | 221.04 | GREEN/green | bounded (12/216206) | ok |

## Scaling slope: `dev` → `real-scale` (150 → 1084 traces, 7.23× growth)

The qmd invariant (R2): for a fixed result size, bounded-query p95 stays ~flat as the corpus grows (ratio ≤ 3.0×). O(corpus) rows (the --files facet scan) are exempt and track corpus growth.

- **bounded queries within slope: yes**

| Row | Archetype | bounded | small p95 | large p95 | ratio | OK |
|-----|-----------|---------|----------:|----------:|------:|----|
| chrono-00 | chronological | bounded | 185.2 | 211.72 | 1.14 | ok |
| chrono-01 | chronological | bounded | 183.91 | 207.86 | 1.13 | ok |
| chrono-02 | chronological | bounded | 193.62 | 208.27 | 1.08 | ok |
| cliff-00 | boundedness_cliff | bounded | 205.14 | 221.04 | 1.08 | ok |
| desc-00 | descriptive | bounded | 185.14 | 209.99 | 1.13 | ok |
| desc-01 | descriptive | bounded | 184.79 | 213.43 | 1.15 | ok |
| desc-02 | descriptive | bounded | 187.34 | 224.45 | 1.2 | ok |
| desc-03 | descriptive | bounded | 185.94 | 209.09 | 1.12 | ok |
| precedent-lex-00 | semantic_precedent | bounded | 185.88 | 209.95 | 1.13 | ok |
| precedent-sem-00 | semantic_precedent | bounded | 196.21 | 232.04 | 1.18 | ok |
| precedent-sem-01 | semantic_precedent | bounded | 193.42 | 233.98 | 1.21 | ok |
| recency-00 | recency | bounded | 203.25 | 230.15 | 1.13 | ok |
| recency-01 | recency | bounded | 203.83 | 233.09 | 1.14 | ok |
| recency-02 | recency | bounded | 206.18 | 241.16 | 1.17 | ok |
| refbare-00 | reference_bare | bounded | 182.93 | 207.08 | 1.13 | ok |
| refbare-01 | reference_bare | bounded | 191.47 | 209.21 | 1.09 | ok |
| refbare-02 | reference_bare | bounded | 194.8 | 206.62 | 1.06 | ok |
| refid-00 | reference_id | bounded | 185.77 | 216.05 | 1.16 | ok |
| refid-01 | reference_id | bounded | 183.48 | 209.36 | 1.14 | ok |
| refid-02 | reference_id | bounded | 183.52 | 221.21 | 1.21 | ok |
| supersede-00 | superseded | bounded | 196.89 | 209.9 | 1.07 | ok |
| supersede-01 | superseded | bounded | 188.57 | 208.24 | 1.1 | ok |
| facet-00 | facet | O(corpus) | 194.28 | 297.79 | 1.53 | ok |
| facet-01 | facet | O(corpus) | 199.78 | 297.27 | 1.49 | ok |

---
_Outcome metrics are deterministic (recall/MRR/NDCG/tau/recency-hit over distinct traces); perf metrics are wall-clock (gate on scaling slope + counters, not absolute ms — see budgets). Generated by `tests/search_eval/report.py`._
